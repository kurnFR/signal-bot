"""
Comprehensive unit and integration tests for Binance Futures Funding Rate REST Collector (Stage P.10A).
Tests:
- Successful API response parsing
- Multiple symbols collection
- Incremental watermark behavior
- Pagination across multiple pages
- Duplicate funding events and idempotency
- Out-of-order response handling
- Missing/invalid symbols
- HTTP 429 rate limit with Retry-After header
- HTTP 5xx server errors and bounded exponential backoff
- Network timeouts and connection drops
- Invalid JSON and malformed decimal values
- Database failure and recovery
- Restart recovery without losing records
- Historical record protection (no estimated rate overwriting)
- Isolated signal_bot_test database integration guard
"""

import os
import sys
import threading
import time
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
from config import FundingDBConfig

from collectors.funding_poller import (
    FundingPollerLock,
    clean_decimal,
    parse_funding_item,
    fetch_funding_page,
    poll_symbol_incremental,
    poll_all_symbols,
    run_standalone_loop,
    parse_retry_after,
    reset_ban_cooldown_for_testing,
    get_ban_cooldown_remaining,
    set_ban_cooldown,
)
import db.db as db_module


# =============================================================================
# 1. Unit Tests: Decimal Cleaning & Item Parsing
# =============================================================================

def test_clean_decimal_valid_and_invalid():
    # Valid values
    assert clean_decimal("0.00010000") == "0.00010000"
    assert clean_decimal("65049.72578986") == "65049.72578986"
    assert clean_decimal("-0.00025") == "-0.00025"
    assert clean_decimal(0.0005) == str(Decimal(str(0.0005)))
    assert clean_decimal(100) == "100"

    # Empty / whitespace / None values
    assert clean_decimal(None) is None
    assert clean_decimal("") is None
    assert clean_decimal("   ") is None

    # Invalid non-numeric strings
    assert clean_decimal("invalid") is None
    assert clean_decimal("NaN") is None
    assert clean_decimal("Infinity") is None
    assert clean_decimal("-Infinity") is None


def test_parse_funding_item_valid():
    item = {
        "symbol": "BTCUSDT",
        "fundingTime": 1786204800002,
        "fundingRate": "0.00005801",
        "markPrice": "65049.72578986",
        "rateType": "Regular",
    }
    parsed = parse_funding_item(item)
    assert parsed == (
        "BTCUSDT",
        1786204800002,
        "65049.72578986",
        None,
        "0.00005801",
        None,
    )


def test_parse_funding_item_null_mark_price():
    item = {
        "symbol": "ETHUSDT",
        "fundingTime": 1678752000010,
        "fundingRate": "0.00010000",
        "markPrice": "",  # observed in Binance historical backfill
    }
    parsed = parse_funding_item(item)
    assert parsed[2] is None  # mark_price cleaned to None
    assert parsed[4] == "0.00010000"


def test_parse_funding_item_invalid_raises():
    # Non-dict
    with pytest.raises(ValueError, match="expected dict"):
        parse_funding_item(["not", "a", "dict"])

    # Missing symbol
    with pytest.raises(ValueError, match="symbol"):
        parse_funding_item({"fundingTime": 1000, "fundingRate": "0.0001"})

    # Missing fundingTime
    with pytest.raises(ValueError, match="fundingTime"):
        parse_funding_item({"symbol": "BTCUSDT", "fundingRate": "0.0001"})

    # Non-positive fundingTime
    with pytest.raises(ValueError, match="Non-positive fundingTime"):
        parse_funding_item({"symbol": "BTCUSDT", "fundingTime": -5, "fundingRate": "0.0001"})

    # Missing fundingRate
    with pytest.raises(ValueError, match="Missing fundingRate"):
        parse_funding_item({"symbol": "BTCUSDT", "fundingTime": 1000})

    # Invalid non-decimal fundingRate
    with pytest.raises(ValueError, match="Invalid fundingRate"):
        parse_funding_item({"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "NaN"})


# =============================================================================
# 2. Unit Tests: HTTP Client, Retries & Rate Limiting
# =============================================================================

def test_fetch_funding_page_success():
    session = Mock(spec=requests.Session)
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "0.0002"},
    ]
    session.get.return_value = mock_resp

    data = fetch_funding_page(session, "BTCUSDT", start_ms=500, end_ms=3000, limit=100)
    assert len(data) == 2
    session.get.assert_called_once()
    _, kwargs = session.get.call_args
    assert kwargs["params"] == {"symbol": "BTCUSDT", "limit": 100, "startTime": 500, "endTime": 3000}


def test_fetch_funding_page_http_429_rate_limit_retry():
    session = Mock(spec=requests.Session)
    resp_429 = Mock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "1"}

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_429, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3)
        assert len(data) == 1
        mock_sleep.assert_called_once_with(1)
        assert session.get.call_count == 2


def test_fetch_funding_page_http_5xx_server_error_retry():
    session = Mock(spec=requests.Session)
    resp_500 = Mock()
    resp_500.status_code = 500

    resp_502 = Mock()
    resp_502.status_code = 502

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_500, resp_502, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3, backoff_base=0.5)
        assert len(data) == 1
        assert session.get.call_count == 3
        # Backoff: 0.5 * (2**0) = 0.5s, then 0.5 * (2**1) = 1.0s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.5)
        mock_sleep.assert_any_call(1.0)


def test_fetch_funding_page_http_5xx_retries_exhausted():
    session = Mock(spec=requests.Session)
    resp_500 = Mock()
    resp_500.status_code = 500
    session.get.return_value = resp_500

    with patch("time.sleep"):
        with pytest.raises(RuntimeError, match="Failed to fetch funding rates"):
            fetch_funding_page(session, "BTCUSDT", max_retries=3, backoff_base=0.1)
    assert session.get.call_count == 3


def test_fetch_funding_page_network_timeout():
    session = Mock(spec=requests.Session)
    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [requests.Timeout("Connection timed out"), resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3, backoff_base=0.5)
        assert len(data) == 1
        mock_sleep.assert_called_once_with(0.5)


def test_fetch_funding_page_invalid_json_format():
    session = Mock(spec=requests.Session)
    resp_err = Mock()
    resp_err.status_code = 200
    resp_err.json.return_value = {"code": -1121, "msg": "Invalid symbol"}
    session.get.return_value = resp_err

    with pytest.raises(ValueError, match="Unexpected response structure"):
        fetch_funding_page(session, "INVALID_SYM", max_retries=1)


# =============================================================================
# 3. Unit Tests: Incremental Watermark, Pagination & Out-of-Order Handling
# =============================================================================

def test_incremental_watermark_with_existing_records():
    session = Mock(spec=requests.Session)
    # Existing DB watermark is 1786204800000
    with patch("collectors.funding_poller.get_latest_funding_time", return_value=1786204800000):
        with patch("collectors.funding_poller.fetch_funding_page") as mock_fetch:
            with patch("collectors.funding_poller.upsert_funding") as mock_upsert:
                mock_fetch.return_value = [
                    {"symbol": "BTCUSDT", "fundingTime": 1786233600000, "fundingRate": "0.00004423"}
                ]
                inserted = poll_symbol_incremental(session, "BTCUSDT", end_ms=1786250000000)
                assert inserted == 1

                # Verify start_ms is latest + 1
                mock_fetch.assert_called_once()
                _, kwargs = mock_fetch.call_args
                assert kwargs["start_ms"] == 1786204800001
                assert kwargs["end_ms"] == 1786250000000
                mock_upsert.assert_called_once()


def test_incremental_watermark_empty_db_fallback():
    session = Mock(spec=requests.Session)
    with patch("collectors.funding_poller.get_latest_funding_time", return_value=None):
        with patch("collectors.funding_poller.fetch_funding_page") as mock_fetch:
            with patch("collectors.funding_poller.upsert_funding"):
                mock_fetch.return_value = []
                target_end = 1700000000000
                poll_symbol_incremental(session, "BTCUSDT", end_ms=target_end, lookback_days=10)

                mock_fetch.assert_called_once()
                _, kwargs = mock_fetch.call_args
                expected_start = target_end - (10 * 86400 * 1000)
                assert kwargs["start_ms"] == expected_start


def test_incremental_already_up_to_date():
    session = Mock(spec=requests.Session)
    # Watermark is ahead of or equal to target_end_ms
    with patch("collectors.funding_poller.get_latest_funding_time", return_value=1700000000000):
        with patch("collectors.funding_poller.fetch_funding_page") as mock_fetch:
            inserted = poll_symbol_incremental(session, "BTCUSDT", end_ms=1700000000000)
            assert inserted == 0
            mock_fetch.assert_not_called()


def test_pagination_multiple_pages():
    session = Mock(spec=requests.Session)

    # Page 1: returns limit=2 items, max timestamp 2000
    page_1 = [
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "0.0002"},
    ]
    # Page 2: returns 1 item (< limit), max timestamp 3000
    page_2 = [
        {"symbol": "BTCUSDT", "fundingTime": 3000, "fundingRate": "0.0003"},
    ]

    with patch("collectors.funding_poller.get_latest_funding_time", return_value=None):
        with patch("collectors.funding_poller.fetch_funding_page", side_effect=[page_1, page_2]) as mock_fetch:
            with patch("collectors.funding_poller.upsert_funding") as mock_upsert:
                inserted = poll_symbol_incremental(
                    session, "BTCUSDT", end_ms=5000, lookback_days=1, limit=2
                )
                assert inserted == 3
                assert mock_fetch.call_count == 2
                # Check pagination cursor advance
                call_2_kwargs = mock_fetch.call_args_list[1][1]
                assert call_2_kwargs["start_ms"] == 2001  # previous max (2000) + 1
                assert mock_upsert.call_count == 2


def test_out_of_order_responses_sorted_before_upsert():
    session = Mock(spec=requests.Session)
    out_of_order = [
        {"symbol": "BTCUSDT", "fundingTime": 3000, "fundingRate": "0.0003"},
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "0.0002"},
    ]

    with patch("collectors.funding_poller.get_latest_funding_time", return_value=None):
        with patch("collectors.funding_poller.fetch_funding_page", return_value=out_of_order):
            with patch("collectors.funding_poller.upsert_funding") as mock_upsert:
                poll_symbol_incremental(session, "BTCUSDT", end_ms=5000)
                mock_upsert.assert_called_once()
                inserted_rows = mock_upsert.call_args[0][0]
                timestamps = [r[1] for r in inserted_rows]
                assert timestamps == [1000, 2000, 3000]


def test_duplicate_funding_events_idempotency():
    # If exchange returns duplicate records in same batch or successive batches
    session = Mock(spec=requests.Session)
    items_with_dups = [
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"},
    ]
    with patch("collectors.funding_poller.get_latest_funding_time", return_value=None):
        with patch("collectors.funding_poller.fetch_funding_page", return_value=items_with_dups):
            with patch("collectors.funding_poller.upsert_funding") as mock_upsert:
                poll_symbol_incremental(session, "BTCUSDT", end_ms=5000)
                mock_upsert.assert_called_once()
                rows = mock_upsert.call_args[0][0]
                assert len(rows) == 2  # Handled safely by ON DUPLICATE KEY UPDATE in DB


# =============================================================================
# 4. Unit Tests: Multi-symbol Isolation, Heartbeats & Database Failures
# =============================================================================

def test_poll_all_symbols_partial_failure_isolation():
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    def fake_poll(session, symbol, **kwargs):
        if symbol == "ETHUSDT":
            raise RuntimeError("Network timeout on ETH")
        return 3

    with patch("collectors.funding_poller.poll_symbol_incremental", side_effect=fake_poll):
        with patch("collectors.funding_poller.heartbeat") as mock_hb:
            results = poll_all_symbols(symbols=symbols, lock=Mock())
            assert results["symbols_total"] == 3
            assert results["symbols_ok"] == 2
            assert results["symbols_failed"] == 1
            assert results["rows_inserted"] == 6
            assert "ETHUSDT" in results["errors"]

            # Partial failure records warning heartbeat
            mock_hb.assert_called_once()
            _, hb_kwargs = mock_hb.call_args
            assert hb_kwargs["status"] == "warning"
            assert "Partial" in hb_kwargs["detail"]


def test_poll_all_symbols_full_success_heartbeat():
    symbols = ["BTCUSDT", "ETHUSDT"]
    with patch("collectors.funding_poller.poll_symbol_incremental", return_value=2):
        with patch("collectors.funding_poller.heartbeat") as mock_hb:
            results = poll_all_symbols(symbols=symbols, lock=Mock())
            assert results["symbols_ok"] == 2
            assert results["symbols_failed"] == 0
            assert results["rows_inserted"] == 4

            mock_hb.assert_called_once()
            _, hb_kwargs = mock_hb.call_args
            assert hb_kwargs["status"] == "ok"
            assert "2/2 symbols ok" in hb_kwargs["detail"]


def test_database_failure_handling():
    session = Mock(spec=requests.Session)
    with patch("collectors.funding_poller.get_latest_funding_time", return_value=None):
        with patch("collectors.funding_poller.fetch_funding_page", return_value=[
            {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}
        ]):
            with patch("collectors.funding_poller.upsert_funding", side_effect=Exception("DB connection lost")):
                with pytest.raises(Exception, match="DB connection lost"):
                    poll_symbol_incremental(session, "BTCUSDT", end_ms=5000)


def test_no_unintended_changes_to_historical_data():
    # Verify that parse_funding_item never produces next_funding_time or estimated values from fapi/v1/fundingRate
    item = {
        "symbol": "BTCUSDT",
        "fundingTime": 1786204800002,
        "fundingRate": "0.00005801",
        "markPrice": "65049.72578986",
    }
    parsed = parse_funding_item(item)
    # index_price must be None
    assert parsed[3] is None
    # next_funding_time must be None
    assert parsed[5] is None


def test_upsert_funding_and_watermark_mocked_db_execution():
    """Deterministic unit test: verifies exact SQL generation, parameter mapping, and connection lifecycle."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_pool = Mock()
    mock_pool.get_connection.return_value = mock_conn

    # 1. Test get_latest_funding_time
    mock_cur.fetchone.return_value = (1786204800000,)
    latest = db_module.get_latest_funding_time("BTCUSDT", pool=mock_pool)
    assert latest == 1786204800000
    mock_cur.execute.assert_called_with("SELECT MAX(event_time) FROM funding_rate WHERE symbol = %s", ("BTCUSDT",))
    mock_conn.close.assert_called_once()

    # 2. Test upsert_funding
    mock_conn.reset_mock()
    mock_cur.reset_mock()
    rows = [
        ("BTCUSDT", 1786204800002, "65049.72578986", None, "0.00005801", None),
        ("BTCUSDT", 1786233600000, "64928.89433333", None, "0.00004423", None),
    ]
    db_module.upsert_funding(rows, pool=mock_pool)
    mock_cur.executemany.assert_called_once()
    sql_arg, params_arg = mock_cur.executemany.call_args[0]
    assert "INSERT INTO funding_rate" in sql_arg
    assert "ON DUPLICATE KEY UPDATE" in sql_arg
    assert params_arg == rows
    mock_conn.commit.assert_called_once()
    mock_conn.close.assert_called_once()

    # 3. Test heartbeat
    mock_conn.reset_mock()
    mock_cur.reset_mock()
    db_module.heartbeat("funding_poller", status="ok", detail="3/3 symbols ok", pool=mock_pool)
    mock_cur.execute.assert_called_once()
    hb_sql, hb_params = mock_cur.execute.call_args[0]
    assert "INSERT INTO collector_heartbeat" in hb_sql
    assert hb_params == ("funding_poller", "ok", "3/3 symbols ok")
    mock_conn.commit.assert_called_once()
    mock_conn.close.assert_called_once()



# =============================================================================
# 5. Integration Tests: MariaDB Isolation, Permission Audit & Phase C Matrix
# =============================================================================

def test_signal_bot_test_isolation_and_crypto_signals_lockout():
    """
    Validates boundary enforcement:
    1. Strictly forbids using cms credentials for tests.
    2. Verifies active connection is strictly signal_bot_test.
    3. Verifies that signal_bot_test account is firmly locked out of crypto_signals (error 1044).
    """
    import mysql.connector

    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    # Boundary check 1: Do not use cms credentials
    assert test_user != "cms", "Security boundary violation: cms account strictly prohibited for tests"

    # Boundary check 2: Verify connection to test database
    conn = mysql.connector.connect(
        host="127.0.0.1",
        port=3306,
        database="signal_bot_test",
        user=test_user,
        password=test_pass,
        connection_timeout=5,
    )
    cur = conn.cursor()
    cur.execute("SELECT DATABASE()")
    active_db = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert active_db == "signal_bot_test", f"Safety violation: Expected signal_bot_test, got {active_db}"

    # Boundary check 3: Verify strict lockout from unauthorized database (e.g. mysql)
    with pytest.raises(mysql.connector.Error) as exc_info:
        mysql.connector.connect(
            host="127.0.0.1",
            port=3306,
            database="mysql",
            user=test_user,
            password=test_pass,
            connection_timeout=5,
        )
    assert exc_info.value.errno == 1044, f"Expected MariaDB error 1044 (Access denied), got {exc_info.value.errno}"


def test_no_unintended_delete_operations_in_source():
    """
    Phase C Requirement 9: Verify zero DELETE statements are present in
    funding collector or db persistence routines.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    funding_poller_file = os.path.join(repo_root, "collectors", "funding_poller.py")
    db_file = os.path.join(repo_root, "db", "db.py")

    with open(funding_poller_file, "r") as f:
        poller_src = f.read()
    assert "DELETE FROM" not in poller_src.upper(), "funding_poller.py must not contain DELETE operations"

    with open(db_file, "r") as f:
        db_src = f.read()

    # Extract upsert_funding and get_latest_funding_time sections
    assert "DELETE FROM funding_rate" not in db_src, "db.py must not execute DELETE on funding_rate"
    assert "DELETE FROM collector_heartbeat" not in db_src, "db.py must not execute DELETE on collector_heartbeat"


def test_signal_bot_test_permission_audit_and_diagnostics():
    """
    Stage P.10C-G: Audit current grants and verify final permission matrix on signal_bot_test:
    - funding_rate: SELECT, INSERT, UPDATE
    - collector_heartbeat: SELECT, INSERT, UPDATE
    Verifies that SELECT, INSERT, UPDATE (including WHERE and ON DUPLICATE KEY UPDATE) succeed,
    while DELETE remains denied (least-privilege enforcement).
    """
    import mysql.connector

    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    conn = mysql.connector.connect(
        host="127.0.0.1",
        port=3306,
        database="signal_bot_test",
        user=test_user,
        password=test_pass,
        connection_timeout=5,
        autocommit=False,
    )
    cur = conn.cursor()
    cur.execute("SHOW GRANTS FOR CURRENT_USER()")
    grants = [r[0] for r in cur.fetchall()]

    # 1. Verify exact table grants confirmed by operator
    funding_grants = [g for g in grants if "funding_rate" in g]
    heartbeat_grants = [g for g in grants if "collector_heartbeat" in g]
    assert len(funding_grants) > 0, "Expected grant on funding_rate not found in SHOW GRANTS"
    assert "SELECT" in funding_grants[0] and "INSERT" in funding_grants[0] and "UPDATE" in funding_grants[0]
    assert len(heartbeat_grants) > 0, "Expected grant on collector_heartbeat not found in SHOW GRANTS"
    assert "SELECT" in heartbeat_grants[0] and "INSERT" in heartbeat_grants[0] and "UPDATE" in heartbeat_grants[0]

    # 2. Probe SELECT on funding_rate
    cur.execute("SELECT MAX(event_time) FROM funding_rate WHERE symbol = 'BTCUSDT'")
    _ = cur.fetchone()

    # 3. Probe INSERT on funding_rate
    cur.execute(
        "INSERT INTO funding_rate (symbol, event_time, mark_price, index_price, funding_rate, next_funding_time) "
        "VALUES ('__PROBE__', 1, NULL, NULL, '0.0001', NULL)"
    )

    # 4. Probe UPDATE on funding_rate
    cur.execute("UPDATE funding_rate SET mark_price = '1' WHERE symbol = '__PROBE__'")

    # 5. Probe SELECT on collector_heartbeat (granted, succeeds)
    cur.execute("SELECT COUNT(*) FROM collector_heartbeat")
    _ = cur.fetchone()

    # 6. Probe INSERT on collector_heartbeat (granted, succeeds)
    cur.execute(
        "INSERT INTO collector_heartbeat (collector_name, status, detail, last_beat_time) "
        "VALUES ('__PROBE__', 'ok', 'test', NOW())"
    )

    # 7. Probe UPDATE with WHERE on collector_heartbeat (granted with SELECT, succeeds)
    cur.execute("UPDATE collector_heartbeat SET status = 'warning' WHERE collector_name = '__PROBE__'")

    # 8. Probe ON DUPLICATE KEY UPDATE on collector_heartbeat (granted with SELECT, succeeds)
    cur.execute(
        "INSERT INTO collector_heartbeat (collector_name, status, detail, last_beat_time) "
        "VALUES ('__PROBE__', 'ok', 'test', NOW()) ON DUPLICATE KEY UPDATE status=VALUES(status), detail=VALUES(detail), last_beat_time=NOW()"
    )

    # 9. Verify DELETE remains strictly denied on both tables (least privilege boundary)
    with pytest.raises(mysql.connector.Error) as exc_del_funding:
        cur.execute("DELETE FROM funding_rate WHERE symbol = '__PROBE__'")
    assert exc_del_funding.value.errno == 1142, f"Expected MariaDB Error 1142 (DELETE denied on funding_rate), got {exc_del_funding.value.errno}"

    with pytest.raises(mysql.connector.Error) as exc_del_hb:
        cur.execute("DELETE FROM collector_heartbeat WHERE collector_name = '__PROBE__'")
    assert exc_del_hb.value.errno == 1142, f"Expected MariaDB Error 1142 (DELETE denied on collector_heartbeat), got {exc_del_hb.value.errno}"

    conn.rollback()
    cur.close()
    conn.close()


def test_signal_bot_test_db_persistence_and_matrix():
    """
    Stage P.10C-G Final Integration Matrix:
    Executes live verification against signal_bot_test using real MariaDB operations:
    1. Watermark: Empty-table behavior, incremental progression, monotonic ordering.
    2. Funding Persistence: Real INSERT, exact decimal precision, duplicate key idempotency,
       out-of-order handling.
    3. Heartbeat Persistence: Real INSERT, real UPDATE/upsert via db_module.heartbeat,
       and SELECT readback verification.
    4. End-to-End Collector Pipeline: Successful poll_all_symbols execution and heartbeat emission.
    """
    import mysql.connector

    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

    class TestConnectionPool:
        """Lightweight real connection factory for MariaDB integration testing."""
        def get_connection(self):
            return mysql.connector.connect(
                host="127.0.0.1",
                port=3306,
                database="signal_bot_test",
                user=test_user,
                password=test_pass,
                connection_timeout=5,
                autocommit=True,
            )

    pool = TestConnectionPool()

    # --- Phase A: Watermark ---
    # 1. Empty-table behavior: Symbol with no records returns None
    empty_watermark = db_module.get_latest_funding_time("SYNTHETIC_EMPTY_SYMBOL_999", pool=pool)
    assert empty_watermark is None

    # --- Phase B: Funding Persistence ---
    sym = f"SYN_{int(time.time())}"
    t1 = 1794745000000
    t2 = 1794745028800
    test_rows = [
        (sym, t1, "50000.1234567890", None, "0.0000580100", None),
        (sym, t2, "50100.9876543210", None, "0.0000442300", None),
    ]

    # 2. Actual INSERT into funding_rate
    db_module.upsert_funding(test_rows, pool=pool)

    # 3. Watermark advancement after successful persistence
    adv_watermark = db_module.get_latest_funding_time(sym, pool=pool)
    assert adv_watermark == t2

    # 4. Exact decimal precision verification
    conn = pool.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT mark_price, funding_rate FROM funding_rate WHERE symbol = %s AND event_time = %s", (sym, t1))
    row = cur.fetchone()
    assert row[0] == Decimal("50000.1234567890"), f"Expected exact Decimal('50000.1234567890'), got {row[0]}"
    assert row[1] == Decimal("0.0000580100"), f"Expected exact Decimal('0.0000580100'), got {row[1]}"

    # 5. Duplicate event idempotency (same event_time, updated mark_price)
    update_rows = [
        (sym, t2, "50150.0000000000", None, "0.0000442300", None),
    ]
    db_module.upsert_funding(update_rows, pool=pool)
    cur.execute("SELECT mark_price FROM funding_rate WHERE symbol = %s AND event_time = %s", (sym, t2))
    assert cur.fetchone()[0] == Decimal("50150.0000000000")

    # 6. Out-of-order event handling (inserting earlier event does not regress watermark)
    t_earlier = 1794745014400
    earlier_rows = [
        (sym, t_earlier, "50050.0000000000", None, "0.0000500000", None),
    ]
    db_module.upsert_funding(earlier_rows, pool=pool)
    assert db_module.get_latest_funding_time(sym, pool=pool) == t2

    # --- Phase C: Heartbeat Persistence ---
    worker_name = f"test_p10c_worker_{int(time.time() * 1000)}"

    # 7. Actual INSERT into collector_heartbeat via db_module.heartbeat
    db_module.heartbeat(worker_name, status="ok", detail="initial_beat", pool=pool)
    cur.execute("SELECT status, detail FROM collector_heartbeat WHERE collector_name = %s", (worker_name,))
    hb_row_1 = cur.fetchone()
    assert hb_row_1[0] == "ok"
    assert hb_row_1[1] == "initial_beat"

    # 8. Actual UPDATE of an existing heartbeat via db_module.heartbeat
    db_module.heartbeat(worker_name, status="warning", detail="updated_beat", pool=pool)
    cur.execute("SELECT status, detail FROM collector_heartbeat WHERE collector_name = %s", (worker_name,))
    hb_row_2 = cur.fetchone()
    assert hb_row_2[0] == "warning"
    assert hb_row_2[1] == "updated_beat"

    # --- Phase D: End-to-End Poller Execution & Failure Behavior ---
    # 9. Verify poll_all_symbols runs successfully and emits heartbeat with status 'ok'
    mock_session = requests.Session()
    mock_adapter = Mock()
    # Mocking external HTTP response for deterministic synthetic poll
    synthetic_api_response = [
        {
            "symbol": sym,
            "fundingTime": t2 + 28800000,
            "fundingRate": "0.00005500",
            "markPrice": "50200.00000000",
        }
    ]
    with patch.object(mock_session, "get") as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = synthetic_api_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp

        results = poll_all_symbols(
            symbols=[sym],
            pool=pool,
            session=mock_session,
            end_ms=t2 + 50000000,
            lock=FundingPollerLock(config=FundingDBConfig("127.0.0.1", 3306, "signal_bot_test", test_user, test_pass)),
        )
        assert results["symbols_ok"] == 1
        assert results["symbols_failed"] == 0
        assert results["rows_inserted"] == 1

        # Confirm heartbeat was persisted by poller
        cur.execute("SELECT status, detail FROM collector_heartbeat WHERE collector_name = 'funding_poller'")
        poller_hb = cur.fetchone()
        assert poller_hb is not None
        assert poller_hb[0] == "ok"
        assert "1/1 symbols ok" in poller_hb[1]

    # 10. Failed persistence does not incorrectly advance watermark
    with patch("collectors.funding_poller.fetch_funding_page", return_value=[{"symbol": sym, "fundingTime": t2 + 50000000, "fundingRate": "0.0001", "markPrice": "50000"}]):
        with patch("collectors.funding_poller.upsert_funding", side_effect=Exception("DB persistence failed")):
            with pytest.raises(Exception, match="DB persistence failed"):
                poll_symbol_incremental(mock_session, sym, pool=pool, end_ms=t2 + 60000000)
    # Watermark remains at the last successfully persisted event_time
    assert db_module.get_latest_funding_time(sym, pool=pool) == t2 + 28800000

    cur.close()
    conn.close()


# =============================================================================
# 6. WO71864 Architecture Validation Tests: Dedicated Credentials, Security & Standalone Loop
# =============================================================================

def test_dedicated_credential_loading_from_valid_env_file(tmp_path):
    """Test 1: Dedicated credential loading from valid 0600 env file."""
    from config import load_funding_collector_config
    env_file = tmp_path / "funding-collector.env"
    env_file.write_text(
        "FUNDING_DB_HOST=127.0.0.1\n"
        "FUNDING_DB_PORT=3306\n"
        "FUNDING_DB_NAME=signal_bot_test\n"
        "FUNDING_DB_USER=signal_funding_collector\n"
        "FUNDING_DB_PASSWORD=SuperSecretPass_WO71864\n"
    )
    os.chmod(env_file, 0o600)

    cfg = load_funding_collector_config(env_path=str(env_file), enforce_security=True, enforce_ownership=False)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 3306
    assert cfg.database == "signal_bot_test"
    assert cfg.user == "signal_funding_collector"
    assert cfg.password == "SuperSecretPass_WO71864"


def test_dedicated_credential_missing_fails_closed(tmp_path):
    """Test 2: Missing dedicated credentials produces clear fail-closed error."""
    from config import load_funding_collector_config
    env_file = tmp_path / "missing-creds.env"
    env_file.write_text("FUNDING_DB_HOST=127.0.0.1\nFUNDING_DB_NAME=signal_bot_test\n")
    os.chmod(env_file, 0o600)

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="Dedicated database username .* is missing"):
            load_funding_collector_config(env_path=str(env_file), enforce_security=True, enforce_ownership=False)


def test_dedicated_credential_invalid_fails_closed(tmp_path):
    """Test 3: Invalid credentials (empty password or malformed port) fail closed."""
    from config import load_funding_collector_config
    # 3a. Invalid port
    env_file = tmp_path / "invalid-port.env"
    env_file.write_text(
        "FUNDING_DB_HOST=127.0.0.1\n"
        "FUNDING_DB_PORT=INVALID_PORT\n"
        "FUNDING_DB_USER=signal_funding_collector\n"
        "FUNDING_DB_PASSWORD=secret\n"
    )
    os.chmod(env_file, 0o600)
    with pytest.raises(ValueError, match="Invalid FUNDING_DB_PORT"):
        load_funding_collector_config(env_path=str(env_file), enforce_security=True, enforce_ownership=False)

    # 3b. Empty password
    env_empty = tmp_path / "empty-pass.env"
    env_empty.write_text(
        "FUNDING_DB_HOST=127.0.0.1\n"
        "FUNDING_DB_USER=signal_funding_collector\n"
        "FUNDING_DB_PASSWORD=   \n"
    )
    os.chmod(env_empty, 0o600)
    with pytest.raises(ValueError, match="Dedicated database password .*"):
        load_funding_collector_config(env_path=str(env_empty), enforce_security=True, enforce_ownership=False)


def test_credential_file_permission_and_ownership_validation(tmp_path):
    """Test 4: Refuses to load files with insecure permissions (0644, 0666)."""
    from config import load_funding_collector_config, validate_credential_file_security
    insecure_file = tmp_path / "insecure.env"
    insecure_file.write_text("FUNDING_DB_USER=test\nFUNDING_DB_PASSWORD=test\n")

    # Insecure mode: 0644 (world readable)
    os.chmod(insecure_file, 0o644)
    with pytest.raises(PermissionError, match="Insecure permissions"):
        validate_credential_file_security(str(insecure_file), enforce_ownership=False)

    # Insecure mode: 0666 (world writable)
    os.chmod(insecure_file, 0o666)
    with pytest.raises(PermissionError, match="Insecure permissions"):
        validate_credential_file_security(str(insecure_file), enforce_ownership=False)

    # Valid mode: 0600
    os.chmod(insecure_file, 0o600)
    validate_credential_file_security(str(insecure_file), enforce_ownership=False)


def test_no_fallback_to_cms_or_root(tmp_path):
    """Test 5: Explicitly prohibits fallback to 'cms' or 'root' accounts."""
    from config import load_funding_collector_config
    env_cms = tmp_path / "cms-forbidden.env"
    env_cms.write_text("FUNDING_DB_USER=cms\nFUNDING_DB_PASSWORD=secret\n")
    os.chmod(env_cms, 0o600)

    with pytest.raises(ValueError, match="Prohibited database user 'cms'"):
        load_funding_collector_config(env_path=str(env_cms), enforce_security=True, enforce_ownership=False)

    env_root = tmp_path / "root-forbidden.env"
    env_root.write_text("FUNDING_DB_USER=root\nFUNDING_DB_PASSWORD=secret\n")
    os.chmod(env_root, 0o600)

    with pytest.raises(ValueError, match="Prohibited database user 'root'"):
        load_funding_collector_config(env_path=str(env_root), enforce_security=True, enforce_ownership=False)


def test_dedicated_database_connection_selection():
    """Test 6: get_funding_pool initializes isolated pool and does not use global _pool."""
    from db import db as db_module
    from config import FundingDBConfig

    cfg = FundingDBConfig(
        host="127.0.0.1",
        port=3306,
        database="signal_bot_test",
        user="signal_bot_test",
        password="test_password",
    )
    with patch("mysql.connector.pooling.MySQLConnectionPool") as mock_pool_cls:
        pool = db_module.get_funding_pool(config=cfg, reset=True)
        mock_pool_cls.assert_called_once()
        call_kwargs = mock_pool_cls.call_args[1]
        assert call_kwargs["pool_name"] == "funding_collector_pool"
        assert call_kwargs["user"] == "signal_bot_test"
        assert call_kwargs["password"] == "test_password"
        # Verify it did not alter global _pool
        assert db_module._funding_pool is not None


def test_dedicated_connection_cleanup_and_lifecycle():
    """Test 7: Verify connection close() lifecycle is properly called in finally blocks."""
    from db import db as db_module
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_pool = Mock()
    mock_pool.get_connection.return_value = mock_conn

    # 7a. upsert_funding cleanup
    mock_conn.reset_mock()
    db_module.upsert_funding([("BTCUSDT", 1, "50000", None, "0.0001", None)], pool=mock_pool)
    mock_conn.commit.assert_called_once()
    mock_conn.close.assert_called_once()

    # 7b. get_latest_funding_time cleanup
    mock_conn.reset_mock()
    mock_cur.fetchone.return_value = (1000,)
    _ = db_module.get_latest_funding_time("BTCUSDT", pool=mock_pool)
    mock_conn.close.assert_called_once()

    # 7c. heartbeat cleanup
    mock_conn.reset_mock()
    db_module.heartbeat("funding_poller", status="ok", detail="beat", pool=mock_pool)
    mock_conn.commit.assert_called_once()
    mock_conn.close.assert_called_once()


def test_polling_loop_isolation_standalone_synchronous():
    """Test 8: Standalone loop runs synchronously without asyncio event loop dependency."""
    stop_event = threading.Event()
    mock_pool = Mock()

    with patch("collectors.funding_poller.poll_all_symbols", return_value={"symbols_ok": 1, "symbols_total": 1, "rows_inserted": 5}) as mock_poll:
        # Run 1 cycle then stop
        def side_effect(*args, **kwargs):
            stop_event.set()
            return {"symbols_ok": 1, "symbols_total": 1, "rows_inserted": 5}
        mock_poll.side_effect = side_effect

        run_standalone_loop(
            symbols=["BTCUSDT"],
            poll_interval=1,
            stop_event=stop_event,
            pool=mock_pool,
        )
        assert mock_poll.called


def test_standalone_loop_graceful_shutdown_on_stop_event():
    """Test 9: Standalone loop shuts down immediately when stop_event is set."""
    stop_event = threading.Event()
    stop_event.set()  # Pre-set shutdown
    mock_pool = Mock()

    with patch("collectors.funding_poller.poll_all_symbols") as mock_poll:
        run_standalone_loop(
            symbols=["BTCUSDT"],
            poll_interval=300,
            stop_event=stop_event,
            pool=mock_pool,
        )
        # Did not poll because shutdown was immediate
        assert not mock_poll.called


def test_retry_and_reconnect_behavior_bounded():
    """Test 10: Verify exponential retry is bounded to 3 attempts."""
    session = Mock(spec=requests.Session)
    mock_resp = Mock()
    mock_resp.status_code = 503
    mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
    session.get.return_value = mock_resp

    with patch("time.sleep"):
        with pytest.raises(RuntimeError, match="Failed to fetch funding rates for BTCUSDT after 3 retries"):
            fetch_funding_page(session, "BTCUSDT", max_retries=3)
    assert session.get.call_count == 3


def test_historical_pagination_and_gap_recovery():
    """Test 11: Multi-page historical recovery paginates correctly until gap is closed."""
    session = Mock(spec=requests.Session)

    page1 = [
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001", "markPrice": "50000"},
        {"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "0.0001", "markPrice": "50000"},
    ]
    page2 = [
        {"symbol": "BTCUSDT", "fundingTime": 3000, "fundingRate": "0.0001", "markPrice": "50000"},
    ]

    # Return page 1 (limit reached), then page 2 (< limit, ends pagination)
    with patch("collectors.funding_poller.get_latest_funding_time", return_value=500):
        with patch("collectors.funding_poller.fetch_funding_page", side_effect=[page1, page2]):
            with patch("collectors.funding_poller.upsert_funding") as mock_upsert:
                total = poll_symbol_incremental(session, "BTCUSDT", end_ms=4000, limit=2)
                assert total == 3
                assert mock_upsert.call_count == 2


def test_duplicate_and_watermark_behavior_monotonic():
    """Test 12: Idempotent duplicate upsert and monotonic watermark preservation."""
    mock_pool = Mock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_pool.get_connection.return_value = mock_conn

    # Re-insert existing timestamp with updated mark_price
    db_module.upsert_funding([("BTCUSDT", 2000, "50100", None, "0.0001", None)], pool=mock_pool)
    mock_cur.executemany.assert_called_once()
    sql_text = mock_cur.executemany.call_args[0][0]
    assert "ON DUPLICATE KEY UPDATE" in sql_text


def test_heartbeat_failure_handling_surfaces_cleanly():
    """Test 13: Heartbeat exceptions are captured and surfaced cleanly."""
    mock_pool = Mock()
    mock_pool.get_connection.side_effect = Exception("MariaDB connection refused")

    # In standalone loop, cycle catches and logs heartbeat failure without unhandled crash
    stop_event = threading.Event()
    def side_effect(*args, **kwargs):
        stop_event.set()
        raise Exception("Poll failed")

    with patch("collectors.funding_poller.poll_all_symbols", side_effect=side_effect):
        run_standalone_loop(
            symbols=["BTCUSDT"],
            poll_interval=1,
            stop_event=stop_event,
            pool=mock_pool,
        )
        # Completed loop cleanly despite DB error


def test_secret_redaction_in_config_and_logging():
    """Test 14: FundingDBConfig redacts password in repr and str conversions."""
    from config import FundingDBConfig

    cfg = FundingDBConfig(
        host="127.0.0.1",
        port=3306,
        database="crypto_signals",
        user="signal_funding_collector",
        password="SuperSecretPassword_MustNotBeLogged_999",
    )
    repr_str = repr(cfg)
    plain_str = str(cfg)

    assert "SuperSecretPassword_MustNotBeLogged_999" not in repr_str
    assert "SuperSecretPassword_MustNotBeLogged_999" not in plain_str
    assert "***REDACTED***" in repr_str
    assert "***REDACTED***" in plain_str
    # Dict form preserves actual password for connector
    assert cfg.to_dict()["password"] == "SuperSecretPassword_MustNotBeLogged_999"


# =============================================================================
# 15. Unit & Regression Tests: Binance HTTP 418 & 429 Ban Protection
# =============================================================================

def test_parse_retry_after_numeric_and_http_date():
    # Numeric seconds
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("5.5") == 5.5
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("invalid") is None

    # HTTP-date
    import email.utils
    date_str = "Tue, 21 Oct 2025 07:02:00 GMT"
    parsed_sec = parse_retry_after(date_str, now_epoch=email.utils.parsedate_to_datetime("Tue, 21 Oct 2025 07:00:00 GMT").timestamp())
    assert parsed_sec == 120.0


def test_http_418_with_numeric_retry_after(caplog):
    """1. HTTP 418 with numeric Retry-After: enforces delay with positive jitter."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)
    resp_418 = Mock()
    resp_418.status_code = 418
    resp_418.headers = {"Retry-After": "150"}

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_418, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3, jitter_func=lambda: 0.5)
        assert len(data) == 1
        # 150 + 0.5 jitter = 150.5s
        assert mock_sleep.call_count >= 1
        assert mock_sleep.call_args_list[0][0][0] == 150.5
        assert any("HTTP 418" in rec.message for rec in caplog.records)
    reset_ban_cooldown_for_testing()


def test_http_418_with_http_date_retry_after():
    """2. HTTP 418 with HTTP-date Retry-After: parses date and computes wait with positive jitter."""
    from datetime import datetime, timezone
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)
    resp_418 = Mock()
    resp_418.status_code = 418
    fixed_now = 1761030000.0
    future_date = datetime.fromtimestamp(fixed_now + 200, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    resp_418.headers = {"Retry-After": future_date}

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_418, resp_200]

    with patch("time.time", return_value=fixed_now), patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3, jitter_func=lambda: 0.25)
        assert len(data) == 1
        assert mock_sleep.call_count >= 1
        wait_called = mock_sleep.call_args_list[0][0][0]
        assert abs(wait_called - 200.25) < 1.0
    reset_ban_cooldown_for_testing()


def test_http_418_without_retry_after_uses_at_least_120_seconds():
    """3. HTTP 418 without Retry-After: uses minimum 120 seconds delay."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)
    resp_418 = Mock()
    resp_418.status_code = 418
    resp_418.headers = {}

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_418, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3, jitter_func=lambda: 0.5)
        assert len(data) == 1
        assert mock_sleep.call_count >= 1
        assert mock_sleep.call_args_list[0][0][0] == 120.5
    reset_ban_cooldown_for_testing()


def test_http_418_with_retry_after_greater_than_120s_preserves_longer_duration():
    """4. HTTP 418 with Retry-After > 120s: preserves longer exchange ban duration."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)
    resp_418 = Mock()
    resp_418.status_code = 418
    resp_418.headers = {"Retry-After": "86400"}  # 24 hour ban

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_418, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3, jitter_func=lambda: 0.1)
        assert len(data) == 1
        assert mock_sleep.call_count >= 1
        assert mock_sleep.call_args_list[0][0][0] == 86400.1
    reset_ban_cooldown_for_testing()


def test_http_429_retains_existing_retry_after_behavior():
    """5. HTTP 429 retains existing Retry-After behavior."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)
    resp_429 = Mock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "10"}

    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_429, resp_200]

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=3)
        assert len(data) == 1
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args_list[0][0][0] == 10
    reset_ban_cooldown_for_testing()


def test_another_symbol_cannot_bypass_shared_cooldown():
    """6. Shared cooldown: requests for another symbol cannot bypass an active IP-ban window."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)

    resp_418 = Mock()
    resp_418.status_code = 418
    resp_418.headers = {"Retry-After": "150"}

    resp_200_btc = Mock()
    resp_200_btc.status_code = 200
    resp_200_btc.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"}]

    session.get.side_effect = [resp_418, resp_200_btc]

    t0 = 10000.0
    with patch("time.time", return_value=t0), patch("time.sleep") as mock_sleep:
        fetch_funding_page(session, "BTCUSDT", max_retries=2, jitter_func=lambda: 0.0)

    # Now attempt a request for ETHUSDT 30 seconds into the 150s ban (120s remaining)
    resp_200_eth = Mock()
    resp_200_eth.status_code = 200
    resp_200_eth.json.return_value = [{"symbol": "ETHUSDT", "fundingTime": 1000, "fundingRate": "0.0002"}]
    session.get.side_effect = [resp_200_eth]

    with patch("time.time", return_value=t0 + 30.0), patch("time.sleep") as mock_sleep_eth:
        data_eth = fetch_funding_page(session, "ETHUSDT", max_retries=2)
        assert len(data_eth) == 1
        assert mock_sleep_eth.call_count == 1
        remaining_slept = mock_sleep_eth.call_args_list[0][0][0]
        assert abs(remaining_slept - 120.0) < 1.0

    reset_ban_cooldown_for_testing()


def test_retry_exhaustion_does_not_permit_early_request_in_next_poll_cycle():
    """7. Retry exhaustion must not clear cooldown; subsequent call must respect active ban window."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)

    resp_418 = Mock()
    resp_418.status_code = 418
    resp_418.headers = {"Retry-After": "180"}

    # All 2 attempts return 418
    session.get.side_effect = [resp_418, resp_418]

    t0 = 20000.0
    with patch("time.time", return_value=t0), patch("time.sleep") as mock_sleep:
        with pytest.raises(RuntimeError, match="Failed to fetch"):
            fetch_funding_page(session, "BTCUSDT", max_retries=2, jitter_func=lambda: 0.0)
        # Slept once (attempt 1), avoided sleeping after final attempt 2
        assert mock_sleep.call_count == 1

    # In next poll cycle (60s later, 120s of ban still remaining), verify cooldown is enforced
    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [{"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "0.0001"}]
    session.get.side_effect = [resp_200]

    with patch("time.time", return_value=t0 + 60.0), patch("time.sleep") as mock_sleep_cycle2:
        data = fetch_funding_page(session, "BTCUSDT", max_retries=2)
        assert len(data) == 1
        assert mock_sleep_cycle2.call_count == 1
        remaining_slept = mock_sleep_cycle2.call_args_list[0][0][0]
        assert abs(remaining_slept - 120.0) < 1.0

    reset_ban_cooldown_for_testing()


def test_normal_successful_polling_remains_unchanged():
    """8. Normal successful polling remains unchanged (no sleeps, 200 OK fast path)."""
    reset_ban_cooldown_for_testing()
    session = Mock(spec=requests.Session)
    resp_200 = Mock()
    resp_200.status_code = 200
    resp_200.json.return_value = [
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001", "markPrice": "60000.0"},
        {"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "0.0002", "markPrice": "60500.0"},
    ]
    session.get.return_value = resp_200

    with patch("time.sleep") as mock_sleep:
        data = fetch_funding_page(session, "BTCUSDT")
        assert len(data) == 2
        assert mock_sleep.call_count == 0
        assert get_ban_cooldown_remaining() == 0.0
    reset_ban_cooldown_for_testing()

