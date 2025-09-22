# app.py
import logging
import traceback
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vnstock-api")

app = Flask(__name__)

def normalize_symbol(sym: str) -> str:
    if not sym:
        return ""
    return sym.strip().upper()

def to_float_safe(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        try:
            s = str(v).replace(",", "").strip()
            return float(s)
        except Exception:
            return None

def extract_from_row_dict(row_dict):
    """
    Given a dict (row), try to extract price(last), time, open, close.
    Keys are checked case-insensitively and with many possible names.
    Returns tuple: (price, time, open_price, close_price)
    """
    if not row_dict:
        return None, None, None, None

    lower = {k.lower(): v for k, v in row_dict.items()}

    # price candidates (prefer last/match/close)
    price_keys = [
        "lastprice", "pricelast", "matchprice", "match_price",
        "last", "close", "price", "closeprice", "close_price",
        "c", "match"
    ]
    price = None
    for k in price_keys:
        if k in lower and lower[k] is not None:
            price = to_float_safe(lower[k])
            if price is not None:
                break

    # time candidates
    time_keys = [
        "time", "datetime", "date", "updatedat", "updated_at", "matchtime", "timestamp"
    ]
    tval = None
    for k in time_keys:
        if k in lower and lower[k] is not None:
            tval = lower[k]
            break
    tstr = str(tval) if tval is not None else None

    # open candidates
    open_keys = ["open", "openprice", "priceopen", "o", "open_price"]
    open_price = None
    for k in open_keys:
        if k in lower and lower[k] is not None:
            open_price = to_float_safe(lower[k])
            if open_price is not None:
                break

    # close candidates (some sources keep close separate)
    close_keys = ["close", "closeprice", "pricelast", "lastprice", "c", "matchprice", "priceclose", "close_price"]
    close_price = None
    for k in close_keys:
        if k in lower and lower[k] is not None:
            close_price = to_float_safe(lower[k])
            if close_price is not None:
                break

    return price, tstr, open_price, close_price

def get_price_from_df(df):
    """
    Given a DataFrame-like object (pandas), return price, time, open, close, or (None,...).
    Uses last row for price/time; uses first row for open if available.
    """
    try:
        if df is None:
            return None, None, None, None

        # check empty
        try:
            if getattr(df, "empty", False):
                return None, None, None, None
        except Exception:
            pass

        # try last row
        try:
            last = df.tail(1).iloc[0]
        except Exception:
            try:
                last = df[-1]
            except Exception:
                last = None

        rd_last = None
        if last is not None:
            try:
                rd_last = last.to_dict()
            except Exception:
                try:
                    rd_last = dict(last)
                except Exception:
                    rd_last = None

        price, time_, open_p, close_p = extract_from_row_dict(rd_last) if rd_last else (None, None, None, None)

        # if open or close missing, try head/tail combos
        if (open_p is None) or (close_p is None):
            try:
                first = df.head(1).iloc[0]
                try:
                    rd_first = first.to_dict()
                except Exception:
                    rd_first = dict(first)
            except Exception:
                rd_first = None

            # if open missing, try first row open or price
            if open_p is None and rd_first:
                _, _, open_from_first, _ = extract_from_row_dict(rd_first)
                if open_from_first is not None:
                    open_p = open_from_first
                else:
                    # try price field in first row (first trade)
                    p_first, _, _, _ = extract_from_row_dict(rd_first)
                    if p_first is not None:
                        open_p = p_first

            # if close missing, try last row close or price
            if close_p is None and rd_last:
                _, _, _, close_from_last = extract_from_row_dict(rd_last)
                if close_from_last is not None:
                    close_p = close_from_last
                else:
                    # last price fallback
                    if price is not None:
                        close_p = price

        # final normalization (floats)
        price = to_float_safe(price)
        open_p = to_float_safe(open_p)
        close_p = to_float_safe(close_p)

        return price, time_, open_p, close_p
    except Exception as e:
        log.exception("get_price_from_df exception: %s", e)
        return None, None, None, None

def try_legacy(symbol):
    """Try old-style top-level vnstock functions."""
    try:
        import vnstock as vn
    except Exception as e:
        log.info("legacy import vnstock failed: %s", e)
        return None, {"error": f"legacy import failed: {e}"}

    try:
        # intraday
        if hasattr(vn, "stock_intraday_data"):
            try:
                df = vn.stock_intraday_data(symbol=symbol, page_num=0, page_size=5000)
            except TypeError:
                df = vn.stock_intraday_data(symbol, 0, 5000)
            price, time_, open_p, close_p = get_price_from_df(df)
            if price is not None or open_p is not None or close_p is not None:
                return {"provider": "vnstock-legacy-intraday", "price": price, "time": time_, "open": open_p, "close": close_p}, None

        # fallback to historical
        if hasattr(vn, "stock_historical_data"):
            try:
                df2 = vn.stock_historical_data(symbol=symbol, start_date="2020-01-01", end_date="2030-12-31", interval="1D")
            except TypeError:
                df2 = vn.stock_historical_data(symbol, "2020-01-01", "2030-12-31")
            price, time_, open_p, close_p = get_price_from_df(df2)
            if price is not None or open_p is not None or close_p is not None:
                return {"provider": "vnstock-legacy-history", "price": price, "time": time_, "open": open_p, "close": close_p}, None

        return None, {"info": "legacy present but returned no data"}
    except Exception as e:
        log.exception("legacy usage error: %s", e)
        return None, {"error": f"legacy usage exception: {e}"}

def try_v3(symbol):
    """Try vnstock v3 style (Vnstock class)."""
    try:
        from vnstock import Vnstock
    except Exception as e:
        log.info("vnstock Vnstock import failed: %s", e)
        return None, {"error": f"v3 import failed: {e}"}

    try:
        v = Vnstock()
        stock_obj = None
        try:
            stock_obj = v.stock(symbol=symbol)
        except Exception:
            try:
                stock_obj = v.stock(symbol)
            except Exception:
                stock_obj = None

        if stock_obj is None:
            for src in ("VCI", "TCBS", "SSI"):
                try:
                    stock_obj = v.stock(symbol=symbol, source=src)
                    if stock_obj:
                        log.info("Vnstock.stock using source %s", src)
                        break
                except Exception:
                    stock_obj = None

        if stock_obj is None:
            return None, {"info": "v3 stock object creation failed"}

        # intraday
        df = None
        try:
            if hasattr(stock_obj, "quote") and hasattr(stock_obj.quote, "intraday"):
                df = stock_obj.quote.intraday(symbol=symbol, page_size=5000, show_log=False)
        except Exception as e:
            log.info("v3 intraday failed: %s", e)
            df = None

        # fallback to history
        if df is None or getattr(df, "empty", True):
            try:
                if hasattr(stock_obj, "quote") and hasattr(stock_obj.quote, "history"):
                    df = stock_obj.quote.history(start="2020-01-01", end="2030-12-31", interval="1D")
            except Exception as e:
                log.info("v3 history failed: %s", e)
                df = None

        price, time_, open_p, close_p = get_price_from_df(df)
        if price is not None or open_p is not None or close_p is not None:
            return {"provider": "vnstock-v3", "price": price, "time": time_, "open": open_p, "close": close_p}, None

        return None, {"info": "v3 returned no data"}
    except Exception as e:
        log.exception("vnstock v3 usage exception: %s", e)
        return None, {"error": f"v3 exception: {e}"}

@app.route("/price")
def price():
    sym = normalize_symbol(request.args.get("symbol") or "")
    debug = request.args.get("debug", "0") in ("1", "true", "yes")
    fallback_to_close = request.args.get("fallback", "close")  # 'close' or 'none'

    if not sym:
        sym = "VNM"

    result = {"symbol": sym, "price": None, "time": None, "open": None, "close": None, "provider": None}
    details = {}

    # 1) try legacy
    try:
        r, info = try_legacy(sym)
        if r:
            result.update({"price": r.get("price"), "time": r.get("time"), "open": r.get("open"), "close": r.get("close"), "provider": r.get("provider")})
            details["legacy"] = "ok"
            if debug:
                details["legacy_detail"] = r
        else:
            details["legacy_info"] = info
    except Exception as e:
        details["legacy_exception"] = str(e) + "\n" + traceback.format_exc()

    # 2) try v3
    if result["price"] is None and result["open"] is None and result["close"] is None:
        try:
            r2, info2 = try_v3(sym)
            if r2:
                result.update({"price": r2.get("price"), "time": r2.get("time"), "open": r2.get("open"), "close": r2.get("close"), "provider": r2.get("provider")})
                details["v3"] = "ok"
                if debug:
                    details["v3_detail"] = r2
            else:
                details["v3_info"] = info2
        except Exception as e:
            details["v3_exception"] = str(e) + "\n" + traceback.format_exc()

    # 3) fallback: aggressively try historical close/open
    if (result["price"] is None and result["open"] is None and result["close"] is None) and fallback_to_close == "close":
        try:
            import vnstock as vnmod
            df = None
            if hasattr(vnmod, "stock_historical_data"):
                try:
                    df = vnmod.stock_historical_data(symbol=sym, start_date="2020-01-01", end_date="2030-12-31")
                except Exception:
                    try:
                        df = vnmod.stock_historical_data(sym, "2020-01-01", "2030-12-31")
                    except Exception:
                        df = None
            if (df is None or getattr(df, "empty", True)) and 'Vnstock' in globals():
                try:
                    from vnstock import Vnstock
                    v = Vnstock()
                    st = v.stock(symbol=sym)
                    if hasattr(st, "quote") and hasattr(st.quote, "history"):
                        df = st.quote.history(start="2020-01-01", end="2030-12-31", interval="1D")
                except Exception:
                    pass

            price, time_, open_p, close_p = get_price_from_df(df)
            if price is not None or open_p is not None or close_p is not None:
                result.update({"price": price, "time": time_, "open": open_p, "close": close_p, "provider": "historical-fallback"})
                details["historical_fallback"] = "ok"
        except Exception as e:
            details["historical_exception"] = str(e)

    out = {
        "symbol": result["symbol"],
        "price": result["price"],
        "time": result["time"],
        "open": result["open"],
        "close": result["close"],
        "provider": result["provider"] or None
    }
    if debug:
        out["_debug"] = details
    return jsonify(out)

@app.route("/")
def index():
    return jsonify({"ok": True, "endpoints": ["/price?symbol=VNM&debug=1"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
