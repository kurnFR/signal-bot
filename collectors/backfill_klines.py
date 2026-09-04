"""
One-off (re-runnable) historical backfill for all symbols x timeframes x
markets defined in config.py. Paginates backwards from "now" using
Binance's startTime/endTime + 1000-candle limit, until BACKFILL_YEARS is
covered. Safe to re-run any time (upsert is idempotent) -- e.g. run this
weekly as a cron job to patch any gaps caused by collector downtime.

Usage:
    python -m collectors.backfill_klines
    python -m collectors.backfill_klines --symbol BTCUSDT --timeframe 1h
"""
import argparse
import logging
import sys
import os
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    SYMBOLS, TIMEFRAMES, MARKETS, SPOT_KLINE_URL, FUTURES_KLINE_URL,
    BACKFILL_YEARS, KLINE_FETCH_LIMIT,
)
from db.db import upsert_ohlcv, init_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill")

SESSION = requests.Session()


def fetch_klines(url, symbol, interval, start_ms, end_ms, retries=3):
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": KLINE_FETCH_LIMIT,
    }
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                logger.warning(f"Rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt+1} failed for {symbol} {interval}: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch klines for {symbol} {interval} after {retries} retries")


def klines_to_rows(symbol, market, timeframe, raw_klines):
    rows = []
    for k in raw_klines:
        # Binance kline array format:
        # [open_time, open, high, low, close, volume, close_time, quote_volume,
        #  num_trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]
        rows.append((
            symbol, market, timeframe,
            int(k[0]), int(k[6]),
            k[1], k[2], k[3], k[4], k[5], k[7],
            int(k[8]), k[9], k[10],
            1,  # is_closed -- historical candles are always closed
        ))
    return rows


def backfill_symbol_timeframe(symbol, market, timeframe, years):
    url = SPOT_KLINE_URL if market == "spot" else FUTURES_KLINE_URL
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=365 * years)).timestamp() * 1000)

    cursor = start_ms
    total_rows = 0
    while cursor < end_ms:
        raw = fetch_klines(url, symbol, timeframe, cursor, end_ms)
        if not raw:
            break
        rows = klines_to_rows(symbol, market, timeframe, raw)
        upsert_ohlcv(rows)
        total_rows += len(rows)

        last_open_time = raw[-1][0]
        next_cursor = last_open_time + 1
        if next_cursor <= cursor:  # safety against infinite loop
            break
        cursor = next_cursor

        if len(raw) < KLINE_FETCH_LIMIT:
            break  # reached the most recent candle

        time.sleep(0.25)  # stay well under Binance rate limits

    logger.info(f"{symbol} {market} {timeframe}: backfilled {total_rows} candles")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Backfill a single symbol only")
    parser.add_argument("--timeframe", help="Backfill a single timeframe only")
    parser.add_argument("--market", choices=["spot", "futures"], help="Backfill a single market only")
    parser.add_argument("--years", type=float, default=BACKFILL_YEARS)
    parser.add_argument("--skip-schema", action="store_true", help="Skip schema init")
    args = parser.parse_args()

    if not args.skip_schema:
        init_schema()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    timeframes = [args.timeframe] if args.timeframe else TIMEFRAMES
    markets = [args.market] if args.market else MARKETS

    for market in markets:
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    backfill_symbol_timeframe(symbol, market, timeframe, args.years)
                except Exception as e:
                    logger.error(f"FAILED {symbol} {market} {timeframe}: {e}")


if __name__ == "__main__":
    main()
