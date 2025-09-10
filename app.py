# app.py (thay / ghi đè file hiện tại)
import logging
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def try_legacy(sym):
    """Try old-style functions (stock_intraday_data, stock_historical_data)."""
    try:
        import vnstock as vn  # try import package
        # prefer direct function if exposed
        if hasattr(vn, "stock_intraday_data"):
            df = vn.stock_intraday_data(symbol=sym, page_num=0, page_size=5000)
            if df is None or getattr(df, "empty", True):
                if hasattr(vn, "stock_historical_data"):
                    df = vn.stock_historical_data(symbol=sym, start_date="2020-01-01", end_date="2030-12-31")
            if df is None or getattr(df, "empty", True):
                return {"symbol": sym, "price": None, "time": None}
            last = df.tail(1).iloc[0]
            try:
                d = last.to_dict()
            except Exception:
                d = dict(last)
            time = d.get("time") or d.get("datetime") or d.get("Date") or None
            price = d.get("close") or d.get("Close") or None
            price = float(price) if price is not None else None
            return {"symbol": sym, "time": str(time), "price": price}
    except Exception as e:
        logging.exception("legacy import/usage failed")
        return {"_legacy_error": str(e)}
    return None  # legacy API not available

def try_v3(sym):
    """Try vnstock v3 API (Vnstock / Quote / stock.quote.intraday)."""
    try:
        from vnstock import Vnstock
    except Exception as e:
        logging.info("vnstock Vnstock import failed: %s", e)
        return {"_v3_error": str(e)} if 'vnstock' in str(e).lower() else None

    try:
        v = Vnstock()
        # try building stock object; try several source fallbacks
        stock = None
        for source in ("TCBS", "VCI", None):
            try:
                if source:
                    stock = v.stock(symbol=sym, source=source)
                else:
                    stock = v.stock(symbol=sym)
                if stock:
                    break
            except Exception:
                stock = None
        if stock is None:
            return {"symbol": sym, "price": None, "time": None}

        # try intraday first
        df = None
        try:
            if hasattr(stock, "quote") and hasattr(stock.quote, "intraday"):
                df = stock.quote.intraday(symbol=sym, page_size=5000, show_log=False)
        except Exception as e:
            logging.info("intraday call raised: %s", e)
            df = None

        # fallback to history
        if df is None or getattr(df, "empty", True):
            try:
                if hasattr(stock, "quote") and hasattr(stock.quote, "history"):
                    df = stock.quote.history(start="2020-01-01", end="2030-12-31", interval="1D")
            except Exception as e:
                logging.info("history call raised: %s", e)
                df = None

        if df is None or getattr(df, "empty", True):
            return {"symbol": sym, "price": None, "time": None}

        last = df.tail(1).iloc[0]
        try:
            lr = last.to_dict()
        except Exception:
            lr = dict(last)
        time = lr.get("time") or lr.get("datetime") or lr.get("Date") or None
        price = lr.get("close") or lr.get("Close") or lr.get("ClosePrice") or lr.get("c") or None
        price = float(price) if price is not None else None
        return {"symbol": sym, "time": str(time), "price": price}
    except Exception as e:
        logging.exception("vnstock v3 usage failed")
        return {"_v3_exception": str(e)}

@app.route("/price")
def price():
    symbol = request.args.get("symbol", "VNM")
    # 1) try legacy functions
    legacy = try_legacy(symbol)
    if legacy and "_legacy_error" not in legacy and legacy.get("price") is not None:
        return jsonify(legacy)

    # 2) try v3 style
    v3 = try_v3(symbol)
    # if v3 returned dict with price or explicit None (success/fallback) -> return it
    if isinstance(v3, dict) and ("price" in v3 or "_v3_exception" in v3 or "_v3_error" in v3):
        return jsonify(v3)

    # 3) final fallback
    return jsonify({"symbol": symbol, "price": None, "time": None})

if __name__ == "__main__":
    # use port 5000 (Render maps container port)
    app.run(host="0.0.0.0", port=5000)
