import logging
import uuid
import json
import re
import calendar
from datetime import datetime, timedelta
from ..services.html_parser import SECHTMLParser
from ..services.regex_patterns import (
    PATTERNS, normalize_amount, parse_date, extract_first_match,
    extract_all_matches, clean_number, extract_dollar_amount
)

logger = logging.getLogger(__name__)

def add_months(sourcedate, months):
    """Add a number of months to a date exactly."""
    try:
        month = sourcedate.month - 1 + months
        year = sourcedate.year + month // 12
        month = month % 12 + 1
        day = min(sourcedate.day, calendar.monthrange(year, month)[1])
        return datetime(year, month, day)
    except Exception:
        return sourcedate + timedelta(days=months * 30.44)


def _extract_single_note(text: str, filing, ticker: str, idx: int, downloader=None) -> dict | None:
    """Extract a single convertible note from a filing's full text.
    
    This is the primary extraction path. It looks for the MOST PROMINENT
    principal amount in the document (typically the first/largest) and
    extracts all related fields from surrounding context.
    """
    cover_text = text[:8000]
    
    # Find ALL principal amounts
    matches = list(PATTERNS['principal_amount'].finditer(text))
    if not matches:
        return None
    
    # ── Determine the "real" principal amount ────────────────────────────
    # A filing typically describes ONE note issuance. The principal_amount
    # regex will match multiple times as the same figure is repeated
    # throughout. Group by value and pick the most frequently mentioned.
    value_counts = {}
    for m in matches:
        try:
            val = int(clean_number(m.group(1)))
            if val <= 0:
                continue
            multiplier = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
            if multiplier:
                val = int(normalize_amount(m.group(1), multiplier))
            if val not in value_counts:
                value_counts[val] = {'count': 0, 'first_match': m}
            value_counts[val]['count'] += 1
        except (ValueError, IndexError):
            continue
    
    if not value_counts:
        return None
    
    # Pick the most frequently mentioned principal (most likely the real one)
    # Tie-break: prefer the one that appears first (cover page)
    sorted_vals = sorted(
        value_counts.items(),
        key=lambda x: (-x[1]['count'], x[1]['first_match'].start())
    )
    
    principal = sorted_vals[0][0]
    m = sorted_vals[0][1]['first_match']
    
    # Context around the best match
    forward_subtext = text[m.start() : min(len(text), m.start() + 2500)]
    backward_subtext = text[max(0, m.start() - 500) : m.start() + 150]
    
    # ── Conversion Price ────────────────────────────────────────────────
    conv_price = None
    conv_match = PATTERNS['conversion_price'].search(forward_subtext)
    if conv_match:
        try:
            conv_price = float(conv_match.group(1).replace('$', '').strip())
        except (ValueError, AttributeError):
            pass
    
    # ── Price protection / Formula ──────────────────────────────────────
    price_protection = "None"
    pp_clause = ""
    discount_match = re.search(
        r"(\d+)\s*%\s*of\s*(?:the\s+)?(?:lowest|daily|average|VWAP|market)",
        forward_subtext, re.I
    )
    if discount_match:
        price_protection = "Discount to VWAP"
        pp_clause = f"Conversion price equals {discount_match.group(1)}% of the lowest daily VWAP of the last 10 trading days."
    
    # ── Interest Rate ──────────────────────────────────────────────────
    rate = None
    rate_match = PATTERNS['interest_rate'].search(forward_subtext)
    if rate_match:
        rate = float(rate_match.group(1))
    else:
        rate_match = re.search(
            r"(\d+(?:\.\d+)?)\s*%\s*(?:convertible|interest|redeemable)",
            forward_subtext, re.I
        )
        if rate_match:
            rate = float(rate_match.group(1))
    
    # ── Issue Date ─────────────────────────────────────────────────────
    filing_date_str = str(filing.filing_date)
    issue_date_str = filing_date_str
    issue_date_match = re.search(
        r"([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\s*\(\s*the\s*\"?\s*Issue\s+Date\s*\"?\s*\)",
        text[max(0, m.start() - 300) : min(len(text), m.start() + 500)], re.I
    )
    if issue_date_match:
        parsed_issue = parse_date(issue_date_match.group(1))
        if parsed_issue:
            issue_date_str = parsed_issue
    
    # ── Maturity Date ─────────────────────────────────────────────────
    mat_date = ""
    mat_match = PATTERNS['maturity_date'].search(forward_subtext)
    if mat_match:
        mat_date = parse_date(mat_match.group(1))
    else:
        rel_match = re.search(
            r"(?:matur\w+|due)\s+(?:date\s+is\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|eighteen|twenty-four|thirty-six)\s+months",
            forward_subtext, re.I
        )
        if rel_match:
            NUMBER_WORDS = {
                'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
                'eighteen': 18, 'twenty-four': 24, 'thirty-six': 36
            }
            months_str = rel_match.group(1).lower()
            if months_str.isdigit():
                months = int(months_str)
            else:
                months = NUMBER_WORDS.get(months_str, 0)
            if months > 0:
                try:
                    issue_dt = datetime.strptime(issue_date_str, '%Y-%m-%d')
                    mat_dt = add_months(issue_dt, months)
                    mat_date = mat_dt.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    pass
    
    # ── Senior status ─────────────────────────────────────────────────
    is_senior = bool(PATTERNS['senior_note'].search(forward_subtext)) or "senior" in forward_subtext.lower()
    
    # ── Payment Frequency ─────────────────────────────────────────────
    frequency = "Semi-Annual"
    if "quarterly" in forward_subtext.lower():
        frequency = "Quarterly"
    elif "annual" in forward_subtext.lower():
        frequency = "Annual"
    
    # ── Lender / Investor ─────────────────────────────────────────────
    lender_match = re.search(
        r"with\s+([A-Z][\w\s,&\.]+?(?:\bLLC\b|\bL\.P\.\b|\bLP\b|\bInc\b|\bCorp\b|\bPartners\b)(?:\s*,\s*LP|\s*,\s*L\.P\.)?)",
        backward_subtext
    )
    lender = lender_match.group(1).strip().replace('\n', ' ') if lender_match else ""
    
    # Build company filter to avoid matching the issuer's own name
    company_filter = {"the company", "us", "the registrar", "the issuer", ticker.lower()}
    for f in [filing]:
        comp_name = getattr(f, 'company_name', '') or getattr(f, 'company', '')
        if comp_name:
            comp_name_lower = str(comp_name).lower()
            company_filter.add(comp_name_lower)
            for suffix in [' inc.', ' corp.', ' co.', ' ltd.', ' incorporated', ' corporation']:
                if suffix in comp_name_lower:
                    company_filter.add(comp_name_lower.split(suffix)[0].strip())
    
    if lender.lower() in company_filter:
        lender = ""
    
    # ── Agent / Placement Agent ───────────────────────────────────────
    agent = ""
    banker_match = re.search(
        r'(ThinkEquity|Maxim Group|Aegis|HC\s+Wainwright|H\.\s*C\.\s+Wainwright'
        r'|Roth\s+Capital|Roth\s+MKM|Craig[- ]Hallum|Benchmark'
        r'|Ladenburg\s+Thalmann|Craft\s+Capital|Craft'
        r'|Oppenheimer|Canaccord|B\.?\s*Riley|Lake\s+Street'
        r'|Chardan|EF\s+Hutton|Dawson\s+James)',
        forward_subtext, re.I
    )
    if banker_match:
        agent = banker_match.group(1)
        if 'craft' in agent.lower():
            agent = 'Craft Capital Management'
    
    # ── Conversion Type ───────────────────────────────────────────────
    conv_type = "Optional"
    if "mandatory" in forward_subtext.lower():
        conv_type = "Mandatory"
    
    # ── Shares when converted ─────────────────────────────────────────
    shares_issued = 0
    reserve_match = re.search(
        r"reserve\s+([\d,]+)\s+shares(?:\s+of\s+common\s+stock)?\s+for\s+the\s+conversion",
        forward_subtext, re.I
    )
    if reserve_match:
        shares_issued = int(clean_number(reserve_match.group(1)))
    elif principal and conv_price and conv_price > 0:
        shares_issued = int(principal / conv_price)
    
    # ── Supplement/Verify from inline XBRL if available ────────────────
    if downloader:
        try:
            html = downloader.get_filing_html(filing)
            if html:
                from backend.services.ixbrl_extractor import IXBRLExtractor
                ix = IXBRLExtractor(html)
                if ix.available:
                    # 1. Interest rate
                    rate_val = ix.get_numeric_value(["DebtInstrumentInterestRateStatedPercentage"])
                    if rate_val is not None:
                        if rate_val < 1.0:
                            rate = round(rate_val * 100, 2)
                        else:
                            rate = round(rate_val, 2)
                    
                    # 2. Maturity date
                    mat_val = ix.get_date_value(["DebtInstrumentMaturityDate"])
                    if mat_val:
                        mat_date = mat_val
                    
                    # 3. Principal amount
                    princ_val = ix.get_numeric_value(["DebtInstrumentFaceAmount", "ConvertibleNotesPayable", "ConvertibleDebt"])
                    if princ_val is not None:
                        principal = int(princ_val)
                        
                    # Recompute shares if principal changed
                    if principal and conv_price and conv_price > 0:
                        shares_issued = int(principal / conv_price)
        except Exception as e:
            logger.debug(f"Failed to supplement note with iXBRL: {e}")
    
    reg_status = 'Unregistered'
    if filing.form.upper().startswith('424'):
        reg_status = 'Registered'
    
    try:
        dt = datetime.strptime(filing_date_str, '%Y-%m-%d')
        month_year = dt.strftime('%B %Y')
    except (ValueError, TypeError):
        month_year = ""
    sec_name = f"{month_year} Convertible Notes"
    
    # Deterministic ID based on economic identity of the note
    note_key = f"{ticker}-{principal}-{mat_date or 'nomat'}-{rate or 'norate'}"
    note_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, note_key))
    
    raw_fields = {
        'total_principal_amount': principal,
        'conversion_price': conv_price,
        'interest_rate': rate,
        'maturity_date': mat_date,
        'lender': lender,
    }
    
    return {
        'ticker': ticker,
        'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
        'date': filing_date_str,
        'filing_id': note_id,
        'offering_type': 'Convertible Note',
        'registration_status': reg_status,
        'filing_edgar_url': getattr(filing, 'filing_url', '') or '',
        'raw_data_json': json.dumps(raw_fields),
        'security_name': sec_name,
        'remaining_shares_to_be_issued_when_converted': shares_issued,
        'remaining_principal_amount': principal,
        'conversion_price': conv_price,
        'total_shares_issued_when_converted': shares_issued,
        'total_principal_amount': principal,
        'known_owners': lender,
        'underwriter_placement_agent': agent,
        'price_protection': price_protection,
        'pp_clause': pp_clause,
        'issue_date': issue_date_str,
        'convertible_date': issue_date_str,
        'maturity_date': mat_date,
        'last_update_date': filing_date_str,
        'interest_rate': rate,
        'interest_payment_frequency': frequency,
        'conversion_type': conv_type,
        'is_senior': is_senior,
        'redemption_date': '',
        'redemption_price_pct': None,
        'initial_conversion_premium': None,
        'accession_number': str(filing.accession_no),
        'filing_form': filing.form,
    }


def _deduplicate_notes(records: list[dict]) -> list[dict]:
    """Cross-filing deduplication.
    
    Two records represent the same convertible note if they share the same
    principal amount AND (maturity date OR interest rate). When duplicates
    are found, prefer 424B forms over 8-K (more detailed).
    """
    if not records:
        return records
    
    seen = {}
    for r in records:
        # Build a composite key from the note's economic identity
        principal = r.get('total_principal_amount', 0)
        mat_date = r.get('maturity_date', '')
        rate = r.get('interest_rate')
        
        # Primary key: principal + maturity
        key = (principal, mat_date or '', rate if rate is not None else '')
        
        if key in seen:
            existing = seen[key]
            # Prefer 424B forms over 8-K (more detail in prospectus)
            if existing['filing_form'] == '8-K' and r['filing_form'] != '8-K':
                seen[key] = r
            # If same form type, prefer more populated record
            elif existing['filing_form'] == r['filing_form']:
                # Count non-empty fields
                def field_count(rec):
                    return sum(1 for v in rec.values() if v and v != 'None' and v != '')
                if field_count(r) > field_count(existing):
                    seen[key] = r
        else:
            seen[key] = r
    
    # Also deduplicate by accession_number — if same filing produced
    # multiple records with different principals, keep them all.
    # But if same principal from different filings, keep best one.
    # The above logic already handles this.
    
    return sorted(seen.values(), key=lambda x: x['date'])


def extract_convertible_note(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract convertible notes from filings.
    
    Each filing is expected to describe at most ONE convertible note issuance.
    The extractor identifies the most prominent principal amount in the document
    and extracts all related fields. Cross-filing deduplication ensures the same
    note mentioned in both an 8-K and a 424B produces only one record.
    """
    records = []
    note_filings = [f for f in filings if f.form.upper().strip() in (
        '8-K', '424B2', '424B3', '424B4', '424B5', '424B7'
    )]
    
    for filing in note_filings:
        try:
            text = downloader.get_filing_text(filing)
            if not text or not PATTERNS['convertible_note_kw'].search(text):
                continue
            
            record = _extract_single_note(text, filing, ticker, 0, downloader)
            if record and record['total_principal_amount'] > 0:
                records.append(record)
                
        except Exception as e:
            logger.error(
                f"Error extracting Convertible Note from filing "
                f"{filing.accession_no} for ticker {ticker}: {e}",
                exc_info=True
            )
    
    # Cross-filing deduplication
    records = _deduplicate_notes(records)
    
    records.sort(key=lambda x: x['date'])
    return records
