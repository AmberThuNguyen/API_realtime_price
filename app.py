from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from vnstock import Vnstock
import pandas as pd

app = FastAPI()
v = Vnstock()   # init 1 lần duy nhất (tối ưu tốc độ)


def safe_float(v):
    try:
        return float(v)
    except:
        return None


def extract_from_df(df):
    if df is None or isinstance(df, dict) or df.empty:
        return None, None, None, None

    row = df.tail(1).iloc[0]

    price = safe_float(row.get("lastPrice") or row.get("close") or row.get("price"))
    open_p = safe_float(row.get("openPrice") or row.get("open"))
    close_p = safe_float(row.get("priorClosePrice") or row.get("close"))
    time_ = row.get("rtd11Time") or row.get("time") or row.get("datetime")

    return price, open_p, close_p, time_


def get_price_v4(symbol):
    st = v.stock(symbol)

    # 1) realtime (mới nhất – nhanh nhất)
    try:
        df_rt = st.quote.realtime(symbol=symbol, show_log=False)
        price, open_p, close_p, time_ = extract_from_df(df_rt)
        if price is not None:
            return {
                "provider": "realtime",
                "price": price,
                "open": open_p,
                "close": close_p,
                "time": time_
            }
    except:
        pass

    # 2) intraday fallback
    try:
        df_intra = st.quote.intraday(symbol=symbol, page_size=5000, show_log=False)
        price, open_p, close_p, time_ = extract_from_df(df_intra)
        if price is not None:
            return {
                "provider": "intraday",
                "price": price,
                "open": open_p,
                "close": close_p,
                "time": time_
            }
    except:
        pass

    # 3) OHLC fallback
    try:
        df_ohlc = st.quote.ohlc(symbol=symbol, interval="1D")
        price, open_p, close_p, time_ = extract_from_df(df_ohlc)
        if price is not None:
            return {
                "provider": "ohlc",
                "price": price,
                "open": open_p,
                "close": close_p,
                "time": time_
            }
    except:
        pass

    return {
        "provider": "none",
        "price": None,
        "open": None,
        "close": None,
        "time": None,
        "error": "No data"
    }


@app.get("/bulk")
async def bulk(symbols: str = Query(...)):
    """
    /bulk?symbols=VNM,SSI,VCB
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    results = {}
    for sym in symbol_list:
        try:
            results[sym] = get_price_v4(sym)
        except Exception as e:
            results[sym] = {
                "provider": "error",
                "price": None,
                "open": None,
                "close": None,
                "time": None,
                "error": str(e)
            }

    return JSONResponse({
        "status": "ok",
        "count": len(results),
        "results": results
    })
