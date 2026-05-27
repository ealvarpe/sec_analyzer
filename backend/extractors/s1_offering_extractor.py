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

def extract_s1_offering(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract S-1 offerings and their historical events from filings."""
    records = []
    
    # We want to process S-1, S-1/A, EFFECT, 424B3, and 424B4 filings
    s1_filings = [f for f in filings if f.form.upper().strip() in ('S-1', 'S-1/A', 'EFFECT', '424B3', '424B4')]
    
    # 1. Identify all base S-1 and S-1/A filings
    base_s1_filings = [f for f in s1_filings if f.form.upper().strip() in ('S-1', 'S-1/A')]
    
    for s1 in base_s1_filings:
        try:
            form = s1.form.upper().strip()
            text = downloader.get_filing_text(s1)
            if not text:
                continue
                
            acc = str(s1.accession_no)
            filing_date_str = str(s1.filing_date)
            
            cover_text = text[:8000]
            
            # Detect resale registration dynamically
            is_resale = any(x in cover_text.lower() for x in ["resale by", "resale of", "selling stockholder", "selling security holder"])
            
            if is_resale:
                # 1. Shares offered by selling holders
                resale_shares_match = re.search(r"resale\s+[\w\s]*?of\s+up\s+to\s+([\d,]+)\s+shares", cover_text, re.I)
                if not resale_shares_match:
                    resale_shares_match = re.search(r"selling\s+stockholder\w*?\s+[\w\s]*?of\s+up\s+to\s+([\d,]+)\s+shares", cover_text, re.I)
                if not resale_shares_match:
                    shares_str = extract_first_match(PATTERNS['shares_offered'], cover_text)
                    shares = int(clean_number(shares_str)) if shares_str else 0
                else:
                    shares = int(clean_number(resale_shares_match.group(1)))
                
                selling_shares = shares
                
                # 2. Warrants/Warrant shares
                warrants = 0
                warrants_match = re.search(r"(?:including|consisting of|and)\s+([\d,]+)\s+shares\s+[\w\s]*?issuable\s+upon\s+(?:the\s+)?exercise\s+of\s+warrants", cover_text, re.I)
                if warrants_match:
                    warrants = int(clean_number(warrants_match.group(1)))
                
                # 3. Exercise Price
                exercise_price = None
                ex_match = re.search(r"exercise\s+price\s+(?:of\s+|equal\s+to\s+|is\s+)?\$\s*([\d,.]+)", cover_text, re.I)
                if ex_match:
                    exercise_price = float(ex_match.group(1))
                    
                agent = "None (Resale Registration)"
                lock_up_days = None
                over_allotment = None
                offering_type_detail = "Resale Registration"
                
                try:
                    dt = datetime.strptime(filing_date_str, '%Y-%m-%d')
                    month_year = dt.strftime('%B %Y')
                except:
                    month_year = ""
                sec_name = f"{month_year} Resale S-1" if month_year else "Resale S-1"
                est_deal_size = "N/A (Secondary Resale)"
            else:
                # General parsing fallback
                shares_str = extract_first_match(PATTERNS['shares_offered'], cover_text)
                shares = int(clean_number(shares_str)) if shares_str else 0
                
                price_str = extract_first_match(PATTERNS['offering_price'], cover_text)
                price = float(price_str.replace('$', '').strip()) if price_str else None
                
                agent = extract_first_match(PATTERNS['underwriter'], cover_text) or extract_first_match(PATTERNS['sales_agent'], cover_text) or ""
                agent = agent.strip().strip(',').strip('.')
                
                lock_up = extract_first_match(PATTERNS['lock_up'], cover_text)
                lock_up_days = int(lock_up) if lock_up else None
                
                over_allotment_shares_str = extract_first_match(PATTERNS['over_allotment'], cover_text)
                over_allotment_shares = int(clean_number(over_allotment_shares_str)) if over_allotment_shares_str else None
                over_allotment = over_allotment_shares
                
                warrants = 0
                exercise_price = None
                selling_shares = None
                offering_type_detail = 'IPO' if 'initial public offering' in cover_text.lower() else 'Follow-on'
                sec_name = "S-1 Offering"
                est_deal_size = f"${shares * price:,.2f}" if shares and price else ""
                
            # Deterministic group ID for this S-1 registration timeline
            group_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}-s1-{acc}"))
            
            # Record 1: The S-1 Filing Event
            raw_fields = {
                'shares_offered': shares,
                'underwriter': agent,
                'lock_up_days': lock_up_days,
            }
            
            records.append({
                'ticker': ticker,
                'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                'date': filing_date_str,
                'filing_id': group_id,
                'offering_type': 'S-1 Offering',
                'registration_status': 'Pending',
                'filing_edgar_url': getattr(s1, 'filing_url', '') or '',
                'raw_data_json': json.dumps(raw_fields),
                'security_name': sec_name,
                'anticipated_deal_size': est_deal_size,
                'status': 'Pending',
                'underwriter_placement_agent': agent,
                's_1_filing_date': filing_date_str,
                'warrant_coverage': warrants,
                'last_update_date': filing_date_str,
                'final_deal_size': "",
                'final_pricing': "",
                'final_shares_offered': 0,
                'final_warrant_coverage': 0,
                'exercise_price': str(exercise_price) if exercise_price else '',
                'offering_type_detail': offering_type_detail,
                'lock_up_period_days': lock_up_days,
                'over_allotment_shares': over_allotment,
                'over_allotment_pct': None,
                'price_range_low': None,
                'price_range_high': None,
                'use_of_proceeds': '',
                'shares_by_selling_holders': selling_shares,
                'accession_number': acc,
                'filing_form': s1.form,
                'amendment_number': 1 if "S-1/A" in form else 0,
            })
            
            # 2. Check for historical events: EFFECT filing
            effect_filings = [f for f in s1_filings if f.form.upper().strip() == 'EFFECT']
            for eff in effect_filings:
                eff_date = str(eff.filing_date)
                if eff_date >= filing_date_str:
                    diff_days = (datetime.strptime(eff_date, '%Y-%m-%d') - datetime.strptime(filing_date_str, '%Y-%m-%d')).days
                    if diff_days <= 45:
                        records.append({
                            'ticker': ticker,
                            'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                            'date': eff_date,
                            'filing_id': group_id,
                            'offering_type': 'S-1 Offering',
                            'registration_status': 'Effective',
                            'filing_edgar_url': getattr(eff, 'filing_url', '') or '',
                            'raw_data_json': json.dumps(raw_fields),
                            'security_name': sec_name,
                            'anticipated_deal_size': est_deal_size,
                            'status': 'Effective',
                            'underwriter_placement_agent': agent,
                            's_1_filing_date': filing_date_str,
                            'warrant_coverage': warrants,
                            'last_update_date': eff_date,
                            'final_deal_size': est_deal_size,
                            'final_pricing': "",
                            'final_shares_offered': shares,
                            'final_warrant_coverage': warrants,
                            'exercise_price': str(exercise_price) if exercise_price else '',
                            'offering_type_detail': offering_type_detail,
                            'lock_up_period_days': lock_up_days,
                            'over_allotment_shares': over_allotment,
                            'over_allotment_pct': None,
                            'price_range_low': None,
                            'price_range_high': None,
                            'use_of_proceeds': '',
                            'shares_by_selling_holders': selling_shares,
                            'accession_number': str(eff.accession_no),
                            'filing_form': eff.form,
                            'amendment_number': 0,
                        })
                        
            # 3. Check for prospectus filings: 424B3 or 424B4 filings
            pros_filings = [f for f in s1_filings if f.form.upper().strip() in ('424B3', '424B4')]
            for pros in pros_filings:
                pros_date = str(pros.filing_date)
                if pros_date >= filing_date_str:
                    diff_days = (datetime.strptime(pros_date, '%Y-%m-%d') - datetime.strptime(filing_date_str, '%Y-%m-%d')).days
                    if diff_days <= 45:
                        records.append({
                            'ticker': ticker,
                            'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                            'date': pros_date,
                            'filing_id': group_id,
                            'offering_type': 'S-1 Offering',
                            'registration_status': 'Effective',
                            'filing_edgar_url': getattr(pros, 'filing_url', '') or '',
                            'raw_data_json': json.dumps(raw_fields),
                            'security_name': sec_name,
                            'anticipated_deal_size': est_deal_size,
                            'status': 'Prospectus Filed',
                            'underwriter_placement_agent': agent,
                            's_1_filing_date': filing_date_str,
                            'warrant_coverage': warrants,
                            'last_update_date': pros_date,
                            'final_deal_size': est_deal_size,
                            'final_pricing': "",
                            'final_shares_offered': shares,
                            'final_warrant_coverage': warrants,
                            'exercise_price': str(exercise_price) if exercise_price else '',
                            'offering_type_detail': offering_type_detail,
                            'lock_up_period_days': lock_up_days,
                            'over_allotment_shares': over_allotment,
                            'over_allotment_pct': None,
                            'price_range_low': None,
                            'price_range_high': None,
                            'use_of_proceeds': '',
                            'shares_by_selling_holders': selling_shares,
                            'accession_number': str(pros.accession_no),
                            'filing_form': pros.form,
                            'amendment_number': 0,
                        })
                        
        except Exception as e:
            logger.error(f"Error S-1 offering extraction failed for {s1.accession_no}: {e}", exc_info=True)
            
    records.sort(key=lambda x: x['date'])
    return records
