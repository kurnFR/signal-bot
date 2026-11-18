"""
Binance USDⓈ-M Futures Funding Rate REST Collector.
Collects historical and periodic settled funding rates via public REST API:
GET https://fapi.binance.com/fapi/v1/fundingRate

Unlike the WebSocket !markPrice@arr stream (which requires fstream.binance.com
and only broadcasts 1-second estimated rates), this REST endpoint is:
1. Reachable without geo-restriction / VPN requirements.
2. Provides authentic settled funding events (typically every 8 hours) with exact
   exchange funding timestamps and settled rates.
3. Completely idempotent against funding_rate PRIMARY KEY (symbol, event_time).
"""

import argparse
import asyncio
import decimal
import email.utils
import logging
import math
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    SYMBOLS,
    FUTURES_FUNDING_HISTORY_URL,
    FUNDING_POLL_INTERVAL_SECONDS,
    load_funding_collector_config,
)
from db.db import upsert_funding, get_latest_funding_time, heartbeat, get_funding_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("funding_poller")

DEFAULT_LIMIT = 1000
DEFAULT_MAX_RETRIES = 3
DEFAULT_LOOKBACK_DAYS = 30

HTTP_418_MIN_DELAY_SECONDS = 120.0
_BAN_COOLDOWN_LOCK = threading.Lock()
_BAN_COOLDOWN_UNTIL = 0.0


def parse_retry_after(header_val: Optional[str], now_epoch: Optional[float] = None) -> Optional[float]:
    """
    Parse Retry-After header value into seconds.
    Supports:
    - Numeric seconds string (e.g. '120', '5', '86400')
    - HTTP-date format (e.g. 'Wed, 21 Oct 2026 07:28:00 GMT')
    Returns seconds as float >= 0.0, or None if missing/invalid.
    """
    if not header_val or not isinstance(header_val, str):
        return None
    val = header_val.strip()
    if not val:
        return None

    # 1. Numeric seconds
    try:
        seconds = float(val)
        if math.isfinite(seconds) and seconds >= 0:
            return float(seconds)
    except (ValueError, TypeError):
        pass

    # 2. HTTP-date format (RFC 2822 / RFC 7231)
    try:
        dt = email.utils.parsedate_to_datetime(val)
        if dt is not None:
            now_dt = datetime.fromtimestamp(now_epoch if now_epoch is not None else time.time(), tz=timezone.utc)
            delta = (dt - now_dt).total_seconds()
            return max(0.0, float(delta))
    except Exception:
        pass

    return None


def get_ban_cooldown_remaining(now_epoch: Optional[float] = None) -> float:
    """Return remaining process-level cooldown seconds under active IP ban."""
    now = now_epoch if now_epoch is not None else time.time()
    with _BAN_COOLDOWN_LOCK:
        if _BAN_COOLDOWN_UNTIL > now:
            return _BAN_COOLDOWN_UNTIL - now
        return 0.0


def set_ban_cooldown(cooldown_seconds: float, now_epoch: Optional[float] = None) -> float:
    """
    Set process-level IP ban cooldown.
    Ensures that retry exhaustion or subsequent calls do not clear an active ban window.
    """
    now = now_epoch if now_epoch is not None else time.time()
    target = now + max(0.0, float(cooldown_seconds))
    with _BAN_COOLDOWN_LOCK:
        global _BAN_COOLDOWN_UNTIL
        if target > _BAN_COOLDOWN_UNTIL:
            _BAN_COOLDOWN_UNTIL = target
        return max(0.0, _BAN_COOLDOWN_UNTIL - now)


def reset_ban_cooldown_for_testing() -> None:
    """Reset the shared process-level ban cooldown for test isolation."""
    with _BAN_COOLDOWN_LOCK:
        global _BAN_COOLDOWN_UNTIL
        _BAN_COOLDOWN_UNTIL = 0.0


class FundingPollerLockError(Exception):
    """Base exception for funding poller advisory lock errors."""
    pass


class FundingPollerLockConnectionError(FundingPollerLockError):
    """Raised when dedicated database connection initialization fails for advisory lock."""
    pass


class FundingPollerLockAuthenticationError(FundingPollerLockConnectionError):
    """Raised when dedicated database authentication fails for advisory lock."""
    pass


class FundingPollerLockQueryError(FundingPollerLockError):
    """Raised when advisory lock query encounters an unexpected database error."""
    pass


class FundingPollerLock:
    """
    MariaDB advisory lock: 'signal_bot_funding_poller'.
    - Non-blocking acquisition using GET_LOCK(name, 0).
    - Dedicated unpooled database connection.
    - Uses existing least-privilege funding collector credentials.
    - Never holds the lock on a connection returned to a shared pool.
    - Releases lock in guaranteed cleanup.
    - Safely handles connection loss and lock-release errors.
    """
    LOCK_NAME = "signal_bot_funding_poller"

    def __init__(
        self,
        config: Optional[Any] = None,
        config_path: Optional[str] = None,
        connection: Optional[Any] = None,
    ):
        self._config = config
        self._config_path = config_path
        self._conn = connection
        self._owned_connection = connection is None
        self._acquired = False

    def acquire(self) -> bool:
        """
        Attempt non-blocking acquisition of signal_bot_funding_poller.
        Returns:
            True if acquired.
            False if held by another process (genuine lock contention).
        Raises:
            FundingPollerLockAuthenticationError: on credential or access-denied failures.
            FundingPollerLockConnectionError: on database connection initialization failures.
            FundingPollerLockQueryError: on unexpected database query failures.
        """
        if self._conn is None:
            try:
                cfg = self._config or load_funding_collector_config(env_path=self._config_path)
                import mysql.connector
                self._conn = mysql.connector.connect(**cfg.to_dict())
                self._owned_connection = True
            except Exception as e:
                self._acquired = False
                self.release()
                err_str = str(e)
                logger.error(f"Failed to connect to MariaDB for advisory lock '{self.LOCK_NAME}': {err_str}")
                if (
                    isinstance(e, (ValueError, PermissionError))
                    or (hasattr(e, "errno") and e.errno == 1045)
                    or "access denied" in err_str.lower()
                    or "credential" in err_str.lower()
                    or "password" in err_str.lower()
                ):
                    raise FundingPollerLockAuthenticationError(
                        f"Authentication failure connecting to MariaDB for advisory lock: {err_str}"
                    ) from e
                raise FundingPollerLockConnectionError(
                    f"Connection failure connecting to MariaDB for advisory lock: {err_str}"
                ) from e

        cur = None
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT GET_LOCK(%s, 0)", (self.LOCK_NAME,))
            res = cur.fetchone()
            cur.close()
            cur = None
        except Exception as e:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            self._acquired = False
            self.release()
            err_str = str(e)
            logger.error(f"Unexpected database exception executing GET_LOCK for '{self.LOCK_NAME}': {err_str}")
            raise FundingPollerLockQueryError(
                f"Unexpected database error executing advisory lock query: {err_str}"
            ) from e

        if res is not None and res[0] == 1:
            self._acquired = True
            logger.info(f"Acquired MariaDB advisory lock '{self.LOCK_NAME}'.")
            return True
        else:
            self._acquired = False
            logger.warning(
                f"MariaDB advisory lock '{self.LOCK_NAME}' is held by another process (result={res})."
            )
            self.release()
            return False

    def release(self) -> None:
        """
        Release advisory lock and safely close the dedicated unpooled connection.
        """
        if self._conn is not None:
            if self._acquired:
                try:
                    cur = self._conn.cursor()
                    cur.execute("SELECT RELEASE_LOCK(%s)", (self.LOCK_NAME,))
                    cur.fetchall()
                    cur.close()
                    logger.info(f"Released MariaDB advisory lock '{self.LOCK_NAME}'.")
                except Exception as e:
                    logger.warning(f"Error releasing MariaDB advisory lock '{self.LOCK_NAME}': {e}")
                finally:
                    self._acquired = False

            if self._owned_connection:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.warning(f"Error closing dedicated advisory lock connection: {e}")
                self._conn = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def clean_decimal(value: Any) -> Optional[str]:
    """
    Validate and clean numeric/decimal values for MySQL/MariaDB DECIMAL columns.
    Converts None, empty strings, whitespace, or invalid non-finite numbers to None.
    Returns cleaned string representation of the decimal value.
    """
    if value is None:
        return None
    if isinstance(value, str):
        val_str = value.strip()
        if not val_str:
            return None
    else:
        val_str = str(value).strip()

    try:
        d = decimal.Decimal(val_str)
        if not d.is_finite():
            return None
        return str(d)
    except (decimal.InvalidOperation, TypeError, ValueError):
        return None


def parse_funding_item(item: Dict[str, Any]) -> Tuple[str, int, Optional[str], Optional[str], str, Optional[int]]:
    """
    Parse and validate a single Binance fundingRate JSON item into a DB row tuple:
    (symbol, event_time, mark_price, index_price, funding_rate, next_funding_time)

    Raises ValueError if symbol, fundingTime, or fundingRate are missing or invalid.
    """
    if not isinstance(item, dict):
        raise ValueError(f"Malformed funding item: expected dict, got {type(item).__name__}")

    symbol_raw = item.get("symbol")
    if not symbol_raw or not isinstance(symbol_raw, str):
        raise ValueError(f"Missing or invalid symbol in funding item: {item}")
    symbol = symbol_raw.strip().upper()

    funding_time_raw = item.get("fundingTime")
    if funding_time_raw is None:
        raise ValueError(f"Missing fundingTime in funding item: {item}")
    try:
        funding_time = int(funding_time_raw)
        if funding_time <= 0:
            raise ValueError(f"Non-positive fundingTime: {funding_time}")
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid fundingTime '{funding_time_raw}': {e}")

    funding_rate_raw = item.get("fundingRate")
    if funding_rate_raw is None:
        raise ValueError(f"Missing fundingRate in funding item: {item}")
    funding_rate = clean_decimal(funding_rate_raw)
    if funding_rate is None:
        raise ValueError(f"Invalid fundingRate value '{funding_rate_raw}' in item: {item}")

    mark_price = clean_decimal(item.get("markPrice"))
    index_price = clean_decimal(item.get("indexPrice"))

    next_funding_time = None
    if item.get("nextFundingTime") is not None:
        try:
            next_funding_time = int(item["nextFundingTime"])
        except (ValueError, TypeError):
            next_funding_time = None

    return (symbol, funding_time, mark_price, index_price, funding_rate, next_funding_time)


def fetch_funding_page(
    session: requests.Session,
    symbol: str,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_url: Optional[str] = None,
    backoff_base: float = 1.0,
    jitter_func: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch a single page of funding rates from Binance public REST API with bounded exponential backoff.
    Handles HTTP 418 (IP ban), HTTP 429 (Retry-After), HTTP 5xx, timeouts, and network disconnects.
    """
    # 1. Enforce shared process-level IP ban cooldown (cannot bypass with another symbol or cycle)
    remaining = get_ban_cooldown_remaining()
    if remaining > 0:
        logger.warning(
            f"Active IP ban/cooldown window in effect ({remaining:.1f}s remaining). "
            f"Enforcing shared cooldown before request for {symbol}."
        )
        time.sleep(remaining)

    url = base_url or FUTURES_FUNDING_HISTORY_URL
    params: Dict[str, Any] = {
        "symbol": symbol.strip().upper(),
        "limit": min(max(1, limit), DEFAULT_LIMIT),
    }
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)

    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=15)

            # 2. IP ban handling (HTTP 418) - never enter generic non-retryable 4xx path
            if resp.status_code == 418:
                ra_header = resp.headers.get("Retry-After")
                parsed_ra = parse_retry_after(ra_header)
                ban_duration = HTTP_418_MIN_DELAY_SECONDS
                if parsed_ra is not None and parsed_ra > HTTP_418_MIN_DELAY_SECONDS:
                    ban_duration = parsed_ra

                jitter = jitter_func() if jitter_func is not None else random.uniform(0.1, 1.0)
                wait = ban_duration + max(0.0, jitter)

                # Record cooldown at process level; retry exhaustion must not clear this
                set_ban_cooldown(wait)
                logger.error(
                    f"HIGH-SEVERITY WARNING: Binance HTTP 418 IP ban detected for {symbol}! "
                    f"Enforcing mandatory ban cooldown of {ban_duration:.1f}s (wait={wait:.2f}s, attempt {attempt + 1}/{max_retries})."
                )

                # Avoid sleeping unnecessarily after the final exhausted attempt
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
                else:
                    break

            # 3. Rate limit handling (HTTP 429) - retains existing Retry-After behavior
            if resp.status_code == 429:
                ra_header = resp.headers.get("Retry-After")
                parsed_ra = parse_retry_after(ra_header)
                if parsed_ra is not None:
                    retry_after = int(parsed_ra) if float(parsed_ra).is_integer() else max(1.0, parsed_ra)
                else:
                    retry_after = 5

                logger.warning(
                    f"Rate limited (HTTP 429) for {symbol}. Sleeping {retry_after}s (attempt {attempt + 1}/{max_retries})"
                )

                if attempt < max_retries - 1:
                    time.sleep(retry_after)
                    continue
                else:
                    break

            # 4. Server error handling (HTTP 5xx)
            if 500 <= resp.status_code < 600:
                wait = min(backoff_base * (2 ** attempt), 30.0)
                logger.warning(
                    f"Server error {resp.status_code} for {symbol}. Retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Unexpected response structure from Binance: expected list, got {type(data).__name__}")
            return data

        except (requests.Timeout, requests.ConnectionError) as e:
            wait = min(backoff_base * (2 ** attempt), 30.0)
            logger.warning(
                f"Network error ({type(e).__name__}) for {symbol}: {e}. Retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(wait)
        except requests.HTTPError as e:
            # Client errors (4xx other than 418 and 429) should fail fast
            logger.error(f"HTTP client error {resp.status_code} for {symbol}: {e}")
            raise
        except ValueError as e:
            logger.error(f"Response format/JSON decoding error for {symbol}: {e}")
            raise

    raise RuntimeError(f"Failed to fetch funding rates for {symbol} after {max_retries} retries")


def poll_symbol_incremental(
    session: requests.Session,
    symbol: str,
    end_ms: Optional[int] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    pool: Any = None,
    limit: int = DEFAULT_LIMIT,
    base_url: Optional[str] = None,
) -> int:
    """
    Incrementally poll funding rates for a single symbol up to end_ms.
    Watermark is determined by querying the latest event_time in funding_rate.
    If no watermark exists, starts from (end_ms - lookback_days).
    Paginates if necessary until reaching the current watermark.
    Returns total rows inserted/updated.
    """
    now_ms = int(time.time() * 1000)
    target_end_ms = end_ms if end_ms is not None else now_ms

    latest_time = get_latest_funding_time(symbol, pool=pool)
    if latest_time is not None:
        start_ms = latest_time + 1
    else:
        start_ms = target_end_ms - int(lookback_days * 86400 * 1000)

    if start_ms >= target_end_ms:
        logger.debug(f"{symbol}: already up to date (watermark {latest_time} >= target {target_end_ms})")
        return 0

    cursor = start_ms
    total_inserted = 0

    while cursor < target_end_ms:
        raw_items = fetch_funding_page(
            session=session,
            symbol=symbol,
            start_ms=cursor,
            end_ms=target_end_ms,
            limit=limit,
            base_url=base_url,
        )
        if not raw_items:
            break

        rows = []
        for item in raw_items:
            try:
                row = parse_funding_item(item)
                rows.append(row)
            except ValueError as e:
                logger.warning(f"Skipping malformed funding item for {symbol}: {e}")

        if rows:
            # Sort rows by event_time to ensure monotonic processing
            rows.sort(key=lambda r: r[1])
            upsert_funding(rows, pool=pool)
            total_inserted += len(rows)

        # Pagination advancement based on highest fundingTime in the batch
        highest_time = max(int(item["fundingTime"]) for item in raw_items if "fundingTime" in item)
        next_cursor = highest_time + 1
        if next_cursor <= cursor:
            # Prevent infinite loop if exchange returns static or non-advancing timestamp
            break
        cursor = next_cursor

        if len(raw_items) < limit:
            break

    logger.info(f"{symbol}: incremental funding poll complete (+{total_inserted} rows)")
    return total_inserted


def poll_all_symbols(
    symbols: Optional[List[str]] = None,
    pool: Any = None,
    session: Optional[requests.Session] = None,
    end_ms: Optional[int] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    base_url: Optional[str] = None,
    acquire_lock: bool = True,
    lock: Optional[FundingPollerLock] = None,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Poll funding rates for all target symbols in a single cycle.
    Updates collector heartbeat.
    Acquires MariaDB advisory lock 'signal_bot_funding_poller' using a dedicated unpooled connection.
    If another poller owns the lock, skips the cycle cleanly.
    """
    target_symbols = symbols if symbols is not None else SYMBOLS
    managed_session = session is None
    req_session = session or requests.Session()

    results: Dict[str, Any] = {
        "symbols_total": len(target_symbols),
        "symbols_ok": 0,
        "symbols_failed": 0,
        "rows_inserted": 0,
        "errors": {},
    }

    lock_ctx: Optional[FundingPollerLock] = None
    if acquire_lock:
        lock_ctx = lock if lock is not None else FundingPollerLock(config_path=config_path)
        try:
            acquired = lock_ctx.acquire()
        except FundingPollerLockError as lock_err:
            logger.error(f"Funding poller advisory lock error: {lock_err}")
            results["error"] = str(lock_err)
            results["lock_error"] = type(lock_err).__name__
            try:
                heartbeat("funding_poller", status="error", detail=f"Lock error: {str(lock_err)[:240]}", pool=pool)
            except Exception as hb_err:
                logger.warning(f"Failed to record error heartbeat: {hb_err}")
            raise
        if not acquired:
            logger.warning(
                "MariaDB advisory lock 'signal_bot_funding_poller' is currently held by another process; "
                "skipping funding poller cycle cleanly."
            )
            results["skipped"] = True
            results["reason"] = "advisory_lock_held"
            return results

    try:
        for symbol in target_symbols:
            try:
                inserted = poll_symbol_incremental(
                    session=req_session,
                    symbol=symbol,
                    end_ms=end_ms,
                    lookback_days=lookback_days,
                    pool=pool,
                    base_url=base_url,
                )
                results["symbols_ok"] += 1
                results["rows_inserted"] += inserted
            except Exception as e:
                results["symbols_failed"] += 1
                results["errors"][symbol] = str(e)
                logger.error(f"Error polling funding rates for {symbol}: {e}")

        if results["symbols_failed"] == 0:
            status = "ok"
            detail = f"{results['symbols_ok']}/{results['symbols_total']} symbols ok (+{results['rows_inserted']} rows)"
        elif results["symbols_ok"] > 0:
            status = "warning"
            detail = f"Partial: {results['symbols_ok']} ok, {results['symbols_failed']} failed"
        else:
            status = "error"
            detail = f"All {results['symbols_total']} symbols failed"

        heartbeat("funding_poller", status=status, detail=detail, pool=pool)

    finally:
        if managed_session:
            req_session.close()
        if lock_ctx is not None:
            lock_ctx.release()

    return results


def run_standalone_loop(
    symbols: Optional[List[str]] = None,
    poll_interval: Optional[int] = None,
    config_path: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    pool: Any = None,
) -> None:
    """
    Synchronous standalone polling loop for dedicated service execution.
    - Runs in its own process without a shared asyncio event loop.
    - Handles SIGTERM and SIGINT signals gracefully.
    - Uses dedicated database connection pool with fail-closed authentication.
    - Cleans up database connections and HTTP sessions on shutdown.
    """
    interval = poll_interval or FUNDING_POLL_INTERVAL_SECONDS
    target_symbols = symbols if symbols is not None else SYMBOLS
    shutdown_flag = stop_event or threading.Event()

    def _handle_signal(signum, _frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(f"Received {sig_name}; initiating graceful shutdown of funding collector...")
        shutdown_flag.set()

    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except (ValueError, AttributeError):
        pass

    active_pool = pool
    if active_pool is None:
        cfg = load_funding_collector_config(env_path=config_path)
        active_pool = get_funding_pool(config=cfg)

    logger.info(
        f"Starting standalone Binance Futures funding rate REST collector "
        f"(symbols={target_symbols}, interval={interval}s)..."
    )

    with requests.Session() as session:
        while not shutdown_flag.is_set():
            start_t = time.time()
            try:
                results = poll_all_symbols(
                    symbols=target_symbols,
                    pool=active_pool,
                    session=session,
                    config_path=config_path,
                )
                if results.get("skipped"):
                    logger.info("Funding poll cycle skipped: MariaDB advisory lock held by another process.")
                else:
                    logger.info(
                        f"Poll cycle completed: {results['symbols_ok']}/{results['symbols_total']} symbols ok, "
                        f"+{results['rows_inserted']} rows"
                    )
            except Exception as e:
                logger.error(f"Unhandled error in funding poller cycle: {e}")
                try:
                    heartbeat("funding_poller", status="error", detail=str(e)[:255], pool=active_pool)
                except Exception as hb_err:
                    logger.error(f"Failed to record error heartbeat: {hb_err}")

            elapsed = time.time() - start_t
            sleep_time = max(0.0, float(interval) - elapsed)
            if shutdown_flag.wait(timeout=sleep_time):
                break

    logger.info("Funding collector standalone loop stopped cleanly.")


async def run(
    symbols: Optional[List[str]] = None,
    poll_interval: Optional[int] = None,
    pool: Any = None,
    config_path: Optional[str] = None,
):
    """
    Continuous asyncio poller loop for integration into daemon runners.
    Executes polling calls via asyncio.to_thread to prevent blocking the
    shared asyncio event loop during HTTP network calls or database operations.
    """
    interval = poll_interval or FUNDING_POLL_INTERVAL_SECONDS
    logger.info(f"Starting funding rate REST poller coroutine (interval={interval}s)...")

    active_pool = pool
    if active_pool is None:
        cfg = load_funding_collector_config(env_path=config_path)
        active_pool = get_funding_pool(config=cfg)

    with requests.Session() as session:
        while True:
            start_t = time.time()
            try:
                await asyncio.to_thread(
                    poll_all_symbols,
                    symbols=symbols,
                    pool=active_pool,
                    session=session,
                    config_path=config_path,
                )
            except Exception as e:
                logger.error(f"Unhandled exception in funding poller loop: {e}")
                try:
                    await asyncio.to_thread(heartbeat, "funding_poller", status="error", detail=str(e)[:255], pool=active_pool)
                except Exception:
                    pass

            elapsed = time.time() - start_t
            sleep_time = max(0.0, float(interval) - elapsed)
            await asyncio.sleep(sleep_time)


def main():
    parser = argparse.ArgumentParser(description="Binance Futures Funding Rate REST Collector")
    parser.add_argument("--symbol", help="Optional specific symbol (e.g. BTCUSDT)")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Lookback days if no watermark")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=FUNDING_POLL_INTERVAL_SECONDS, help="Poll interval seconds")
    parser.add_argument("--config", help="Path to dedicated funding collector environment file")
    parser.add_argument("--async-loop", action="store_true", help="Run using asyncio loop instead of standalone synchronous loop")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS

    if args.once:
        logger.info(f"Running one-shot funding poll for symbols: {symbols}")
        cfg = load_funding_collector_config(env_path=args.config)
        pool = get_funding_pool(config=cfg)
        results = poll_all_symbols(
            symbols=symbols,
            pool=pool,
            lookback_days=args.lookback_days,
            config_path=args.config,
        )
        if results.get("skipped"):
            logger.info(f"One-shot execution skipped cleanly: {results}")
        else:
            logger.info(f"Execution finished: {results}")
    elif args.async_loop:
        cfg = load_funding_collector_config(env_path=args.config)
        pool = get_funding_pool(config=cfg)
        asyncio.run(run(symbols=symbols, poll_interval=args.interval, pool=pool, config_path=args.config))
    else:
        run_standalone_loop(symbols=symbols, poll_interval=args.interval, config_path=args.config)


if __name__ == "__main__":
    main()
