from flask import Flask, request, jsonify
from vnstock import stock_intraday_data, stock_historical_data

app = Flask(__name__)

@app.route("/price")
def price():
    symbol = request.args.get("symbol", "VNM")
    try:
        df = stock_intraday_data(symbol, "1D")
        if df is None or df.empty:
            df = stock_historical_data(symbol, "2020-01-01", "2030-12-31", "1D")
        if df is None or df.empty:
            return jsonify({"symbol": symbol, "price": None, "time": None})
        last = df.tail(1).iloc[0].to_dict()
        return jsonify({
            "symbol": symbol,
            "time": str(last.get("time") or last.get("Date")),
            "price": float(last.get("close") or last.get("Close"))
        })
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
