"""
Historical funding rate backfill. Unlike open interest and liquidations,
Binance provides FULL historical funding rate data via REST (funding
settles every 8 hours, so even 5 years of history is a small number of
rows -- ~5,475 per symbol) -- this is the one futures data series that can
be properly backtested across the same time range as OHLCV.

Only needs REST access to fapi.binance.com -- does NOT require futures
WebSocket access (which was found to be geo-restricted earlier) or
ENABLE_FUTURES=true. Run check_futures_access.py first to confirm REST
access works before running this.

Usage:
    python3 -m collectors.backfill_funding
    python3 -m collectors.backfill_funding --symbol BTCUSDT --years 5
"""
import argparse
import logging
import sys
import os
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, FUTURES_FUNDING_HISTORY_URL, BACKFILL_YEARS
from db.db import upsert_funding

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_funding")

SESSION = requests.Session()
FETCH_LIMIT = 1000


def fetch_funding(symbol, start_ms, end_ms, retries=3):
    params = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": FETCH_LIMIT}
    for attempt in range(retries):
        try:
            resp = SESSION.get(FUTURES_FUNDING_HISTORY_URL, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                logger.warning(f"Rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt+1} failed for {symbol}: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch funding rate for {symbol} after {retries} retries")


def _clean_decimal(value):
    """
    Binance's historical funding endpoint sometimes returns an empty
    string for markPrice on certain records (observed in practice, not
    documented) -- MySQL can't cast '' to DECIMAL, so convert empty/None
    to None here so it's stored as NULL instead of crashing the insert.
    """
    if value is None or value == "":
        return None
    return value


def rows_from_response(symbol, raw):
    rows = []
    for item in raw:
        rows.append((
            symbol,
            int(item["fundingTime"]),
            _clean_decimal(item.get("markPrice")),
            None,  # index_price not provided by the historical endpoint
            _clean_decimal(item.get("fundingRate")),
            None,  # next_funding_time not applicable to historical rows
        ))
    return rows


def backfill_symbol(symbol, years):
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=365 * years)).timestamp() * 1000)

    cursor = start_ms
    total = 0
    while cursor < end_ms:
        raw = fetch_funding(symbol, cursor, end_ms)
        if not raw:
            break
        rows = rows_from_response(symbol, raw)
        upsert_funding(rows)
        total += len(rows)

        last_time = raw[-1]["fundingTime"]
        next_cursor = last_time + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if len(raw) < FETCH_LIMIT:
            break
        time.sleep(0.25)

    logger.info(f"{symbol}: backfilled {total} funding rate records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--years", type=float, default=BACKFILL_YEARS)
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    for symbol in symbols:
        try:
            backfill_symbol(symbol, args.years)
        except Exception as e:
            logger.error(f"FAILED {symbol}: {e}")


if __name__ == "__main__":
    main()
