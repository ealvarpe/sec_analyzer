import logging
import uuid
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


def extract_cash_position(ticker: str, filings: list, downloader) -> list[dict]:
    """
    Extract cash position from 10-K/10-Q filings via XBRL.
    
    Returns list of dicts matching CashPositionRecord schema.
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
            
            # Get period end date
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
            
            # Try to extract financial data using multiple tiers:
            # 1. Company Facts API
            # 2. iXBRL HTML extractor
            # 3. edgartools balance sheet/cash flow statement
            
            # Cash
            cash = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsAndShortTermInvestments', 'Cash', 'CashAndCashEquivalents']
            )
            if cash is None:
                cash = _get_fact_from_html(filing, downloader, ['CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsAndShortTermInvestments', 'Cash', 'CashAndCashEquivalents'])
                
            # Short term investments
            short_term_investments = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['ShortTermInvestments', 'AvailableForSaleSecuritiesCurrent', 'MarketableSecuritiesCurrent']
            )
            if short_term_investments is None:
                short_term_investments = _get_fact_from_html(filing, downloader, ['ShortTermInvestments', 'AvailableForSaleSecuritiesCurrent', 'MarketableSecuritiesCurrent'])
                
            # Total current assets
            total_current_assets = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['AssetsCurrent']
            )
            if total_current_assets is None:
                total_current_assets = _get_fact_from_html(filing, downloader, ['AssetsCurrent'])
                
            # Total current liabilities
            total_current_liabilities = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['LiabilitiesCurrent']
            )
            if total_current_liabilities is None:
                total_current_liabilities = _get_fact_from_html(filing, downloader, ['LiabilitiesCurrent'])
                
            # Total debt
            total_debt = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['LongTermDebt', 'DebtCurrent', 'LongTermDebtAndCapitalLeaseObligations']
            )
            if total_debt is None:
                total_debt = _get_fact_from_html(filing, downloader, ['LongTermDebt', 'DebtCurrent', 'LongTermDebtAndCapitalLeaseObligations'])
                
            # Operating cash flow
            operating_cashflow = _get_fact_by_accession(
                company_facts, 
                filing.accession_no, 
                ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByOperatingActivities', 'CashFlowsFromOperatingActivities']
            )
            if operating_cashflow is None:
                operating_cashflow = _get_fact_from_html(filing, downloader, ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByOperatingActivities', 'CashFlowsFromOperatingActivities'])
            
            # Fallback to edgartools financials balance sheet & cash flow if still missing
            if cash is None or short_term_investments is None or total_current_assets is None or total_current_liabilities is None or total_debt is None or operating_cashflow is None:
                try:
                    financials = obj.financials
                    if financials:
                        bs = financials.balance_sheet
                        if bs is not None:
                            if cash is None:
                                cash = _safe_get_fact(bs, [
                                    'CashAndCashEquivalentsAtCarryingValue',
                                    'CashCashEquivalentsAndShortTermInvestments',
                                    'Cash',
                                    'CashAndCashEquivalents',
                                ])
                            if short_term_investments is None:
                                short_term_investments = _safe_get_fact(bs, [
                                    'ShortTermInvestments',
                                    'AvailableForSaleSecuritiesCurrent',
                                    'MarketableSecuritiesCurrent',
                                ])
                            if total_current_assets is None:
                                total_current_assets = _safe_get_fact(bs, ['AssetsCurrent'])
                            if total_current_liabilities is None:
                                total_current_liabilities = _safe_get_fact(bs, ['LiabilitiesCurrent'])
                            if total_debt is None:
                                total_debt = _safe_get_fact(bs, [
                                    'LongTermDebt',
                                    'DebtCurrent',
                                    'LongTermDebtAndCapitalLeaseObligations',
                                ])
                        
                        cf = financials.cash_flow_statement
                        if cf is not None and operating_cashflow is None:
                            operating_cashflow = _safe_get_fact(cf, [
                                'NetCashProvidedByUsedInOperatingActivities',
                                'NetCashProvidedByOperatingActivities', 
                                'CashFlowsFromOperatingActivities',
                            ])
                except Exception as e:
                    logger.debug(f'Error extracting financials fallback for {ticker} {period_date}: {e}')
            
            # Convert to millions where applicable
            cash_m = cash / 1e6 if cash else None
            sti_m = short_term_investments / 1e6 if short_term_investments else None
            tca_m = total_current_assets / 1e6 if total_current_assets else None
            tcl_m = total_current_liabilities / 1e6 if total_current_liabilities else None
            td_m = total_debt / 1e6 if total_debt else None
            ocf_m = operating_cashflow / 1e6 if operating_cashflow else None
            
            # Compute derived fields
            working_capital = None
            if tca_m is not None and tcl_m is not None:
                working_capital = round(tca_m - tcl_m, 2)
            
            cash_and_investments = None
            if cash_m is not None:
                cash_and_investments = round(cash_m + (sti_m or 0), 2)
            
            # Determine period type
            period_type = 'Annual' if filing.form == '10-K' else 'Quarterly'
            
            # Format date for name field like '03/31/2024'
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
                'cash': round(cash_m, 2) if cash_m is not None else None,
                'recent_offerings': 0,
                'quarterly_operating_cashflow': round(ocf_m, 2) if ocf_m is not None else None,
                'last_cash_date': '',  # Will be set by normalizer
                'short_term_investments': round(sti_m, 2) if sti_m is not None else None,
                'cash_and_investments_total': cash_and_investments,
                'total_current_assets': round(tca_m, 2) if tca_m is not None else None,
                'total_current_liabilities': round(tcl_m, 2) if tcl_m is not None else None,
                'working_capital': working_capital,
                'total_debt': round(td_m, 2) if td_m is not None else None,
                'quarterly_burn_rate': None,  # Computed after all records collected
                'months_of_cash_remaining': None,  # Computed after
                'filing_form': filing.form,
                'period_type': period_type,
                'accession_number': str(filing.accession_no),
            })
            
        except Exception as e:
            logger.warning(f'Error processing {filing.form} for {ticker}: {e}')
    
    # Sort by date
    records.sort(key=lambda r: r['date'])
    
    # Post-process: compute burn rate and months remaining
    _compute_burn_rates(records)
    
    # Set last_cash_date to the most recent date
    if records:
        last_date = records[-1]['date']
        for r in records:
            r['last_cash_date'] = last_date
    
    return records


def _safe_get_fact(financial_stmt, concept_names: list[str]) -> Optional[float]:
    """Try multiple XBRL concept names and return first found value."""
    # The edgartools financials object might be a DataFrame or custom object
    # Try different access patterns
    for name in concept_names:
        try:
            # Try as attribute
            val = getattr(financial_stmt, name, None)
            if val is not None:
                return float(val)
        except Exception:
            pass
        
        try:
            # Try as dict/index access
            if hasattr(financial_stmt, 'get_fact'):
                val = financial_stmt.get_fact(name)
                if val is not None:
                    return float(val.value if hasattr(val, 'value') else val)
        except Exception:
            pass
        
        try:
            # Try as DataFrame row
            if hasattr(financial_stmt, 'data') and isinstance(financial_stmt.data, pd.DataFrame):
                df = financial_stmt.data
                if name in df.index:
                    val = df.loc[name].iloc[-1]  # Most recent column
                    if pd.notna(val):
                        return float(val)
        except Exception:
            pass
        
        try:
            # Try string matching on labels
            if hasattr(financial_stmt, '__str__'):
                import re
                text = str(financial_stmt)
                # Look for the concept in the string representation
                pattern = re.compile(rf'{name}.*?([\d,.]+)', re.I)
                match = pattern.search(text)
                if match:
                    return float(match.group(1).replace(',', ''))
        except Exception:
            pass
    
    return None


def _compute_burn_rates(records: list[dict]):
    """Compute quarterly burn rate and months of cash remaining."""
    for i in range(1, len(records)):
        curr = records[i]
        prev = records[i - 1]
        
        if curr['cash'] is not None and prev['cash'] is not None:
            cash_change = curr['cash'] - prev['cash']
            if cash_change < 0:  # Burning cash
                curr['quarterly_burn_rate'] = round(abs(cash_change), 2)
                if curr['quarterly_burn_rate'] > 0 and curr['cash'] is not None:
                    monthly_burn = curr['quarterly_burn_rate'] / 3
                    if monthly_burn > 0:
                        curr['months_of_cash_remaining'] = round(curr['cash'] / monthly_burn, 1)
