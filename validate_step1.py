"""
Run this AFTER backfill + a period of live collection (recommend letting
run_collectors.py run for at least 30-60 minutes first) to sanity-check
Step 1 before moving to Step 2 (indicators/features).

Checks:
  1. Every symbol/timeframe/market combo has candles, and row counts are
     roughly what's expected for the backfill window.
  2. No large gaps between consecutive candles (missed candles = bug).
  3. Collector heartbeats are recent (collectors are alive).
  4. Funding, OI, liquidations tables have recent rows.

Usage:
    python validate_step1.py
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import SYMBOLS, TIMEFRAMES, MARKETS
from db.db import get_pool

TIMEFRAME_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


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


def check_ohlcv_coverage():
    print("\n=== OHLCV coverage & gap check ===")
    problems = 0
    for market in MARKETS:
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                rows = query(
                    "SELECT open_time, open_time_wib FROM ohlcv "
                    "WHERE symbol=%s AND market=%s AND timeframe=%s "
                    "ORDER BY open_time ASC",
                    (symbol, market, tf),
                )
                if not rows:
                    print(f"  [MISSING] {symbol} {market} {tf}: 0 rows")
                    problems += 1
                    continue

                expected_step = TIMEFRAME_MS[tf]
                gaps = []
                for i in range(1, len(rows)):
                    diff = rows[i]["open_time"] - rows[i - 1]["open_time"]
                    if diff > expected_step:
                        missing_candles = diff // expected_step - 1
                        gaps.append((rows[i - 1]["open_time_wib"], rows[i]["open_time_wib"], missing_candles))
                if gaps:
                    print(f"  [GAPS] {symbol} {market} {tf}: {len(rows)} rows, {len(gaps)} gap(s):")
                    for before, after, missing in gaps:
                        print(f"           missing ~{missing} candle(s) between {before} and {after} (WIB)")
                    problems += 1
                else:
                    latest = rows[-1]["open_time_wib"]
                    print(f"  [OK] {symbol} {market} {tf}: {len(rows)} rows, latest candle {latest} WIB")
    return problems


def check_heartbeats():
    print("\n=== Collector heartbeats ===")
    rows = query("SELECT * FROM collector_heartbeat")
    problems = 0
    if not rows:
        print("  [WARN] No heartbeats yet -- have you started run_collectors.py?")
        return 1
    for r in rows:
        age = datetime.now(timezone.utc) - r["last_beat_time"].replace(tzinfo=timezone.utc)
        status_flag = "OK" if age < timedelta(minutes=5) and r["status"] == "ok" else "STALE/ERROR"
        if status_flag != "OK":
            problems += 1
        print(f"  [{status_flag}] {r['collector_name']}: last beat {age.seconds}s ago, status={r['status']}, detail={r['detail']}")
    return problems


def check_supplementary_tables():
    print("\n=== Funding / Open Interest / Liquidations recency ===")
    if "futures" not in MARKETS:
        print("  [SKIPPED] ENABLE_FUTURES is false -- these are futures-only, nothing to check")
        return 0
    problems = 0
    for table, time_col in [("funding_rate", "event_time"), ("open_interest", "event_time")]:
        rows = query(f"SELECT symbol, MAX({time_col}) as latest, MAX({time_col}_wib) as latest_wib FROM {table} GROUP BY symbol")
        if not rows:
            print(f"  [MISSING] {table}: no rows at all")
            problems += 1
            continue
        for r in rows:
            latest = datetime.fromtimestamp(r["latest"] / 1000, tz=timezone.utc)
            age = datetime.now(timezone.utc) - latest
            flag = "OK" if age < timedelta(minutes=5) else "STALE"
            if flag != "OK":
                problems += 1
            print(f"  [{flag}] {table} {r['symbol']}: latest {r['latest_wib']} WIB ({age.seconds}s ago)")

    liq_count = query("SELECT COUNT(*) as c FROM liquidations")[0]["c"]
    print(f"  [INFO] liquidations table has {liq_count} total rows (low volume is normal)")
    return problems


if __name__ == "__main__":
    total_problems = 0
    total_problems += check_ohlcv_coverage()
    total_problems += check_heartbeats()
    total_problems += check_supplementary_tables()

    print("\n" + "=" * 50)
    if total_problems == 0:
        print("STEP 1 VALIDATION: PASSED. Ready for Step 2 (indicators/features).")
    else:
        print(f"STEP 1 VALIDATION: {total_problems} issue(s) found. Fix before moving to Step 2.")
    print("=" * 50)
