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

def extract_warrant(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract warrants from filings and group them into historical streams."""
    records = []
    # Skip S-3, S-3/A as they are universal shelf registrations and do not issue specific warrants
    warrant_filings = [f for f in filings if f.form.upper().strip() in (
        '8-K', '424B5', '424B2', '424B3', '424B4', '424B7', 'S-1', 'S-1/A'
    )]
    
    for filing in warrant_filings:
        try:
            text = downloader.get_filing_text(filing)
            if not text or 'warrant' not in text.lower():
                continue
                
            cover_text = text[:8000]
            
            # 1. Exercise Price (with context filtering to avoid false positives)
            price = None
            price_matches = list(PATTERNS['exercise_price'].finditer(text))
            for m in price_matches:
                match_start = m.start()
                context = text[max(0, match_start - 150): match_start].lower()
                if any(x in context for x in ["outstanding option", "outstanding warrant", "weighted average", "other than the"]):
                    continue
                try:
                    price = float(m.group(1).replace('$', '').strip())
                    break
                except:
                    pass
            
            # If it's a Warrant Exchange Agreement, we might not have a new exercise price, but we have text
            is_exchange = "warrant exchange" in text.lower() or "exchange of warrants" in text.lower()
            
            if price is None and not is_exchange:
                # If we couldn't find a valid exercise price and it's not an exchange, skip it
                continue
                
            # 2. Warrant Count
            # Require at least one digit in the count
            p_count = re.compile(
                r"(?:warrants?\s+to\s+purchase\s+(?:up\s+to\s+)?|(?:issue|issu\w+)\s+(?:up\s+to\s+)?)?(\d+[\d,]*)\s+(?:common\s+)?warrants?",
                re.I
            )
            p_shares = re.compile(
                r"warrants?\s+to\s+purchase\s+(?:up\s+to\s+)?(\d+[\d,]*)\s+shares",
                re.I
            )
            
            count_str = None
            # Check cover text first
            m_count = p_count.search(cover_text)
            if m_count:
                count_str = m_count.group(1)
            else:
                m_shares = p_shares.search(cover_text)
                if m_shares:
                    count_str = m_shares.group(1)
                    
            if not count_str:
                # Fallback to full text but make sure it has context of new issue
                for m in p_count.finditer(text):
                    context = text[max(0, m.start() - 100): m.start()].lower()
                    if "outstanding" in context or "exercised" in context or "held by" in context:
                        continue
                    count_str = m.group(1)
                    break
                    
            count = int(clean_number(count_str)) if count_str else 0
            
            # 3. Warrant type
            w_type = 'Investor Warrant'
            if PATTERNS['pre_funded_warrant'].search(text):
                w_type = 'Pre-Funded Warrant'
            elif 'placement agent' in text.lower() and 'placement agent warrant' in text.lower():
                w_type = 'Placement Agent Warrant'
                
            # 4. Underwriter/Agent
            agent = extract_first_match(PATTERNS['sales_agent'], cover_text) or extract_first_match(PATTERNS['underwriter'], cover_text) or ""
            if not agent:
                # Search first 40,000 chars for known investment banks
                body_sample = text[:40000]
                banker_match = re.search(r'(ThinkEquity|Maxim Group|Aegis|HC\s+Wainwright|H\.\s*C\.\s+Wainwright|Roth\s+Capital|Roth\s+MKM|Craig[- ]Hallum|Benchmark|Ladenburg\s+Thalmann)', body_sample, re.I)
                if banker_match:
                    agent = banker_match.group(1)
            agent = agent.strip().strip(',').strip('.')
            
            # Normalize Roth names
            if 'roth' in agent.lower():
                agent = 'Roth Capital Partners'
            
            # 5. Expiry & Exercisable Date
            expiry_str = extract_first_match(PATTERNS['warrant_expiry'], text)
            expiry_date = parse_date(expiry_str) if expiry_str else ""
            
            exercisable_str = extract_first_match(PATTERNS['warrant_exercisable'], text)
            exercisable_date = parse_date(exercisable_str) if exercisable_str else str(filing.filing_date)
            
            # 6. Cashless Exercise
            cashless = None
            if PATTERNS['cashless_exercise'].search(text):
                cashless = True
                
            # 7. Anti-dilution / Price protection
            anti_dilution = "None"
            pp_clause = ""
            if "full ratchet" in text.lower():
                anti_dilution = "Full Ratchet"
                pp_clause = "Full ratchet price protection clause detected."
            elif "weighted average" in text.lower():
                anti_dilution = "Weighted Average"
                pp_clause = "Broad-based weighted average anti-dilution clause."
                
            # 8. Registration status
            reg_status = 'Unregistered'
            if filing.form.upper().startswith('424') or filing.form.upper().startswith('S-'):
                reg_status = 'Registered'
                
            filing_date_str = str(filing.filing_date)
            try:
                dt = datetime.strptime(filing_date_str, '%Y-%m-%d')
                month_year = dt.strftime('%B %Y')
            except:
                month_year = ""
            sec_name = f"{month_year} Warrants"
            
            raw_fields = {
                'total_warrants_issued': count,
                'exercise_price': price,
                'warrant_type': w_type,
                'expiration_date': expiry_date,
            }
            
            records.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': filing_date_str,
                'offering_type': 'Warrant',
                'registration_status': reg_status,
                'filing_edgar_url': getattr(filing, 'filing_url', '') or '',
                'raw_data_json': json.dumps(raw_fields),
                'security_name': sec_name,
                'remaining_warrants_outstanding': count,
                'exercise_price': price,
                'total_warrants_issued': count,
                'known_owners': '',
                'underwriter_placement_agent': agent,
                'price_protection': anti_dilution,
                'pp_clause': pp_clause,
                'issue_date': filing_date_str,
                'exercisable_date': exercisable_date,
                'expiration_date': expiry_date,
                'last_update_date': filing_date_str,
                'warrant_type': w_type,
                'shares_per_warrant': 1.0,
                'cashless_exercise': cashless,
                'anti_dilution_type': anti_dilution,
                'related_offering_date': filing_date_str,
                'accession_number': str(filing.accession_no),
                'filing_form': filing.form,
                'is_exchange': is_exchange,
                'raw_text_ref': text[:20000]
            })
        except Exception as e:
            logger.error(f"Error extracting Warrant from filing {filing.accession_no} for ticker {ticker}: {e}", exc_info=True)
            
    # --- Grouping & Linking Phase ---
    groups = []
    records.sort(key=lambda x: x['date'])
    
    for r in records:
        matched_group = None
        
        # If it's an exchange/cancellation agreement, check if it references a previous filing date
        if r['is_exchange']:
            ref_matches = re.findall(
                r'(January|February|March|April|May|June|July|August|September|October|November|December)'
                r'\s+(\d{1,2}),?\s+(\d{4})',
                r['raw_text_ref']
            )
            for month, day, year in ref_matches:
                ref_date_str = parse_date(f"{month} {day} {year}")
                for g in groups:
                    for member in g['members']:
                        try:
                            m_dt = datetime.strptime(member['date'], '%Y-%m-%d')
                            r_dt = datetime.strptime(ref_date_str, '%Y-%m-%d')
                            if abs((m_dt - r_dt).days) <= 7:
                                matched_group = g
                                break
                        except (ValueError, TypeError):
                            if member['date'] == ref_date_str:
                                matched_group = g
                                break
                    if matched_group:
                        break
                if matched_group:
                    break
                    
        # Group by exercise price + date proximity (±7 days for cross-filing dedup)
        if not matched_group and r['exercise_price'] is not None:
            r_date = datetime.strptime(r['date'], '%Y-%m-%d')
            for g in groups:
                price_match = any(
                    m['exercise_price'] == r['exercise_price']
                    for m in g['members']
                    if m['exercise_price'] is not None
                )
                if price_match:
                    dates = [datetime.strptime(m['date'], '%Y-%m-%d') for m in g['members']]
                    # Same warrant: same price within 7 days = cross-filing dup
                    # Same warrant: same price within 3 years = historical event
                    if any(abs((r_date - d).days) <= 7 for d in dates):
                        # Cross-filing duplicate — merge, prefer 424B
                        matched_group = g
                        break
                    elif any(abs((r_date - d).days) <= 3 * 365 for d in dates):
                        # Historical event for same warrant
                        matched_group = g
                        break
                        
        if matched_group:
            matched_group['members'].append(r)
        else:
            groups.append({
                'members': [r]
            })
            
    final_records = []
    for g in groups:
        members = g['members']
        members.sort(key=lambda x: x['date'])
        
        # Deterministic group ID based on exercise price + earliest date
        earliest_date = members[0]['date']
        price = next((m['exercise_price'] for m in members if m['exercise_price'] is not None), 0)
        group_key = f"{ticker}-warrant-{price}-{earliest_date}"
        group_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, group_key))
        
        max_issued = max(m['total_warrants_issued'] for m in members)
        agent = next((m['underwriter_placement_agent'] for m in members if m['underwriter_placement_agent']), "")
        
        # Deduplicate cross-filing entries on the same date (8-K + 424B)
        seen_dates = {}
        deduped_members = []
        for r in members:
            if r['date'] in seen_dates:
                existing = seen_dates[r['date']]
                # Prefer 424B over 8-K
                if existing['filing_form'] == '8-K' and r['filing_form'] != '8-K':
                    seen_dates[r['date']] = r
                    deduped_members = [m for m in deduped_members if m['date'] != r['date']]
                    deduped_members.append(r)
            else:
                # Check for near-date duplicates (within 7 days)
                is_dup = False
                r_dt = datetime.strptime(r['date'], '%Y-%m-%d')
                for existing_date, existing_rec in seen_dates.items():
                    try:
                        e_dt = datetime.strptime(existing_date, '%Y-%m-%d')
                        if abs((r_dt - e_dt).days) <= 7 and not r.get('is_exchange'):
                            # Cross-filing dup — prefer 424B
                            if existing_rec['filing_form'] == '8-K' and r['filing_form'] != '8-K':
                                deduped_members = [m for m in deduped_members if m['date'] != existing_date]
                                del seen_dates[existing_date]
                                seen_dates[r['date']] = r
                                deduped_members.append(r)
                            is_dup = True
                            break
                    except (ValueError, TypeError):
                        pass
                if not is_dup:
                    seen_dates[r['date']] = r
                    deduped_members.append(r)
        
        for idx, r in enumerate(deduped_members):
            r['filing_id'] = group_id
            
            if not r['underwriter_placement_agent']:
                r['underwriter_placement_agent'] = agent
            if r['exercise_price'] is None:
                r['exercise_price'] = price
                
            if r['total_warrants_issued'] == 0:
                r['total_warrants_issued'] = max_issued
                
            if r['is_exchange']:
                r['remaining_warrants_outstanding'] = 0
                r['registration_status'] = 'Retired'
            else:
                r['remaining_warrants_outstanding'] = max_issued
                if r['filing_form'] == '8-K':
                    r['registration_status'] = 'Unregistered'
                elif r['filing_form'].startswith('S-') or r['filing_form'].startswith('424'):
                    r['registration_status'] = 'Registered'
            
            # Cleanup helper fields
            del r['is_exchange']
            del r['raw_text_ref']
            final_records.append(r)
            
    final_records.sort(key=lambda x: x['date'])
    return final_records

