import logging
from pathlib import Path
from datetime import datetime
import json
import traceback
from typing import Callable, Optional, Any

from backend.config import get_db_path
from backend.models.database import (
    get_engine, create_tables, get_session,
    FilingIndex, ExtractionLog, AnalysisRun
)
from backend.models.schemas import CATEGORY_SCHEMA_MAP
from backend.services.sec_downloader import SECDownloader
from backend.services.filing_classifier import classify_filing, update_filing_categories
from backend.services.parquet_io import write_records_to_parquet
import backend.extractors as extractors

logger = logging.getLogger(__name__)

# Map category name to extractor function
CATEGORY_EXTRACTOR_MAP = {
    "shares_outstanding": extractors.extract_shares_outstanding,
    "completed_offering": extractors.extract_completed_offering,
    "atm": extractors.extract_atm,
    "shelf": extractors.extract_shelf,
    "warrant": extractors.extract_warrant,
    "convertible_note": extractors.extract_convertible_note,
    "convertible_preferred": extractors.extract_convertible_preferred,
    "equity_line": extractors.extract_equity_line,
    "s1_offering": extractors.extract_s1_offering,
    "cash_position": extractors.extract_cash_position,
}

def run_analysis_pipeline(
    tickers: list[str],
    data_root: str | Path,
    date_from: str,
    date_to: str,
    progress_callback: Optional[Callable[[dict], Any]] = None
) -> dict:
    """
    Run end-to-end SEC analysis pipeline for a list of tickers.
    
    progress_callback: function that accepts a dict with progress updates
    """
    data_root = Path(data_root)
    db_path = get_db_path(data_root)
    engine = get_engine(db_path)
    create_tables(engine)
    session = get_session(engine)
    
    # Register AnalysisRun
    run_record = AnalysisRun(
        tickers=",".join(tickers),
        data_root=str(data_root),
        date_from=date_from,
        date_to=date_to,
        status="running",
        started_at=datetime.utcnow()
    )
    session.add(run_record)
    session.commit()
    run_id = run_record.id
    
    summary = {}
    
    try:
        downloader = SECDownloader(session)
        
        for ticker_idx, ticker in enumerate(tickers):
            ticker = ticker.upper().strip()
            if not ticker:
                continue
                
            try:
                logger.info(f"Starting pipeline for {ticker}")
                if progress_callback:
                    progress_callback({
                        "ticker": ticker,
                        "status": "fetching",
                        "progress": (ticker_idx + 0.1) / len(tickers),
                        "current_category": None,
                        "categories_done": [],
                        "errors": []
                    })
                    
                # 1. Fetch filings
                filings = downloader.fetch_filings(
                    ticker=ticker,
                    date_from=date_from,
                    date_to=date_to,
                    progress_callback=lambda msg: logger.info(f"[{ticker}] {msg}")
                )
                
                if not filings:
                    logger.warning(f"No filings found for {ticker}")
                    summary[ticker] = {"status": "no_filings", "records": {}}
                    if progress_callback:
                        progress_callback({
                            "ticker": ticker,
                            "status": "completed",
                            "progress": (ticker_idx + 1.0) / len(tickers),
                            "current_category": None,
                            "categories_done": list(CATEGORY_EXTRACTOR_MAP.keys()),
                            "errors": ["No filings found"]
                        })
                    continue
                    
                # Deduplicate filings by accession number to avoid duplicate processing
                seen_accessions = set()
                unique_filings = []
                for f in filings:
                    if f.accession_no not in seen_accessions:
                        seen_accessions.add(f.accession_no)
                        unique_filings.append(f)
                filings = unique_filings
                    
                # 2. Classify filings
                logger.info(f"Classifying filings for {ticker}")
                from backend.services.filing_classifier import _REFINE_FORMS
                for filing in filings:
                    try:
                        form_upper = filing.form.upper().strip()
                        text = None
                        if form_upper in _REFINE_FORMS:
                            text = downloader.get_filing_text(filing)
                        categories = classify_filing(filing, text)
                        update_filing_categories(session, filing.accession_no, categories)
                    except Exception as e:
                        logger.error(f"Failed to classify filing {filing.accession_no}: {e}")
                        
                # 3. Extract and save by category
                categories_done = []
                ticker_errors = []
                ticker_summary = {}
                
                for cat_idx, (category, extractor_fn) in enumerate(CATEGORY_EXTRACTOR_MAP.items()):
                    logger.info(f"Extracting category {category} for {ticker}")
                    if progress_callback:
                        progress_callback({
                            "ticker": ticker,
                            "status": "extracting",
                            "progress": (ticker_idx + 0.2 + 0.7 * (cat_idx / len(CATEGORY_EXTRACTOR_MAP))) / len(tickers),
                            "current_category": category,
                            "categories_done": categories_done.copy(),
                            "errors": ticker_errors.copy()
                        })
                        
                    try:
                        records_dicts = extractor_fn(ticker, filings, downloader)
                        
                        # Convert to Pydantic models for validation and schema enforcement
                        schema_cls = CATEGORY_SCHEMA_MAP[category]
                        valid_records = []
                        
                        for r_dict in records_dicts:
                            try:
                                # Set missing fields to default values
                                valid_r = schema_cls(**r_dict)
                                valid_records.append(valid_r)
                            except Exception as val_err:
                                logger.warning(f"Validation error for {ticker} {category} record: {val_err}")
                                
                        # Write to parquet
                        if valid_records:
                            write_records_to_parquet(valid_records, category, ticker, data_root)
                            records_count = len(valid_records)
                        else:
                            records_count = 0
                            
                        ticker_summary[category] = records_count
                        
                        # Log extraction success
                        log_entry = ExtractionLog(
                            accession_number=filings[0].accession_no if filings else "N/A",
                            ticker=ticker,
                            category=category,
                            status="success",
                            records_extracted=records_count
                        )
                        session.add(log_entry)
                        session.commit()
                        
                    except Exception as e:
                        err_msg = f"Error extracting {category}: {str(e)}"
                        logger.error(err_msg, exc_info=True)
                        ticker_errors.append(err_msg)
                        
                        log_entry = ExtractionLog(
                            accession_number=filings[0].accession_no if filings else "N/A",
                            ticker=ticker,
                            category=category,
                            status="error",
                            error_message=traceback.format_exc()[:1000],
                            records_extracted=0
                        )
                        session.add(log_entry)
                        session.commit()
                        
                    categories_done.append(category)
                    
                summary[ticker] = {"status": "success", "records": ticker_summary, "errors": ticker_errors}
                
                # Ticker completed
                if progress_callback:
                    progress_callback({
                        "ticker": ticker,
                        "status": "completed",
                        "progress": (ticker_idx + 1.0) / len(tickers),
                        "current_category": None,
                        "categories_done": categories_done.copy(),
                        "errors": ticker_errors.copy()
                    })
            except Exception as ticker_err:
                logger.error(f"Pipeline failed for ticker {ticker}: {ticker_err}", exc_info=True)
                summary[ticker] = {"status": "failed", "records": {}, "errors": [str(ticker_err)]}
                if progress_callback:
                    progress_callback({
                        "ticker": ticker,
                        "status": "failed",
                        "progress": (ticker_idx + 1.0) / len(tickers),
                        "current_category": None,
                        "categories_done": [],
                        "errors": [str(ticker_err)]
                    })
                
        # Pipeline finished
        session.query(AnalysisRun).filter_by(id=run_id).update({
            "status": "completed",
            "completed_at": datetime.utcnow(),
            "results_summary": json.dumps(summary)
        })
        session.commit()
        
    except Exception as run_err:
        logger.exception("Analysis run failed")
        session.query(AnalysisRun).filter_by(id=run_id).update({
            "status": "failed",
            "completed_at": datetime.utcnow(),
            "results_summary": json.dumps({"error": str(run_err)})
        })
        session.commit()
        raise run_err
    finally:
        session.close()
        
    return summary
