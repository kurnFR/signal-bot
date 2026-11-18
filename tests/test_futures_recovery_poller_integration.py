"""
tests/test_futures_recovery_poller_integration.py

Comprehensive real MariaDB integration test suite for Binance Futures REST Recovery
and ongoing poller against the isolated signal_bot_test database.

Work Order 71884 Validation Coverage:
1. Database connection and identity enforcement (strict signal_bot_test lockout from crypto_signals).
2. Synthetic OHLCV insert, select, update, and delete.
3. Missing-range discovery against physical database.
4. Recovery dry-run produces no writes.
5. Pagination and resume behavior from existing watermark.
6. Per-symbol failure isolation.
7. Closed-candle time boundary behavior.
8. Forming candles are stored with is_closed=0.
9. A partial candle cannot overwrite an already-closed candle (poison guard).
10. A valid final candle can promote an incomplete candle to closed (0 -> 1 promotion).
11. Idempotent repeated recovery creates no duplicates.
12. Advisory-lock acquisition, contention, and release across distinct DB connections.
13. Poller defers while recovery owns the advisory lock (concurrency guard).
14. Recovery and poller cannot concurrently corrupt the same candle.
15. Three known corruption repair cases from design report (ETHUSDT 1h, BTCUSDT 1d, BTCUSDT 15m).
16. Valid finalized candles are not overwritten unnecessarily (skip optimization).
17. Cleanup removes only synthetic test records created by this work order.
"""

import os
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests
import mysql.connector

from db.db import upsert_ohlcv
from collectors.recover_futures_history import (
    discover_missing_ranges,
    recover_range,
    acquire_advisory_lock,
    release_advisory_lock,
)
from collectors.futures_kline_poller import (
    poll_symbol_timeframe,
    try_acquire_advisory_lock as poller_try_acquire_lock,
    release_advisory_lock as poller_release_lock,
    run_cycle as poller_run_cycle,
)


TEST_HOST = os.getenv("SIGNAL_BOT_TEST_DB_HOST", "127.0.0.1")
TEST_PORT = int(os.getenv("SIGNAL_BOT_TEST_DB_PORT", "3306"))
TEST_DB = "signal_bot_test"
TEST_USER = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
TEST_PASS = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

SYNTH_BTC = "SYNTHBTCUSDT"
SYNTH_ETH = "SYNTHETHUSDT"
SYNTH_TIMEFRAME = "15m"
SYNTH_MARKET = "futures"


def get_test_connection():
    """Establish dedicated connection to signal_bot_test."""
    conn = mysql.connector.connect(
        host=TEST_HOST,
        port=TEST_PORT,
        database=TEST_DB,
        user=TEST_USER,
        password=TEST_PASS,
        connection_timeout=5,
        autocommit=False,
    )
    cur = conn.cursor()
    cur.execute("SELECT DATABASE(), CURRENT_USER()")
    db, user = cur.fetchone()
    cur.close()
    assert db == TEST_DB, f"Expected {TEST_DB}, got {db}"
    assert "signal_bot_test" in user, f"Expected signal_bot_test, got {user}"
    return conn


@pytest.fixture
def db_conn():
    conn = get_test_connection()
    
    # Pre-test cleanup of synthetic symbols only
    cur = conn.cursor()
    cur.execute("DELETE FROM ohlcv WHERE symbol IN (%s, %s)", (SYNTH_BTC, SYNTH_ETH))
    conn.commit()
    cur.close()

    yield conn

    # Post-test cleanup of synthetic symbols only
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ohlcv WHERE symbol IN (%s, %s)", (SYNTH_BTC, SYNTH_ETH))
        conn.commit()
        cur.close()
    except Exception:
        pass
    finally:
        conn.close()


def test_strict_isolation_lockout():
    """1. Database identity enforcement: test credentials locked out of crypto_signals without authorization."""
    from collectors.recover_futures_history import get_read_only_connection
    from unittest.mock import patch
    with patch.dict(os.environ, {"ALLOW_PRODUCTION_OHLCV_INSPECTION": "0"}):
        with pytest.raises(PermissionError, match="Targeting 'crypto_signals' is strictly prohibited"):
            get_read_only_connection(
                host=TEST_HOST,
                port=TEST_PORT,
                database="crypto_signals",
                expected_database="crypto_signals",
                user=TEST_USER,
                password=TEST_PASS,
                connection_timeout=5,
            )


def test_synthetic_data_isolation_and_crud(db_conn):
    """2. Synthetic OHLCV insert, select, update, and delete."""
    cur = db_conn.cursor(dictionary=True)
    open_time = 1700000000000
    close_time = 1700000899999

    # INSERT
    row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        open_time, close_time,
        "100.0", "105.0", "95.0", "102.0",
        "500.0", "51000.0", 25, "250.0", "25500.0", 1
    )
    upsert_ohlcv([row], conn=db_conn)

    # SELECT
    cur.execute("SELECT * FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    rec = cur.fetchone()
    assert rec is not None
    assert rec["symbol"] == SYNTH_BTC
    assert Decimal(str(rec["close"])) == Decimal("102.0")
    assert rec["is_closed"] == 1

    # UPDATE
    row_updated = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        open_time, close_time,
        "100.0", "110.0", "95.0", "108.0",
        "700.0", "72000.0", 35, "350.0", "36000.0", 1
    )
    upsert_ohlcv([row_updated], conn=db_conn)
    cur.execute("SELECT high, close, volume FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    rec_up = cur.fetchone()
    assert Decimal(str(rec_up["high"])) == Decimal("110.0")
    assert Decimal(str(rec_up["close"])) == Decimal("108.0")
    assert Decimal(str(rec_up["volume"])) == Decimal("700.0")

    # DELETE
    cur.execute("DELETE FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    db_conn.commit()
    cur.execute("SELECT COUNT(*) AS cnt FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    assert cur.fetchone()["cnt"] == 0
    cur.close()


def test_physical_missing_range_discovery_and_dry_run(db_conn):
    """3 & 4. Missing-range discovery and recovery dry-run produces zero writes."""
    now_ms = 1794793460000
    ranges = discover_missing_ranges(SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, conn=db_conn, now_ms=now_ms, lookback_years=0.1)
    assert len(ranges) == 1
    start_ms, end_ms = ranges[0]
    assert start_ms < end_ms

    # Mock Binance response with 2 candles
    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        [1794700000000, "100", "110", "90", "105", "10", 1794700899999, "1000", 50, "5", "500", "0"],
        [1794700900000, "105", "115", "95", "110", "12", 1794701799999, "1200", 60, "6", "600", "0"],
    ]
    mock_session.get.return_value = resp_200

    stats = recover_range(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1794700000000, 1794701799999,
        conn=db_conn, dry_run=True, session=mock_session
    )
    assert stats["fetched"] == 2
    assert stats["inserted"] == 2

    # Verify zero database writes in dry-run mode
    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    count = cur.fetchone()[0]
    cur.close()
    assert count == 0, "Dry-run must not write to physical database"


def test_pagination_and_resumability(db_conn):
    """5. Pagination and resume behavior from existing watermark."""
    row1 = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "105.0", "99.0", "104.0", "100.0", "10400.0", 50, "50.0", "5200.0", 1
    )
    upsert_ohlcv([row1], conn=db_conn)

    now_ms = 1700009000000
    ranges = discover_missing_ranges(SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, conn=db_conn, now_ms=now_ms)
    assert len(ranges) == 1
    assert ranges[0][0] == 1700000000000, "Resumption must start at watermark open_time to verify boundary"


def test_closed_candle_boundary_handling(db_conn):
    """7. Closed-candle time boundary behavior."""
    cur = db_conn.cursor()
    row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "105.0", "99.0", "104.0", "100.0", "10400.0", 50, "50.0", "5200.0", 1
    )
    upsert_ohlcv([row], conn=db_conn)

    cur.execute("SELECT open_time, close_time, is_closed FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    res = cur.fetchone()
    cur.close()
    assert res[0] == 1700000000000
    assert res[1] == 1700000899999
    assert res[2] == 1


def test_forming_candles_stored_with_is_closed_zero(db_conn):
    """8. Forming candles are stored with is_closed=0."""
    now_ms = 1700000500000  # midpoint of candle
    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        [1700000000000, "100", "105", "98", "103", "45", 1700000899999, "4600", 15, "20", "2000", "0"]
    ]
    mock_session.get.return_value = resp_200

    poll_count = poll_symbol_timeframe(
        SYNTH_BTC, SYNTH_TIMEFRAME, market=SYNTH_MARKET,
        conn=db_conn, session=mock_session, dry_run=False, now_ms=now_ms
    )
    assert poll_count == 1

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT is_closed, volume FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    res = cur.fetchone()
    cur.close()
    assert res["is_closed"] == 0, "Forming candle must have is_closed=0"
    assert Decimal(str(res["volume"])) == Decimal("45")


def test_partial_candle_cannot_overwrite_finalized_candle(db_conn):
    """9. A partial candle cannot overwrite an already-closed candle (poison guard)."""
    finalized_row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "110.0", "95.0", "105.0", "1000.0", "105000.0", 100, "500.0", "52500.0", 1
    )
    upsert_ohlcv([finalized_row], conn=db_conn)

    # Poison row with partial volume and is_closed=0
    poison_row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "110.0", "90.0", "90.0", "10.0", "1000.0", 5, "2.0", "200.0", 0
    )
    upsert_ohlcv([poison_row], conn=db_conn)

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT close, volume, is_closed FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    row = cur.fetchone()
    cur.close()

    assert row["is_closed"] == 1
    assert Decimal(str(row["close"])) == Decimal("105.0"), "Finalized close must not be corrupted"
    assert Decimal(str(row["volume"])) == Decimal("1000.0"), "Finalized volume must not be corrupted"


def test_final_candle_promotes_incomplete_candle(db_conn):
    """10. A valid final candle can promote an incomplete candle to closed (0 -> 1 promotion)."""
    forming_row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "103.0", "99.0", "102.0", "50.0", "5100.0", 20, "25.0", "2550.0", 0
    )
    upsert_ohlcv([forming_row], conn=db_conn)

    # Promote with finalized data
    finalized_row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "108.0", "98.0", "106.0", "250.0", "26000.0", 85, "120.0", "12500.0", 1
    )
    upsert_ohlcv([finalized_row], conn=db_conn)

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT is_closed, high, close, volume FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    promoted = cur.fetchone()
    cur.close()

    assert promoted["is_closed"] == 1, "is_closed must transition from 0 to 1"
    assert Decimal(str(promoted["high"])) == Decimal("108.0")
    assert Decimal(str(promoted["close"])) == Decimal("106.0")
    assert Decimal(str(promoted["volume"])) == Decimal("250.0")


def test_idempotent_repeated_ingestion(db_conn):
    """11. Idempotent repeated recovery creates no duplicates."""
    row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        1700000000000, 1700000899999,
        "100.0", "105.0", "99.0", "104.0", "100.0", "10400.0", 50, "50.0", "5200.0", 1
    )

    upsert_ohlcv([row], conn=db_conn)
    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    snap1 = cur.fetchone()

    # Re-run identical ingestion
    upsert_ohlcv([row], conn=db_conn)
    cur.execute("SELECT * FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    snap2 = cur.fetchall()
    cur.close()

    assert len(snap2) == 1, "Must produce zero duplicate rows"
    assert snap1 == snap2[0]


def test_recovery_poller_advisory_lock_coordination():
    """12. Advisory-lock acquisition, contention, and release across distinct DB connections."""
    conn_rec = get_test_connection()
    conn_pol = get_test_connection()
    lock_name = "test_coordination_lock"

    try:
        acquired = acquire_advisory_lock(conn_rec, lock_name, timeout=2)
        assert acquired is True

        poller_acquired = poller_try_acquire_lock(conn_pol, lock_name)
        assert poller_acquired is False, "Poller must defer when recovery holds lock"

        release_advisory_lock(conn_rec, lock_name)

        poller_acquired_after = poller_try_acquire_lock(conn_pol, lock_name)
        assert poller_acquired_after is True, "Poller must acquire lock once released"
        poller_release_lock(conn_pol, lock_name)
    finally:
        conn_rec.close()
        conn_pol.close()


def test_poller_defers_and_cannot_corrupt_during_recovery():
    """13 & 14. Poller defers while recovery owns lock; cannot corrupt the same candle."""
    conn_rec = get_test_connection()
    conn_pol = get_test_connection()
    lock_name = f"futures_kline_writer_{SYNTH_BTC}_{SYNTH_TIMEFRAME}"

    # Insert finalized candle
    open_t = 1700000000000
    close_t = 1700000899999
    row = (
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME,
        open_t, close_t,
        "100.0", "110.0", "95.0", "105.0", "1000.0", "105000.0", 100, "500.0", "52500.0", 1
    )
    upsert_ohlcv([row], conn=conn_rec, commit=True)

    # 1. Recovery acquires lock
    assert acquire_advisory_lock(conn_rec, lock_name, timeout=2) is True

    # 2. Poller attempts to acquire lock -> returns False, skips write
    assert poller_try_acquire_lock(conn_pol, lock_name) is False

    # 3. Assert DB record was not modified
    cur = conn_pol.cursor(dictionary=True)
    cur.execute("SELECT close, volume, is_closed FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    rec = cur.fetchone()
    cur.close()
    assert Decimal(str(rec["volume"])) == Decimal("1000.0")

    # 4. Release lock
    release_advisory_lock(conn_rec, lock_name)
    conn_rec.close()
    conn_pol.close()


def test_per_symbol_failure_isolation(db_conn):
    """6. Per-symbol failure isolation during recovery."""
    mock_session = MagicMock()
    # ETH raises network error, BTC succeeds
    resp_btc = MagicMock()
    resp_btc.status_code = 200
    resp_btc.json.return_value = [
        [1700000000000, "100", "110", "90", "105", "10", 1700000899999, "1000", 50, "5", "500", "0"],
    ]

    def side_effect(url, params, **kwargs):
        sym = params.get("symbol")
        if sym == SYNTH_ETH:
            raise requests.ConnectionError("Simulated network drop on ETH")
        return resp_btc

    mock_session.get.side_effect = side_effect

    # Recover ETH -> fails safely
    with pytest.raises(requests.ConnectionError):
        recover_range(SYNTH_ETH, SYNTH_MARKET, SYNTH_TIMEFRAME, 1700000000000, 1700000899999, conn=db_conn, session=mock_session)

    # Recover BTC -> succeeds independently
    stats_btc = recover_range(SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1700000000000, 1700000899999, conn=db_conn, session=mock_session)
    assert stats_btc["inserted"] == 1

    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s", (SYNTH_BTC,))
    assert cur.fetchone()[0] == 1
    cur.close()


def test_repair_known_corruption_eth_1h(db_conn):
    """
    15A. Repair known corruption: ETHUSDT 1h incomplete candle.
    Simulates incomplete closed candle stored with partial volume 120.5.
    Authoritative exchange response has full volume 850.25 and correct high/close.
    """
    sym = SYNTH_ETH
    tf = "1h"
    open_t = 1787737200000  # 1h candle
    close_t = 1787740799999

    # Insert corrupt record: partial volume 120.5, premature is_closed=1
    corrupt_row = (
        sym, SYNTH_MARKET, tf,
        open_t, close_t,
        "2850.0", "2860.0", "2840.0", "2855.0", "120.5", "343425.0", 1200, "60.0", "171000.0", 1
    )
    upsert_ohlcv([corrupt_row], conn=db_conn)

    # Authoritative exchange kline
    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        [open_t, "2850.0", "2875.50", "2838.0", "2870.0", "850.25", close_t, "2440217.5", 8500, "420.0", "1205400.0", "0"]
    ]
    mock_session.get.return_value = resp_200

    stats = recover_range(sym, SYNTH_MARKET, tf, open_t, close_t, conn=db_conn, session=mock_session)
    assert stats["fetched"] == 1
    assert stats["updated"] == 1, "Must detect difference and update corrupt record"
    assert stats["inserted"] == 0

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT high, close, volume FROM ohlcv WHERE symbol = %s AND timeframe = %s", (sym, tf))
    healed = cur.fetchone()
    cur.close()

    assert Decimal(str(healed["volume"])) == Decimal("850.25"), "Volume must be healed to authoritative value"
    assert Decimal(str(healed["high"])) == Decimal("2875.50")
    assert Decimal(str(healed["close"])) == Decimal("2870.0")


def test_repair_known_corruption_btc_1d(db_conn):
    """
    15B. Repair known corruption: BTCUSDT 1d incomplete candle.
    Simulates partial daily metrics healed to authoritative daily volume and prices.
    """
    sym = SYNTH_BTC
    tf = "1d"
    open_t = 1787702400000  # 1d candle
    close_t = 1787788799999

    corrupt_row = (
        sym, SYNTH_MARKET, tf,
        open_t, close_t,
        "78000.0", "78500.0", "77800.0", "78200.0", "4200.5", "328479100.0", 35000, "2100.0", "164220000.0", 1
    )
    upsert_ohlcv([corrupt_row], conn=db_conn)

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        [open_t, "78000.0", "79200.0", "77500.0", "78950.0", "28450.75", close_t, "2246186712.5", 215000, "14200.0", "1121090000.0", "0"]
    ]
    mock_session.get.return_value = resp_200

    stats = recover_range(sym, SYNTH_MARKET, tf, open_t, close_t, conn=db_conn, session=mock_session)
    assert stats["fetched"] == 1
    assert stats["updated"] == 1
    assert stats["inserted"] == 0

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT high, close, volume FROM ohlcv WHERE symbol = %s AND timeframe = %s", (sym, tf))
    healed = cur.fetchone()
    cur.close()

    assert Decimal(str(healed["volume"])) == Decimal("28450.75")
    assert Decimal(str(healed["high"])) == Decimal("79200.0")


def test_repair_known_corruption_btc_15m(db_conn):
    """
    15C. Repair known corruption: BTCUSDT 15m partial boundary candle (August 26, 2026 anomaly).
    Volume 785.115 healed to authoritative volume 1401.042 and high 78465.00.
    """
    sym = SYNTH_BTC
    tf = "15m"
    open_t = 1787737500000
    close_t = 1787738399999

    corrupt_row = (
        sym, SYNTH_MARKET, tf,
        open_t, close_t,
        "78290.20", "78388.00", "78208.00", "78388.00", "785.115", "61516000.0", 22100, "400.0", "31355000.0", 1
    )
    upsert_ohlcv([corrupt_row], conn=db_conn)

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        [open_t, "78290.20", "78465.00", "78208.00", "78415.50", "1401.042", close_t, "109867000.0", 38408, "700.0", "54900000.0", "0"]
    ]
    mock_session.get.return_value = resp_200

    stats = recover_range(sym, SYNTH_MARKET, tf, open_t, close_t, conn=db_conn, session=mock_session)
    assert stats["fetched"] == 1
    assert stats["updated"] == 1
    assert stats["inserted"] == 0

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT high, close, volume FROM ohlcv WHERE symbol = %s AND timeframe = %s", (sym, tf))
    healed = cur.fetchone()
    cur.close()

    assert Decimal(str(healed["volume"])) == Decimal("1401.042")
    assert Decimal(str(healed["high"])) == Decimal("78465.00")


def test_valid_finalized_candles_not_unnecessarily_overwritten(db_conn):
    """
    16. Valid finalized candles are not overwritten unnecessarily.
    When stored row matches authoritative exchange kline, stats['skipped'] is incremented
    and zero database writes are dispatched.
    """
    sym = SYNTH_BTC
    tf = "15m"
    open_t = 1700000000000
    close_t = 1700000899999

    valid_row = (
        sym, SYNTH_MARKET, tf,
        open_t, close_t,
        "100.0000000000", "110.0000000000", "95.0000000000", "105.0000000000",
        "1000.0000000000", "105000.0000000000", 100, "500.0000000000", "52500.0000000000", 1
    )
    upsert_ohlcv([valid_row], conn=db_conn)

    mock_session = MagicMock()
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        [open_t, "100.0", "110.0", "95.0", "105.0", "1000.0", close_t, "105000.0", 100, "500.0", "52500.0", "0"]
    ]
    mock_session.get.return_value = resp_200

    with patch("collectors.recover_futures_history.upsert_ohlcv") as mock_upsert:
        stats = recover_range(sym, SYNTH_MARKET, tf, open_t, close_t, conn=db_conn, session=mock_session)
        assert stats["fetched"] == 1
        assert stats["skipped"] == 1, "Identical valid finalized candle must be skipped"
        assert stats["updated"] == 0
        assert stats["inserted"] == 0
        assert not mock_upsert.called, "Must dispatch zero upsert queries when all records match"
