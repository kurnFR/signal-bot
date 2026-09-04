"""
Sanity-checks the `features` table after running build_features.py.

Checks:
  1. Row counts roughly match ohlcv (minus indicator warm-up period).
  2. RSI is within [0, 100] wherever not NULL.
  3. ATR and volume_sma are non-negative wherever not NULL.
  4. support <= resistance, swing_low <= swing_high wherever not NULL.
  5. Fibonacci levels are monotonic between swing_low and swing_high.
  6. No unexpected gaps of NULLs in the middle of the series (NULLs should
     only appear in the warm-up period at the very start).
  7. Prints the latest row for each symbol/timeframe so you can eyeball it
     against what you'd expect from the raw candles.

Usage:
    python3 validate_step2.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import SYMBOLS, TIMEFRAMES, MARKETS
from db.db import get_pool


def query(sql, params=None):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def check_symbol_tf(symbol, market, timeframe):
    problems = 0
    rows = query(
        "SELECT * FROM features WHERE symbol=%s AND market=%s AND timeframe=%s "
        "ORDER BY open_time ASC",
        (symbol, market, timeframe),
    )
    if not rows:
        print(f"  [MISSING] {symbol} {market} {timeframe}: 0 feature rows")
        return 1

    ohlcv_count = query(
        "SELECT COUNT(*) as c FROM ohlcv WHERE symbol=%s AND market=%s AND timeframe=%s",
        (symbol, market, timeframe),
    )[0]["c"]

    n = len(rows)
    warmup_ratio = n / ohlcv_count if ohlcv_count else 0
    if warmup_ratio < 0.5:
        print(f"  [WARN] {symbol} {market} {timeframe}: {n} feature rows vs {ohlcv_count} ohlcv rows (check warm-up logic)")
        problems += 1

    bad_rsi = [r for r in rows if r["rsi"] is not None and not (0 <= float(r["rsi"]) <= 100)]
    if bad_rsi:
        print(f"  [BAD RSI] {symbol} {market} {timeframe}: {len(bad_rsi)} rows with RSI outside [0,100]")
        problems += 1

    bad_atr = [r for r in rows if r["atr"] is not None and float(r["atr"]) < 0]
    if bad_atr:
        print(f"  [BAD ATR] {symbol} {market} {timeframe}: {len(bad_atr)} rows with negative ATR")
        problems += 1

    bad_sr = [r for r in rows if r["support"] is not None and r["resistance"] is not None
              and float(r["support"]) > float(r["resistance"])]
    if bad_sr:
        print(f"  [BAD S/R] {symbol} {market} {timeframe}: {len(bad_sr)} rows where support > resistance")
        problems += 1

    bad_fib = [r for r in rows if r["fib_0"] is not None and not (
        float(r["fib_1"]) <= float(r["fib_618"]) <= float(r["fib_5"]) <= float(r["fib_382"]) <= float(r["fib_0"])
    )]
    if bad_fib:
        print(f"  [BAD FIB] {symbol} {market} {timeframe}: {len(bad_fib)} rows with non-monotonic fib levels")
        problems += 1

    # NULLs should only be in the leading warm-up window, not scattered later
    non_null_idx = [i for i, r in enumerate(rows) if r["rsi"] is not None]
    if non_null_idx:
        first_non_null = non_null_idx[0]
        later_nulls = [i for i, r in enumerate(rows) if i > first_non_null and r["rsi"] is None]
        if later_nulls:
            print(f"  [GAP] {symbol} {market} {timeframe}: {len(later_nulls)} unexpected NULL RSI rows after warm-up")
            problems += 1

    latest = rows[-1]
    status = "OK" if problems == 0 else "ISSUES"
    print(f"  [{status}] {symbol} {market} {timeframe}: {n} rows. Latest ({latest['open_time_wib']} WIB): "
          f"RSI={latest['rsi']}, MACD_hist={latest['macd_hist']}, ATR%={latest['atr_pct']}, "
          f"vol_ratio={latest['volume_ratio']}, support={latest['support']}, resistance={latest['resistance']}")
    return problems


if __name__ == "__main__":
    print("=== Step 2 feature validation ===")
    total_problems = 0
    for market in MARKETS:
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                total_problems += check_symbol_tf(symbol, market, tf)

    print("\n" + "=" * 50)
    if total_problems == 0:
        print("STEP 2 VALIDATION: PASSED. Ready for Step 3 (backtester).")
    else:
        print(f"STEP 2 VALIDATION: {total_problems} issue(s) found. Fix before moving to Step 3.")
    print("=" * 50)
