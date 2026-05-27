import logging
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _get_fact_by_accession(company_facts: Optional[dict], accession: str, concept_names: list[str]) -> Optional[float]:
    if not company_facts:
        return None
    facts_root = company_facts.get("facts", {})
    best_val = None
    best_end = ""
    # Standard taxonomies to check
    taxonomies = ["dei", "us-gaap"]
    for taxonomy in taxonomies:
        tax_dict = facts_root.get(taxonomy, {})
        for concept in concept_names:
            concept_data = tax_dict.get(concept)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            for unit_key, entries in units.items():
                for entry in entries:
                    if entry.get("accn") == accession:
                        val = entry.get("val")
                        if val is not None:
                            end = entry.get("end", "")
                            if best_val is None or end > best_end:
                                best_val = float(val)
                                best_end = end
    return best_val


def _get_fact_from_html(filing, downloader, concept_names: list[str]) -> Optional[float]:
    try:
        html = downloader.get_filing_html(filing)
        if html:
            from backend.services.ixbrl_extractor import IXBRLExtractor
            ix = IXBRLExtractor(html)
            if ix.available:
                return ix.get_numeric_value(concept_names)
    except Exception as e:
        logger.debug(f"Failed to parse iXBRL from HTML for {filing.accession_no}: {e}")
    return None


def extract_shares_outstanding(ticker: str, filings: list, downloader) -> list[dict]:
    """
    Extract shares outstanding from 10-K/10-Q filings.
    
    Returns list of dicts matching SharesOutstandingRecord schema.
    """
    records = []
    seen_periods = set()
    
    from backend.models.database import TickerMetadata
    from backend.services.sec_xbrl_api import get_company_facts
    
    cik = None
    try:
        meta = downloader.session.query(TickerMetadata).filter_by(ticker=ticker.upper()).first()
        cik = meta.cik if meta else None
    except Exception:
        pass
        
    if not cik:
        try:
            company = downloader.get_company(ticker)
            cik = str(getattr(company, "cik", "")) if company else None
        except Exception:
            pass
            
    company_facts = None
    if cik:
        try:
            company_facts = get_company_facts(cik, downloader.session)
        except Exception as e:
            logger.warning(f"Failed to fetch SEC Company Facts API for {ticker}: {e}")
            
    for filing in filings:
        if filing.form not in ('10-K', '10-Q', '10-K/A', '10-Q/A'):
            continue
        
        try:
            obj = filing.obj()
            if obj is None:
                continue
            
            # Get period
            period_date = None
            if hasattr(filing, 'period_of_report'):
                period_date = str(filing.period_of_report)
            elif hasattr(obj, 'period_of_report'):
                period_date = str(obj.period_of_report)
            else:
                period_date = str(filing.filing_date)
            
            if period_date in seen_periods:
                continue
            seen_periods.add(period_date)
            
            # Extract shares data using multiple tiers:
            # 1. Company Facts API
            # 2. iXBRL parse of HTML
            # 3. edgartools balance sheet
            # 4. Text/Regex cover page
            
            # Shares outstanding
            shares_outstanding = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['EntityCommonStockSharesOutstanding', 'CommonStockSharesOutstanding', 'CommonStockSharesIssued']
            )
            if shares_outstanding is None:
                shares_outstanding = _get_fact_from_html(
                    filing, 
                    downloader, 
                    ['EntityCommonStockSharesOutstanding', 'CommonStockSharesOutstanding', 'CommonStockSharesIssued']
                )
                
            # Authorized shares
            authorized_shares = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['CommonStockSharesAuthorized']
            )
            if authorized_shares is None:
                authorized_shares = _get_fact_from_html(filing, downloader, ['CommonStockSharesAuthorized'])
                
            # Preferred shares
            preferred_shares = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['PreferredStockSharesOutstanding', 'PreferredStockSharesAuthorized']
            )
            if preferred_shares is None:
                preferred_shares = _get_fact_from_html(filing, downloader, ['PreferredStockSharesOutstanding', 'PreferredStockSharesAuthorized'])
                
            # Treasury shares
            treasury_shares = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['TreasuryStockShares', 'TreasuryStockCommonShares']
            )
            if treasury_shares is None:
                treasury_shares = _get_fact_from_html(filing, downloader, ['TreasuryStockShares', 'TreasuryStockCommonShares'])
            
            # Fallback to edgartools financials balance sheet if still missing
            if shares_outstanding is None:
                try:
                    financials = obj.financials
                    if financials and financials.balance_sheet is not None:
                        bs = financials.balance_sheet
                        shares_outstanding = _safe_get_shares(bs, [
                            'CommonStockSharesOutstanding',
                            'EntityCommonStockSharesOutstanding',
                            'CommonStockSharesIssued',
                        ])
                        if authorized_shares is None:
                            authorized_shares = _safe_get_shares(bs, ['CommonStockSharesAuthorized'])
                        if preferred_shares is None:
                            preferred_shares = _safe_get_shares(bs, ['PreferredStockSharesOutstanding'])
                        if treasury_shares is None:
                            treasury_shares = _safe_get_shares(bs, ['TreasuryStockShares', 'TreasuryStockCommonShares'])
                except Exception as e:
                    logger.debug(f'XBRL balance sheet extraction failed for {ticker} {period_date}: {e}')
            
            # Fallback: try to get from filing text (cover page) if still missing
            if shares_outstanding is None:
                try:
                    text = downloader.get_filing_text(filing)
                    if text:
                        shares_outstanding = _extract_shares_from_text(text)
                except Exception:
                    pass
            
            # Convert to millions
            shares_m = shares_outstanding / 1e6 if shares_outstanding else None
            auth_m = authorized_shares / 1e6 if authorized_shares else None
            pref_m = preferred_shares / 1e6 if preferred_shares else None
            treasury_m = treasury_shares / 1e6 if treasury_shares else None
            
            try:
                dt = datetime.strptime(period_date, '%Y-%m-%d')
                name = dt.strftime('%m/%d/%Y')
            except Exception:
                name = period_date
            
            records.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': period_date,
                'name': name,
                'shares_outstanding': round(shares_m, 2) if shares_m else None,
                'current_outstanding': None,  # Set after all records
                'is_current': False,  # Set after
                'authorized_shares': round(auth_m, 2) if auth_m else None,
                'preferred_shares_outstanding': round(pref_m, 2) if pref_m else None,
                'treasury_shares': round(treasury_m, 2) if treasury_m else None,
                'fully_diluted_shares': None,  # Hard to compute without all data
                'options_outstanding': None,
                'warrants_outstanding_at_date': None,
                'pct_change_qoq': None,  # Computed after
                'filing_form': filing.form,
                'accession_number': str(filing.accession_no),
            })
        
        except Exception as e:
            logger.warning(f'Error processing {filing.form} for {ticker}: {e}')
    
    # Sort by date
    records.sort(key=lambda r: r['date'])
    
    # Post-process
    if records:
        # Set current_outstanding to latest value
        latest_shares = None
        for r in reversed(records):
            if r['shares_outstanding'] is not None:
                latest_shares = r['shares_outstanding']
                break
        
        for i, r in enumerate(records):
            r['current_outstanding'] = latest_shares
            r['is_current'] = (i == len(records) - 1)
            
            # Compute QoQ change
            if i > 0 and r['shares_outstanding'] and records[i-1]['shares_outstanding']:
                prev = records[i-1]['shares_outstanding']
                if prev > 0:
                    r['pct_change_qoq'] = round(
                        ((r['shares_outstanding'] - prev) / prev) * 100, 2
                    )
    
    return records


def _safe_get_shares(financial_stmt, concept_names: list[str]) -> Optional[float]:
    """Try multiple XBRL concept names for share counts."""
    for name in concept_names:
        try:
            val = getattr(financial_stmt, name, None)
            if val is not None:
                return float(val)
        except Exception:
            pass
        
        try:
            if hasattr(financial_stmt, 'get_fact'):
                val = financial_stmt.get_fact(name)
                if val is not None:
                    return float(val.value if hasattr(val, 'value') else val)
        except Exception:
            pass
        
        try:
            if hasattr(financial_stmt, 'data') and isinstance(financial_stmt.data, pd.DataFrame):
                df = financial_stmt.data
                if name in df.index:
                    val = df.loc[name].iloc[-1]
                    if pd.notna(val):
                        return float(val)
        except Exception:
            pass
    
    return None


def _extract_shares_from_text(text: str) -> Optional[float]:
    """Fallback: extract shares outstanding from filing text (cover page)."""
    import re
    patterns = [
        # Search for "shares outstanding" followed by a number with commas
        re.compile(r'shares\s+outstanding.*?(\d{1,3}(?:,\d{3})+)', re.I | re.S),
        # Search for "we had X shares"
        re.compile(r'(?:we\s+had|had|registrant\s+had)\s+(\d{1,3}(?:,\d{3})+)\s+shares', re.I | re.S),
        re.compile(r'([\d,]+)\s+shares\s+(?:of\s+)?(?:common\s+stock\s+)?(?:issued\s+and\s+)?outstanding', re.I),
        re.compile(r'shares\s+(?:of\s+common\s+stock\s+)?outstanding[:\s]+([\d,]+)', re.I),
        re.compile(r'common\s+stock[,\s]+([\d,]+)\s+shares\s+(?:issued\s+and\s+)?outstanding', re.I),
    ]
    for pattern in patterns:
        match = pattern.search(text[:100000])  # Search up to first 100k characters
        if match:
            try:
                val = match.group(1).replace(',', '')
                # Ensure it's not a year
                if val not in ('2020', '2021', '2022', '2023', '2024', '2025', '2026', '2027'):
                    return float(val)
            except Exception:
                pass
    return None
