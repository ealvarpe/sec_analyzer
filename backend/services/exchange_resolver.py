import httpx
import logging

logger = logging.getLogger("exchange_resolver")

# Memory cache for resolved tickers
_exchange_cache = {}

def resolve_ticker_exchange(ticker: str) -> str:
    """
    Resolve a stock ticker to its TradingView exchange (NASDAQ, NYSE, AMEX).
    """
    ticker = ticker.upper().strip()
    if ticker in _exchange_cache:
        return _exchange_cache[ticker]
        
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            quotes = data.get("quotes", [])
            for q in quotes:
                if q.get("symbol") == ticker:
                    exchange = q.get("exchange")
                    exch_disp = q.get("exchDisp")
                    
                    # Map to TradingView exchanges
                    tv_exchange = "NASDAQ"
                    if exchange in ["NYQ", "NYS", "ASE"] or "nyse" in str(exch_disp).lower():
                        tv_exchange = "NYSE"
                    elif "amex" in str(exch_disp).lower() or exchange in ["ASE"]:
                        tv_exchange = "AMEX"
                    elif "nasdaq" in str(exch_disp).lower():
                        tv_exchange = "NASDAQ"
                        
                    _exchange_cache[ticker] = tv_exchange
                    logger.info(f"Resolved ticker {ticker} to exchange {tv_exchange}")
                    return tv_exchange
                    
            # Fallback to first quote if no exact match
            if quotes:
                q = quotes[0]
                exchange = q.get("exchange")
                exch_disp = q.get("exchDisp")
                tv_exchange = "NASDAQ"
                if exchange in ["NYQ", "NYS", "ASE"] or "nyse" in str(exch_disp).lower():
                    tv_exchange = "NYSE"
                elif "amex" in str(exch_disp).lower() or exchange in ["ASE"]:
                    tv_exchange = "AMEX"
                
                _exchange_cache[ticker] = tv_exchange
                logger.info(f"Resolved ticker {ticker} to exchange {tv_exchange} via fallback")
                return tv_exchange
    except Exception as e:
        logger.error(f"Error resolving exchange for ticker {ticker}: {e}", exc_info=True)
        
    # Default fallback
    return "NASDAQ"
