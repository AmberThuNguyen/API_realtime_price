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
        "last", "close", "price", "closepr
