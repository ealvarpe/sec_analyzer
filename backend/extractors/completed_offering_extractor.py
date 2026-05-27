import logging
import uuid
import re
from datetime import datetime
from ..services.html_parser import SECHTMLParser
from ..services.regex_patterns import (
    PATTERNS, normalize_amount, parse_date, extract_first_match,
    extract_all_matches, clean_number, extract_dollar_amount,
    detect_offering_type
)

logger = logging.getLogger(__name__)

def clean_banker(bank_str, cover_text=""):
    if not bank_str:
        return ""
    bank_str = bank_str.strip().strip(',').strip('.')
    lower_bank = bank_str.lower()
    
    fillers = (
        'or principal', 'may sell shares', 'acting as', 'is', 'are',
        'sales agent', 'placement agent', 'sales agent or principal',
        'underwriter', 'lead manager', 'book-running manager',
        'the company', 'our common stock', 'common stock', 'shares of',
        'those', 'certain', 'several', 'each', 'any', 'individual',
        'investors', 'purchasers', 'selling', 'holders', 'referred', 'parties', 'them',
        'buyer', 'purchaser', 'subscriber', 'investor', 'creditor', 'lender', 'underwriters',
        'agents', 'affiliate', 'affiliates', 'stockholder', 'stockholders', 'shareholder',
        'shareholders', 'entity', 'entities', 'person', 'persons', 'party', 'parties',
        'holder', 'a holder'
    )
    
    invalid_bankers = {
        'the', 'those', 'certain', 'several', 'each', 'any', 'individual', 'investors',
        'purchasers', 'selling', 'holders', 'referred', 'parties', 'them', 'our', 'us',
        'company', 'their', 'buyer', 'purchaser', 'subscriber', 'investor', 'creditor',
        'lender', 'underwriters', 'agents', 'affiliate', 'affiliates', 'stockholder',
        'stockholders', 'shareholder', 'shareholders', 'entity', 'entities', 'person',
        'persons', 'party', 'parties', 'holder', 'a holder'
    }
    
    if not bank_str or any(f in lower_bank for f in fillers) or lower_bank.strip() in invalid_bankers or len(bank_str.strip()) < 3:
        if cover_text and 'ThinkEquity' in cover_text:
            return 'ThinkEquity'
        if cover_text and ('Alliance Global' in cover_text or 'A.G.P.' in cover_text or 'A. G. P.' in cover_text):
            return 'Alliance Global Partners'
        return ""
        
    # Normalization mapping
    norm = bank_str.lower().replace(" ", "").replace(".", "").replace(",", "").replace("-", "")
    if "alliance" in norm or "agp" in norm or "agpartners" in norm or "allianceglobal" in norm or norm == "ag":
        return "Alliance Global Partners"
    if "roth" in norm:
        return "Roth Capital Partners"
    if "thinkequity" in norm:
        return "ThinkEquity"
    if "wainwright" in norm:
        return "H.C. Wainwright & Co."
    if "maxim" in norm:
        return "Maxim Group"
    if "aegis" in norm:
        return "Aegis Capital Corp."
    if "briley" in norm:
        return "B. Riley Securities"
    if "craighallum" in norm:
        return "Craig-Hallum Capital Group"
    if "benchmark" in norm:
        return "Benchmark Company"
    if "ladenburg" in norm:
        return "Ladenburg Thalmann"
    if "chardan" in norm:
        return "Chardan Capital Markets"
    if "efhutton" in norm:
        return "EF Hutton"
    if "dawsonjames" in norm:
        return "Dawson James Securities"
        
    return bank_str

def find_banker_in_ticker_filings(filings, downloader):
    """Scan S-1, S-3, and 424 filings in history to link bankers to dates dynamically."""
    banker_map = {}
    known_banks_rx = re.compile(
        r'\b(Alliance\s+Global\s+Partners|A\.?\s*G\.?\s*P\.?|ThinkEquity|Maxim\s+Group|Maxim|Aegis|HC\s+Wainwright|H\.\s*C\.\s+Wainwright|Roth\s+Capital|Roth\s+MKM|Roth\s+Capital\s+Partners|Craig[- ]Hallum|Benchmark|Ladenburg\s+Thalmann|Craft\s+Capital|Oppenheimer|Canaccord|B\.?\s*Riley|Lake\s+Street|Chardan|EF\s+Hutton|Dawson\s+James)\b',
        re.I
    )
    for f in filings:
        form = f.form.upper().strip()
        if form in ('S-1', 'S-1/A', 'S-3', 'S-3/A', '424B3', '424B4', '424B5'):
            try:
                t = downloader.get_filing_text(f) or ""
                # Scan first 80,000 characters for banker name
                m = known_banks_rx.search(t[:80000])
                if m:
                    bank_name = clean_banker(m.group(1))
                        
                    # Associate with filing date
                    banker_map[str(f.filing_date)] = bank_name
                    # Look for referenced dates
                    ref_dates = re.findall(
                        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
                        t[:40000]
                    )
                    for month, day, year in ref_dates:
                        ref_dt = parse_date(f"{month} {day} {year}")
                        if ref_dt:
                            banker_map[ref_dt] = bank_name
            except Exception as e:
                logger.debug(f"Failed to pre-scan banker in filing {f.accession_no}: {e}")
    return banker_map

def lookup_banker(date_str, banker_map):
    """Find a banker associated with a date (including a +/- 5 days window for closing/pricing differences)."""
    if not date_str or not banker_map:
        return ""
    if date_str in banker_map:
        return banker_map[date_str]
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        for k, v in banker_map.items():
            try:
                k_dt = datetime.strptime(k, '%Y-%m-%d')
                if abs((dt - k_dt).days) <= 5:
                    return v
            except:
                continue
    except:
        pass
    return ""

def extract_investor_names(text, ticker):
    """Extract investment funds or institutional investors dynamically from text."""
    investors = []
    # Search for LLC, L.P., LP, Inc., Corp, Partners, Capital
    matches = re.findall(
        r"([A-Z][\w\s,&\.]+?(?:\bLLC\b|\bL\.P\.\b|\bLP\b|\bInc\b|\bCorp\b|\bPartners\b|\bCapital\b)(?:\s*,\s*LP|\s*,\s*L\.P\.)?)",
        text[:20000]
    )
    
    ticker_lower = ticker.lower()
    for m in matches:
        m_clean = m.strip().replace('\n', ' ')
        m_lower = m_clean.lower()
        # Filter out self-referential or broker-dealer entities
        if not any(x in m_lower for x in [
            "the company", "us", "registrar", "underwriter", "placement agent",
            "sales agent", "thinkequity", "roth capital", "wainwright", "exhibit", ticker_lower
        ]):
            if len(m_clean) > 5 and len(m_clean) < 60:
                investors.append(m_clean)
                
    seen = set()
    deduped = []
    for inv in investors:
        if inv.lower() not in seen:
            seen.add(inv.lower())
            deduped.append(inv)
    return ", ".join(deduped[:3])

def extract_completed_offering(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract completed offerings from filings dynamically without hardcoding."""
    records = []
    
    # We want to process 8-K, 424B2, 424B3, 424B4, 424B5, 424B7, and FWP filings
    completed_filings = [f for f in filings if f.form.upper().strip() in (
        '8-K', '424B2', '424B3', '424B4', '424B5', '424B7', 'FWP'
    )]
    
    # Pre-scan S-1/S-3/424 files for banker name association
    banker_map = find_banker_in_ticker_filings(filings, downloader)
    
    for filing in completed_filings:
        try:
            form = filing.form.upper().strip()
            acc = str(filing.accession_no)
            filing_date_str = str(filing.filing_date)
            
            # Read plain text content of filing
            text = downloader.get_filing_text(filing) or ""
            if not text:
                continue
                
            cover_text = text[:8000]
            
            # Skip resale registration statements (selling stockholder secondary sales)
            is_resale = any(x in cover_text.lower() for x in ["resale by", "resale of", "selling stockholder", "selling security holder"])
            if is_resale:
                continue
            
            # If 8-K, check if it relates to completing/closing a transaction
            if form == '8-K':
                is_completed = bool(PATTERNS['closing_completed'].search(text))
                is_completed = is_completed or "closing date" in text.lower() or "issue date" in text.lower()
                is_completed = is_completed or ("entered into" in text.lower() and "purchase agreement" in text.lower())
                if not is_completed:
                    continue
            
            # 1. Offering Type
            off_type = detect_offering_type(text)
            if off_type == 'Convertible':
                off_type = 'Convertible Note'
            
            # 2. Method / Registration type
            method = 'S-3'
            if form == '424B4':
                method = 'S-1'
            elif form == '8-K':
                method = 'Unregistered' if ('private placement' in text.lower() or 'section 4(a)(2)' in text.lower()) else 'S-3'
                
            # 3. Shares Equivalent
            shares = None
            shares_match = re.search(r"We are offering (\d+[\d,]*)\s+shares", cover_text, re.I)
            if not shares_match:
                # Check for convertible note reserved shares text
                reserve_match = re.search(r"reserve\s+([\d,]+)\s+shares(?:\s+of\s+common\s+stock)?\s+for\s+the\s+conversion", text[:20000], re.I)
                if reserve_match:
                    shares = float(clean_number(reserve_match.group(1)))
            if not shares_match and not shares:
                shares_str = extract_first_match(PATTERNS['shares_offered'], cover_text)
                shares = float(clean_number(shares_str)) if shares_str else None
            elif shares_match:
                shares = float(clean_number(shares_match.group(1)))
                
            # 4. Price
            price = None
            price_match = re.search(r"purchase price for each share is \$([\d,.]+)", cover_text, re.I)
            if not price_match:
                price_match = re.search(r"price per share is \$([\d,.]+)", cover_text, re.I)
            if price_match:
                try:
                    price = float(price_match.group(1).rstrip('.').replace(',', ''))
                except ValueError:
                    pass
            if price is None:
                price_str = extract_first_match(PATTERNS['offering_price'], cover_text)
                if price_str:
                    try:
                        price = float(price_str.replace('$', '').rstrip('.').replace(',', '').strip())
                    except ValueError:
                        pass
                
            # 5. Warrants
            warrants = 0.0
            warrant_str = extract_first_match(PATTERNS['warrant_count'], cover_text)
            if warrant_str:
                warrants = float(clean_number(warrant_str))
                
            # 6. Pre-funded Warrants
            pre_funded = 0
            if PATTERNS['pre_funded_warrant'].search(text):
                pre_funded = 1
                
            # 7. Underwriter / Bank
            bank = extract_first_match(PATTERNS['sales_agent'], cover_text) or extract_first_match(PATTERNS['underwriter'], cover_text) or ""
            bank = clean_banker(bank, cover_text)
            if not bank:
                # Try the "with [Agent Name]" pattern
                pat_with = re.compile(
                    r'(?:[Ss]ales\s+[Aa]greement|[Dd]istribution\s+[Aa]greement|[Pp]lacement\s+[Aa]greement|[Pp]lacement\s+[Aa]gency\s+[Aa]greement|[Uu]nderwriting\s+[Aa]greement|[Aa]gency\s+[Aa]greement|(?<![Pp]urchase\s)(?<![Ss]ecurities\s[Pp]urchase\s)\b[Aa]greements?\b).{1,150}?\s+with\s+([A-Z][\w\s,&\.\/]{2,60}\b)'
                )
                m_with = pat_with.search(cover_text)
                if m_with:
                    bank = m_with.group(1)
                    bank = re.split(r'\s*(?:acting|dated|the|\(|has|is|are)\b', bank.strip(), flags=re.I)[0]
            bank = clean_banker(bank, cover_text)
            if not bank:
                # Search known banks in the cover/body text
                banker_match = re.search(
                    r'\b(Alliance\s+Global\s+Partners|A\.?\s*G\.?\s*P\.?|ThinkEquity|Maxim\s+Group|Maxim|Aegis|HC\s+Wainwright|H\.\s*C\.\s+Wainwright|Roth\s+Capital|Roth\s+MKM|Roth\s+Capital\s+Partners|Craig[- ]Hallum|Benchmark|Ladenburg\s+Thalmann|Craft\s+Capital|Oppenheimer|Canaccord|B\.?\s*Riley|Lake\s+Street|Chardan|EF\s+Hutton|Dawson\s+James)\b',
                    text[:20000],
                    re.I
                )
                if banker_match:
                    bank = banker_match.group(1)
            bank = clean_banker(bank, cover_text)
            
            # Normalize bank names
            if bank:
                lower_bank = bank.lower()
                if 'roth' in lower_bank:
                    bank = 'Roth Capital Partners'
                elif 'thinkequity' in lower_bank:
                    bank = 'ThinkEquity'
                elif 'wainwright' in lower_bank:
                    bank = 'H.C. Wainwright & Co.'
                elif 'alliance' in lower_bank or 'a.g.p' in lower_bank or 'agp' in lower_bank:
                    bank = 'Alliance Global Partners'
                elif 'maxim' in lower_bank:
                    bank = 'Maxim Group'
                elif 'aegis' in lower_bank:
                    bank = 'Aegis Capital Corp.'
                elif 'b. riley' in lower_bank or 'b riley' in lower_bank:
                    bank = 'B. Riley Securities'
                elif 'craig' in lower_bank:
                    bank = 'Craig-Hallum Capital Group'
                
            # 8. Commission rate
            commission = None
            comm_match = re.search(r"Underwriting\s+discounts\s+and\s+commissions\s*\(?(\d+(?:\.\d+)?)\s*%\)?", cover_text, re.I)
            if comm_match:
                commission = float(comm_match.group(1))
            else:
                comm_str = extract_first_match(PATTERNS['commission_rate'], cover_text)
                commission = float(comm_str) if comm_str else None
                
            # 9. Gross and Net Proceeds
            p_gross = re.compile(r"gross\s+proceeds\s+[\w\s]{0,50}?(?:of|is|was|were)\s+(?:approximately\s+)?\$\s*([\d,.]+)\s*(million|billion|thousand)?", re.I)
            p_net = re.compile(r"net\s+proceeds\s+[\w\s]{0,50}?(?:of|is|was|were)\s+(?:approximately\s+)?\$\s*([\d,.]+)\s*(million|billion|thousand)?", re.I)
            
            gross_val = None
            m_g = p_gross.search(text)
            if m_g:
                mult = m_g.group(2) or ""
                gross_val = normalize_amount(m_g.group(1), mult)
                
            net_val = None
            m_n = p_net.search(text)
            if m_n:
                mult = m_n.group(2) or ""
                net_val = normalize_amount(m_n.group(1), mult)
                
            # 10. Offering Amount
            amount = 0
            # If principal amounts are found (e.g. for convertible notes), sum them up
            principal_matches = list(PATTERNS['principal_amount'].finditer(text))
            if principal_matches and (off_type == 'Convertible Note' or 'convertible' in text.lower()):
                unique_principals = []
                for m in principal_matches:
                    val = int(clean_number(m.group(1)))
                    if val > 0 and val not in unique_principals:
                        unique_principals.append(val)
                if unique_principals:
                    gross_val = float(sum(unique_principals))
                    amount = int(gross_val)
            
            if not amount:
                if gross_val:
                    amount = int(gross_val)
                elif shares and price:
                    amount = int(shares * price)
                else:
                    amt = extract_dollar_amount(text, 'aggregate_amount')
                    amount = int(amt) if amt else 0
                
            # 11. Closing date
            closing_str = extract_first_match(PATTERNS['closing_date'], text)
            closing_date = parse_date(closing_str) if closing_str else filing_date_str
            
            # Post-lookup placement agent from S-1 / S-3 / Warrants pre-scanned map if still empty
            if not bank:
                bank = lookup_banker(closing_date, banker_map) or lookup_banker(filing_date_str, banker_map)
                bank = clean_banker(bank, cover_text)
                
            if not bank and method == 'Unregistered':
                bank = 'None (Direct Issuance)'
                
            # 12. Investors
            investors = extract_investor_names(text, ticker)
            if not investors and method != 'Unregistered':
                investors = 'Public Investors'
            elif not investors:
                investors = 'Institutional Investors'
                
            # 13. Discount to market (Convertible Note discount to VWAP formula)
            discount = None
            if off_type == 'Convertible Note' or 'convertible' in text.lower():
                vwap_match = re.search(r"(\d+)\s*%\s*(?:of\s+)?(?:the\s+)?(?:daily\s+)?(?:volume[- ]?weighted\s+average\s+price|VWAP)", text, re.I)
                if vwap_match:
                    discount = 100.0 - float(vwap_match.group(1))
            
            # 14. Specific fallbacks for ATM filings
            if off_type == 'ATM':
                investors = 'ATM Sales'
                warrants = 0.0
                atm_sales_match = re.search(
                    r"sold\s+an\s+aggregate\s+of\s+([\d,]+)\s+shares\s+[\w\s]*?for\s+aggregate\s+gross\s+proceeds\s+of\s+(?:approximately\s+)?\$([\d,.]+)",
                    text,
                    re.I
                )
                if atm_sales_match:
                    shares = float(clean_number(atm_sales_match.group(1)))
                    gross_val = float(clean_number(atm_sales_match.group(2)))
                    amount = int(gross_val)
                    if shares > 0:
                        price = round(gross_val / shares, 4)
                    if commission:
                        net_val = gross_val * (1 - commission / 100.0)
                else:
                    shares = None
                    price = None
                    gross_val = amount
            
            # Enforce calculations for unregistered direct deals
            if gross_val is None:
                gross_val = float(amount)
            if net_val is None and gross_val is not None:
                if commission:
                    net_val = gross_val * (1.0 - commission / 100.0)
                else:
                    net_val = gross_val
                    
            # Prioritize calculated amount over rounded text amount if close
            if shares and price:
                calc_amt = int(shares * price)
                if abs(amount - calc_amt) / calc_amt < 0.05:
                    amount = calc_amt
                    gross_val = float(calc_amt)
                    if commission:
                        net_val = gross_val * (1.0 - commission / 100.0)
                        
            # Filter out false-positive 8-K filings with zero values and no banker info
            if form == '8-K' and amount == 0 and (shares is None or shares == 0):
                continue
                
            records.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': filing_date_str,
                'offering_id': str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}-completed-{acc}")),
                'symbol': ticker,
                'pricing_datetime': filing_date_str,
                'type': off_type,
                'method': method,
                'shares_equivalent': shares,
                'price': price,
                'warrants': warrants,
                'offering_amount': amount,
                'bank': bank,
                'investors': investors,
                'pre_funded_warrants': pre_funded,
                'pre_funded_exercise_price': 0.0001 if pre_funded else None,
                'placement_agent_warrants': None,
                'discount_to_market_pct': discount,
                'gross_proceeds': gross_val,
                'net_proceeds': net_val,
                'agent_commission_pct': commission,
                'closing_date': closing_date,
                'registration_type': method,
                'accession_number': acc,
                'filing_form': filing.form,
                'related_shelf_number': extract_first_match(PATTERNS['file_number'], text) or '',
            })
        except Exception as e:
            logger.error(f"Error extracting Completed Offering from filing {filing.accession_no} for ticker {ticker}: {e}", exc_info=True)
            
    # Deduplicate completed offerings
    deduped = []
    seen = {}
    for r in records:
        key = (r['date'], r['offering_amount'])
        if key in seen:
            existing = seen[key]
            # Prefer 424B over 8-K if dates/amounts are the same
            if existing['filing_form'] == '8-K' and r['filing_form'] != '8-K':
                seen[key] = r
        else:
            seen[key] = r
            
    deduped = list(seen.values())
    deduped.sort(key=lambda x: x['date'])
    return deduped
