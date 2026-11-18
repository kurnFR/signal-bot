"""
collectors/futures_kline_poller.py

Ongoing REST-based Binance USD-M Futures kline poller daemon.
Maintains continuous up-to-date futures candles in table `ohlcv` to overcome
the silent broadcast WebSocket frame filter on Indonesian network origins.

Key Guarantees:
1. Pacing & Quota Safety: Polls configured symbols and intervals every 60 seconds
   with limit=3 (only 12 requests/min = 60 weight/min; 2.5% of quota).
2. Forming Candle Handling: Ingests the current active bar with is_closed=0,
   providing live market context for paper/engine.py:fetch_latest_open_candle().
3. Finalized Promotion: Once now_ms > close_time, promotes the candle to is_closed=1.
4. Concurrency Guard: Checks advisory lock 'futures_kline_writer_<sym>_<tf>' with
   0s timeout before writing; gracefully defers if historical recovery is active.
5. Invariant Protection: Reuses db.db:upsert_ohlcv, guaranteeing forming bars
   cannot overwrite finalized candles.
6. Heartbeat & Isolation: Emits heartbeat('futures_kline_poller'); isolates errors
   per symbol/interval.

Usage:
    python3 -m collectors.futures_kline_poller
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, TIMEFRAMES, FUTURES_KLINE_URL
from collectors.backfill_klines import is_candle_closed
from db.db import get_pool, upsert_ohlcv, heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("futures_kline_poller")

DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_POLL_LIMIT = 3


def fetch_recent_klines(
    symbol: str,
    interval: str,
    limit: int = DEFAULT_POLL_LIMIT,
    session: Optional[requests.Session] = None,
    timeout: float = 10.0,
) -> List[list]:
    """
    Fetch the most recent `limit` klines for symbol and interval.
    """
    s = session or requests
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    resp = s.get(FUTURES_KLINE_URL, params=params, timeout=timeout)
    if resp.status_code == 429:
        wait = int(resp.headers.get("Retry-After", 5))
        logger.warning(f"HTTP 429 Rate limited for {symbol} {interval}, Retry-After {wait}s")
        time.sleep(wait)
        return []
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Expected list of klines, got {type(data)}")
    return data


def try_acquire_advisory_lock(conn: Any, lock_name: str) -> bool:
    """Non-blocking advisory lock acquisition (timeout=0)."""
    if not hasattr(conn, "cursor"):
        return True
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
        res = cur.fetchone()
        return bool(res and res[0] == 1)
    except Exception as e:
        logger.warning(f"Advisory lock check error for {lock_name}: {e}")
        return True
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def release_advisory_lock(conn: Any, lock_name: str) -> bool:
    if not hasattr(conn, "cursor"):
        return True
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        res = cur.fetchone()
        return bool(res and res[0] == 1)
    except Exception as e:
        logger.warning(f"Advisory lock release error for {lock_name}: {e}")
        return False
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def poll_symbol_timeframe(
    symbol: str,
    interval: str,
    market: str = "futures",
    pool: Optional[Any] = None,
    conn: Optional[Any] = None,
    session: Optional[requests.Session] = None,
    dry_run: bool = False,
    now_ms: Optional[int] = None,
) -> int:
    """
    Polls Binance Futures REST for a single (symbol, interval), evaluates candle closure,
    and upserts rows into table ohlcv.
    Returns the count of rows successfully prepared/upserted.
    """
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    raw_klines = fetch_recent_klines(symbol, interval, limit=DEFAULT_POLL_LIMIT, session=session)
    if not raw_klines:
        return 0

    rows = []
    for k in raw_klines:
        try:
            open_time = int(k[0])
            close_time = int(k[6])
            open_val = str(k[1])
            high_val = str(k[2])
            low_val = str(k[3])
            close_val = str(k[4])
            vol_val = str(k[5])
            qvol_val = str(k[7])
            num_trades = int(k[8])
            taker_base = str(k[9])
            taker_quote = str(k[10])
        except (IndexError, ValueError, TypeError) as e:
            logger.warning(f"Malformed kline entry for {symbol} {interval}: {k} ({e})")
            continue

        closed_flag = is_candle_closed(close_time, now_ms=now_ms)
        rows.append((
            symbol, market, interval,
            open_time, close_time,
            open_val, high_val, low_val, close_val,
            vol_val, qvol_val, num_trades,
            taker_base, taker_quote,
            closed_flag,
        ))

    if rows and not dry_run:
        upsert_ohlcv(rows, pool=pool, conn=conn)

    return len(rows)


async def run_cycle(
    symbols: List[str],
    timeframes: List[str],
    market: str = "futures",
    pool: Optional[Any] = None,
    session: Optional[requests.Session] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Executes a single polling pass across all configured symbols and intervals.
    """
    cycle_stats = {"polled": 0, "errors": 0}
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    for sym in symbols:
        for tf in timeframes:
            lock_name = f"futures_kline_writer_{sym}_{tf}"
            c = None
            if pool is not None:
                try:
                    c = pool.get_connection()
                except Exception as e:
                    logger.error(f"Failed to get DB connection from pool: {e}")

            locked = True
            if c is not None and not dry_run:
                locked = try_acquire_advisory_lock(c, lock_name)
                if not locked:
                    logger.info(f"[{sym} {tf}] Writer lock busy (recovery in progress?); skipping this cycle")
                    c.close()
                    continue

            try:
                count = poll_symbol_timeframe(
                    symbol=sym,
                    interval=tf,
                    market=market,
                    conn=c,
                    pool=pool if c is None else None,
                    session=session,
                    dry_run=dry_run,
                    now_ms=now_ms,
                )
                cycle_stats["polled"] += count
            except Exception as e:
                logger.warning(f"[{sym} {tf}] Error during poll: {e}")
                cycle_stats["errors"] += 1
            finally:
                if c is not None and not dry_run:
                    if locked:
                        release_advisory_lock(c, lock_name)
                    c.close()

    status = "ok" if cycle_stats["errors"] == 0 else "degraded"
    detail = f"Polled {cycle_stats['polled']} rows, errors={cycle_stats['errors']}"
    if not dry_run:
        try:
            heartbeat("futures_kline_poller", status=status, detail=detail, pool=pool)
        except Exception as e:
            logger.warning(f"Could not record poller heartbeat: {e}")

    return cycle_stats


async def run(
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    stop_event: Optional[asyncio.Event] = None,
    pool: Optional[Any] = None,
    dry_run: bool = False,
) -> None:
    """
    Long-running async polling loop for ongoing futures kline collection.
    """
    target_symbols = symbols or SYMBOLS
    target_timeframes = timeframes or TIMEFRAMES
    logger.info(
        f"Starting Futures Kline Poller (interval={poll_interval}s, "
        f"symbols={target_symbols}, timeframes={target_timeframes}, dry_run={dry_run})"
    )

    s = requests.Session()
    try:
        while True:
            t0 = time.time()
            try:
                stats = await run_cycle(
                    symbols=target_symbols,
                    timeframes=target_timeframes,
                    market="futures",
                    pool=pool,
                    session=s,
                    dry_run=dry_run,
                )
                logger.info(f"Poll cycle complete in {time.time()-t0:.2f}s: {stats}")
            except Exception as e:
                logger.error(f"Unhandled error in futures kline poll cycle: {e}", exc_info=True)

            if stop_event is not None and stop_event.is_set():
                logger.info("Futures Kline Poller received stop signal. Exiting.")
                break

            # Sleep remaining time until next poll interval
            elapsed = time.time() - t0
            sleep_sec = max(1.0, poll_interval - elapsed)

            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=sleep_sec)
                    if stop_event.is_set():
                        break
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(sleep_sec)
    except asyncio.CancelledError:
        logger.info("Futures Kline Poller task cancelled. Cleaning up.")
    finally:
        s.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ongoing Binance Futures Kline REST Poller")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS, help="Polling interval in seconds (default 60)")
    parser.add_argument("--dry-run", action="store_true", help="Poll and evaluate without writing to database")
    args = parser.parse_args()

    pool = None
    if not args.dry_run:
        try:
            pool = get_pool()
        except Exception as e:
            logger.error(f"Failed to initialize DB pool: {e}")
            sys.exit(1)

    try:
        asyncio.run(run(poll_interval=args.interval, pool=pool, dry_run=args.dry_run))
    except KeyboardInterrupt:
        logger.info("Interrupted by operator. Shutting down.")


if __name__ == "__main__":
    main()
