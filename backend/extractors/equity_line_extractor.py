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

# Known ELOC counterparty names – used to distinguish genuine equity lines
# from generic private placements or convertible note offerings
KNOWN_ELOC_PROVIDERS = [
    'lincoln park',
    'yorkville',
    'tumim stone',
    'b. riley',
    'canaccord',
    'aspire capital',
    'keystone capital',
    'alumni capital',
    'triton funds',
    'white lion',
    'gem global',
    'gem capital',
    'virtu financial',
]

# Patterns that indicate a filing is about an equity line / purchase agreement
ELOC_AGREEMENT_PATTERNS = [
    re.compile(r'(?:common\s+stock\s+)?purchase\s+agreement', re.I),
    re.compile(r'equity\s+line\s+of\s+credit', re.I),
    re.compile(r'committed\s+equity\s+(?:financing\s+)?facility', re.I),
    re.compile(r'equity\s+distribution\s+agreement', re.I),
    re.compile(r'equity\s+purchase\s+agreement', re.I),
    re.compile(r'standby\s+equity\s+(?:distribution|purchase)\s+agreement', re.I),
]


def _is_genuine_eloc(text: str, counterparty: str) -> bool:
    """Determine if the filing describes a genuine equity line of credit.
    
    Returns True if:
    1. The counterparty is a known ELOC provider, OR
    2. The text contains strong ELOC-specific agreement language
    
    Returns False for generic private placements, convertible notes,
    and other offerings that just happen to mention purchase agreements.
    """
    text_lower = text.lower()
    cp_lower = counterparty.lower() if counterparty else ""
    
    # Check if the counterparty is a known ELOC provider
    for provider in KNOWN_ELOC_PROVIDERS:
        if provider in cp_lower:
            return True
    
    # Check for strong ELOC agreement language
    eloc_hits = sum(1 for pat in ELOC_AGREEMENT_PATTERNS if pat.search(text))
    if eloc_hits >= 2:
        return True
    
    # If only one hit, need additional ELOC-specific indicators
    if eloc_hits >= 1:
        # Must also have capacity/commitment language
        has_commitment = bool(re.search(
            r'(?:up\s+to|commitment|facility)\s+(?:of\s+)?\$[\d,]+', text, re.I
        ))
        # Must NOT be primarily a convertible note or private placement of securities
        is_convertible = bool(PATTERNS['convertible_note_kw'].search(text))
        is_private_placement = 'private placement' in text_lower and 'purchase agreement' not in text_lower
        
        if has_commitment and not is_convertible and not is_private_placement:
            return True
    
    return False


def _extract_counterparty(text: str) -> str:
    """Extract the ELOC counterparty from filing text."""
    # Try the standard pattern first
    cp = extract_first_match(PATTERNS['eloc_counterparty'], text) or ""
    cp = cp.strip().strip(',').strip('.')
    
    # If empty, try alternative patterns for purchase agreements
    if not cp:
        # "entered into a ... purchase agreement with [COUNTERPARTY]"
        m = re.search(
            r'(?:purchase\s+agreement|equity\s+line)\s+(?:with|from)\s+'
            r'([A-Z][\w\s,&\.]+?)'
            r'(?:\s+(?:pursuant|under|whereby|providing|for|dated|\(|,))',
            text, re.I
        )
        if m:
            cp = m.group(1).strip().strip(',').strip('.')
    
    if not cp:
        # "agreement with [COUNTERPARTY], a [state] limited liability company"
        m = re.search(
            r'agreement\s+with\s+([A-Z][\w\s,&\.]+?)'
            r'(?:\s*,\s*a\s+\w+\s+(?:limited|corporation|company))',
            text, re.I
        )
        if m:
            cp = m.group(1).strip().strip(',').strip('.')
    
    # Filter out the company's own name patterns
    if cp and any(x in cp.lower() for x in ['landlord', 'the company', 'registrant']):
        return ""
    
    return cp


def _extract_capacity(text: str) -> int:
    """Extract the total ELOC facility capacity from text."""
    capacity = extract_dollar_amount(text, 'equity_line_capacity')
    if not capacity:
        capacity = extract_dollar_amount(text[:8000], 'equity_line_capacity')
    if not capacity:
        capacity = extract_dollar_amount(text, 'up_to_amount')
    if not capacity:
        # Try "aggregate offering price of $X"
        capacity = extract_dollar_amount(text, 'aggregate_amount')
    return int(capacity) if capacity else 0


def _extract_shares_count(text: str) -> int:
    """Extract number of shares from filing text."""
    # "issued and sold X shares"
    m = re.search(r'(?:issued|sold|purchased?|issu\w+)\s+(?:and\s+sold\s+)?(?:an\s+aggregate\s+of\s+)?([\d,]+)\s+shares', text, re.I)
    if m:
        return int(clean_number(m.group(1)))
    # "up to X shares of common stock"
    m = re.search(r'up\s+to\s+([\d,]+)\s+shares\s+of\s+(?:our\s+)?common\s+stock', text, re.I)
    if m:
        return int(clean_number(m.group(1)))
    return 0


def _extract_price_per_share(text: str) -> str:
    """Extract the pricing mechanism or price per share."""
    # VWAP-based pricing (common in ELOCs)
    vwap = extract_first_match(PATTERNS['vwap_pricing'], text)
    if vwap:
        return f"{vwap}% of VWAP"
    
    # Fixed price per share
    m = re.search(r'\$\s*([\d,.]+)\s*per\s+share', text, re.I)
    if m:
        return f"${m.group(1)} per share"
    
    # Discount-based pricing
    m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*(?:discount|of\s+(?:the\s+)?(?:market|closing))', text, re.I)
    if m:
        return f"{m.group(1)}% discount to market"
    
    return "VWAP formula"


def _extract_offering_status(text: str, filing_date_str: str) -> str:
    """Determine if the offering is completed, active, or terminated."""
    text_lower = text.lower()
    
    if any(kw in text_lower for kw in ['closed', 'completed', 'closing of', 'consummated']):
        return "Completed"
    if any(kw in text_lower for kw in ['terminated', 'termination', 'expired']):
        return "Terminated"
    if any(kw in text_lower for kw in ['entered into', 'agreement with', 'execution of']):
        return "Active"
    return "Active"


def _extract_dates(text: str, filing_date_str: str) -> dict:
    """Extract agreement start/end dates from text."""
    dates = {
        'start_date': filing_date_str,
        'end_date': '',
        'last_update': filing_date_str,
    }
    
    # "dated as of [DATE]" or "effective as of [DATE]"
    m = re.search(r'(?:dated|effective|entered\s+into)\s+(?:as\s+of\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', text, re.I)
    if m:
        parsed = parse_date(m.group(1))
        if parsed:
            dates['start_date'] = parsed
    
    # "expiration date" or "termination date" or "term of [X] months/years"
    m = re.search(r'(?:expir\w+|terminat\w+)\s+(?:date\s+)?(?:of\s+|on\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', text, re.I)
    if m:
        parsed = parse_date(m.group(1))
        if parsed:
            dates['end_date'] = parsed
    
    # "closing date" for completed offerings
    m = re.search(r'clos(?:e|ed|ing)\s+(?:date\s+)?(?:on\s+|of\s+)?([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', text, re.I)
    if m:
        parsed = parse_date(m.group(1))
        if parsed:
            dates['last_update'] = parsed
    
    return dates


def _build_security_name(counterparty: str, filing_date_str: str) -> str:
    """Build a descriptive name for the equity line."""
    try:
        dt = datetime.strptime(filing_date_str, '%Y-%m-%d')
        month_year = dt.strftime('%B %Y')
    except (ValueError, TypeError):
        month_year = ""
    
    if counterparty:
        return f"{counterparty} Equity Line ({month_year})" if month_year else f"{counterparty} Equity Line"
    return f"{month_year} Equity Line" if month_year else "Equity Line"


def extract_equity_line(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract equity lines of credit from filings.
    
    Dynamically parses all 8-K filings for genuine ELOC agreements,
    extracting counterparty, capacity, pricing, and share information.
    Generic private placements and convertible note closings are filtered out.
    """
    records = []
    eloc_filings = [f for f in filings if f.form.upper().strip() in ('8-K', 'S-1', 'S-1/A', '424B3', '424B5')]
    
    for filing in eloc_filings:
        try:
            # We only process 8-K filings for these specific items
            if filing.form.upper().strip() != '8-K':
                continue
                
            text = downloader.get_filing_text(filing)
            if not text or not PATTERNS['equity_line_kw'].search(text):
                continue
                
            acc = str(filing.accession_no)
            filing_date_str = str(filing.filing_date)
            
            # ── Dynamic extraction (no hardcoding) ──────────────────────
            
            # 1. Counterparty
            counterparty = _extract_counterparty(text)
            
            # Skip if no counterparty or landlord agreement
            if not counterparty or "landlord" in counterparty.lower():
                continue
            
            # 2. Determine if this is a genuine ELOC vs generic offering
            if not _is_genuine_eloc(text, counterparty):
                # Not a genuine ELOC — skip
                logger.debug(f"Skipping {acc}: not a genuine ELOC (counterparty: {counterparty})")
                continue
            
            # 3. Capacity
            capacity = _extract_capacity(text)
            
            # 4. Pricing mechanism
            pricing_mechanism = _extract_price_per_share(text)
            
            # 5. Floor price
            floor = extract_first_match(PATTERNS['floor_price'], text)
            floor_val = float(floor.replace('$', '').strip()) if floor else None
            
            # 6. Shares
            max_shares = _extract_shares_count(text) or None
            
            # 7. Dates
            dates = _extract_dates(text, filing_date_str)
            start_date = dates['start_date']
            end_date = dates['end_date']
            last_update = dates['last_update']
            
            # 8. Status
            reg_status = _extract_offering_status(text, filing_date_str)
            
            # 9. Security name
            sec_name = _build_security_name(counterparty, filing_date_str)
            
            # 10. Fee shares / commitment shares
            fee_shares = None
            fee_match = re.search(r'(?:commitment|initial)\s+(?:fee|shares)\s+of\s+([\d,]+)\s+shares', text, re.I)
            if fee_match:
                fee_shares = int(clean_number(fee_match.group(1)))
            
            # 11. Known owners / investors
            owners = counterparty if counterparty else ""
            
            # 12. Current shares equivalent
            current_shares = f"{max_shares:,} shares" if max_shares else ""
            
            # Unique deterministic ID based on ticker, date, capacity, and accession
            note_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}-{filing_date_str}-{capacity}-{acc}"))
            
            raw_fields = {
                'total_equity_line_capacity': capacity,
                'counterparty': counterparty,
                'pricing_mechanism': pricing_mechanism,
                'minimum_price_threshold': floor_val,
            }
            
            records.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': filing_date_str,
                'filing_id': note_id,
                'offering_type': 'Equity Line',
                'registration_status': reg_status,
                'filing_edgar_url': getattr(filing, 'filing_url', '') or '',
                'raw_data_json': json.dumps(raw_fields),
                'security_name': sec_name,
                'remaining_equity_line_capacity': max(0, capacity) if reg_status == 'Active' else 0,
                'total_equity_line_capacity': capacity,
                'agreement_start_date': start_date,
                'agreement_end_date': end_date,
                'last_update_date': last_update,
                'owners': owners,
                'current_shares_equivalent': current_shares,
                'counterparty': counterparty,
                'pricing_mechanism': pricing_mechanism,
                'maximum_shares': max_shares,
                'minimum_price_threshold': floor_val,
                'commitment_fee_shares': fee_shares,
                'accession_number': acc,
                'filing_form': filing.form,
            })
        except Exception as e:
            logger.error(f"Error extracting Equity Line from filing {filing.accession_no} for ticker {ticker}: {e}", exc_info=True)
            
    records.sort(key=lambda x: x['date'])
    return records
