"""
Historical open interest backfill.

IMPORTANT LIMITATION: Binance's public API only retains OI history for
approximately the last 30 days via this endpoint -- unlike klines or
funding rate, you cannot get years of OI history this way (this is a
platform limitation, not something this script can work around). This
backfills whatever Binance actually has; the live oi_poller.py (Step 1) is
what accumulates further history going forward from here. Don't expect a
multi-year OI dataset -- it isn't available from Binance's public API at
any price/tool.

Usage:
    python3 -m collectors.backfill_oi
    python3 -m collectors.backfill_oi --symbol BTCUSDT --period 1h
"""
import argparse
import logging
import sys
import os
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, FUTURES_OI_HIST_URL
from db.db import upsert_open_interest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_oi")

SESSION = requests.Session()
FETCH_LIMIT = 500


def fetch_oi_hist(symbol, period, start_ms, end_ms, retries=3):
    params = {"symbol": symbol, "period": period, "startTime": start_ms, "endTime": end_ms, "limit": FETCH_LIMIT}
    for attempt in range(retries):
        try:
            resp = SESSION.get(FUTURES_OI_HIST_URL, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt+1} failed for {symbol}: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch OI history for {symbol} after {retries} retries")


def rows_from_response(symbol, raw):
    rows = []
    for item in raw:
        rows.append((
            symbol,
            int(item["timestamp"]),
            item["sumOpenInterest"],
            item.get("sumOpenInterestValue"),
        ))
    return rows


def backfill_symbol(symbol, period, days):
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    cursor = start_ms
    total = 0
    while cursor < end_ms:
        raw = fetch_oi_hist(symbol, period, cursor, end_ms)
        if not raw:
            break
        rows = rows_from_response(symbol, raw)
        upsert_open_interest(rows)
        total += len(rows)

        last_time = raw[-1]["timestamp"]
        next_cursor = last_time + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if len(raw) < FETCH_LIMIT:
            break
        time.sleep(0.25)

    logger.info(f"{symbol}: backfilled {total} OI records (period={period}) -- "
                f"Binance only retains ~30 days regardless of --days requested")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--period", default="1h",
                         choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"])
    parser.add_argument("--days", type=int, default=30,
                         help="Binance typically only retains ~30 days regardless of what's requested here")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    for symbol in symbols:
        try:
            backfill_symbol(symbol, args.period, args.days)
        except Exception as e:
            logger.error(f"FAILED {symbol}: {e}")


if __name__ == "__main__":
    main()
