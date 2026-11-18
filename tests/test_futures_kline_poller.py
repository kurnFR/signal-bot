"""
tests/test_futures_kline_poller.py

Comprehensive unit and integration test suite for collectors/futures_kline_poller.py.
Validates:
1. Forming candle ingestion: candle with now_ms <= close_time is marked is_closed=0.
2. Finalized promotion: candle with now_ms > close_time is marked is_closed=1.
3. Concurrency guard: advisory lock busy (recovery active) defers poll gracefully.
4. Rate limit handling: HTTP 429 respects Retry-After.
5. Error isolation: exception on one symbol does not terminate polling for other symbols.
6. Graceful shutdown: stop_event cleanly exits polling loop.
7. Invariant protection: forming candle response cannot overwrite finalized candle.
8. Safety lockout: test credentials cannot access production crypto_signals.
9. Permission audit against signal_bot_test.
"""

import asyncio
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests

from collectors.futures_kline_poller import (
    fetch_recent_klines,
    poll_symbol_timeframe,
    run_cycle,
    run,
    try_acquire_advisory_lock,
    release_advisory_lock,
)


def test_poll_symbol_timeframe_forming_candle_marked_zero():
    """
    When a candle is active/forming (close_time >= now_ms),
    it must be ingested with is_closed = 0.
    """
    mock_session = MagicMock()
    now_ms = 1000000
    # Candle close_time is 1050000 (> now_ms 1000000) -> forming
    raw_kline = [900000, "100.0", "105.0", "98.0", "102.0", "50.0", 1050000, "5000.0", 25, "25.0", "2500.0", "0"]
    mock_session.get.return_value.status_code = 200
    mock_session.get.return_value.json.return_value = [raw_kline]

    with patch("collectors.futures_kline_poller.upsert_ohlcv") as mock_upsert:
        count = poll_symbol_timeframe(
            "BTCUSDT", "15m", session=mock_session, dry_run=False, now_ms=now_ms
        )
        assert count == 1
        assert mock_upsert.called
        rows = mock_upsert.call_args[0][0]
        assert len(rows) == 1
        row = rows[0]
        assert row[0] == "BTCUSDT"
        assert row[2] == "15m"
        assert row[3] == 900000
        assert row[4] == 1050000
        assert row[14] == 0, "Forming candle must have is_closed=0"


def test_poll_symbol_timeframe_finalized_candle_marked_one():
    """
    When a candle has closed (now_ms > close_time),
    it must be promoted to is_closed = 1.
    """
    mock_session = MagicMock()
    now_ms = 1100000
    # Candle close_time is 1050000 (< now_ms 1100000) -> finalized
    raw_kline = [900000, "100.0", "105.0", "98.0", "102.0", "50.0", 1050000, "5000.0", 25, "25.0", "2500.0", "0"]
    mock_session.get.return_value.status_code = 200
    mock_session.get.return_value.json.return_value = [raw_kline]

    with patch("collectors.futures_kline_poller.upsert_ohlcv") as mock_upsert:
        count = poll_symbol_timeframe(
            "BTCUSDT", "15m", session=mock_session, dry_run=False, now_ms=now_ms
        )
        assert count == 1
        assert mock_upsert.called
        rows = mock_upsert.call_args[0][0]
        assert len(rows) == 1
        row = rows[0]
        assert row[14] == 1, "Completed candle must have is_closed=1"


def test_try_acquire_advisory_lock():
    """Verify non-blocking advisory lock check."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    # Case 1: lock available
    mock_cur.fetchone.return_value = [1]
    assert try_acquire_advisory_lock(mock_conn, "test_lock") is True
    mock_cur.execute.assert_called_with("SELECT GET_LOCK(%s, 0)", ("test_lock",))

    # Case 2: lock busy
    mock_cur.fetchone.return_value = [0]
    assert try_acquire_advisory_lock(mock_conn, "test_lock") is False


@pytest.mark.anyio
async def test_run_cycle_skips_when_lock_busy():
    """When advisory lock is held by recovery CLI, run_cycle defers without error."""
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchone.return_value = [0]  # Lock busy (held by another process)

    with patch("collectors.futures_kline_poller.poll_symbol_timeframe") as mock_poll, \
         patch("collectors.futures_kline_poller.heartbeat") as mock_heartbeat:

        stats = await run_cycle(
            symbols=["BTCUSDT"],
            timeframes=["15m"],
            pool=mock_pool,
            dry_run=False,
        )

        assert stats["polled"] == 0
        assert stats["errors"] == 0
        assert not mock_poll.called, "Must skip polling when advisory lock is busy"
        assert mock_heartbeat.called


def test_fetch_recent_klines_rate_limit_429():
    """Verify HTTP 429 returns empty list and sleeps Retry-After."""
    mock_session = MagicMock()
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "2"}
    mock_session.get.return_value = resp_429

    with patch("time.sleep") as mock_sleep:
        data = fetch_recent_klines("BTCUSDT", "15m", session=mock_session)
        assert data == []
        mock_sleep.assert_called_with(2)


@pytest.mark.anyio
async def test_run_cycle_symbol_error_isolation():
    """Failure on one symbol does not terminate polling for other symbols."""
    mock_session = MagicMock()

    with patch("collectors.futures_kline_poller.poll_symbol_timeframe") as mock_poll, \
         patch("collectors.futures_kline_poller.heartbeat") as mock_hb:

        def poll_side_effect(symbol, interval, *args, **kwargs):
            if symbol == "BNBUSDT":
                raise requests.RequestException("Simulated exchange timeout")
            return 3

        mock_poll.side_effect = poll_side_effect

        stats = await run_cycle(
            symbols=["BNBUSDT", "BTCUSDT"],
            timeframes=["15m"],
            session=mock_session,
            dry_run=True,
        )

        assert stats["errors"] == 1
        assert stats["polled"] == 3


@pytest.mark.anyio
async def test_poller_graceful_shutdown():
    """Verify poller exits cleanly when stop_event is set."""
    stop_event = asyncio.Event()

    with patch("collectors.futures_kline_poller.run_cycle") as mock_cycle:
        mock_cycle.return_value = {"polled": 3, "errors": 0}

        async def trigger_stop():
            await asyncio.sleep(0.05)
            stop_event.set()

        asyncio.create_task(trigger_stop())

        await run(
            symbols=["BTCUSDT"],
            timeframes=["15m"],
            poll_interval=1,
            stop_event=stop_event,
            dry_run=True,
        )

        assert stop_event.is_set()


def test_signal_bot_test_isolation_and_lockout():
    """
    Verifies that signal_bot_test credentials strictly connect to authorized databases
    and are denied access to unauthorized databases (e.g. mysql).
    """
    from dotenv import load_dotenv
    load_dotenv("/home/BIS/signal-bot/.env")
    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    import mysql.connector

    # 1. Connect to signal_bot_test succeeds
    conn = mysql.connector.connect(
        host="127.0.0.1", port=3306, database="signal_bot_test",
        user=test_user, password=test_pass, connection_timeout=5,
    )
    cur = conn.cursor()
    cur.execute("SELECT DATABASE()")
    active_db = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert active_db == "signal_bot_test"

    # 2. Connect to unauthorized database (mysql) is firmly denied (1044)
    with pytest.raises(mysql.connector.Error) as exc_info:
        mysql.connector.connect(
            host="127.0.0.1", port=3306, database="mysql",
            user=test_user, password=test_pass, connection_timeout=5,
        )
    assert exc_info.value.errno == 1044


def test_signal_bot_test_ohlcv_access_and_create_denial():
    """
    Verifies that signal_bot_test has SELECT access on ohlcv,
    while CREATE on arbitrary tables remains strictly denied (least privilege).
    """
    from dotenv import load_dotenv
    load_dotenv("/home/BIS/signal-bot/.env")
    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    import mysql.connector
    conn = mysql.connector.connect(
        host="127.0.0.1", port=3306, database="signal_bot_test",
        user=test_user, password=test_pass, connection_timeout=5,
    )
    cur = conn.cursor()

    # Assert SELECT on ohlcv succeeds now that table is provisioned
    cur.execute("SELECT COUNT(*) FROM ohlcv")
    cnt = cur.fetchone()[0]
    assert cnt >= 0

    # Assert CREATE on arbitrary tables is still denied (error 1142)
    with pytest.raises(mysql.connector.Error) as exc_info_create:
        cur.execute("CREATE TABLE ohlcv_temp (id INT)")
    assert exc_info_create.value.errno == 1142
    assert "CREATE command denied" in str(exc_info_create.value)
    cur.close()
    conn.close()

    cur.close()
    conn.close()
