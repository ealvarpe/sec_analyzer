import logging
import uuid
import json
import os
import re
from datetime import datetime, timedelta
import pandas as pd
from ..services.html_parser import SECHTMLParser
from ..services.regex_patterns import (
    PATTERNS, normalize_amount, parse_date, extract_first_match,
    clean_number, extract_dollar_amount
)

logger = logging.getLogger(__name__)


# ── Baby-shelf threshold ($75 million) ───────────────────────────────────────
BABY_SHELF_THRESHOLD = 75_000_000


def get_shares_outstanding_at_date(ticker: str, date_str: str, data_root: str) -> int:
    """Load shares outstanding from shares_outstanding parquet file at the closest date."""
    try:
        parquet_path = os.path.join(data_root, 'filings_shares_outstanding', f'{ticker}.parquet')
        if os.path.exists(parquet_path):
            df = pd.read_parquet(parquet_path)
            df_filtered = df[df['date'] <= date_str]
            if not df_filtered.empty:
                row = df_filtered.sort_values('date', ascending=False).iloc[0]
                shares = row['shares_outstanding']
                if shares and not pd.isna(shares):
                    return int(shares * 1e6)
    except Exception as e:
        logger.warning(f"Error loading shares outstanding for {ticker}: {e}")
    return 0


def extract_baby_shelf_metrics(text: str) -> dict:
    """Extract public float, highest 60-day close, and placement agent from supplement text.
    
    This is the primary dynamic extraction method for baby-shelf data.
    It searches for I.B.6 cover page disclosures and related data points.
    """
    if not text:
        return {}
    metrics = {}
    
    # 1. Float – multiple pattern attempts
    # Pattern: "our public float ... was approximately $X"
    float_match = re.search(
        r'public\s+float\s*(?:was|is)\s*(?:approximately\s+)?\$\s*([\d,]+)',
        text, re.I
    )
    if not float_match:
        # Pattern: "aggregate market value of ... held by non-affiliates ... was $X"
        float_match = re.search(
            r'(?:aggregate\s+)?market\s+value\s+of\s+[\w\s]*?held\s+by\s+non[- ]?affiliates'
            r'\s+[\w\s]*?\s*(?:is|was)\s*(?:approximately\s+)?\$\s*([\d,]+)',
            text, re.I
        )
    if not float_match:
        # Pattern: "non-affiliates ... $X" in I.B.6 section
        float_match = re.search(
            r'(?:I\.B\.6|instruction\s+I\.B\.6)[\s\S]{0,500}?\$\s*([\d,]+)',
            text, re.I
        )
    if float_match:
        try:
            metrics['float_'] = int(float_match.group(1).replace(',', ''))
            metrics['ib6_float_value'] = metrics['float_']
        except (ValueError, AttributeError):
            pass
            
    # 2. Highest 60-day close – multiple pattern attempts
    close_match = re.search(
        r'highest\s+closing\s+(?:sale\s+)?price\s+[\w\s]*?'
        r'\s+within\s+the\s+(?:prior\s+)?60\s+(?:trading\s+|calendar\s+)?days'
        r'\s*(?:was|is)?\s*\$\s*([\d\.]+)',
        text, re.I
    )
    if not close_match:
        close_match = re.search(
            r'price\s+of\s+\$([\d\.]+)[,\s\w]*?highest\s+closing\s+sale\s+price'
            r'[,\s\w]*?within\s+the\s+(?:prior\s+)?60\s+(?:trading\s+|calendar\s+)?days',
            text, re.I
        )
    if not close_match:
        # "highest ... closing ... price ... $X.XX ... 60 days" in any order within a paragraph
        close_match = re.search(
            r'highest[\s\S]{0,100}?closing[\s\S]{0,100}?price[\s\S]{0,50}?\$\s*([\d\.]+)',
            text, re.I
        )
    if close_match:
        try:
            metrics['highest_60_day_close'] = float(close_match.group(1))
        except (ValueError, AttributeError):
            pass
            
    # 3. Last banker – scan for known investment banks
    banker_match = re.search(
        r'(ThinkEquity|Maxim\s+Group|Aegis|HC\s+Wainwright|H\.?\s*C\.?\s+Wainwright'
        r'|Roth\s+(?:Capital|MKM)|Craig[- ]Hallum|Benchmark|Ladenburg\s+Thalmann'
        r'|Craft\s+Capital|Oppenheimer|Canaccord|B\.?\s*Riley|Lake\s+Street'
        r'|Chardan|EF\s+Hutton|Dawson\s+James)',
        text, re.I
    )
    if banker_match:
        metrics['last_banker'] = banker_match.group(1).strip()
        
    return metrics


def _extract_capacity_from_text(cover_text: str, full_text: str = None) -> int:
    """Dynamically extract shelf capacity from cover page text."""
    capacity = extract_dollar_amount(cover_text, 'shelf_amount')
    if not capacity:
        capacity = extract_dollar_amount(cover_text, 'aggregate_amount')
    if not capacity:
        capacity = extract_dollar_amount(cover_text, 'up_to_amount')
    if not capacity:
        capacity = extract_dollar_amount(cover_text, 'dollar_amount')
    if not capacity and full_text:
        # Broader search in the full text for "maximum aggregate offering price"
        m = re.search(
            r'(?:maximum|proposed)\s+(?:aggregate\s+)?offering\s+(?:price|amount)'
            r'\s+(?:of\s+)?(?:up\s+to\s+)?\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?',
            full_text[:15000], re.I
        )
        if m:
            try:
                capacity = normalize_amount(m.group(1), m.group(2) or "")
            except (ValueError, AttributeError):
                pass
    return int(capacity) if capacity else 0


def _extract_securities(cover_text: str) -> str:
    """Dynamically extract the types of securities registered from cover text."""
    securities = []
    cover_lower = cover_text.lower()
    if 'common stock' in cover_lower or 'common shares' in cover_lower:
        securities.append('Common Stock')
    if 'preferred stock' in cover_lower or 'preferred shares' in cover_lower:
        securities.append('Preferred Stock')
    if 'warrant' in cover_lower:
        securities.append('Warrants')
    if 'debt' in cover_lower or 'notes' in cover_lower or 'bonds' in cover_lower:
        securities.append('Debt')
    if 'unit' in cover_lower:
        securities.append('Units')
    if 'right' in cover_lower:
        securities.append('Rights')
    return ", ".join(securities) if securities else "Common Stock, Warrants, Units"


def _compute_baby_shelf_price(float_val: float, highest_close: float) -> float | None:
    """Compute the stock price needed to exceed baby shelf threshold.
    
    Under I.B.6, the company can only sell up to 1/3 of its public float
    if float < $75M. Price to exceed = $75M * highest_close / float.
    """
    if float_val and float_val > 0 and highest_close and highest_close > 0:
        return round(BABY_SHELF_THRESHOLD * highest_close / float_val, 2)
    return None


def _scan_baby_shelf_metrics_from_filings(
    downloader, 
    atm_updates: list, 
    offering_updates: list,
    event_date: str
) -> dict:
    """Scan related ATM/offering filings for baby shelf metrics up to a given date."""
    metrics = {}
    
    all_refs = list(atm_updates) + list(offering_updates)
    # Sort by date so we pick up the latest available metrics
    try:
        all_refs_sorted = sorted(all_refs, key=lambda x: str(x.get('date', '') if isinstance(x, dict) else x['date']))
    except Exception:
        all_refs_sorted = all_refs
    
    for ref_upd in all_refs_sorted:
        ref_date = str(ref_upd['date']) if hasattr(ref_upd, '__getitem__') else str(ref_upd.get('date', ''))
        if ref_date <= event_date:
            try:
                acc_no = ref_upd.get('accession_number', '') if isinstance(ref_upd, dict) else ref_upd['accession_number']
                if not acc_no:
                    continue
                # Try to get the filing text from downloader's cache
                cursor = downloader.session.execute(
                    "SELECT text_content FROM filing_cache WHERE accession_number = :acc",
                    {"acc": acc_no}
                )
                r_row = cursor.fetchone()
                if r_row and r_row[0]:
                    parsed_metrics = extract_baby_shelf_metrics(r_row[0])
                    if parsed_metrics:
                        metrics.update(parsed_metrics)
            except Exception as e:
                logger.debug(f"Failed to scan baby shelf metrics from {acc_no}: {e}")
    
    return metrics


def extract_shelf(ticker: str, filings: list, downloader) -> list[dict]:
    """Extract shelf registrations and their capacity depletion history dynamically.
    
    All extraction is document-agnostic — no ticker-specific overrides.
    Baby shelf metrics are computed dynamically from I.B.6 cover page disclosures.
    """
    
    # 1. Identify database/data root path from downloader session bind
    data_root = "D:\\tmp"
    try:
        bind = downloader.session.get_bind()
        db_url = str(bind.url)
        if db_url.startswith("sqlite:///"):
            db_path = db_url[len("sqlite:///"):]
            data_root = os.path.dirname(db_path)
    except Exception as e:
        logger.warning(f"Could not infer data_root from session: {e}")
        
    # 2. Separate S-3s and EFFECTs
    s3_filings = [f for f in filings if f.form.upper().strip() in ('S-3', 'S-3/A', 'S-3ASR', 'POS AM')]
    effect_filings = [f for f in filings if f.form.upper().strip() == 'EFFECT']
    
    # Pre-parse EFFECT filings to map file number -> effect date
    file_to_effect = {}
    for ef in effect_filings:
        try:
            text = downloader.get_filing_text(ef) or ""
            file_no = extract_first_match(PATTERNS['file_number'], text)
            if file_no:
                file_to_effect[file_no] = ef.filing_date
        except Exception as e:
            logger.warning(f"Error parsing EFFECT filing {ef.accession_no}: {e}")
            
    all_shelf_records = []
    
    # 3. Process S-3 filings
    for filing in s3_filings:
        try:
            form = filing.form.upper().strip()
            is_asr = (form == 'S-3ASR')
            
            html_content = downloader.get_filing_html(filing)
            if not html_content:
                continue
                
            parser = SECHTMLParser(html_content)
            cover_text = parser.get_cover_page_text()
            full_text = parser.full_text
            
            # Extract file number
            file_no = extract_first_match(PATTERNS['file_number'], cover_text) or ""
            
            effect_date = ""
            if not file_no:
                # Proximity matching within 60 days
                matching_ef = None
                for ef in effect_filings:
                    if ef.filing_date >= filing.filing_date:
                        try:
                            s3_dt = datetime.strptime(str(filing.filing_date), '%Y-%m-%d')
                            ef_dt = datetime.strptime(str(ef.filing_date), '%Y-%m-%d')
                            if 0 <= (ef_dt - s3_dt).days <= 60:
                                if matching_ef is None or ef.filing_date < matching_ef.filing_date:
                                    matching_ef = ef
                        except:
                            pass
                if matching_ef:
                    ef_text = downloader.get_filing_text(matching_ef) or ""
                    file_no = extract_first_match(PATTERNS['file_number'], ef_text) or ""
                    effect_date = str(matching_ef.filing_date)
            else:
                if file_no in file_to_effect:
                    effect_date = str(file_to_effect[file_no])
                    
            if not file_no:
                file_no = f"Pending-{filing.filing_date}"
                
            # ── Capacity (dynamic, no overrides) ─────────────────────────
            capacity = _extract_capacity_from_text(cover_text, full_text)
                    
            # ── Expiration date (3 years after effective date) ───────────
            expiration_date = ""
            if effect_date:
                try:
                    dt = datetime.strptime(effect_date, '%Y-%m-%d')
                    exp_dt = dt + timedelta(days=3*365)
                    expiration_date = exp_dt.strftime('%Y-%m-%d')
                except:
                    pass
                    
            # ── Extract securities registered (dynamic) ──────────────────
            securities_registered = _extract_securities(cover_text)
                    
            # Link ID by file number deterministically
            key = f"{ticker}-{file_no}"
            filing_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
            
            # Determine shelf name from the effective date or filing date
            name_date_str = effect_date if effect_date else str(filing.filing_date)
            try:
                dt = datetime.strptime(name_date_str, '%Y-%m-%d')
                month_year = dt.strftime('%B %Y')
            except:
                month_year = ""
            sec_name = f"{month_year} Shelf"
                    
            # ── Find related updates (completed offerings & ATMs) ────────
            atm_updates = []
            try:
                atm_path = os.path.join(data_root, 'filings_atm', f'{ticker}.parquet')
                if os.path.exists(atm_path):
                    atm_df = pd.read_parquet(atm_path)
                    for idx, row in atm_df.iterrows():
                        row_shelf = str(row.get('shelf_registration_number', ''))
                        is_match = False
                        if file_no and (file_no in row_shelf or row_shelf in file_no):
                            is_match = True
                        elif not row_shelf and effect_date and expiration_date:
                            if effect_date <= row['date'] <= expiration_date:
                                is_match = True
                        if is_match:
                            atm_updates.append(row)
            except Exception as e:
                logger.warning(f"Error scanning ATM updates: {e}")
                
            offering_updates = []
            try:
                off_path = os.path.join(data_root, 'filings_completed_offering', f'{ticker}.parquet')
                if os.path.exists(off_path):
                    off_df = pd.read_parquet(off_path)
                    for idx, row in off_df.iterrows():
                        row_shelf = str(row.get('related_shelf_number', ''))
                        is_match = False
                        if file_no and (file_no in row_shelf or row_shelf in file_no):
                            is_match = True
                        elif not row_shelf and effect_date and expiration_date:
                            if effect_date <= row['date'] <= expiration_date:
                                if row.get('registration_type', 'S-3') == 'S-3' or 'S-3' in str(row.get('method', '')):
                                    is_match = True
                        if is_match:
                            offering_updates.append(row)
            except Exception as e:
                logger.warning(f"Error scanning completed offering updates: {e}")
                
            # ── Compile all events chronologically ───────────────────────
            shelf_events = []
            
            # 1. Filing event
            shelf_events.append({
                'date': str(filing.filing_date),
                'status': 'Pending Effect',
                'amount': 0,
                'type': 'Filing',
                'url': getattr(filing, 'filing_url', '') or '',
                'accession_number': str(filing.accession_no),
                'form': filing.form
            })
            
            # 2. Effectiveness event
            if effect_date:
                # Find the EFFECT filing accession if available
                ef_acc = str(filing.accession_no)
                ef_url = getattr(filing, 'filing_url', '') or ''
                for ef in effect_filings:
                    if str(ef.filing_date) == effect_date:
                        ef_acc = str(ef.accession_no)
                        ef_url = getattr(ef, 'filing_url', '') or ''
                        break
                shelf_events.append({
                    'date': effect_date,
                    'status': 'Registered',
                    'amount': 0,
                    'type': 'Effectiveness',
                    'url': ef_url,
                    'accession_number': ef_acc,
                    'form': 'S-3'
                })
                
            # 3. Drawdowns (ATM & Offerings)
            for upd in atm_updates:
                raised_val = upd.get('amount_raised_to_date', 0)
                if pd.isna(raised_val) or raised_val is None:
                    raised_val = 0
                shelf_events.append({
                    'date': upd['date'],
                    'status': 'Updated',
                    'amount': int(raised_val),
                    'type': 'ATM Update',
                    'url': upd.get('filing_edgar_url', '') or '',
                    'accession_number': upd.get('accession_number', '') or '',
                    'form': upd.get('filing_form', '424B5')
                })
                
            for upd in offering_updates:
                if upd.get('type') == 'ATM':
                    continue
                raised_val = upd.get('offering_amount', 0)
                if pd.isna(raised_val) or raised_val is None:
                    raised_val = 0
                shelf_events.append({
                    'date': upd['date'],
                    'status': 'Updated',
                    'amount': int(raised_val),
                    'type': 'Completed Offering',
                    'url': upd.get('filing_edgar_url', '') or '',
                    'accession_number': upd.get('accession_number', '') or '',
                    'form': upd.get('filing_form', '424B5')
                })
                
            # 4. Expiration event
            if expiration_date:
                today_str = datetime.utcnow().strftime('%Y-%m-%d')
                if today_str >= expiration_date:
                    # Find EFFECT URL as fallback
                    ef_url = getattr(filing, 'filing_url', '') or ''
                    for ef in effect_filings:
                        if str(ef.filing_date) == effect_date:
                            ef_url = getattr(ef, 'filing_url', '') or ''
                            break
                    shelf_events.append({
                        'date': expiration_date,
                        'status': 'Expired',
                        'amount': capacity,
                        'type': 'Expiration',
                        'url': ef_url,
                        'accession_number': str(filing.accession_no),
                        'form': 'S-3'
                    })
                    
            # Deduplicate events on the same date/status
            seen_events = {}
            for ev in shelf_events:
                key = (ev['date'], ev['status'])
                if key not in seen_events:
                    seen_events[key] = ev
                else:
                    # Keep one with higher amount if different
                    if ev['amount'] > seen_events[key]['amount']:
                        seen_events[key] = ev
            shelf_events = list(seen_events.values())
            
            # Sort chronologically
            shelf_events.sort(key=lambda x: (
                x['date'],
                x['status'] != 'Pending Effect',
                x['status'] != 'Registered',
                x['status'] == 'Expired'
            ))
            
            # ── Compute running capacity depletion ───────────────────────
            running_raised = 0
            for idx, ev in enumerate(shelf_events):
                if ev['type'] == 'Completed Offering':
                    running_raised += ev['amount']
                    ev['total_raised'] = running_raised
                elif ev['type'] == 'ATM Update':
                    # ATM updates are cumulative
                    running_raised = ev['amount']
                    ev['total_raised'] = running_raised
                elif ev['type'] == 'Expiration':
                    ev['total_raised'] = capacity
                else:
                    ev['total_raised'] = 0
                    
                # For Pending Effect / Registered initial states, raised is 0
                if ev['status'] in ('Pending Effect', 'Registered'):
                    ev['total_raised'] = 0
                    
            # ── Baby shelf metrics: extracted dynamically ────────────────
            # Check the S-3 cover text itself for baby shelf disclosures
            cover_lower = cover_text.lower()
            has_baby_shelf_indicator = (
                'i.b.6' in cover_lower 
                or 'baby shelf' in cover_lower
                or 'instruction i.b' in cover_lower
                or ('public float' in cover_lower and 'non-affiliate' in cover_lower)
            )
            
            # Extract metrics from the S-3 cover text
            base_metrics = extract_baby_shelf_metrics(full_text)
            
            # Extract the banker from the cover page
            base_banker = base_metrics.get('last_banker', '')
            if not base_banker:
                # Try to find banker in cover text separately
                banker_match = re.search(
                    r'(ThinkEquity|Maxim\s+Group|Aegis|HC\s+Wainwright|H\.?\s*C\.?\s+Wainwright'
                    r'|Roth\s+(?:Capital|MKM)|Craig[- ]Hallum|Benchmark|Ladenburg\s+Thalmann'
                    r'|Craft\s+Capital|Oppenheimer|Canaccord|B\.?\s*Riley|Lake\s+Street'
                    r'|Chardan|EF\s+Hutton|Dawson\s+James)',
                    cover_text, re.I
                )
                if banker_match:
                    base_banker = banker_match.group(1).strip()
                    
            # ── Populate fields for each history checkpoint ──────────────
            for ev in shelf_events:
                raised = ev['total_raised']
                raisable = max(0, capacity - raised)
                
                # Default metrics
                outstanding = get_shares_outstanding_at_date(ticker, ev['date'], data_root)
                float_val = base_metrics.get('float_', 0)
                highest_close = base_metrics.get('highest_60_day_close', None)
                ib6_float = base_metrics.get('ib6_float_value', 0)
                banker = base_banker
                baby_shelf = has_baby_shelf_indicator
                
                # Try to get updated metrics from related filings at this event date
                ref_metrics = _scan_baby_shelf_metrics_from_filings(
                    downloader, atm_updates, offering_updates, ev['date']
                )
                if ref_metrics:
                    if 'float_' in ref_metrics:
                        float_val = ref_metrics['float_']
                        ib6_float = float_val
                    if 'highest_60_day_close' in ref_metrics:
                        highest_close = ref_metrics['highest_60_day_close']
                    if 'last_banker' in ref_metrics:
                        banker = ref_metrics['last_banker']
                    baby_shelf = True
                
                # Compute the price-to-exceed-baby-shelf dynamically
                price_to_exceed = _compute_baby_shelf_price(float_val, highest_close) if baby_shelf else None
                    
                # Construct record dict
                all_shelf_records.append({
                    'ticker': ticker,
                    'source_downloaded_at': datetime.utcnow().isoformat() + '+00:00',
                    'date': ev['date'],
                    'filing_id': filing_id,
                    'offering_type': 'Shelf',
                    'registration_status': ev['status'],
                    'filing_edgar_url': ev['url'],
                    'raw_data_json': json.dumps({
                        'total_shelf_capacity': capacity,
                        'shelf_registration_number': file_no,
                        'effect_date': effect_date,
                        'expiration_date': expiration_date,
                        'is_asr': is_asr,
                    }),
                    'security_name': sec_name,
                    'current_raisable_amount': raisable,
                    'total_shelf_capacity': capacity,
                    'baby_shelf_restriction': baby_shelf,
                    'total_amount_raised': raised,
                    'total_amount_raised_last_12_mo_under_ib6': raised,
                    'outstanding_shares': outstanding,
                    'float_': float_val,
                    'highest_60_day_close': highest_close,
                    'price_to_exceed_baby_shelf': price_to_exceed,
                    'ib6_float_value': ib6_float,
                    'last_banker': banker,
                    'effect_date': effect_date,
                    'expiration_date': expiration_date,
                    'last_update_date': ev['date'],
                    'shelf_registration_number': file_no if not file_no.startswith("Pending") else "",
                    'filing_form': ev['form'],
                    'securities_registered': securities_registered,
                    'initial_filing_date': str(filing.filing_date),
                    'amendment_count': 0,
                    'accession_number': ev['accession_number'],
                    'is_asr': is_asr,
                })
        except Exception as e:
            logger.error(f"Error extracting S-3 shelf registration {filing.accession_no} for ticker {ticker}: {e}", exc_info=True)
            
    all_shelf_records.sort(key=lambda x: (x['date'], x['registration_status'] == 'Expired'))
    return all_shelf_records
