"""
Computes all indicators (via features/indicators.py) for every configured
symbol x market x timeframe and upserts the results into the `features`
table.

This recomputes the FULL history each run (simple and correct -- with only
a few symbols/timeframes this is fast; if/when you scale up to 10 symbols x
6 timeframes, switch to incremental computation using a lookback window
plus only the new candles, but don't optimize that prematurely).

Usage:
    python3 -m features.build_features
    python3 -m features.build_features --symbol BTCUSDT --timeframe 1h
"""
import argparse
import logging
import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, TIMEFRAMES, MARKETS, RSI_WINDOW, MACD_FAST, MACD_SLOW, \
    MACD_SIGNAL, ATR_PERIOD, VOLUME_SMA_PERIOD, HTF_S_R_LOOKBACK_BARS
from db.db import fetch_ohlcv_df, upsert_features
from features.indicators import compute_all_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_features")

PARAMS = {
    "RSI_WINDOW": RSI_WINDOW,
    "MACD_FAST": MACD_FAST,
    "MACD_SLOW": MACD_SLOW,
    "MACD_SIGNAL": MACD_SIGNAL,
    "ATR_PERIOD": ATR_PERIOD,
    "VOLUME_SMA_PERIOD": VOLUME_SMA_PERIOD,
    "HTF_S_R_LOOKBACK_BARS": HTF_S_R_LOOKBACK_BARS,
}

FEATURE_COLUMNS = [
    "rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct",
    "volume_sma", "volume_ratio", "support", "resistance",
    "swing_high", "swing_low", "fib_0", "fib_236", "fib_382",
    "fib_5", "fib_618", "fib_786", "fib_1",
]


def call_with_retry(fn, description, attempts=3):
    """
    Generic retry-with-backoff. Used around both the DB read and the DB
    write in build_one(), since either can hit a transient connection
    error and the fix (chunked writes / pool_pre_ping) may not eliminate
    every possible cause.
    """
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            logger.warning(f"{description}: attempt {attempt}/{attempts} failed ({e}), retrying...")
            time.sleep(1.5 * attempt)
    raise last_err


def build_one(symbol, market, timeframe):
    desc = f"{symbol} {market} {timeframe}"
    df = call_with_retry(lambda: fetch_ohlcv_df(symbol, market, timeframe), f"{desc} fetch")
    min_required = max(RSI_WINDOW, MACD_SLOW, ATR_PERIOD, VOLUME_SMA_PERIOD, HTF_S_R_LOOKBACK_BARS) + 1
    if len(df) < min_required:
        logger.warning(
            f"{symbol} {market} {timeframe}: only {len(df)} candles, "
            f"need >= {min_required} for full indicator warm-up -- skipping"
        )
        return 0

    feats = compute_all_features(df, PARAMS)
    feats["open_time"] = df["open_time"].values

    rows = []
    for _, r in feats.iterrows():
        rows.append((
            symbol, market, timeframe, int(r["open_time"]),
            *[r[c] for c in FEATURE_COLUMNS],
        ))

    call_with_retry(lambda: upsert_features(rows), f"{desc} upsert")
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--timeframe")
    parser.add_argument("--market", choices=["spot", "futures"])
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    timeframes = [args.timeframe] if args.timeframe else TIMEFRAMES
    markets = [args.market] if args.market else MARKETS

    for market in markets:
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    n = build_one(symbol, market, timeframe)
                    if n:
                        logger.info(f"{symbol} {market} {timeframe}: wrote {n} feature rows")
                except Exception as e:
                    logger.error(f"FAILED {symbol} {market} {timeframe}: {e}")


if __name__ == "__main__":
    main()
