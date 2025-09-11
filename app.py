# app.py
import logging
import traceback
from flask import Flask, request, jsonify
from datetime import datetime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vnstock-api")

app = Flask(__name__)

def normalize_symbol(sym: str) -> str:
    if not sym:
        return ""
    return sym.strip().upper()

def row_get_price_time(row_dict):
    """Try many field names to extract price and time from a row dict."""
    if row_dict is None:
        return None, None

    # normalize keys to lowercase for robust lookup
    lower = {k.lower(): v for k, v in row_dict.items()}

    # price candidates in order of preference
    price_keys = [
        "lastprice", "pricelast", "matchprice", "match_price",
        "last", "close", "price", "closeprice", "close_price", "c", "match"
    ]
    price = None
    for k in price_keys:
        if k in lower and lower[k] is not None:
            try:
                price = float(lower[k])
                break
            except Exception:
                try:
                    # sometimes value is string with comma
                    price = float(str(lower[k]).replace(",", ""))
                    break
                except Exception:
                    price = None

    # time candidates
    time_keys = [
        "time", "datetime", "date", "updatedat", "updated_at", "matchtime", "timestamp"
    ]
    tval = None
    for k in time_keys:
        if k in lower and lower[k] is not None:
            tval = lower[k]
            break

    # normalize time to iso string if possible
    if tval is not None:
        try:
            # if it's pandas Timestamp-like, convert to string
            tstr = str(tval)
        except Exception:
            tstr = None
    else:
        tstr = None

    return price, tstr

def get_price_from_df(df):
    """Return (price, time) or (None, None). df may be pandas DataFrame-like."""
    try:
        if df is None:
            return None, None
        # pandas DataFrame has .empty
        empty = False
        try:
            empty = getattr(df, "empty", False)
        except Exception:
            empty = False
        if empty:
            return None, None

        # get last row
        try:
            last = df.tail(1).iloc[0]
        except Exception:
            # maybe df is list-like dicts
            try:
                last = df[-1]
            except Exception:
                return None, None

        # convert to dict
        try:
            rd = last.to_dict()
        except Exception:
            try:
                rd = dict(last)
            except Exception:
                return None, None

        price, time = row_get_price_time(rd)
        return price, time
    except Exception as e:
        log.exception("get_price_from_df failed: %s", e)
        return None, None

def try_legacy(symbol):
    """Try old-style functions exposed at top-level of vnstock package."""
    try:
        import vnstock as vn
    except Exception as e:
        log.info("legacy import vnstock failed: %s", e)
        return None, {"error": f"legacy import failed: {e}"}

    # Try stock_intraday_data if available
    try:
        if hasattr(vn, "stock_intraday_data"):
            log.info("Using legacy stock_intraday_data for %s", symbol)
            try:
                df = vn.stock_intraday_data(symbol=symbol, page_num=0, page_size=5000)
            except TypeError:
                # some versions have slightly different signature
                df = vn.stock_intraday_data(symbol, 0, 5000)
            price, time = get_price_from_df(df)
            if price is not None:
                return {"provider": "vnstock-legacy-intraday", "price": price, "time": time}, None

        # fallback to historical
        if hasattr(vn, "stock_historical_data"):
            log.info("Legacy intraday empty, trying stock_historical_data for %s", symbol)
            df2 = vn.stock_historical_data(symbol=symbol, start_date="2020-01-01", end_date="2030-12-31", interval="1D")
            price, time = get_price_from_df(df2)
            if price is not None:
                return {"provider": "vnstock-legacy-history", "price": price, "time": time}, None

        return None, {"info": "legacy functions present but returned no data"}
    except Exception as e:
        log.exception("legacy usage error: %s", e)
        return None, {"error": f"legacy usage exception: {e}"}

def try_v3(symbol):
    """Try vnstock v3 style usage (Vnstock / Quote)."""
    try:
        # import Vnstock class
        from vnstock import Vnstock
    except Exception as e:
        log.info("vnstock Vnstock import failed: %s", e)
        return None, {"error": f"v3 import failed: {e}"}

    try:
        v = Vnstock()
        # create stock object; try with default behavior first
        stock_obj = None
        try:
            stock_obj = v.stock(symbol=symbol)
        except Exception as e:
            log.info("Vnstock.stock default failed, trying without source: %s", e)
            # try without named parameter
            try:
                stock_obj = v.stock(symbol)
            except Exception as ee:
                log.info("Vnstock.stock fallback failed: %s", ee)
                stock_obj = None

        if stock_obj is None:
            # try alternate known sources (vcb/vci/TCBS) by name if supported
            for src in ("VCI", "TCBS", "SSI"):
                try:
                    stock_obj = v.stock(symbol=symbol, source=src)
                    if stock_obj:
                        log.info("Vnstock.stock: found using source %s", src)
                        break
                except Exception:
                    stock_obj = None

        if stock_obj is None:
            return None, {"info": "v3 stock object creation failed"}

        # try intraday via stock.quote.intraday
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

        price, time = get_price_from_df(df)
        if price is not None:
            return {"provider": "vnstock-v3", "price": price, "time": time}, None

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
        # default example: VNM (keeps backward compatibility)
        sym = "VNM"

    result = {"symbol": sym, "price": None, "time": None, "provider": None}
    details = {}

    # 1) Try legacy top-level API
    try:
        r, info = try_legacy(sym)
        if r:
            result.update({"price": r["price"], "time": r["time"], "provider": r.get("provider")})
            details["legacy"] = "ok"
            if debug:
                details["legacy_detail"] = r
        else:
            details["legacy_info"] = info
    except Exception as e:
        details["legacy_exception"] = str(e) + "\n" + traceback.format_exc()

    # 2) If still no data, try v3
    if result["price"] is None:
        try:
            r2, info2 = try_v3(sym)
            if r2:
                result.update({"price": r2["price"], "time": r2["time"], "provider": r2.get("provider")})
                details["v3"] = "ok"
                if debug:
                    details["v3_detail"] = r2
            else:
                details["v3_info"] = info2
        except Exception as e:
            details["v3_exception"] = str(e) + "\n" + traceback.format_exc()

    # 3) If still none and fallback==close, try explicitly historical close (aggressive)
    if result["price"] is None and fallback_to_close == "close":
        try:
            # try to explicitly call historical data via any available API
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
            # or try v3 history if available
            if (df is None or getattr(df, "empty", True)) and 'Vnstock' in globals():
                try:
                    from vnstock import Vnstock
                    v = Vnstock()
                    st = v.stock(symbol=sym)
                    if hasattr(st, "quote") and hasattr(st.quote, "history"):
                        df = st.quote.history(start="2020-01-01", end="2030-12-31", interval="1D")
                except Exception:
                    pass

            price, time = get_price_from_df(df)
            if price is not None:
                result.update({"price": price, "time": time, "provider": "historical-fallback"})
                details["historical_fallback"] = "ok"
        except Exception as e:
            details["historical_exception"] = str(e)

    # 4) return
    out = {"symbol": result["symbol"], "price": result["price"], "time": result["time"], "provider": result["provider"] or None}
    if debug:
        out["_debug"] = details
    return jsonify(out)

@app.route("/")
def index():
    return jsonify({"ok": True, "endpoints": ["/price?symbol=VNM&debug=1"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
