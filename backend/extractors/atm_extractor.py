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

def clean_agent(agent_str, cover_text):
    """Clean the extracted agent string and fall back to known agents if filler text is matched."""
    if not agent_str:
        return ""
    agent_str = agent_str.strip().strip(',').strip('.')
    lower_agent = agent_str.lower()
    
    # List of known filler/garbage text patterns that sometimes match
    fillers = (
        'or principal', 'may sell shares of', 'acting as', 'is', 'are',
        'sales agent', 'placement agent', 'sales agent or principal',
        'those', 'certain', 'several', 'each', 'any', 'individual',
        'investors', 'purchasers', 'selling', 'holders', 'referred', 'parties', 'them',
        'buyer', 'purchaser', 'subscriber', 'investor', 'creditor', 'lender', 'underwriters',
        'agents', 'affiliate', 'affiliates', 'stockholder', 'stockholders', 'shareholder',
        'shareholders', 'entity', 'entities', 'person', 'persons', 'party', 'parties',
        'holder', 'a holder'
    )
    
    invalid_agents = {
        'the', 'those', 'certain', 'several', 'each', 'any', 'individual', 'investors',
        'purchasers', 'selling', 'holders', 'referred', 'parties', 'them', 'our', 'us',
        'company', 'their', 'buyer', 'purchaser', 'subscriber', 'investor', 'creditor',
        'lender', 'underwriters', 'agents', 'affiliate', 'affiliates', 'stockholder',
        'stockholders', 'shareholder', 'shareholders', 'entity', 'entities', 'person',
        'persons', 'party', 'parties', 'holder', 'a holder'
    }
    
    if not agent_str or any(f in lower_agent for f in fillers) or lower_agent.strip() in invalid_agents or len(agent_str.strip()) < 3:
        if 'ThinkEquity' in cover_text:
            return 'ThinkEquity'
        if 'Alliance Global' in cover_text or 'A.G.P.' in cover_text or 'A. G. P.' in cover_text:
            return 'Alliance Global Partners'
        return ""
        
    # Normalization mapping
    norm = agent_str.lower().replace(" ", "").replace(".", "").replace(",", "").replace("-", "")
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
        
    return agent_str

def extract_atm(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract ATM offerings from filings, including historical updates from 10-K/10-Q."""
    records = []
    
    # 1. Extract base registration programs from 424B5, S-3, etc.
    atm_filings = [f for f in filings if f.form.upper().strip() in ('424B5', 'S-3', 'S-3/A', 'S-3ASR')]
    base_programs = []
    
    for filing in atm_filings:
        try:
            html_content = downloader.get_filing_html(filing)
            if not html_content:
                continue
            
            parser = SECHTMLParser(html_content)
            cover_text = parser.get_cover_page_text()
            
            # Check if ATM is mentioned
            if not PATTERNS['atm_keyword'].search(cover_text) and 'at-the-market' not in cover_text.lower():
                continue
                
            # Extract capacity
            total_capacity = extract_dollar_amount(cover_text, 'atm_program')
            if not total_capacity:
                total_capacity = extract_dollar_amount(cover_text, 'up_to_amount')
            if not total_capacity:
                total_capacity = extract_dollar_amount(cover_text, 'dollar_amount')
            if not total_capacity:
                total_capacity = extract_dollar_amount(cover_text, 'aggregate_amount')
            total_capacity = int(total_capacity) if total_capacity else 0
            
            # Extract placement agent
            pat_agent = re.compile(
                r'(?:[Ss]ales\s+[Aa]greement|[Dd]istribution\s+[Aa]greement|[Pp]lacement\s+[Aa]greement|[Pp]lacement\s+[Aa]gency\s+[Aa]greement|[Uu]nderwriting\s+[Aa]greement|[Aa]gency\s+[Aa]greement|(?<![Pp]urchase\s)(?<![Ss]ecurities\s[Pp]urchase\s)\b[Aa]greements?\b).{1,150}?\s+with\s+([A-Z][\w\s,&\.\/]{2,60}\b)'
            )
            agent = extract_first_match(pat_agent, cover_text) or ""
            if not agent:
                agent = extract_first_match(PATTERNS['sales_agent'], cover_text) or ""
            agent = clean_agent(agent, cover_text)
            
            # Extract commission
            comm_str = extract_first_match(r'(?:commission|fee)[\w\s\(\),]*?\s+(\d+(?:\.\d+)?)\s*%', cover_text)
            if not comm_str:
                comm_str = extract_first_match(PATTERNS['commission_rate'], cover_text)
            commission = float(comm_str) if comm_str else None
            
            # Extract file number (shelf registration)
            shelf_reg = extract_first_match(r'\b(333-\d+)\b', cover_text) or ""
            
            # Find related shelf filing URL
            related_url = ""
            if shelf_reg:
                for f in filings:
                    if f.form.upper().strip() in ('S-3', 'S-3ASR') and getattr(f, 'file_number', '') == shelf_reg:
                        related_url = getattr(f, 'filing_url', '') or ''
                        break
            if not related_url:
                # Fallback: find any S-3 filed before
                for f in filings:
                    if f.form.upper().strip() in ('S-3', 'S-3ASR') and f.filing_date <= filing.filing_date:
                        related_url = getattr(f, 'filing_url', '') or ''
                        break
            
            # Extract dates
            filing_date_str = str(filing.filing_date)
            ag_date_str = extract_first_match(r'(?:agreement|distribution\s+agreement)[\w\s\(\),]*?dated\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})', cover_text)
            if not ag_date_str:
                ag_date_str = extract_first_match(r'(?:agreement|distribution\s+agreement)[\w\s\(\),]*?dated\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})', html_content[:20000])
            agreement_start_date = parse_date(ag_date_str) if ag_date_str else filing_date_str
            
            # Check if limited by baby shelf
            atm_limited_by_baby_shelf = "i.b.6" in cover_text.lower() or "i.b.6" in html_content[:20000].lower()
            
            # Generate deterministic UUID linking tranches of the same registration
            key = f"{ticker}-{shelf_reg}" if shelf_reg else f"{ticker}-{agreement_start_date}"
            filing_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
            
            try:
                dt = datetime.strptime(filing_date_str, '%Y-%m-%d')
                month_year = dt.strftime('%B %Y')
            except:
                month_year = ""
                
            sec_name = f"{month_year} {agent} ATM".strip() if agent else f"{month_year} ATM".strip()
            
            raw_fields = {
                'total_atm_capacity': total_capacity,
                'placement_agent': agent,
                'commission_rate': commission,
                'shelf_registration_number': shelf_reg,
            }
            
            base_programs.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': filing_date_str,
                'filing_id': filing_id,
                'offering_type': 'ATM',
                'registration_status': 'Registered',
                'filing_edgar_url': getattr(filing, 'filing_url', '') or '',
                'raw_data_json': json.dumps(raw_fields),
                'security_name': sec_name,
                'remaining_atm_capacity': total_capacity,
                'total_atm_capacity': total_capacity,
                'atm_limited_by_baby_shelf': atm_limited_by_baby_shelf,
                'placement_agent': agent,
                'agreement_start_date': agreement_start_date,
                'last_update_date': filing_date_str,
                'commission_rate': commission,
                'shelf_registration_number': shelf_reg,
                'related_shelf_filing_url': related_url,
                'shares_sold_to_date': 0,
                'amount_raised_to_date': 0,
                'filing_form': filing.form,
                'accession_number': str(filing.accession_no),
            })
        except Exception as e:
            logger.error(f"Error extracting ATM from filing {filing.accession_no} for ticker {ticker}: {e}", exc_info=True)
            
    if not base_programs:
        return []
        
    # Sort base programs descending by date to assign status
    base_programs.sort(key=lambda x: x['date'], reverse=True)
    for idx, r in enumerate(base_programs):
        if idx == 0:
            r['registration_status'] = 'Registered'
        else:
            r['registration_status'] = 'Replaced'
            
    records.extend(base_programs)
    
    # 2. Scan 10-K and 10-Q filings for historical updates
    reporting_filings = [f for f in filings if f.form.upper().strip() in ('10-K', '10-Q', '10-K/A', '10-Q/A')]
    reporting_filings.sort(key=lambda x: x.filing_date) # oldest first
    
    updates = []
    
    for rf in reporting_filings:
        try:
            rf_text = downloader.get_filing_text(rf) or ""
            if not rf_text:
                continue
                
            # Clean single newlines to reconstruct sentences
            cleaned_text = re.sub(r'(?<!\n)\n(?!\n)', ' ', rf_text)
            
            # Heuristic 1: Scan for tabular matches in the entire document
            tabular_matches = re.finditer(r'sold\s+([\d,]+)\s+([\d\.]+)\s+([\d,]+)(?:\s+([\d,]+))?', cleaned_text, re.I)
            for tm in tabular_matches:
                shares = int(tm.group(1).replace(',', ''))
                price = float(tm.group(2))
                net_proceeds = float(tm.group(3).replace(',', ''))
                expenses = float(tm.group(4).replace(',', '')) if tm.group(4) else None
                
                # Apply scaling
                expected = shares * price
                if abs(expected - net_proceeds * 1000) / expected < 0.2:
                    net_proceeds *= 1000
                    if expenses:
                        expenses *= 1000
                elif abs(expected - net_proceeds * 1000000) / expected < 0.2:
                    net_proceeds *= 1000000
                    if expenses:
                        expenses *= 1000000
                        
                gross_proceeds = int(net_proceeds + (expenses or 0))
                
                # Find update date: search for "as of" in the surrounding 300 characters
                context = cleaned_text[max(0, tm.start() - 300):min(len(cleaned_text), tm.end() + 300)]
                as_of_m = re.search(r'as\s+of\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})', context, re.I)
                update_date = parse_date(as_of_m.group(1)) if as_of_m else str(rf.filing_date)
                
                # Find the correct base program to link (using p['date'] <= update_date)
                linked_program = None
                for p in base_programs:
                    if p['date'] <= update_date:
                        if linked_program is None or p['date'] > linked_program['date']:
                            linked_program = p
                            
                if linked_program:
                    remaining = max(0, linked_program['total_atm_capacity'] - gross_proceeds)
                    
                    raw_fields_update = {
                        'total_atm_capacity': linked_program['total_atm_capacity'],
                        'placement_agent': linked_program['placement_agent'],
                        'commission_rate': linked_program['commission_rate'],
                        'shelf_registration_number': linked_program['shelf_registration_number'],
                        'shares_sold': shares,
                        'price': price,
                        'net_proceeds': net_proceeds,
                        'expenses': expenses
                    }
                    
                    updates.append({
                        'ticker': ticker,
                        'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                        'date': update_date,
                        'filing_id': linked_program['filing_id'],
                        'offering_type': 'ATM',
                        'registration_status': 'Updated',
                        'filing_edgar_url': getattr(rf, 'filing_url', '') or '',
                        'raw_data_json': json.dumps(raw_fields_update),
                        'security_name': linked_program['security_name'],
                        'remaining_atm_capacity': remaining,
                        'total_atm_capacity': linked_program['total_atm_capacity'],
                        'atm_limited_by_baby_shelf': linked_program['atm_limited_by_baby_shelf'],
                        'placement_agent': linked_program['placement_agent'],
                        'agreement_start_date': linked_program['agreement_start_date'],
                        'last_update_date': update_date,
                        'commission_rate': linked_program['commission_rate'],
                        'shelf_registration_number': linked_program['shelf_registration_number'],
                        'related_shelf_filing_url': linked_program['related_shelf_filing_url'],
                        'shares_sold_to_date': shares,
                        'amount_raised_to_date': gross_proceeds,
                        'filing_form': rf.form,
                        'accession_number': str(rf.accession_no),
                    })
                    
            # Heuristic 2: Scan sentences for text-based disclosures
            sentences = cleaned_text.split('.')
            for sentence in sentences:
                sentence_lower = sentence.lower()
                if (
                    ('sales agreement' in sentence_lower or 'atm offering' in sentence_lower or 'at-the-market' in sentence_lower or 'atm facility' in sentence_lower)
                    and 'shares' in sentence_lower
                    and 'sold' in sentence_lower
                ):
                    shares_m = re.search(r'sold\s+([\d,]+)\s+shares', sentence, re.I)
                    price_m = re.search(r'weighted-average\s+price\s+(?:of\s+)?\$([\d\.,]+)', sentence, re.I) or re.search(r'average\s+price\s+(?:of\s+)?\$([\d\.,]+)', sentence, re.I)
                    net_m = re.search(r'net\s+proceeds\s+(?:of\s+)?\$([\d,]+)', sentence, re.I)
                    exp_m = re.search(r'expenses\s+(?:of\s+)?\$([\d,]+)', sentence, re.I)
                    
                    if shares_m:
                        shares = int(shares_m.group(1).replace(',', ''))
                        price = float(price_m.group(1)) if price_m else None
                        net_proceeds = float(net_m.group(1).replace(',', '')) if net_m else None
                        expenses = float(exp_m.group(1).replace(',', '')) if exp_m else None
                        
                        # Apply scaling
                        if price and net_proceeds:
                            expected = shares * price
                            if abs(expected - net_proceeds * 1000) / expected < 0.2:
                                net_proceeds *= 1000
                                if expenses:
                                    expenses *= 1000
                            elif abs(expected - net_proceeds * 1000000) / expected < 0.2:
                                net_proceeds *= 1000000
                                if expenses:
                                    expenses *= 1000000
                                    
                        gross_proceeds = int(net_proceeds + (expenses or 0)) if net_proceeds else (int(shares * price) if price else 0)
                        
                        as_of_m = re.search(r'as\s+of\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})', sentence, re.I)
                        update_date = parse_date(as_of_m.group(1)) if as_of_m else str(rf.filing_date)
                        
                        linked_program = None
                        for p in base_programs:
                            if p['date'] <= update_date:
                                if linked_program is None or p['date'] > linked_program['date']:
                                    linked_program = p
                                    
                        if linked_program:
                            remaining = max(0, linked_program['total_atm_capacity'] - gross_proceeds)
                            
                            raw_fields_update = {
                                'total_atm_capacity': linked_program['total_atm_capacity'],
                                'placement_agent': linked_program['placement_agent'],
                                'commission_rate': linked_program['commission_rate'],
                                'shelf_registration_number': linked_program['shelf_registration_number'],
                                'shares_sold': shares,
                                'price': price,
                                'net_proceeds': net_proceeds,
                                'expenses': expenses
                            }
                            
                            updates.append({
                                'ticker': ticker,
                                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                                'date': update_date,
                                'filing_id': linked_program['filing_id'],
                                'offering_type': 'ATM',
                                'registration_status': 'Updated',
                                'filing_edgar_url': getattr(rf, 'filing_url', '') or '',
                                'raw_data_json': json.dumps(raw_fields_update),
                                'security_name': linked_program['security_name'],
                                'remaining_atm_capacity': remaining,
                                'total_atm_capacity': linked_program['total_atm_capacity'],
                                'atm_limited_by_baby_shelf': linked_program['atm_limited_by_baby_shelf'],
                                'placement_agent': linked_program['placement_agent'],
                                'agreement_start_date': linked_program['agreement_start_date'],
                                'last_update_date': update_date,
                                'commission_rate': linked_program['commission_rate'],
                                'shelf_registration_number': linked_program['shelf_registration_number'],
                                'related_shelf_filing_url': linked_program['related_shelf_filing_url'],
                                'shares_sold_to_date': shares,
                                'amount_raised_to_date': gross_proceeds,
                                'filing_form': rf.form,
                                'accession_number': str(rf.accession_no),
                            })
        except Exception as e:
            logger.warning(f"Error parsing filing {rf.accession_no} for ATM updates: {e}")
            
    # Deduplicate updates, keeping the one with the higher amount_raised_to_date
    seen_updates = {}
    for u in updates:
        key = (u['filing_id'], u['date'])
        if key in seen_updates:
            existing = seen_updates[key]
            if (u['amount_raised_to_date'] or 0) > (existing['amount_raised_to_date'] or 0):
                seen_updates[key] = u
        else:
            seen_updates[key] = u
            
    deduped_updates = list(seen_updates.values())
    records.extend(deduped_updates)
    
    # Sort records by date ascending
    records.sort(key=lambda x: x['date'])
    return records
