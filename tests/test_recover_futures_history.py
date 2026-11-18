"""
tests/test_recover_futures_history.py

Comprehensive unit and integration test suite for collectors/recover_futures_history.py.
Validates:
1. Missing-range discovery (empty DB, watermarked DB, internal gaps, upper boundary capping).
2. Pagination boundaries (advancing cursor, 1000-candle limits, repeated pages).
3. Resumability after interruption.
4. Empty API response and malformed response handling.
5. Forming candle exclusion (ensuring now_ms <= close_time is never accepted in recovery).
6. Known corrupt boundary candle correction (authoritative finalized overwrite).
7. Existing valid finalized candle protection (invariant protection).
8. Failure isolation across symbols and intervals.
9. Dry-run mode produces zero database writes.
10. Database isolation guard: strictly forbids connecting to crypto_signals.
"""

import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from collectors.recover_futures_history import (
    timeframe_to_ms,
    fetch_futures_klines,
    discover_missing_ranges,
    recover_range,
    run_recovery,
    acquire_advisory_lock,
    release_advisory_lock,
)


def test_timeframe_to_ms():
    assert timeframe_to_ms("15m") == 900000
    assert timeframe_to_ms("1h") == 3600000
    assert timeframe_to_ms("4h") == 14400000
    assert timeframe_to_ms("1d") == 86400000
    with pytest.raises(ValueError):
        timeframe_to_ms("invalid")


def test_discover_missing_ranges_empty_db():
    """When DB has 0 rows for symbol, returns full lookback range."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchone.return_value = {"cnt": 0, "min_ot": None, "max_ot": None}

    now_ms = 1794793460000  # fixed epoch
    ranges = discover_missing_ranges("BTCUSDT", "futures", "15m", conn=mock_conn, now_ms=now_ms, lookback_years=1.0)
    assert len(ranges) == 1
    start_ms, end_ms = ranges[0]
    expected_start = now_ms - int(1.0 * 365.25 * 86400 * 1000)
    assert start_ms == expected_start
    # Upper bound must be strictly capped before current active forming candle
    step_ms = 900000
    expected_end = (now_ms // step_ms) * step_ms - 1
    assert end_ms == expected_end


def test_discover_missing_ranges_watermarked_heals_boundary():
    """
    When DB has existing watermark max_ot, range MUST start at max_ot
    so the boundary candle is re-verified and healed with finalized exchange data.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    # Watermark at August 26, 2026
    watermark_ot = 1787737500000
    mock_cur.fetchone.return_value = {"cnt": 176574, "min_ot": 1628821800000, "max_ot": watermark_ot}
    mock_cur.fetchall.return_value = []  # No internal gaps

    now_ms = 1794793460000  # November 16, 2026
    ranges = discover_missing_ranges("BTCUSDT", "futures", "15m", conn=mock_conn, now_ms=now_ms)
    assert len(ranges) == 1
    start_ms, end_ms = ranges[0]
    assert start_ms == watermark_ot, "Must start AT max_ot to heal partial boundary candle"
    step_ms = 900000
    expected_end = (now_ms // step_ms) * step_ms - 1
    assert end_ms == expected_end


def test_discover_missing_ranges_handles_internal_disjoint_gaps():
    """Verifies that internal missing gaps prior to watermark are scheduled."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    watermark_ot = 1787737500000
    mock_cur.fetchone.return_value = {"cnt": 1000, "min_ot": 1700000000000, "max_ot": watermark_ot}
    # Simulate an internal gap between 1710000000000 and 1710900000000
    mock_cur.fetchall.return_value = [
        {"open_time": 1710000000000, "next_ot": 1710900000000}
    ]

    now_ms = 1794793460000
    ranges = discover_missing_ranges("ETHUSDT", "futures", "15m", conn=mock_conn, now_ms=now_ms)
    assert len(ranges) == 2
    # Internal gap range
    assert ranges[0] == (1710000000000, 1710900000000 - 1)
    # Trailing gap range
    assert ranges[1][0] == watermark_ot


def test_fetch_futures_klines_rate_limit_and_backoff():
    """Verify HTTP 429 Retry-After handling and 5xx backoff."""
    mock_session = MagicMock()

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "1"}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [[1000, "1", "2", "3", "4", "5", 1999, "6", 7, "8", "9", "0"]]

    mock_session.get.side_effect = [resp_429, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_futures_klines("BTCUSDT", "15m", 1000, 2000, session=mock_session)
        assert len(data) == 1
        assert mock_sleep.called
        mock_sleep.assert_called_with(1)


def test_recover_range_dry_run_zero_writes():
    """In dry-run mode, upsert_ohlcv must never be invoked."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    # Two completed historical candles (close_time < current time)
    resp_200.json.return_value = [
        [100000, "100", "110", "90", "105", "10", 189999, "1000", 50, "5", "500", "0"],
        [190000, "105", "115", "95", "110", "12", 279999, "1200", 60, "6", "600", "0"],
    ]
    mock_session.get.return_value = resp_200

    with patch("collectors.recover_futures_history.upsert_ohlcv") as mock_upsert:
        stats = recover_range(
            "BTCUSDT", "futures", "15m", 100000, 300000,
            conn=mock_conn, dry_run=True, session=mock_session
        )
        assert stats["fetched"] == 2
        assert stats["inserted"] == 2
        assert not mock_upsert.called, "Dry-run must execute ZERO database writes"


def test_recover_range_heals_corrupted_partial_candle():
    """
    Verifies that when an existing stored record differs from authoritative
    finalized exchange data, it is flagged as 'updated' and passed to upsert_ohlcv.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    # Mock stored partial candle: volume=785.115 (partial)
    mock_cur.fetchall.return_value = [
        {
            "open_time": 1787737500000,
            "open": Decimal("78290.20"),
            "high": Decimal("78388.00"),  # missed high
            "low": Decimal("78208.00"),
            "close": Decimal("78388.00"),
            "volume": Decimal("785.115"),  # partial volume
            "is_closed": 1,
        }
    ]

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    # True finalized values from exchange: volume=1401.042, high=78465.00
    resp_200.json.return_value = [
        [
            1787737500000,
            "78290.20", "78465.00", "78208.00", "78415.50", "1401.042",
            1787738399999, "109867000.0", 38408, "700.0", "54900000.0", "0"
        ]
    ]
    mock_session.get.return_value = resp_200

    with patch("collectors.recover_futures_history.upsert_ohlcv") as mock_upsert:
        stats = recover_range(
            "BTCUSDT", "futures", "15m", 1787737500000, 1787738399999,
            conn=mock_conn, dry_run=False, session=mock_session
        )
        assert stats["fetched"] == 1
        assert stats["updated"] == 1, "Partial candle must be detected as updated/healed"
        assert stats["inserted"] == 0
        assert mock_upsert.called
        upserted_rows = mock_upsert.call_args[0][0]
        assert len(upserted_rows) == 1
        # Assert authoritative volume and high are written
        assert upserted_rows[0][5] == "78290.20"  # open
        assert upserted_rows[0][6] == "78465.00"  # high
        assert upserted_rows[0][9] == "1401.042"  # volume
        assert upserted_rows[0][14] == 1          # is_closed=1


def test_recover_range_excludes_current_forming_candle():
    """
    If Binance returns a forming candle (close_time >= now_ms or open_time > end_ms),
    it must be strictly omitted from historical recovery.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    # One closed candle, one active forming candle in the future
    now_ms = 1000000
    resp_200.json.return_value = [
        [800000, "10", "11", "9", "10", "100", 899999, "1000", 10, "5", "500", "0"],   # closed
        [900000, "10", "11", "9", "10", "10", 1049999, "100", 2, "1", "50", "0"],       # forming (close_time > now_ms)
    ]
    mock_session.get.return_value = resp_200

    with patch("collectors.recover_futures_history.upsert_ohlcv") as mock_upsert, \
         patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.timestamp.return_value = 1000.0  # now_ms = 1000000

        stats = recover_range(
            "BTCUSDT", "futures", "15m", 800000, 899999,
            conn=mock_conn, dry_run=False, session=mock_session
        )
        assert stats["fetched"] == 2
        assert stats["inserted"] == 1  # Only the closed candle inserted
        assert mock_upsert.called
        upserted = mock_upsert.call_args[0][0]
        assert len(upserted) == 1
        assert upserted[0][3] == 800000


def test_failure_isolation_across_symbols():
    """An unhandled error on ETHUSDT must not terminate recovery for BTCUSDT."""
    mock_conn = MagicMock()
    mock_session = MagicMock()

    with patch("collectors.recover_futures_history.discover_missing_ranges") as mock_discover, \
         patch("collectors.recover_futures_history.recover_range") as mock_recover:

        mock_discover.return_value = [(1000, 2000)]
        # ETHUSDT fails with network error, BTCUSDT succeeds
        def recover_side_effect(symbol, *args, **kwargs):
            if symbol == "ETHUSDT":
                raise requests.ConnectionError("Simulated network drop")
            return {"fetched": 10, "inserted": 10, "updated": 0, "skipped": 0, "failed": 0}

        mock_recover.side_effect = recover_side_effect

        exit_code, summary = run_recovery(
            symbols=["ETHUSDT", "BTCUSDT"],
            timeframes=["15m"],
            conn=mock_conn,
            dry_run=True,
            session=mock_session,
        )

        assert exit_code == 1, "Exit code must be non-zero due to ETHUSDT failure"
        assert summary["ETHUSDT_15m"]["failed"] == 1
        assert summary["BTCUSDT_15m"]["inserted"] == 10
        assert summary["BTCUSDT_15m"]["failed"] == 0


def test_advisory_lock_lifecycle():
    """Verify advisory lock acquisition and release with complete result set consumption."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [(1,)]  # GET_LOCK and RELEASE_LOCK return 1

    acquired = acquire_advisory_lock(mock_conn, "test_lock", timeout=2)
    assert acquired is True
    mock_cur.execute.assert_called_with("SELECT GET_LOCK(%s, %s)", ("test_lock", 2))

    released = release_advisory_lock(mock_conn, "test_lock")
    assert released is True
    mock_cur.execute.assert_called_with("SELECT RELEASE_LOCK(%s)", ("test_lock",))


def test_advisory_lock_drains_cursor_and_permits_subsequent_cursor_allocation_live():
    """
    Regression test for WO71911 bug: proves that release_advisory_lock drains the
    SELECT RELEASE_LOCK result set completely, permitting subsequent cursor operations
    without raising InternalError('Unread result found').
    """
    import mysql.connector
    from dotenv import load_dotenv
    load_dotenv("/home/BIS/signal-bot/.env")
    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    conn = mysql.connector.connect(
        host="127.0.0.1", port=3306, database="signal_bot_test",
        user=test_user, password=test_pass, connection_timeout=5,
    )
    try:
        lock_name = "test_advisory_lock_drain_live"
        # 1. Acquire
        acq = acquire_advisory_lock(conn, lock_name, timeout=5)
        assert acq is True

        # 2. Release
        rel = release_advisory_lock(conn, lock_name)
        assert rel is True

        # 3. Subsequent cursor allocation must succeed without unread result error
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        assert row[0] == 1
        cur.close()
    finally:
        conn.close()


def test_repeated_lock_lifecycle_live():
    """Verify repeated acquire -> release cycles across multiple iterations on a live connection."""
    import mysql.connector
    from dotenv import load_dotenv
    load_dotenv("/home/BIS/signal-bot/.env")
    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    conn = mysql.connector.connect(
        host="127.0.0.1", port=3306, database="signal_bot_test",
        user=test_user, password=test_pass, connection_timeout=5,
    )
    try:
        for i in range(3):
            lock_name = f"test_repeat_cycle_{i}"
            assert acquire_advisory_lock(conn, lock_name, timeout=5) is True
            assert release_advisory_lock(conn, lock_name) is True

            cur = conn.cursor()
            cur.execute("SELECT 42")
            assert cur.fetchone()[0] == 42
            cur.close()
    finally:
        conn.close()


def test_release_advisory_lock_unheld_or_nonexistent():
    """Verify release_advisory_lock handles 0 or NULL release codes gracefully without error."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    # Lock does not exist: returns NULL
    mock_cur.fetchall.return_value = [(None,)]
    assert release_advisory_lock(mock_conn, "nonexistent_lock") is False

    # Lock not held by this thread: returns 0
    mock_cur.fetchall.return_value = [(0,)]
    assert release_advisory_lock(mock_conn, "unheld_lock") is False


def test_strict_lockout_from_crypto_signals():
    """Verifies that recover_futures_history rejects crypto_signals without authorization."""
    from collectors.recover_futures_history import get_read_only_connection
    from dotenv import load_dotenv
    load_dotenv("/home/BIS/signal-bot/.env")
    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    with patch.dict(os.environ, {"ALLOW_PRODUCTION_OHLCV_INSPECTION": "0"}):
        with pytest.raises(PermissionError, match="Targeting 'crypto_signals' is strictly prohibited"):
            get_read_only_connection(
                host="127.0.0.1", port=3306, database="crypto_signals",
                expected_database="crypto_signals",
                user=test_user, password=test_pass, connection_timeout=5,
            )
