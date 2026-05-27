import logging
import httpx
import pandas as pd
import re
from pathlib import Path
from fastapi import HTTPException
from backend.services.parquet_io import read_parquet_for_ticker
from tradingview_scraper.symbols.stream.streamer import Streamer

logger = logging.getLogger("recommendation_service")

class CustomStreamer(Streamer):
    """Subclass of Streamer that overrides _export to prevent writing JSON/CSV files to disk."""
    def _export(self, json_data, symbol, data_category):
        pass  # Do not write files

def get_pricing_levels(ticker: str, current_price: float | None, data_root: str) -> list[dict]:
    """
    Extract key offering prices and exercise prices from database/parquet records
    for the stock ticker, filtered to levels between 0.0 and 200% of current_price.
    """
    ticker = ticker.upper().strip()
    levels = []
    
    ref_price = current_price if current_price is not None else 2.0
    
    def _to_float(val) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)
            if pd.isna(f):
                return None
            return f
        except Exception:
            return None

    # 1. Warrants
    try:
        df = read_parquet_for_ticker("warrant", ticker, data_root)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                p = _to_float(row.get("exercise_price"))
                if p is not None:
                    levels.append({
                        "price": p,
                        "label": f"Warrant Ex (${p:.2f})",
                        "category": "warrant",
                        "date": str(row.get("date", ""))
                    })
    except Exception as e:
        logger.error(f"Error reading warrant levels: {e}")
        
    # 2. Convertible Notes
    try:
        df = read_parquet_for_ticker("convertible_note", ticker, data_root)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                p = _to_float(row.get("conversion_price"))
                if p is not None:
                    levels.append({
                        "price": p,
                        "label": f"Conv Note (${p:.2f})",
                        "category": "convertible_note",
                        "date": str(row.get("date", ""))
                    })
    except Exception as e:
        logger.error(f"Error reading convertible note levels: {e}")

    # 3. Convertible Preferred
    try:
        df = read_parquet_for_ticker("convertible_preferred", ticker, data_root)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                p = _to_float(row.get("conversion_price"))
                if p is not None:
                    levels.append({
                        "price": p,
                        "label": f"Conv Pref (${p:.2f})",
                        "category": "convertible_preferred",
                        "date": str(row.get("date", ""))
                    })
    except Exception as e:
        logger.error(f"Error reading convertible preferred levels: {e}")

    # 4. Equity Lines
    try:
        df = read_parquet_for_ticker("equity_line", ticker, data_root)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                p = _to_float(row.get("minimum_price_threshold"))
                if p is not None:
                    levels.append({
                        "price": p,
                        "label": f"ELOC Floor (${p:.2f})",
                        "category": "equity_line",
                        "date": str(row.get("date", ""))
                    })
    except Exception as e:
        logger.error(f"Error reading equity line levels: {e}")

    # 5. S-1 Offerings
    try:
        df = read_parquet_for_ticker("s1_offering", ticker, data_root)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                low = _to_float(row.get("price_range_low"))
                high = _to_float(row.get("price_range_high"))
                if low is not None:
                    levels.append({
                        "price": low,
                        "label": f"S-1 Range Low (${low:.2f})",
                        "category": "s1_offering",
                        "date": str(row.get("date", ""))
                    })
                if high is not None:
                    levels.append({
                        "price": high,
                        "label": f"S-1 Range High (${high:.2f})",
                        "category": "s1_offering",
                        "date": str(row.get("date", ""))
                    })
                ep = row.get("exercise_price")
                if ep:
                    try:
                        m = re.search(r"\d+\.?\d*", str(ep))
                        if m:
                            val = float(m.group(0))
                            levels.append({
                                "price": val,
                                "label": f"S-1 Warrant Ex (${val:.2f})",
                                "category": "s1_offering",
                                "date": str(row.get("date", ""))
                            })
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"Error reading S1 levels: {e}")

    # 6. Completed Offerings
    try:
        df = read_parquet_for_ticker("completed_offering", ticker, data_root)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                p = _to_float(row.get("price"))
                if p is not None:
                    levels.append({
                        "price": p,
                        "label": f"Completed Offering (${p:.2f})",
                        "category": "completed_offering",
                        "date": str(row.get("date", ""))
                    })
    except Exception as e:
        logger.error(f"Error reading completed offering levels: {e}")

    # Deduplicate
    deduped = {}
    for lvl in levels:
        key = (round(lvl["price"], 4), lvl["category"])
        if key not in deduped:
            deduped[key] = lvl
        else:
            if lvl["date"] > deduped[key]["date"]:
                deduped[key] = lvl
                
    filtered_levels = []
    min_p = ref_price * 0.0
    max_p = ref_price * 2.0
    
    for lvl in deduped.values():
        if min_p <= lvl["price"] <= max_p:
            filtered_levels.append(lvl)
            
    filtered_levels.sort(key=lambda x: x["price"])
    return filtered_levels

async def generate_recommendation(
    ticker: str,
    provider: str,
    model: str,
    api_key: str,
    current_price: float | None,
    recent_price_action: str | None,
    custom_instructions: str | None,
    data_root: str
) -> str:
    """
    Fetch all offering and filings data for the ticker, format it into CSV segments,
    construct a detailed dilution-focused prompt, and query the selected LLM provider.
    """
    ticker = ticker.upper().strip()
    provider = provider.lower().strip()
    model = model.strip()
    api_key = api_key.strip()
    
    # 1. Fetch data for all offering categories
    categories = [
        "atm", "shelf", "warrant", "convertible_note", 
        "convertible_preferred", "equity_line", "s1_offering", 
        "completed_offering", "cash_position", "shares_outstanding"
    ]
    
    csv_blocks = []
    for cat in categories:
        try:
            df = read_parquet_for_ticker(cat, ticker, data_root)
            if df is not None and not df.empty:
                # Remove raw_data_json if it exists to preserve context space
                if "raw_data_json" in df.columns:
                    df = df.drop(columns=["raw_data_json"])
                
                # Format to CSV
                csv_str = df.to_csv(index=False)
                cat_title = cat.upper().replace("_", " ")
                csv_blocks.append(f"### {cat_title} DATA\n```csv\n{csv_str}```")
        except Exception as e:
            logger.error(f"Error loading parquet data for {ticker} category {cat}: {e}")
            
    csv_data_text = "\n\n".join(csv_blocks) if csv_blocks else "No historical dilution or offering filings data found."

    # 2. Build the prompt
    price_context = ""
    if current_price is not None:
        price_context += f"- Current Stock Price: ${current_price:.4f}\n"
    if recent_price_action:
        price_context += f"- Recent Price Action/Context: {recent_price_action}\n"
        
    custom_text = ""
    if custom_instructions:
        custom_text = f"\n### Custom Analysis Instructions:\n{custom_instructions}\n"

    prompt = f"""You are a specialized financial analyst and Stock Dilution Analyzer.
Your task is to analyze the historical and active offerings/filings data for stock ticker **{ticker}** and provide a detailed prediction and explanation of potential dilution risk, key pricing levels, and future price direction.

## Context & Stock Price Information:
{price_context or "- No current price information provided. You should search or infer it if possible based on your training."}
{custom_text}

## SEC Filings & Offerings Data (CSV formats):
{csv_data_text}

## Analysis Framework & Instructions:
Perform a thorough, step-by-step analysis resolving the following:

1. **Cash Runway & Operational Burn Rate**:
   - Check the cash_position and shares_outstanding tables.
   - Estimate the quarterly operational burn rate, remaining cash, and estimated months of runway before the company runs out of cash.
   
2. **At-The-Market (ATM) Offering Status**:
   - Check the active/remaining ATM capacity, commissions, and placement agents.
   - Assess if the ATM is restricted by baby shelf rules (Instruction I.B.6 of Form S-3: cannot sell more than 1/3 of public float in 12 months if public float is under $75 million).

3. **Shelf Registrations & S-1 Status**:
   - Identify active or pending Shelf (S-3, F-3) and S-1/F-1 registration statements.
   - Discuss upcoming deal sizes, underwriter agents, and any lock-up periods.

4. **Convertible Securities & Debt (Notes and Preferred)**:
   - Identify active convertible notes or convertible preferred shares, total principal/dollar amounts, interest/dividend rates, maturity dates, and known holders.
   - Analyze the **conversion price** and any **price protection clauses** (e.g., floor prices, downward price reset covenants, or toxic terms).

5. **Warrants Outstanding**:
   - Look at the exercise prices, issuance/expiration dates, and anti-dilution structures for outstanding warrants.

6. **Equity Lines of Credit (ELOC)**:
   - Identify active ELOCs, counterparties (e.g., Tumim Stone Green, Lincoln Park, Yorkville), capacity, pricing mechanisms (e.g., 90-95% of lowest daily VWAP), and minimum price thresholds.

7. **Dilution Velocity & Price Trajectory Prediction**:
   - Based on the current price and recent price action, predict how the company and security holders are likely to act.
   - If the price is squeezed or elevated, will holders convert notes/exercise warrants? Will the company aggressively sell ATM/ELOC shares?
   - Identify key price points:
     - Where might the price "stick" or find a floor/ceiling based on warrant exercise prices, note conversion prices, or ELOC floors?
     - What is the overall dilution risk (High, Medium, Low) in the near term (next 3-6 months)?
   - Explain the reasoning behind your price prediction.

Provide your final analysis in a clean, professional, markdown format. Do not use generic placeholders.

## Mandatory Chart Targets Format:
At the very end of your response, you MUST append a JSON code block containing the resistance/support/squeeze target levels that you have identified. The system will parse this block and display these levels directly on the price chart.
Do not put any text after this block.

Format example:
```json
{{
  "levels": [
    {{"price": 2.50, "type": "resistance", "label": "Seller Resistance"}},
    {{"price": 1.95, "type": "support", "label": "Major Support"}},
    {{"price": 4.20, "type": "target", "label": "Squeeze Target"}}
  ]
}}
```
"""

    # 3. Call LLM
    headers = {}
    timeout = httpx.Timeout(120.0, connect=15.0)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        if provider == "gemini":
            # Gemini beta API
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
            try:
                response = await client.post(url, json=payload)
                if response.status_code != 200:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message", response.text)
                    except Exception:
                        err_msg = response.text
                    raise HTTPException(
                        status_code=response.status_code, 
                        detail=f"Gemini API returned error (HTTP {response.status_code}): {err_msg}"
                    )
                resp_data = response.json()
                try:
                    return resp_data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    raise HTTPException(
                        status_code=500,
                        detail=f"Unexpected Gemini API response structure: {resp_data}"
                    )
            except HTTPException as exc:
                raise exc
            except httpx.RequestError as exc:
                logger.error(f"HTTP request to Gemini failed: {exc}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"HTTP request to Gemini failed ({type(exc).__name__}): {repr(exc)}")
            except Exception as exc:
                logger.error(f"Gemini call failed with unexpected error: {exc}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Gemini call failed with unexpected error ({type(exc).__name__}): {repr(exc)}")

        elif provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": model,
                "max_tokens": 4000,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code != 200:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message", response.text)
                    except Exception:
                        err_msg = response.text
                    raise HTTPException(
                        status_code=response.status_code, 
                        detail=f"Anthropic API returned error (HTTP {response.status_code}): {err_msg}"
                    )
                resp_data = response.json()
                try:
                    return resp_data["content"][0]["text"]
                except (KeyError, IndexError):
                    raise HTTPException(
                        status_code=500,
                        detail=f"Unexpected Anthropic API response structure: {resp_data}"
                    )
            except HTTPException as exc:
                raise exc
            except httpx.RequestError as exc:
                logger.error(f"HTTP request to Anthropic failed: {exc}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"HTTP request to Anthropic failed ({type(exc).__name__}): {repr(exc)}")
            except Exception as exc:
                logger.error(f"Anthropic call failed with unexpected error: {exc}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Anthropic call failed with unexpected error ({type(exc).__name__}): {repr(exc)}")

        elif provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code != 200:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message", response.text)
                    except Exception:
                        err_msg = response.text
                    raise HTTPException(
                        status_code=response.status_code, 
                        detail=f"OpenAI API returned error (HTTP {response.status_code}): {err_msg}"
                    )
                resp_data = response.json()
                try:
                    return resp_data["choices"][0]["message"]["content"]
                except (KeyError, IndexError):
                    raise HTTPException(
                        status_code=500,
                        detail=f"Unexpected OpenAI API response structure: {resp_data}"
                    )
            except HTTPException as exc:
                raise exc
            except httpx.RequestError as exc:
                logger.error(f"HTTP request to OpenAI failed: {exc}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"HTTP request to OpenAI failed ({type(exc).__name__}): {repr(exc)}")
            except Exception as exc:
                logger.error(f"OpenAI call failed with unexpected error: {exc}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"OpenAI call failed with unexpected error ({type(exc).__name__}): {repr(exc)}")
        
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported LLM provider: {provider}")
