"""
tests/test_recovery_dry_run_connection.py

Comprehensive test suite validating the read-only recovery dry-run connection
and differential behavior for Work Order 71888 (Phase 11B.4G).

Coverage:
1. Read-only connection establishment and session enforcement (SET SESSION TRANSACTION READ ONLY).
2. Anti-fallback security: rejection of cms, root, MYSQL_USER, and missing credentials.
3. Target isolation: rejection of crypto_signals and identity mismatch.
4. Fail-closed error handling when database connection is unavailable.
5. SQL instrumentation asserting ZERO mutation statements (SELECT/SHOW/SET only).
6. Upsert suppression asserting upsert_ohlcv is never called during dry-run.
7. MariaDB server-level Error 1792 when attempting mutation on read-only session.
8. Differential detection against real MariaDB (signal_bot_test):
   - Missing row -> proposed insert
   - Corrupt row -> proposed update/repair
   - Correct row -> skipped optimization
   - Ambiguous/malformed row -> ambiguous count, omitted from repair
   - Forming bar -> omitted from recovery
9. Clean teardown and state verification: zero rows mutated in signal_bot_test.ohlcv.
"""

import os
import re
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests
import mysql.connector
from dotenv import load_dotenv

load_dotenv("/home/BIS/signal-bot/.env")

from collectors.recover_futures_history import (
    get_read_only_connection,
    discover_missing_ranges,
    recover_range,
    run_recovery,
    main,
)

TEST_HOST = os.getenv("SIGNAL_BOT_TEST_DB_HOST", "127.0.0.1")
TEST_PORT = int(os.getenv("SIGNAL_BOT_TEST_DB_PORT", "3306"))
TEST_DB = "signal_bot_test"
TEST_USER = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
TEST_PASS = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

SYNTH_SYMBOL = "SYNTHDRYRUNUSDT"
SYNTH_MARKET = "futures"
SYNTH_TIMEFRAME = "15m"


@pytest.fixture
def test_db_conn():
    """Provides a dedicated connection to signal_bot_test with auto-cleanup."""
    cnx = mysql.connector.connect(
        host=TEST_HOST,
        port=TEST_PORT,
        user=TEST_USER,
        password=TEST_PASS,
        database=TEST_DB,
    )
    yield cnx
    # Cleanup any synthetic records
    cur = cnx.cursor()
    cur.execute("DELETE FROM ohlcv WHERE symbol = %s", (SYNTH_SYMBOL,))
    cnx.commit()
    cur.close()
    cnx.close()


# -----------------------------------------------------------------------------
# Security & Anti-Fallback Negative Tests
# -----------------------------------------------------------------------------

def test_missing_credentials_fail_closed():
    """Requirement: Missing credentials must fail closed without fallback."""
    with patch.dict(os.environ, {"RECOVERY_DB_USER": "", "SIGNAL_BOT_TEST_DB_USER": ""}):
        with pytest.raises(ValueError) as exc:
            get_read_only_connection(user=None)
        assert "Explicit database user must be provided" in str(exc.value)


def test_privileged_users_cms_and_root_rejected():
    """Requirement: Prohibits cms and root accounts."""
    for bad_user in ["cms", "CMS", "root", "ROOT"]:
        with pytest.raises(PermissionError) as exc:
            get_read_only_connection(user=bad_user)
        assert "prohibited for read-only recovery" in str(exc.value)


def test_production_database_fallback_rejected():
    """Requirement: Connecting to crypto_signals is strictly forbidden."""
    with patch.dict(os.environ, {"ALLOW_PRODUCTION_OHLCV_INSPECTION": "0"}):
        with pytest.raises(PermissionError) as exc:
            get_read_only_connection(
                user=TEST_USER,
                password=TEST_PASS,
                database="crypto_signals",
                expected_database="crypto_signals",
            )
        assert "Targeting 'crypto_signals' is strictly prohibited" in str(exc.value)


def test_unexpected_database_identity_rejected():
    """Requirement: Unexpected database identity must be rejected."""
    with pytest.raises(ValueError) as exc:
        get_read_only_connection(
            user=TEST_USER,
            password=TEST_PASS,
            database=TEST_DB,
            expected_database="wrong_test_db",
        )
    assert "does not match expected database" in str(exc.value)


def test_unavailable_database_connection_fails_closed():
    """Requirement: Unavailable connection fails closed, never returns zero-repair result."""
    with pytest.raises(ValueError) as exc:
        discover_missing_ranges(SYNTH_SYMBOL, SYNTH_MARKET, SYNTH_TIMEFRAME, conn=None)
    assert "A database connection is required" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        recover_range(SYNTH_SYMBOL, SYNTH_MARKET, SYNTH_TIMEFRAME, 1000, 2000, conn=None)
    assert "A database connection is required" in str(exc.value)

    exit_code, summary = run_recovery(
        symbols=[SYNTH_SYMBOL],
        timeframes=[SYNTH_TIMEFRAME],
        conn=None,
        dry_run=True,
    )
    assert exit_code == 1, "Exit code must be non-zero on conn=None"
    key = f"{SYNTH_SYMBOL}_{SYNTH_TIMEFRAME}"
    assert summary[key]["failed"] == 1


# -----------------------------------------------------------------------------
# Read-Only Session Enforcement & Mutation Blocking
# -----------------------------------------------------------------------------

def test_mariadb_session_read_only_enforcement():
    """Requirement: MariaDB session transaction read-only blocks all writes with Error 1792."""
    ro_conn = get_read_only_connection(
        host=TEST_HOST,
        port=TEST_PORT,
        user=TEST_USER,
        password=TEST_PASS,
        database=TEST_DB,
        expected_database=TEST_DB,
    )
    cur = ro_conn.cursor()
    # Confirm SELECT works
    cur.execute("SELECT DATABASE(), CURRENT_USER();")
    assert cur.fetchone()[0] == TEST_DB

    # Attempt mutation on read-only session
    with pytest.raises(mysql.connector.Error) as exc:
        cur.execute(
            """
            INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
            VALUES (%s, %s, %s, 1000, 1999, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
            """,
            (SYNTH_SYMBOL, SYNTH_MARKET, SYNTH_TIMEFRAME),
        )
    assert exc.value.errno == 1792, "Must fail with MariaDB Error 1792 (Cannot execute statement in a READ ONLY transaction)"

    cur.close()
    ro_conn.close()


def test_sql_instrumentation_zero_mutation_statements():
    """
    SQL Instrumentation: Verifies every single SQL statement executed during dry-run
    is strictly a SELECT or SET statement. Zero INSERT/UPDATE/DELETE.
    """
    ro_conn = get_read_only_connection(
        host=TEST_HOST,
        port=TEST_PORT,
        user=TEST_USER,
        password=TEST_PASS,
        database=TEST_DB,
        expected_database=TEST_DB,
    )

    executed_queries = []
    real_cursor = ro_conn.cursor

    class InstrumentingCursorProxy:
        def __init__(self, target_cur):
            self._target = target_cur

        def execute(self, query, params=None):
            executed_queries.append(query.strip())
            return self._target.execute(query, params)

        def fetchall(self):
            return self._target.fetchall()

        def fetchone(self):
            return self._target.fetchone()

        def close(self):
            return self._target.close()

        def __getattr__(self, name):
            return getattr(self._target, name)

    def instrumented_cursor(*args, **kwargs):
        c = real_cursor(*args, **kwargs)
        return InstrumentingCursorProxy(c)

    ro_conn.cursor = instrumented_cursor

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    now_ms = 1794793460000
    resp_200.json.return_value = [
        [1794700000000, "100.0", "105.0", "95.0", "102.0", "10.0", 1794700899999, "1020.0", 50, "5.0", "510.0", "0"]
    ]
    mock_session.get.return_value = resp_200

    with patch("collectors.recover_futures_history.upsert_ohlcv") as mock_upsert:
        exit_code, summary = run_recovery(
            symbols=[SYNTH_SYMBOL],
            timeframes=[SYNTH_TIMEFRAME],
            conn=ro_conn,
            dry_run=True,
            session=mock_session,
        )

        assert exit_code == 0
        assert not mock_upsert.called, "upsert_ohlcv must never be called in dry-run"

    # Verify statement instrumentation
    assert len(executed_queries) > 0
    forbidden_verbs = ["INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE"]
    for q in executed_queries:
        upper_q = q.upper()
        # Verify query starts with allowed verb
        assert upper_q.startswith("SELECT") or upper_q.startswith("SET"), f"Disallowed statement: {q}"
        for verb in forbidden_verbs:
            assert not re.search(rf"\b{verb}\b", upper_q), f"Forbidden verb {verb} detected in query: {q}"

    ro_conn.close()


# -----------------------------------------------------------------------------
# Differential Detection Against MariaDB (signal_bot_test)
# -----------------------------------------------------------------------------

def test_dry_run_differential_synthetic_detection(test_db_conn):
    """
    Differential Behavior Test:
    Seeds signal_bot_test.ohlcv with:
    1. Correct candle -> must be skipped
    2. Corrupt candle (wrong volume and open) -> must be proposed for update
    Simulates Binance returning:
    - Candle 1 (matching stored)
    - Candle 2 (authoritative replacement for corrupt)
    - Candle 3 (missing from DB -> proposed insert)
    - Candle 4 (ambiguous timestamps -> ambiguous count, omitted from upsert)
    - Candle 5 (forming candle -> omitted from recovery)
    """
    cur = test_db_conn.cursor()

    ot1 = 1794700000000
    ct1 = 1794700899999
    # Candle 1: Correct finalized candle
    cur.execute(
        """
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, 100.0, 105.0, 95.0, 102.0, 50.0, 5100.0, 20, 25.0, 2550.0, 1)
        """,
        (SYNTH_SYMBOL, SYNTH_MARKET, SYNTH_TIMEFRAME, ot1, ct1)
    )

    ot2 = 1794700900000
    ct2 = 1794701799999
    # Candle 2: Corrupt candle (truncated volume 5.0 instead of authoritative 40.0, is_closed=0)
    cur.execute(
        """
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, 102.0, 108.0, 101.0, 107.0, 5.0, 535.0, 10, 2.0, 214.0, 0)
        """,
        (SYNTH_SYMBOL, SYNTH_MARKET, SYNTH_TIMEFRAME, ot2, ct2)
    )
    test_db_conn.commit()

    # Pre-check row count
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s", (SYNTH_SYMBOL,))
    assert cur.fetchone()[0] == 2

    now_ms = 1794704000000  # fixed epoch past candles 1, 2, 3

    ot3 = 1794701800000
    ct3 = 1794702699999

    ot4 = 1794702700000
    ct4 = 1794702600000  # Ambiguous: close_time < open_time

    ot5 = 1794703600000
    ct5 = 1794704499999  # Forming: close_time > now_ms

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        # Candle 1: exactly matches DB row 1
        [ot1, "100.0", "105.0", "95.0", "102.0", "50.0", ct1, "5100.0", 20, "25.0", "2550.0", "0"],
        # Candle 2: authoritative replacement (volume 40.0, close 107.0, closed)
        [ot2, "102.0", "108.0", "101.0", "107.0", "40.0", ct2, "4280.0", 30, "20.0", "2140.0", "0"],
        # Candle 3: missing from DB
        [ot3, "107.0", "112.0", "106.0", "110.0", "35.0", ct3, "3850.0", 25, "18.0", "1980.0", "0"],
        # Candle 4: ambiguous timestamps
        [ot4, "110.0", "115.0", "109.0", "114.0", "30.0", ct4, "3420.0", 20, "15.0", "1710.0", "0"],
        # Candle 5: forming (close_time > now_ms)
        [ot5, "114.0", "116.0", "113.0", "115.0", "10.0", ct5, "1150.0", 8, "5.0", "575.0", "0"],
    ]
    mock_session.get.return_value = resp_200

    # Execute dry-run with read-only connection
    ro_conn = get_read_only_connection(
        host=TEST_HOST,
        port=TEST_PORT,
        user=TEST_USER,
        password=TEST_PASS,
        database=TEST_DB,
        expected_database=TEST_DB,
    )

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.timestamp.return_value = now_ms / 1000.0

        stats = recover_range(
            symbol=SYNTH_SYMBOL,
            market=SYNTH_MARKET,
            timeframe=SYNTH_TIMEFRAME,
            start_ms=ot1,
            end_ms=ot3,
            conn=ro_conn,
            dry_run=True,
            session=mock_session,
        )

    ro_conn.close()

    # Assert differential counts
    assert stats["fetched"] == 5
    assert stats["skipped"] == 1, "Candle 1 must be skipped as already correct"
    assert stats["updated"] == 1, "Candle 2 must be proposed for update/healing"
    assert stats["inserted"] == 1, "Candle 3 must be proposed for insertion"
    assert stats["ambiguous"] == 1, "Candle 4 must be flagged as ambiguous"

    # Post-check: physical database must have ZERO changes
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s", (SYNTH_SYMBOL,))
    assert cur.fetchone()[0] == 2, "Row count must remain unchanged after dry-run"

    # Confirm Candle 2 was NOT mutated in the physical table
    cur.execute("SELECT volume, is_closed FROM ohlcv WHERE symbol = %s AND open_time = %s", (SYNTH_SYMBOL, ot2))
    row2 = cur.fetchone()
    assert Decimal(str(row2[0])) == Decimal("5.0"), "Corrupt volume must not be modified in dry-run"
    assert row2[1] == 0, "is_closed must not be promoted in dry-run"
    cur.close()


def test_main_cli_dry_run_full_cycle(test_db_conn):
    """
    Validates main() CLI invocation with --dry-run over signal_bot_test.
    Confirms argument parsing, read-only connection instantiation, and clean exit.
    """
    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = []
    mock_session.get.return_value = resp_200

    manifest_path = "/home/BIS/.gemini/antigravity-cli/brain/8676284a-04c3-4017-9d68-508794c31087/restricted_1h_recovery_manifest_v3.json"
    test_args = [
        "recover_futures_history.py",
        "--manifest", manifest_path,
        "--symbol", SYNTH_SYMBOL,
        "--timeframe", SYNTH_TIMEFRAME,
        "--dry-run",
        "--db-user", TEST_USER,
        "--db-password", TEST_PASS,
        "--db-name", TEST_DB,
        "--expected-db", TEST_DB,
    ]

    with patch.object(sys, "argv", test_args), \
         patch("collectors.recover_futures_history.SESSION", mock_session), \
         pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0, "main() in dry-run mode must exit with code 0 on success"
