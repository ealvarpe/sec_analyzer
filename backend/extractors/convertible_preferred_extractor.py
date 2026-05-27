import logging
import uuid
import json
import re
from datetime import datetime
from ..services.html_parser import SECHTMLParser
from ..services.regex_patterns import (
    PATTERNS, normalize_amount, parse_date, extract_first_match,
    extract_all_matches, clean_number, extract_dollar_amount
)

logger = logging.getLogger(__name__)


def _deduplicate_preferred(records: list[dict]) -> list[dict]:
    """Cross-filing deduplication for convertible preferred stock.
    
    Two records represent the same preferred issuance if they share the same
    series designation AND similar total dollar amount. Prefer 424B over 8-K.
    """
    if not records:
        return records
    
    seen = {}
    for r in records:
        series = r.get('series_designation', 'Preferred')
        amount = r.get('total_dollar_amount_issued', 0)
        liq = r.get('liquidation_preference')
        
        # Key by series + liquidation preference (most unique identifiers)
        key = (series, liq if liq is not None else '', amount)
        
        if key in seen:
            existing = seen[key]
            # Prefer 424B forms over 8-K
            if existing['filing_form'] == '8-K' and r['filing_form'] != '8-K':
                seen[key] = r
            elif existing['filing_form'] == r['filing_form']:
                # Prefer record with more populated fields
                def field_count(rec):
                    return sum(1 for v in rec.values() if v and v != 'None' and v != '' and v is not None)
                if field_count(r) > field_count(existing):
                    seen[key] = r
        else:
            seen[key] = r
    
    return sorted(seen.values(), key=lambda x: x['date'])


def extract_convertible_preferred(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract convertible preferred stock from filings.
    
    Produces one record per unique preferred series issuance.
    Cross-filing deduplication ensures the same issuance mentioned
    in both 8-K and 424B produces only one record.
    """
    records = []
    pref_filings = [f for f in filings if f.form.upper().strip() in (
        '8-K', 'S-1', 'S-1/A', '424B2', '424B3', '424B5'
    )]
    
    for filing in pref_filings:
        try:
            text = downloader.get_filing_text(filing)
            if not text or not PATTERNS['convertible_preferred_kw'].search(text):
                continue
                
            cover_text = text[:8000]
            
            # Series Designation
            series = extract_first_match(PATTERNS['series_designation'], text) or "Preferred"
            
            # Dividend Rate
            div_rate_str = extract_first_match(PATTERNS['dividend_rate'], text)
            div_rate = float(div_rate_str) if div_rate_str else None
            
            # Liquidation Preference
            liq_str = extract_first_match(PATTERNS['liquidation_preference'], text)
            liq_pref = float(liq_str.replace('$', '').strip()) if liq_str else None
            
            # Conversion Price
            conv_price_str = extract_first_match(PATTERNS['conversion_price'], text)
            conv_price = float(conv_price_str.replace('$', '').strip()) if conv_price_str else None
            
            # Total Amount Issued
            total_amount = extract_dollar_amount(text, 'aggregate_amount')
            if not total_amount:
                total_amount = extract_dollar_amount(cover_text, 'dollar_amount')
            total_amount = int(total_amount) if total_amount else 0
            
            # Mandatory Conversion
            mandatory = False
            if "mandatory conversion" in text.lower() or "forced conversion" in text.lower():
                mandatory = True
                
            # Voting rights
            voting = "None"
            if "voting rights" in text.lower():
                voting = "Voting rights exist. Standard preferred stock voting terms apply."
                
            # Shares when converted
            shares_issued = 0.0
            if total_amount and conv_price and conv_price > 0:
                shares_issued = float(total_amount / conv_price)
                
            filing_date_str = str(filing.filing_date)
            try:
                dt = datetime.strptime(filing_date_str, '%Y-%m-%d')
                month_year = dt.strftime('%B %Y')
            except (ValueError, TypeError):
                month_year = ""
            sec_name = f"{month_year} Series {series} Preferred" if series != "Preferred" else f"{month_year} Preferred"
            
            # Deterministic ID based on economic identity
            pref_key = f"{ticker}-preferred-{series}-{liq_pref or 0}-{total_amount}"
            filing_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, pref_key))
            
            raw_fields = {
                'series_designation': series,
                'dividend_rate': div_rate,
                'liquidation_preference': liq_pref,
                'conversion_price': conv_price,
            }
            
            records.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': filing_date_str,
                'filing_id': filing_id,
                'offering_type': 'Convertible Preferred',
                'registration_status': 'Registered' if filing.form.upper().startswith('424') else 'Unregistered',
                'filing_edgar_url': getattr(filing, 'filing_url', '') or '',
                'raw_data_json': json.dumps(raw_fields),
                'security_name': sec_name,
                'remaining_shares_to_be_issued_when_converted': int(shares_issued),
                'remaining_dollar_amount': total_amount,
                'conversion_price': conv_price,
                'total_shares_issued_when_converted': shares_issued,
                'total_dollar_amount_issued': total_amount,
                'known_owners': '',
                'underwriter_placement_agent': '',
                'price_protection': 'None',
                'pp_clause': '',
                'issue_date': filing_date_str,
                'convertible_date': filing_date_str,
                'maturity_date': '',
                'last_update_date': filing_date_str,
                'series_designation': series,
                'dividend_rate': div_rate,
                'liquidation_preference': liq_pref,
                'voting_rights': voting,
                'mandatory_conversion': mandatory,
                'mandatory_conversion_date': '',
                'shares_authorized': None,
                'accession_number': str(filing.accession_no),
                'filing_form': filing.form,
            })
        except Exception as e:
            logger.error(f"Error extracting Preferred Stock from filing {filing.accession_no} for ticker {ticker}: {e}", exc_info=True)
    
    # Cross-filing deduplication
    records = _deduplicate_preferred(records)
    
    records.sort(key=lambda x: x['date'])
    return records
