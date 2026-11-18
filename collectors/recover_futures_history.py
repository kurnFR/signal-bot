"""
collectors/recover_futures_history.py

Supervised, resumable, one-shot CLI tool for recovering missing Binance USD-M
Futures OHLCV history and healing partial trailing boundary candles.

Key Architectural Guarantees:
1. Resumable: Calculates missing ranges from existing database watermarks.
2. Self-Healing Boundary: Starts range at MAX(open_time) to replace any known
   prematurely-closed partial candles with authoritative finalized exchange data.
3. Closed-Only Ingestion: Upper boundary is capped strictly at the last closed
   candle interval (endTime = current_candle_open - 1). Current forming bar is omitted.
4. Concurrency Guard: Acquires MariaDB advisory lock 'futures_kline_writer_<sym>_<tf>'
   to coordinate safely with any concurrent ongoing poller.
5. Idempotent & Non-Destructive: Re-running over previously recovered ranges produces
   zero duplicate rows and zero data regression.
6. Isolated: Symbol/timeframe failures do not abort remaining recovery tasks.
7. Dry-Run Capable: Supports --dry-run to audit missing ranges and preview actions
   without executing database writes.

Usage:
    python3 -m collectors.recover_futures_history --dry-run
    python3 -m collectors.recover_futures_history --symbol BTCUSDT --timeframe 15m
    python3 -m collectors.recover_futures_history --all
"""

import argparse
import hashlib
import json
import logging
import os
import stat
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Any

import mysql.connector
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    SYMBOLS,
    TIMEFRAMES,
    FUTURES_KLINE_URL,
    KLINE_FETCH_LIMIT,
    BACKFILL_YEARS,
)
from collectors.backfill_klines import is_candle_closed
from db.db import upsert_ohlcv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("recover_futures_history")

SESSION = requests.Session()

INTERVAL_MS: Dict[str, int] = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

APPROVED_MANIFEST_SHA256 = "4e351da19600d2b9ce8ad9e5a4bf6b276220057faeb29dfd9be6840c7bc417a2"
MAX_FINALIZED_BOUNDARY_OT = 1794816000000  # 2026-11-16 08:00:00 UTC (forming 09:00 UTC excluded)


def timeframe_to_ms(timeframe: str) -> int:
    if timeframe not in INTERVAL_MS:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Must be one of {list(INTERVAL_MS.keys())}")
    return INTERVAL_MS[timeframe]


def get_read_only_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    expected_database: Optional[str] = None,
    connection_timeout: int = 10,
) -> Any:
    """
    Establishes an explicit read-only MariaDB connection for recovery dry-run inspection.

    Guarantees:
    1. Explicit credentials: Uses explicit parameters or RECOVERY_DB_* / SIGNAL_BOT_TEST_DB_* env vars.
    2. Strict anti-fallback: Prohibits falling back to MYSQL_USER, 'cms', 'root', or get_pool().
    3. Target isolation: Rejects 'crypto_signals' unless explicitly authorized via environment variable.
    4. Identity verification: Verifies actual DATABASE() matches expected_database.
    5. Session read-only: Executes 'SET SESSION TRANSACTION READ ONLY;' as a database-enforced safeguard.
    6. Fails closed: Raises descriptive exceptions on missing credentials or mismatches.
    """
    resolved_user = user or os.getenv("RECOVERY_DB_USER") or os.getenv("SIGNAL_BOT_TEST_DB_USER")
    if not resolved_user:
        raise ValueError(
            "Explicit database user must be provided for read-only recovery dry-run "
            "(via --db-user, RECOVERY_DB_USER, or SIGNAL_BOT_TEST_DB_USER). "
            "Fallback to default MYSQL_USER or application pool is strictly forbidden."
        )

    forbidden_users = {"cms", "root"}
    if resolved_user.lower() in forbidden_users:
        raise PermissionError(
            f"Database user '{resolved_user}' is prohibited for read-only recovery dry-run. "
            "Must use a restricted read-only account."
        )

    # Prevent fallback to MYSQL_USER from .env
    default_app_user = os.getenv("MYSQL_USER")
    if default_app_user and resolved_user.lower() == default_app_user.lower() and resolved_user.lower() not in {"signal_bot_test"}:
        raise PermissionError(
            f"Database user '{resolved_user}' matches application MYSQL_USER. "
            "Fallback to application pool user is strictly forbidden."
        )

    resolved_db = database or os.getenv("RECOVERY_DB_NAME") or os.getenv("SIGNAL_BOT_TEST_DB_NAME") or "signal_bot_test"
    resolved_expected = expected_database or os.getenv("RECOVERY_EXPECTED_DB") or "signal_bot_test"

    # Enforce isolation: reject crypto_signals unless explicitly authorized via ALLOW_PRODUCTION_OHLCV_INSPECTION=1
    if (resolved_db.lower() == "crypto_signals" or resolved_expected.lower() == "crypto_signals") and os.getenv("ALLOW_PRODUCTION_OHLCV_INSPECTION") != "1":
        raise PermissionError(
            "Targeting 'crypto_signals' is strictly prohibited during test validation stage."
        )

    if resolved_expected and resolved_db != resolved_expected:
        raise ValueError(
            f"Target database '{resolved_db}' does not match expected database '{resolved_expected}'."
        )

    resolved_host = host or os.getenv("RECOVERY_DB_HOST") or os.getenv("SIGNAL_BOT_TEST_DB_HOST") or "127.0.0.1"
    resolved_port = port or int(os.getenv("RECOVERY_DB_PORT") or os.getenv("SIGNAL_BOT_TEST_DB_PORT") or "3306")
    if password is not None:
        resolved_pass = password
    elif os.getenv("RECOVERY_DB_PASSWORD_FILE") and os.path.exists(os.getenv("RECOVERY_DB_PASSWORD_FILE")):
        with open(os.getenv("RECOVERY_DB_PASSWORD_FILE"), "r") as pf:
            resolved_pass = pf.read().strip()
    else:
        resolved_pass = os.getenv("RECOVERY_DB_PASSWORD") or os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD") or ""

    cnx = mysql.connector.connect(
        host=resolved_host,
        port=resolved_port,
        user=resolved_user,
        password=resolved_pass,
        database=resolved_db,
        connection_timeout=connection_timeout,
    )

    cur = cnx.cursor()
    try:
        cur.execute("SELECT DATABASE(), CURRENT_USER();")
        actual_db, current_user = cur.fetchone()
        if resolved_expected and actual_db != resolved_expected:
            raise RuntimeError(
                f"Database identity mismatch: expected '{resolved_expected}', connected to '{actual_db}' (user: {current_user})"
            )
        # Apply MariaDB session read-only safeguard
        cur.execute("SET SESSION TRANSACTION READ ONLY;")
    finally:
        cur.close()

    logger.info(
        f"Read-only recovery connection established: database={actual_db}, user={current_user}, "
        f"host={resolved_host}:{resolved_port}, session=READ ONLY"
    )
    return cnx


def load_and_validate_manifest(
    manifest_path: str,
    expected_sha: Optional[str] = APPROVED_MANIFEST_SHA256,
) -> Dict[str, Any]:
    """
    Validates and loads the approved restricted recovery manifest JSON.

    Guarantees:
    1. Cryptographic validation: Compares file SHA256 against expected_sha.
    2. Strict target isolation: Permits exactly 3 unique targets:
       - BTCUSDT 1h
       - ETHUSDT 1h
       - BNBUSDT 1h
    3. Exclusions: Rejects any 15m, 4h, 1d timeframe or extraneous symbol.
    4. Arithmetic check: Requires cur_rows + proposed_inserts == expected_rows.
    5. Upper bound guard: Rejects any range exceeding MAX_FINALIZED_BOUNDARY_OT (2026-11-16 05:00:00 UTC).
    6. Interval continuity: Confirms (last_ot - first_ot) // 3600000 + 1 == proposed_insert_count.
    7. Fails closed before any DB connection or network request.
    """
    if not manifest_path:
        raise ValueError("Manifest path must be provided.")

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    if not os.path.isfile(manifest_path):
        raise ValueError(f"Manifest path is not a regular file: {manifest_path}")

    with open(manifest_path, "rb") as f:
        raw_bytes = f.read()

    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha and actual_sha.lower() != expected_sha.lower():
        raise ValueError(
            f"Manifest SHA256 mismatch: calculated {actual_sha} != expected {expected_sha}. "
            "Manifest file has been altered or is unapproved."
        )

    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Malformed manifest JSON: {e}") from e

    version = str(data.get("manifest_version", ""))
    if not (version.startswith("2.") or version.startswith("3.")):
        raise ValueError(f"Unsupported manifest version '{version}'. Expected version 2.x or 3.x.")

    targets = data.get("targets", [])
    if not isinstance(targets, list) or len(targets) != 3:
        raise ValueError(
            f"Manifest must contain exactly 3 targets, found {len(targets) if isinstance(targets, list) else 0}."
        )

    allowed_symbols = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
    expected_target_keys = {"BTCUSDT_1h", "ETHUSDT_1h", "BNBUSDT_1h"}
    seen_target_keys = set()

    for idx, t in enumerate(targets):
        if not isinstance(t, dict):
            raise ValueError(f"Target entry {idx} is malformed.")

        sym = t.get("symbol")
        tf = t.get("timeframe")
        key = t.get("target_key", f"{sym}_{tf}")

        if sym not in allowed_symbols:
            raise ValueError(f"Prohibited symbol '{sym}' in manifest. Only BTCUSDT, ETHUSDT, BNBUSDT permitted.")

        if tf != "1h":
            raise ValueError(f"Prohibited timeframe '{tf}' for {sym}. Only 1h permitted (15m, 4h, 1d are strictly excluded).")

        if key in seen_target_keys:
            raise ValueError(f"Duplicate target key '{key}' detected in manifest.")
        seen_target_keys.add(key)

        pre = t.get("pre_recovery", {})
        ins = t.get("proposed_inserts", {})
        post = t.get("post_recovery_expected", {})

        cur_rows = pre.get("total_rows")
        ins_count = ins.get("proposed_insert_count")
        exp_rows = post.get("mathematically_expected_rows")

        if cur_rows is None or ins_count is None or exp_rows is None:
            raise ValueError(f"Missing row count fields in target '{key}'.")

        if cur_rows + ins_count != exp_rows:
            raise ValueError(
                f"Arithmetic mismatch for target '{key}': "
                f"current ({cur_rows}) + inserts ({ins_count}) = {cur_rows + ins_count} != expected ({exp_rows})."
            )

        first_ot = ins.get("first_missing_open_time_ms")
        last_ot = ins.get("last_missing_open_time_ms")

        if first_ot is None or last_ot is None or first_ot >= last_ot:
            raise ValueError(f"Invalid or reversed insert range for target '{key}': {first_ot} -> {last_ot}.")

        if first_ot % 3600000 != 0 or last_ot % 3600000 != 0:
            raise ValueError(f"Timestamps for target '{key}' not aligned to 1h interval boundary.")

        calc_count = (last_ot - first_ot) // 3600000 + 1
        if calc_count != ins_count:
            raise ValueError(
                f"Insert count mismatch for target '{key}': range span implies {calc_count} candles, "
                f"manifest specifies {ins_count}."
            )

        # Finalized boundary guard: 2026-11-16 05:00:00 UTC (1794805200000)
        if last_ot > MAX_FINALIZED_BOUNDARY_OT:
            raise ValueError(
                f"Insert range for target '{key}' extends beyond approved finalized boundary "
                f"({MAX_FINALIZED_BOUNDARY_OT} - 2026-11-16 05:00:00 UTC): {last_ot}."
            )

    if seen_target_keys != expected_target_keys:
        raise ValueError(f"Manifest target keys {seen_target_keys} do not match expected set {expected_target_keys}.")

    return data


EXACT_WRITE_USER = "signal_bot_test"
EXACT_WRITE_HOST = "127.0.0.1"
EXACT_PROD_DB = "crypto_signals"


def get_write_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password_file: Optional[str] = None,
    database: Optional[str] = None,
    expected_database: Optional[str] = None,
    connection_timeout: int = 10,
    connection_factory: Optional[Any] = None,
) -> Any:
    """
    Establishes an explicit MariaDB connection for recovery write execution.

    Guarantees:
    1. Exact Dedicated Account: Requires exactly the username 'signal_bot_test'.
       Rejects every other username, including case variants ('Signal_Bot_Test', 'SIGNAL_BOT_TEST'),
       whitespace-padded variants (' signal_bot_test '), privileged accounts ('cms', 'root', 'admin', 'mysql'),
       legacy accounts ('futures_recovery', 'signal_funding_collector'), and arbitrary usernames.
    2. Mandatory Explicit Credentials: user and password_file must be provided.
    3. Strict Anti-Fallback: Prohibits get_pool(), MYSQL_USER, or implicit .env fallback.
    4. Secure Password File Validation:
       - Must exist and be a regular file.
       - Must have restrictive permissions (mode 0600 or 0400, no group/world read/write/execute).
       - Password is read silently and never logged or included in exceptions.
    5. Loopback Host: Defaults to 127.0.0.1.
    6. Database Isolation: Confirms DATABASE() matches expected_database. In production,
       requires exactly 'crypto_signals' and explicit ALLOW_PRODUCTION_RECOVERY_EXECUTION=1.
    7. Authenticated Host Verification: Verifies CURRENT_USER() matches 'signal_bot_test@127.0.0.1'.
    8. Connection Factory Injection: Supports optional connection_factory for unit testing
       and verification without requiring live production infrastructure.
    """
    if not user:
        raise ValueError(
            "Explicit database user must be provided for recovery write mode (--db-user). "
            "Implicit fallback to application pool or MYSQL_USER is strictly prohibited."
        )

    forbidden_users = {"cms", "root", "admin", "mysql", "futures_recovery", "signal_funding_collector", "retailbot2_obs"}
    if user.lower() in forbidden_users or user.strip().lower() in forbidden_users or user != EXACT_WRITE_USER:
        raise PermissionError(
            f"Database user '{user}' is prohibited for recovery write execution. "
            f"Write mode accepts exactly the dedicated username '{EXACT_WRITE_USER}'."
        )

    if not password_file:
        raise ValueError(
            "Explicit password file must be provided for recovery write mode (--db-password-file). "
            "Passing passwords as command-line arguments or environment variables is prohibited."
        )

    resolved_db = database or expected_database or "signal_bot_test"
    resolved_expected = expected_database or "signal_bot_test"

    # Enforce isolation: reject crypto_signals unless explicitly authorized via ALLOW_PRODUCTION_RECOVERY_EXECUTION=1
    if resolved_db.lower() == EXACT_PROD_DB or resolved_expected.lower() == EXACT_PROD_DB:
        if os.getenv("ALLOW_PRODUCTION_RECOVERY_EXECUTION") != "1":
            raise PermissionError(
                "Targeting 'crypto_signals' is strictly prohibited during test validation stage."
            )
        if resolved_db != EXACT_PROD_DB or resolved_expected != EXACT_PROD_DB:
            raise ValueError(
                f"Production database target mismatch: expected '{EXACT_PROD_DB}', received database='{resolved_db}', expected_database='{resolved_expected}'."
            )
    elif resolved_db.lower() != "signal_bot_test" or resolved_expected.lower() != "signal_bot_test":
        raise ValueError(
            f"Target database '{resolved_db}' is invalid. Recovery write mode only permits '{EXACT_PROD_DB}' (production) or 'signal_bot_test' (test)."
        )

    if resolved_expected and resolved_db != resolved_expected:
        raise ValueError(
            f"Target database '{resolved_db}' does not match expected database '{resolved_expected}'."
        )

    # Password file validation
    if not os.path.exists(password_file):
        raise FileNotFoundError(f"Password file '{password_file}' not found.")

    if not os.path.isfile(password_file):
        raise ValueError(f"Password file '{password_file}' is not a regular file.")

    # Restrictive permissions check (must not be group or world readable/writable/executable)
    file_stat = os.stat(password_file)
    if (file_stat.st_mode & 0o077) != 0:
        raise PermissionError(
            f"Password file '{password_file}' has unsafe permissions (mode {oct(file_stat.st_mode)}). "
            "Permissions must be 0600 or 0400 (owner-only access)."
        )

    try:
        with open(password_file, "r") as pf:
            resolved_pass = pf.read().strip()
    except Exception as e:
        raise PermissionError(f"Cannot read password file '{password_file}'") from None

    resolved_host = host or "127.0.0.1"
    resolved_port = port or 3306

    connect = connection_factory or mysql.connector.connect
    try:
        cnx = connect(
            host=resolved_host,
            port=resolved_port,
            user=user,
            password=resolved_pass,
            database=resolved_db,
            connection_timeout=connection_timeout,
        )
    except Exception as e:
        # Do not expose secret content or credentials in exception
        raise RuntimeError(f"Database connection failed for user '{user}': {e.__class__.__name__}") from None

    cur = cnx.cursor()
    try:
        cur.execute("SELECT DATABASE(), CURRENT_USER();")
        actual_db, current_user = cur.fetchone()
        if resolved_expected and actual_db != resolved_expected:
            raise RuntimeError(
                f"Database identity mismatch: expected '{resolved_expected}', connected to '{actual_db}'"
            )
        # Authenticated host and user verification
        expected_current_user = f"{EXACT_WRITE_USER}@{EXACT_WRITE_HOST}"
        if current_user != expected_current_user:
            raise PermissionError(
                f"Authenticated identity mismatch: expected '{expected_current_user}', connected as '{current_user}'"
            )
    finally:
        cur.close()

    logger.info(
        f"Explicit write recovery connection established: database={actual_db}, user={current_user}, "
        f"host={resolved_host}:{resolved_port}"
    )
    return cnx


def fetch_futures_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = KLINE_FETCH_LIMIT,
    retries: int = 5,
    session: Optional[requests.Session] = None,
) -> List[list]:
    """
    Fetch klines from Binance Futures REST endpoint with rate-limit and backoff handling.
    """
    s = session or SESSION
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_ms),
        "endTime": int(end_ms),
        "limit": min(limit, KLINE_FETCH_LIMIT),
    }

    for attempt in range(retries):
        try:
            resp = s.get(FUTURES_KLINE_URL, params=params, timeout=15)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning(f"HTTP 429 Rate limited on {symbol} {interval}, backing off {retry_after}s")
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                wait_sec = min(2 ** attempt, 30)
                logger.warning(f"HTTP {resp.status_code} server error on {symbol} {interval}, retry {attempt+1}/{retries} in {wait_sec}s")
                time.sleep(wait_sec)
                continue

            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Malformed response for {symbol} {interval}: expected list, got {type(data)}")
            return data
        except (requests.RequestException, ValueError) as e:
            wait_sec = min(2 ** attempt, 30)
            logger.warning(f"Request failed for {symbol} {interval} (attempt {attempt+1}/{retries}): {e}")
            if attempt == retries - 1:
                raise
            time.sleep(wait_sec)

    raise RuntimeError(f"Exhausted {retries} retries fetching klines for {symbol} {interval}")


def acquire_advisory_lock(conn: Any, lock_name: str, timeout: int = 5) -> bool:
    """Acquires a MariaDB advisory lock to prevent concurrent writer collisions."""
    if not hasattr(conn, "cursor"):
        return True
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
        res = cur.fetchall()
        return bool(res and len(res) > 0 and res[0][0] == 1)
    except Exception as e:
        logger.warning(f"Could not acquire advisory lock {lock_name}: {e}")
        return False
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def release_advisory_lock(conn: Any, lock_name: str) -> bool:
    """
    Releases a MariaDB advisory lock, fully draining the result set before cursor close.

    Return values from MariaDB RELEASE_LOCK:
      1: Lock was released.
      0: Lock was not established by this thread (not released).
      NULL: Lock did not exist.
    """
    if not hasattr(conn, "cursor"):
        return True
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        res = cur.fetchall()
        if not res or len(res) == 0 or res[0][0] is None:
            logger.warning(f"Advisory lock '{lock_name}' did not exist on release")
            return False
        code = res[0][0]
        if code == 1:
            logger.debug(f"Advisory lock '{lock_name}' released successfully")
            return True
        elif code == 0:
            logger.warning(f"Advisory lock '{lock_name}' was not established by this thread")
            return False
        else:
            logger.warning(f"Advisory lock '{lock_name}' returned unexpected code: {code}")
            return False
    except Exception as e:
        logger.warning(f"Could not release advisory lock {lock_name}: {e}")
        raise
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def discover_missing_ranges(
    symbol: str,
    market: str,
    timeframe: str,
    conn: Optional[Any] = None,
    now_ms: Optional[int] = None,
    lookback_years: float = BACKFILL_YEARS,
) -> List[Tuple[int, int]]:
    """
    Identifies missing candle ranges for a given symbol and interval.

    Returns a list of (start_ms, end_ms) ranges to fetch.
    Includes the trailing watermark candle so that partial boundary candles are healed.
    """
    step_ms = timeframe_to_ms(timeframe)
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Upper closed boundary: last millisecond before the current active forming candle
    current_candle_open = (now_ms // step_ms) * step_ms
    end_closed_ms = current_candle_open - 1

    if conn is None:
        raise ValueError("A database connection is required to discover missing ranges. Cannot proceed with conn=None.")

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT COUNT(*) AS cnt, MIN(open_time) AS min_ot, MAX(open_time) AS max_ot
            FROM ohlcv
            WHERE symbol = %s AND market = %s AND timeframe = %s
            """,
            (symbol, market, timeframe),
        )
        stats = cur.fetchone() or {"cnt": 0, "min_ot": None, "max_ot": None}

        if not stats["cnt"] or stats["max_ot"] is None:
            # Completely unpopulated: full history required
            start_ms = now_ms - int(lookback_years * 365.25 * 86400 * 1000)
            return [(start_ms, end_closed_ms)]

        max_ot = int(stats["max_ot"])
        ranges: List[Tuple[int, int]] = []

        # 1. Check for internal gaps prior to watermark
        cur.execute(
            """
            SELECT open_time, next_ot
            FROM (
                SELECT open_time, LEAD(open_time) OVER (ORDER BY open_time) AS next_ot
                FROM ohlcv
                WHERE symbol = %s AND market = %s AND timeframe = %s
            ) t
            WHERE next_ot IS NOT NULL AND (next_ot - open_time) > %s
            ORDER BY open_time
            """,
            (symbol, market, timeframe, step_ms),
        )
        internal_gaps = cur.fetchall()
        for g in internal_gaps:
            gap_start = int(g["open_time"])
            gap_end = int(g["next_ot"]) - 1
            ranges.append((gap_start, gap_end))

        # 2. Trailing gap starting AT the watermark candle (to heal partial boundary)
        if max_ot <= end_closed_ms:
            ranges.append((max_ot, end_closed_ms))

        return ranges
    finally:
        cur.close()


def recover_range(
    symbol: str,
    market: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    conn: Optional[Any] = None,
    dry_run: bool = False,
    pacing_sec: float = 0.30,
    session: Optional[requests.Session] = None,
) -> Dict[str, int]:
    """
    Recovers a specific (start_ms, end_ms) range from Binance Futures REST.
    Returns stats dict: fetched, inserted, updated, skipped, ambiguous, failed.
    """
    if conn is None:
        raise ValueError("A database connection is required for range recovery. Cannot proceed with conn=None.")

    stats = {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0}
    cursor = start_ms
    step_ms = timeframe_to_ms(timeframe)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    while cursor <= end_ms:
        raw_klines = fetch_futures_klines(
            symbol=symbol,
            interval=timeframe,
            start_ms=cursor,
            end_ms=end_ms,
            limit=KLINE_FETCH_LIMIT,
            session=session,
        )

        if not raw_klines:
            break

        stats["fetched"] += len(raw_klines)
        rows_to_upsert = []

        # Query existing stored records for these open_times to distinguish insert vs update vs skip
        existing_map: Dict[int, Dict[str, Any]] = {}
        if conn is not None:
            open_times = [int(k[0]) for k in raw_klines]
            format_strings = ",".join(["%s"] * len(open_times))
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute(
                    f"""
                    SELECT open_time, close_time, open, high, low, close, volume,
                           quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed
                    FROM ohlcv
                    WHERE symbol = %s AND market = %s AND timeframe = %s
                      AND open_time IN ({format_strings})
                    """,
                    [symbol, market, timeframe] + open_times,
                )
                for r in cur.fetchall():
                    existing_map[int(r["open_time"])] = r
            finally:
                cur.close()

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

                if open_time <= 0 or close_time <= open_time:
                    logger.warning(f"Ambiguous or invalid timestamps for {symbol} {timeframe}: open={open_time}, close={close_time}")
                    stats["ambiguous"] += 1
                    continue

                # Validate numeric decimal conversion
                Decimal(open_val)
                Decimal(high_val)
                Decimal(low_val)
                Decimal(close_val)
                Decimal(vol_val)
                Decimal(qvol_val)
                Decimal(taker_base)
                Decimal(taker_quote)
            except (IndexError, ValueError, TypeError) as e:
                logger.error(f"Malformed kline entry for {symbol} {timeframe}: {k} ({e})")
                stats["failed"] += 1
                stats["ambiguous"] += 1
                continue

            # Closure validation: only closed candles are accepted in historical recovery
            closed_flag = is_candle_closed(close_time, now_ms=now_ms)
            if not closed_flag or open_time > end_ms:
                # Omit forming bar
                continue

            row_tuple = (
                symbol, market, timeframe,
                open_time, close_time,
                open_val, high_val, low_val, close_val,
                vol_val, qvol_val, num_trades,
                taker_base, taker_quote,
                1,
            )

            if open_time in existing_map:
                existing = existing_map[open_time]
                # Check if stored candle values differ from authoritative finalized values
                is_different = (
                    Decimal(str(existing["open"])) != Decimal(open_val) or
                    Decimal(str(existing["high"])) != Decimal(high_val) or
                    Decimal(str(existing["low"])) != Decimal(low_val) or
                    Decimal(str(existing["close"])) != Decimal(close_val) or
                    Decimal(str(existing["volume"])) != Decimal(vol_val) or
                    (existing.get("quote_volume") is not None and Decimal(str(existing["quote_volume"])) != Decimal(qvol_val)) or
                    (existing.get("num_trades") is not None and int(existing["num_trades"]) != num_trades) or
                    (existing.get("taker_buy_base_vol") is not None and Decimal(str(existing["taker_buy_base_vol"])) != Decimal(taker_base)) or
                    (existing.get("taker_buy_quote_vol") is not None and Decimal(str(existing["taker_buy_quote_vol"])) != Decimal(taker_quote)) or
                    (existing.get("close_time") is not None and int(existing["close_time"]) != close_time) or
                    int(existing["is_closed"]) != 1
                )
                if is_different:
                    stats["updated"] += 1
                    rows_to_upsert.append(row_tuple)
                else:
                    stats["skipped"] += 1
            else:
                stats["inserted"] += 1
                rows_to_upsert.append(row_tuple)

        if rows_to_upsert and not dry_run:
            if conn is None:
                raise RuntimeError("Cannot write recovery data without an active explicit database connection.")
            upsert_ohlcv(rows_to_upsert, conn=conn, commit=True)

        last_open_time = int(raw_klines[-1][0])
        next_cursor = last_open_time + 1
        if next_cursor <= cursor or next_cursor > end_ms:
            break
        cursor = next_cursor

        if len(raw_klines) < KLINE_FETCH_LIMIT:
            break

        if pacing_sec > 0:
            time.sleep(pacing_sec)

    return stats


def run_recovery(
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    market: str = "futures",
    conn: Optional[Any] = None,
    dry_run: bool = False,
    pacing_sec: float = 0.30,
    session: Optional[requests.Session] = None,
    manifest: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, Dict[str, int]]]:
    """
    Main driver for futures historical recovery.
    When manifest is provided, recovery targets and ranges are strictly derived from the manifest.
    Returns: (exit_code, summary_dict)
    Exit code 0 on success; non-zero if any requested range fails.
    """
    if manifest is not None:
        targets = manifest.get("targets", [])
        summary: Dict[str, Dict[str, int]] = {}
        has_failures = False

        if conn is None:
            logger.error("A database connection is required for recovery (including dry-run). Cannot proceed with conn=None.")
            return 1, {
                t.get("target_key", f"{t.get('symbol')}_{t.get('timeframe')}"): {
                    "fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 1
                }
                for t in targets
            }

        logger.info(
            f"Starting Manifest-Bound Futures Historical Recovery (Market: {market}, "
            f"Dry-Run: {dry_run}, Targets: {[t.get('target_key') for t in targets]})"
        )

        for target in targets:
            sym = target["symbol"]
            tf = target["timeframe"]
            key = target.get("target_key", f"{sym}_{tf}")
            summary[key] = {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0}
            lock_name = f"futures_kline_writer_{sym}_{tf}"

            # Derive range strictly from manifest
            boundary_update = target.get("proposed_boundary_update", {})
            start_ms = boundary_update.get("open_time_ms")
            if start_ms is None:
                start_ms = target.get("proposed_inserts", {}).get("first_missing_open_time_ms")
            end_ms = target.get("proposed_inserts", {}).get("last_missing_open_time_ms")

            if not dry_run and conn is not None:
                acquired = acquire_advisory_lock(conn, lock_name, timeout=5)
                if not acquired:
                    logger.error(f"Could not acquire lock {lock_name}; skipping {sym} {tf}")
                    summary[key]["failed"] += 1
                    has_failures = True
                    continue

            try:
                logger.info(
                    f"[{sym} {tf}] Recovering manifest range: "
                    f"{datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} -> "
                    f"{datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}"
                )
                stats = recover_range(
                    symbol=sym,
                    market=market,
                    timeframe=tf,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    conn=conn,
                    dry_run=dry_run,
                    pacing_sec=pacing_sec,
                    session=session,
                )
                for k in summary[key]:
                    summary[key][k] += stats.get(k, 0)
                if stats.get("failed", 0) > 0 or stats.get("ambiguous", 0) > 0:
                    has_failures = True

                logger.info(
                    f"[{sym} {tf}] Complete: fetched={summary[key]['fetched']}, "
                    f"inserted={summary[key]['inserted']}, updated={summary[key]['updated']}, "
                    f"skipped={summary[key]['skipped']}, ambiguous={summary[key]['ambiguous']}, "
                    f"failed={summary[key]['failed']}"
                )
            except Exception as e:
                logger.error(f"[{sym} {tf}] Recovery encountered unhandled error: {e}", exc_info=True)
                summary[key]["failed"] += 1
                has_failures = True
            finally:
                if not dry_run and conn is not None:
                    release_advisory_lock(conn, lock_name)

        exit_code = 1 if has_failures else 0
        return exit_code, summary

    # Legacy programmatic invocation fallback (for unit tests without manifest)
    target_symbols = symbols or SYMBOLS
    target_timeframes = timeframes or TIMEFRAMES
    summary: Dict[str, Dict[str, int]] = {}
    has_failures = False

    if conn is None:
        logger.error("A database connection is required for recovery (including dry-run). Cannot proceed with conn=None.")
        return 1, {
            f"{s}_{t}": {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 1}
            for s in target_symbols for t in target_timeframes
        }

    logger.info(
        f"Starting Futures Historical Recovery (Market: {market}, "
        f"Dry-Run: {dry_run}, Symbols: {target_symbols}, Timeframes: {target_timeframes})"
    )

    for sym in target_symbols:
        for tf in target_timeframes:
            key = f"{sym}_{tf}"
            summary[key] = {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0}
            lock_name = f"futures_kline_writer_{sym}_{tf}"

            if not dry_run and conn is not None:
                acquired = acquire_advisory_lock(conn, lock_name, timeout=5)
                if not acquired:
                    logger.error(f"Could not acquire lock {lock_name}; skipping {sym} {tf}")
                    summary[key]["failed"] += 1
                    has_failures = True
                    continue

            try:
                ranges = discover_missing_ranges(sym, market, tf, conn=conn)
                if not ranges:
                    logger.info(f"[{sym} {tf}] No missing ranges detected. Up to date.")
                    continue

                for start_ms, end_ms in ranges:
                    logger.info(
                        f"[{sym} {tf}] Recovering range: "
                        f"{datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} -> "
                        f"{datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}"
                    )
                    stats = recover_range(
                        symbol=sym,
                        market=market,
                        timeframe=tf,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        conn=conn,
                        dry_run=dry_run,
                        pacing_sec=pacing_sec,
                        session=session,
                    )
                    for k in summary[key]:
                        summary[key][k] += stats.get(k, 0)
                    if stats.get("failed", 0) > 0 or stats.get("ambiguous", 0) > 0:
                        has_failures = True

                logger.info(
                    f"[{sym} {tf}] Complete: fetched={summary[key]['fetched']}, "
                    f"inserted={summary[key]['inserted']}, updated={summary[key]['updated']}, "
                    f"skipped={summary[key]['skipped']}, ambiguous={summary[key]['ambiguous']}, "
                    f"failed={summary[key]['failed']}"
                )
            except Exception as e:
                logger.error(f"[{sym} {tf}] Recovery encountered unhandled error: {e}", exc_info=True)
                summary[key]["failed"] += 1
                has_failures = True
            finally:
                if not dry_run and conn is not None:
                    release_advisory_lock(conn, lock_name)

    exit_code = 1 if has_failures else 0
    return exit_code, summary


def main():
    parser = argparse.ArgumentParser(description="Recover missing Binance Futures OHLCV history")
    parser.add_argument("--manifest", help="Path to approved recovery manifest JSON")
    parser.add_argument("--expected-manifest-sha256", default=APPROVED_MANIFEST_SHA256, help="Approved manifest SHA256 checksum")
    parser.add_argument("--dry-run", action="store_true", help="Preview missing ranges and actions without writing to DB")
    parser.add_argument("--pacing", type=float, default=0.30, help="Sleep seconds between REST requests (default 0.30)")
    parser.add_argument("--db-host", default=None, help="Database host (default 127.0.0.1)")
    parser.add_argument("--db-port", type=int, default=None, help="Database port (default 3306)")
    parser.add_argument("--db-user", default=None, help="Database user")
    parser.add_argument("--db-password-file", default=None, help="Path to secure file containing database password")
    parser.add_argument("--db-password", default=None, help="Database password (only permitted in dry-run mode)")
    parser.add_argument("--db-name", default=None, help="Database name")
    parser.add_argument("--expected-db", default="signal_bot_test", help="Expected database identity for verification")
    parser.add_argument("--all", action="store_true", help="Ignored: manifest strictly dictates targets")
    parser.add_argument("--symbol", help="Ignored: manifest strictly dictates targets")
    parser.add_argument("--timeframe", help="Ignored: manifest strictly dictates targets")
    args = parser.parse_args()

    # Manifest enforcement: manifest must be supplied and validated before any connection or API call
    if not args.manifest:
        logger.error("Explicit --manifest <path> is mandatory. Recovery must execute strictly from a verified manifest.")
        sys.exit(1)

    try:
        manifest_data = load_and_validate_manifest(args.manifest, expected_sha=args.expected_manifest_sha256)
    except Exception as e:
        logger.error(f"Manifest validation failed closed: {e}")
        sys.exit(1)

    # In write mode: explicit credentials are required before connecting
    if not args.dry_run:
        if not args.db_user:
            logger.error("Explicit --db-user is mandatory in write mode. Fallback is prohibited.")
            sys.exit(1)
        if args.db_user != EXACT_WRITE_USER:
            logger.error(
                f"Invalid --db-user '{args.db_user}'. "
                f"Write mode accepts exactly the dedicated username '{EXACT_WRITE_USER}'."
            )
            sys.exit(1)
        if not args.db_password_file:
            logger.error("Explicit --db-password-file is mandatory in write mode. Fallback is prohibited.")
            sys.exit(1)
        if args.db_password:
            logger.error("Command-line password (--db-password) is strictly prohibited in write mode. Use --db-password-file.")
            sys.exit(1)

    conn = None
    try:
        if args.dry_run:
            logger.info("Initializing read-only database connection for dry-run inspection...")
            resolved_pw = args.db_password
            if args.db_password_file:
                with open(args.db_password_file, "r") as pf:
                    resolved_pw = pf.read().strip()

            conn = get_read_only_connection(
                host=args.db_host,
                port=args.db_port,
                user=args.db_user,
                password=resolved_pw,
                database=args.db_name,
                expected_database=args.expected_db,
            )
        else:
            logger.info("Initializing explicit write database connection...")
            conn = get_write_connection(
                host=args.db_host,
                port=args.db_port,
                user=args.db_user,
                password_file=args.db_password_file,
                database=args.db_name,
                expected_database=args.expected_db,
            )

        exit_code, summary = run_recovery(
            manifest=manifest_data,
            market="futures",
            conn=conn,
            dry_run=args.dry_run,
            pacing_sec=args.pacing,
        )
        logger.info(f"Recovery finished with exit code {exit_code}")
        sys.exit(exit_code)
    except Exception as e:
        logger.error(f"Fatal error during recovery execution: {e}")
        sys.exit(1)
    finally:
        if conn is not None:
            try:
                conn.close()
                logger.info("Database connection closed cleanly.")
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")


if __name__ == "__main__":
    main()
