import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import os
import shutil

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import DEFAULT_DATA_ROOT, DEFAULT_DATE_FROM, DEFAULT_DATE_TO, get_db_path
from backend.models.schemas import AnalysisRequest, AnalysisStatus
from backend.models.database import get_engine, get_session, AnalysisRun, create_tables
from backend.services.parquet_io import read_parquet_for_ticker, list_analyzed_tickers
from backend.pipeline import run_analysis_pipeline
from backend.services.recommendation_service import generate_recommendation, get_pricing_levels, CustomStreamer
from backend.services.exchange_resolver import resolve_ticker_exchange

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sec_analyzer_api")

app = FastAPI(title="SEC Analyzer API", version="1.0.0")

# Enable CORS for frontend UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active ticker statuses in memory for fast lookup
active_statuses: Dict[str, Dict[str, Any]] = {}

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket client disconnected")

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send websocket message: {e}")

manager = ConnectionManager()

@app.on_event("startup")
def startup_event():
    # Ensure tables exist
    db_path = get_db_path(DEFAULT_DATA_ROOT)
    engine = get_engine(db_path)
    create_tables(engine)
    logger.info("FastAPI backend started and database tables verified.")

@app.post("/analyze")
async def analyze_tickers(request: AnalysisRequest, background_tasks: BackgroundTasks):
    """
    Kick off SEC analysis pipeline in the background.
    """
    tickers = request.tickers
    if not tickers:
        raise HTTPException(status_code=400, detail="Ticker list cannot be empty")
        
    loop = asyncio.get_running_loop()
    
    # Define progress callback to save status and broadcast to clients
    def progress_callback(progress_data: dict):
        ticker = progress_data.get("ticker", "UNKNOWN")
        active_statuses[ticker] = progress_data
        
        # Schedule the coroutine on the main event loop
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({
                "type": "progress_update",
                "data": progress_data
            }),
            loop
        )

    # Queue the background pipeline task
    background_tasks.add_task(
        run_analysis_pipeline,
        tickers=tickers,
        data_root=request.data_root,
        date_from=request.date_from,
        date_to=request.date_to,
        progress_callback=progress_callback
    )
    
    return {
        "status": "queued",
        "message": f"Analysis queued for tickers: {', '.join(tickers)}",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/status")
def get_all_statuses():
    """Get the current progress status for all active/recent tickers."""
    return active_statuses

@app.get("/tickers")
def get_analyzed_tickers(data_root: str = "D:\\tmp"):
    """List all tickers that have generated parquet files across categories."""
    try:
        return list_analyzed_tickers(data_root)
    except Exception as e:
        logger.error(f"Error scanning parquet files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/tickers/{ticker}")
def delete_ticker(ticker: str, data_root: str = "D:\\tmp"):
    """
    Delete all database entries and parquet files associated with a ticker.
    """
    ticker = ticker.upper().strip()
    logger.info(f"Received request to delete ticker: {ticker}")
    
    # 1. Database Deletion
    db_path = get_db_path(data_root)
    engine = get_engine(db_path)
    session = get_session(engine)
    
    try:
        from backend.models.database import FilingIndex, FilingCache, ExtractionLog, TickerMetadata
        
        # Find accession numbers to clean up filing cache
        accession_nos = session.query(FilingIndex.accession_number).filter(FilingIndex.ticker == ticker).all()
        accession_nos = [r[0] for r in accession_nos]
        
        if accession_nos:
            session.query(FilingCache).filter(FilingCache.accession_number.in_(accession_nos)).delete(synchronize_session=False)
            
        session.query(FilingIndex).filter(FilingIndex.ticker == ticker).delete(synchronize_session=False)
        session.query(ExtractionLog).filter(ExtractionLog.ticker == ticker).delete(synchronize_session=False)
        session.query(TickerMetadata).filter(TickerMetadata.ticker == ticker).delete(synchronize_session=False)
        
        session.commit()
        logger.info(f"SQL database records for {ticker} deleted successfully.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error deleting database records for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database deletion error: {str(e)}")
    finally:
        session.close()
        
    # 2. Parquet Files Deletion
    from backend.models.schemas import CATEGORY_SCHEMA_MAP
    
    deleted_files = []
    data_root_path = Path(data_root)
    for category in CATEGORY_SCHEMA_MAP.keys():
        parquet_path = data_root_path / f"filings_{category}" / f"{ticker}.parquet"
        if parquet_path.exists():
            try:
                parquet_path.unlink()
                deleted_files.append(str(parquet_path))
            except Exception as e:
                logger.error(f"Error deleting parquet file {parquet_path}: {e}")
                
    # 3. Clear memory statuses
    if ticker in active_statuses:
        del active_statuses[ticker]
        
    return {
        "status": "success",
        "ticker": ticker,
        "message": f"Deleted database entries and {len(deleted_files)} parquet files for ticker {ticker}."
    }

@app.get("/filings/{ticker}")
def get_ticker_filings(
    ticker: str,
    data_root: str = "D:\\tmp",
    date_from: str = "2020-01-01",
    date_to: str = "2026-05-25"
):
    """Retrieve the full list of SEC filings for a given ticker and date range."""
    ticker = ticker.upper().strip()
    db_path = get_db_path(data_root)
    engine = get_engine(db_path)
    session = get_session(engine)
    
    try:
        from backend.models.database import FilingIndex
        from backend.models.schemas import CATEGORY_SCHEMA_MAP
        
        # 1. Fetch filings from database
        filings = session.query(FilingIndex).filter(
            FilingIndex.ticker == ticker,
            FilingIndex.filing_date >= date_from,
            FilingIndex.filing_date <= date_to
        ).all()
        
        if not filings:
            # On-the-fly sync of filing metadata if empty
            logger.info(f"No cached filings found for {ticker} in range {date_from} to {date_to}. Fetching metadata on-the-fly.")
            from backend.services.sec_downloader import SECDownloader
            downloader = SECDownloader(session)
            downloader.fetch_filings(ticker, date_from, date_to)
            
            # Re-query
            filings = session.query(FilingIndex).filter(
                FilingIndex.ticker == ticker,
                FilingIndex.filing_date >= date_from,
                FilingIndex.filing_date <= date_to
            ).all()
            
        # 2. Get used accession numbers per category
        used_accessions_by_cat = {}
        for category in CATEGORY_SCHEMA_MAP.keys():
            used_accessions_by_cat[category] = set()
            try:
                df = read_parquet_for_ticker(category, ticker, data_root)
                if df is not None and not df.empty:
                    if "accession_number" in df.columns:
                        accs = df["accession_number"].dropna().unique()
                        for acc in accs:
                            used_accessions_by_cat[category].add(str(acc).strip())
            except Exception as e:
                logger.error(f"Error reading parquet for accession check of {ticker} {category}: {e}")
                
        # 3. Categorization taxonomy helper
        def get_dataset_category(form_type: str) -> str:
            form = form_type.upper().strip()
            if form.startswith("10-K") or form.startswith("10Q") or form.startswith("10-Q") or form.endswith("-F") or form in ("20-F", "40-F"):
                return "Annual & Quarterly Reports"
            elif form.startswith("8-K") or form.startswith("6-K") or form == "1-U":
                return "Material Events & Disclosures"
            elif "14A" in form or "14C" in form or form.startswith("DEF") or form.startswith("PRE"):
                return "Proxy & Shareholder Voting"
            elif form in ("3", "4", "5", "144") or form.startswith("FORM 3") or form.startswith("FORM 4") or form.startswith("FORM 5"):
                return "Corporate Insiders (§16)"
            elif form.startswith("13D") or form.startswith("13G"):
                return "Beneficial Ownership (5%+)"
            elif form.startswith("13F"):
                return "Institutional Holdings"
            elif form.startswith("N-") or "497" in form:
                return "Fund Registration & Holdings"
            elif form.startswith("S-") or form.startswith("F-") or "424" in form or form in ("EFFECT", "POS AM", "FWP"):
                return "Public Offerings & Prospectuses"
            elif form in ("D", "1-A", "1-K", "1-Z", "C") or form.startswith("FORM D"):
                return "Exempt Offerings"
            elif form in ("X-17A-5", "TA-1", "TA-2", "MA", "ATS-N"):
                return "Broker-Dealers & Market Infrastructure"
            else:
                return "Other Filings"
                
        # 4. Format and flag filings list
        results = []
        for f in filings:
            acc = str(f.accession_number).strip()
            used_flags = {}
            for category in CATEGORY_SCHEMA_MAP.keys():
                used_flags[f"used_in_{category}"] = acc in used_accessions_by_cat[category]
                
            results.append({
                "ticker": f.ticker,
                "accession_number": acc,
                "form_type": f.form_type,
                "filing_date": f.filing_date,
                "filing_url": f.filing_url,
                "company_name": f.company_name,
                "cik": f.cik,
                "dataset_category": get_dataset_category(f.form_type),
                **used_flags
            })
            
        # Sort by date descending
        results.sort(key=lambda x: x["filing_date"], reverse=True)
        return results
        
    except Exception as e:
        logger.error(f"Error fetching filings for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

@app.get("/results/{ticker}/{category}")
def get_results(ticker: str, category: str, data_root: str = "D:\\tmp"):
    """Retrieve extracted records from the parquet files for a given ticker and category."""
    ticker = ticker.upper().strip()
    category = category.lower().strip()
    
    try:
        df = read_parquet_for_ticker(category, ticker, data_root)
        if df is None or df.empty:
            return []
        
        # Convert nan/NaT values to None for clean JSON response
        import pandas as pd
        records = df.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if pd.isna(v):
                    r[k] = None
        return records
    except Exception as e:
        logger.error(f"Error reading parquet for {ticker} {category}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cache/size")
def get_cache_size():
    """Get the size of the SEC edgar cache directory."""
    cache_dir = Path.home() / ".edgar"
    if not cache_dir.exists():
        return {"size_bytes": 0, "formatted_size": "0 B"}
        
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(cache_dir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
                
    # Format size
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if total_size < 1024.0:
            formatted_size = f"{total_size:.2f} {unit}"
            break
        total_size /= 1024.0
    else:
        formatted_size = f"{total_size:.2f} PB"
        
    return {
        "size_bytes": total_size * (1024 ** ['B', 'KB', 'MB', 'GB', 'TB'].index(unit)) if 'unit' in locals() else 0,
        "formatted_size": formatted_size
    }

@app.delete("/cache")
def delete_cache():
    """Delete all files in the SEC edgar cache directory."""
    cache_dir = Path.home() / ".edgar"
    if not cache_dir.exists():
        return {"status": "success", "message": "Cache directory does not exist."}
        
    try:
        shutil.rmtree(cache_dir)
        # Recreate the empty directory
        cache_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "success", "message": "Cache deleted successfully."}
    except Exception as e:
        logger.error(f"Error deleting cache: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete cache: {str(e)}")


class RecommendationRequest(BaseModel):
    ticker: str
    provider: str
    model: str
    api_key: str
    current_price: Optional[float] = None
    recent_price_action: Optional[str] = None
    custom_instructions: Optional[str] = None
    data_root: str = "D:\\tmp"


@app.post("/recommendation")
async def post_recommendation(request: RecommendationRequest):
    """
    Generate stock dilution analysis using the selected LLM provider.
    """
    try:
        result = await generate_recommendation(
            ticker=request.ticker,
            provider=request.provider,
            model=request.model,
            api_key=request.api_key,
            current_price=request.current_price,
            recent_price_action=request.recent_price_action,
            custom_instructions=request.custom_instructions,
            data_root=request.data_root
        )
        return {"recommendation": result}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error generating recommendation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/chart/history")
async def get_chart_history(
    ticker: str,
    timeframe: str = "5m",
    limit: int = 600
):
    """
    Retrieve historical OHLCV data for a ticker from TradingView WebSocket scraper.
    """
    ticker = ticker.upper().strip()
    exchange = resolve_ticker_exchange(ticker)
    
    logger.info(f"Fetching {limit} bars of {timeframe} history for {exchange}:{ticker}...")
    
    try:
        def fetch_data():
            streamer = CustomStreamer(export_result=True)
            res = streamer.stream(
                exchange=exchange,
                symbol=ticker,
                timeframe=timeframe,
                numb_price_candles=limit
            )
            return res.get("ohlc", [])
            
        loop = asyncio.get_running_loop()
        ohlc = await loop.run_in_executor(None, fetch_data)
        
        formatted = []
        for bar in ohlc:
            formatted.append({
                "time": int(bar["timestamp"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]) if "volume" in bar else 0.0
            })
            
        formatted.sort(key=lambda x: x["time"])
        logger.info(f"Returned {len(formatted)} bars for {ticker}.")
        return formatted
    except Exception as e:
        logger.error(f"Error fetching chart history for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch market data: {str(e)}")


@app.get("/chart/levels")
def get_chart_levels(
    ticker: str,
    current_price: Optional[float] = None,
    data_root: str = "D:\\tmp"
):
    """
    Extract key offering prices (warrants, convertible notes, etc.) for a ticker,
    filtered to +/- 100% around the current stock price.
    """
    try:
        levels = get_pricing_levels(ticker, current_price, data_root)
        return levels
    except Exception as e:
        logger.error(f"Error getting chart levels for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/chart/{ticker}")
async def websocket_chart(
    websocket: WebSocket,
    ticker: str,
    timeframe: str = "5m"
):
    """
    WebSocket endpoint to stream real-time price updates for a ticker.
    """
    await websocket.accept()
    ticker = ticker.upper().strip()
    exchange = resolve_ticker_exchange(ticker)
    
    logger.info(f"WebSocket client connected for real-time chart: {exchange}:{ticker} ({timeframe})")
    
    stop_event = asyncio.Event()
    
    def extract_ohlcv_update(pkt):
        m = pkt.get("m")
        if m not in ["timescale_update", "du"]:
            return None
        p = pkt.get("p", [])
        if len(p) < 2 or not isinstance(p[1], dict):
            return None
        sds = p[1].get("sds_1", {})
        if not isinstance(sds, dict):
            return None
        s = sds.get("s", [])
        if not s:
            return None
        
        updates = []
        for entry in s:
            if not isinstance(entry, dict) or "v" not in entry:
                continue
            v = entry["v"]
            if len(v) < 5:
                continue
            update = {
                "time": int(v[0]),
                "open": float(v[1]),
                "high": float(v[2]),
                "low": float(v[3]),
                "close": float(v[4]),
                "volume": float(v[5]) if len(v) > 5 else 0.0
            }
            updates.append(update)
        return updates

    async def push_stream():
        try:
            streamer = CustomStreamer(export_result=False)
            gen = streamer.stream(
                exchange=exchange,
                symbol=ticker,
                timeframe=timeframe,
                numb_price_candles=1
            )
            
            def next_packet():
                try:
                    return next(gen)
                except StopIteration:
                    return None
                except Exception as e:
                    logger.error(f"Error in TradingView gen: {e}")
                    return None
                    
            loop = asyncio.get_running_loop()
            
            while not stop_event.is_set():
                pkt = await loop.run_in_executor(None, next_packet)
                if pkt is None:
                    break
                    
                updates = extract_ohlcv_update(pkt)
                if updates:
                    for update in updates:
                        try:
                            await websocket.send_json({
                                "type": "candle_update",
                                "data": update
                            })
                        except Exception:
                            stop_event.set()
                            break
        except Exception as e:
            logger.error(f"Error in chart push stream for {ticker}: {e}")
        finally:
            stop_event.set()

    push_task = asyncio.create_task(push_stream())
    
    try:
        while not stop_event.is_set():
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from real-time chart: {ticker}")
    except Exception as e:
        logger.error(f"WebSocket error in {ticker}: {e}")
    finally:
        stop_event.set()
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass


@app.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    """WebSocket endpoint to receive real-time progress updates."""
    await manager.connect(websocket)
    
    # Send initial state of all currently active tickers
    try:
        await websocket.send_json({
            "type": "initial_state",
            "data": active_statuses
        })
        
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket exception: {e}")
        manager.disconnect(websocket)
