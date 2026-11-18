"""
Tests for Work Order 71874: Futures Candle Closure Integrity Fix.

Covers:
1. Candle close timestamp in the future -> is_closed=0.
2. Current time equals close timestamp -> is_closed=0.
3. Current time is strictly later than close timestamp -> is_closed=1.
4. A partial candle cannot overwrite an already closed candle (upsert_ohlcv poison guard).
5. A verified final candle can close an incomplete candle.
6. Duplicate final-candle processing is idempotent.
7. UTC boundary and timezone handling are correct.
8. Missing or malformed close timestamps fail closed.
9. Different supported intervals (15m, 1h, 4h, 1d) evaluate close timestamps correctly.
10. Spot-candle behavior remains consistent and safe across backfill and live ingestion.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from collectors.backfill_klines import is_candle_closed, klines_to_rows
import db.db as db_mod


# --- Test 1, 2, 3: Direct Closure Rule Tests with Frozen Clocks ---

def test_closure_rule_future_close_time_is_open():
    """Scenario 1: Candle close timestamp is in the future -> is_closed=0."""
    now_ms = 1787737800000        # 16:50:00.000 UTC
    close_time = 1787738399999    # 16:59:59.999 UTC (future)
    assert is_candle_closed(close_time, now_ms=now_ms) == 0


def test_closure_rule_exact_equal_timestamp_is_open():
    """Scenario 2: Current time exactly equals close timestamp -> is_closed=0."""
    close_time = 1787738399999
    now_ms = 1787738399999
    assert is_candle_closed(close_time, now_ms=now_ms) == 0


def test_closure_rule_strictly_later_timestamp_is_closed():
    """Scenario 3: Current time is strictly later than close timestamp -> is_closed=1."""
    close_time = 1787738399999
    now_ms = 1787738400000        # 1 millisecond past close
    assert is_candle_closed(close_time, now_ms=now_ms) == 1

    # Further in future
    assert is_candle_closed(close_time, now_ms=now_ms + 60000) == 1


# --- Test 8: Missing or Malformed Close Timestamps Fail Closed ---

def test_closure_rule_malformed_and_missing_timestamps_fail_closed():
    """Scenario 8: Missing, None, negative, or invalid timestamps fail closed (0)."""
    now_ms = 1787738400000
    assert is_candle_closed(None, now_ms=now_ms) == 0
    assert is_candle_closed("", now_ms=now_ms) == 0
    assert is_candle_closed("invalid_int", now_ms=now_ms) == 0
    assert is_candle_closed(-1000, now_ms=now_ms) == 0
    assert is_candle_closed(0, now_ms=now_ms) == 0
    assert is_candle_closed(1787738399999, now_ms="invalid_now") == 0


# --- Test 9: Supported Intervals Evaluation ---

def test_different_supported_intervals_closure_evaluation():
    """
    Scenario 9: Validates that 15m, 1h, 4h, and 1d Binance intervals
    evaluate the exchange-provided close_time accurately against frozen clocks.
    """
    intervals = [
        # (interval_name, duration_ms)
        ("15m", 15 * 60 * 1000),
        ("1h", 60 * 60 * 1000),
        ("4h", 4 * 60 * 60 * 1000),
        ("1d", 24 * 60 * 60 * 1000),
    ]
    base_open_ms = 1700000000000  # Arbitrary aligned epoch ms

    for name, dur_ms in intervals:
        close_time = base_open_ms + dur_ms - 1

        # Check mid-candle
        mid_now = base_open_ms + (dur_ms // 2)
        assert is_candle_closed(close_time, now_ms=mid_now) == 0, f"{name} should be open mid-interval"

        # Check exact close timestamp
        assert is_candle_closed(close_time, now_ms=close_time) == 0, f"{name} should be open at close_time"

        # Check 1 ms after close
        assert is_candle_closed(close_time, now_ms=close_time + 1) == 1, f"{name} should be closed +1ms"


# --- Test 7: UTC Boundary and Timezone Handling ---

def test_utc_boundary_and_midnight_transitions():
    """
    Scenario 7: Verifies clean UTC boundary transitions at midnight (day rollover)
    and month/year boundaries without local timezone skew.
    """
    # UTC Midnight transition: 2026-12-31 23:59:59.999 UTC
    dt_close = datetime(2026, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)
    close_time_ms = int(dt_close.timestamp() * 1000)

    # 1ms before
    now_before = close_time_ms - 1
    assert is_candle_closed(close_time_ms, now_ms=now_before) == 0

    # Exactly at boundary
    assert is_candle_closed(close_time_ms, now_ms=close_time_ms) == 0

    # 1ms after (in new year: 2027-01-01 00:00:00.000 UTC)
    now_after = close_time_ms + 1
    assert is_candle_closed(close_time_ms, now_ms=now_after) == 1


# --- Test 10: Spot vs Futures Consistency in klines_to_rows ---

def test_klines_to_rows_spot_and_futures_consistency():
    """
    Scenario 10: Verifies klines_to_rows processes both spot and futures identically,
    preventing premature closure of the in-progress tip candle for both markets.
    """
    now_ms = 1787738000000  # 16:50:00 UTC

    raw_klines = [
        # Candle 1: Fully completed 16:30 - 16:44:59.999
        [1787736600000, "61000", "61100", "60900", "61050", "100.0", 1787737499999, "6100000", 1500, "50.0", "3050000", "0"],
        # Candle 2: Currently forming 16:45 - 16:59:59.999 (now_ms is 16:50:00)
        [1787737500000, "61050", "61200", "61010", "61150", "45.0", 1787738399999, "2750000", 800, "25.0", "1525000", "0"],
    ]

    for market in ["spot", "futures"]:
        rows = klines_to_rows("BTCUSDT", market, "15m", raw_klines, now_ms=now_ms)
        assert len(rows) == 2

        # Completed candle must have is_closed == 1
        assert rows[0][14] == 1, f"{market} historical candle must be closed"
        assert rows[0][3] == 1787736600000
        assert rows[0][4] == 1787737499999

        # Forming candle must have is_closed == 0
        assert rows[1][14] == 0, f"{market} in-progress candle must NOT be marked closed"
        assert rows[1][3] == 1787737500000
        assert rows[1][4] == 1787738399999


# --- Test 4, 5, 6: State Transition Logic and Poison Guard ---

def test_upsert_ohlcv_sql_query_has_poison_guard():
    """
    Verifies that upsert_ohlcv executes an ON DUPLICATE KEY UPDATE query
    that includes the state transition guard preventing partial rows
    from overwriting verified closed rows.
    """
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn

    test_rows = [
        ("BTCUSDT", "futures", "15m", 1787737500000, 1787738399999,
         "61000", "61100", "60900", "61050", "100.0", "6100000", 1500, "50.0", "3050000", 1)
    ]

    db_mod.upsert_ohlcv(test_rows, pool=mock_pool)

    assert mock_cursor.executemany.called
    executed_sql = mock_cursor.executemany.call_args[0][0]

    # Verify query contains conditional guards on ohlcv.is_closed = 1 AND VALUES(is_closed) = 0
    assert "IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.open, VALUES(open))" in executed_sql
    assert "IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.volume, VALUES(volume))" in executed_sql
    assert "is_closed = GREATEST(ohlcv.is_closed, VALUES(is_closed))" in executed_sql


def test_upsert_ohlcv_state_transition_matrix_simulation():
    """
    Scenarios 4, 5, 6: Pure Python verification of the MariaDB ON DUPLICATE KEY UPDATE
    state-transition semantics.
    Simulates the exact SQL expressions:
      new_val = existing_val if (existing_closed == 1 and incoming_closed == 0) else incoming_val
      new_closed = max(existing_closed, incoming_closed)
    """
    def apply_transition(existing_record, incoming_record):
        """Simulates MariaDB ON DUPLICATE KEY UPDATE logic."""
        if existing_record is None:
            return incoming_record  # First insert

        ex_closed = existing_record["is_closed"]
        in_closed = incoming_record["is_closed"]

        poison_condition = (ex_closed == 1 and in_closed == 0)

        result = {}
        for col in ("open", "high", "low", "close", "volume", "quote_volume", "num_trades"):
            result[col] = existing_record[col] if poison_condition else incoming_record[col]

        result["is_closed"] = max(ex_closed, in_closed)
        result["open_time"] = existing_record["open_time"]
        result["close_time"] = existing_record["close_time"] if poison_condition else incoming_record["close_time"]
        return result

    # --- Scenario 4: Partial candle CANNOT overwrite already closed candle ---
    verified_closed = {
        "open_time": 1787737500000, "close_time": 1787738399999,
        "open": 61048.5, "high": 61168.0, "low": 60950.0, "close": 61022.0,
        "volume": 1401.037, "quote_volume": 85556128.9, "num_trades": 38408,
        "is_closed": 1
    }
    partial_incoming = {
        "open_time": 1787737500000, "close_time": 1787738399999,
        "open": 61048.5, "high": 61150.0, "low": 61010.1, "close": 61085.3,
        "volume": 785.120, "quote_volume": 47949850.2, "num_trades": 18673,
        "is_closed": 0
    }

    res_after_partial = apply_transition(verified_closed, partial_incoming)
    assert res_after_partial["is_closed"] == 1, "is_closed must remain 1"
    assert res_after_partial["volume"] == 1401.037, "Volume must NOT be overwritten with truncated value"
    assert res_after_partial["close"] == 61022.0, "Close price must NOT be overwritten"

    # --- Scenario 5: Verified final candle CAN close an incomplete candle ---
    forming_initial = {
        "open_time": 1787737500000, "close_time": 1787738399999,
        "open": 61048.5, "high": 61150.0, "low": 61010.1, "close": 61085.3,
        "volume": 785.120, "quote_volume": 47949850.2, "num_trades": 18673,
        "is_closed": 0
    }
    final_incoming = {
        "open_time": 1787737500000, "close_time": 1787738399999,
        "open": 61048.5, "high": 61168.0, "low": 60950.0, "close": 61022.0,
        "volume": 1401.037, "quote_volume": 85556128.9, "num_trades": 38408,
        "is_closed": 1
    }

    res_after_final = apply_transition(forming_initial, final_incoming)
    assert res_after_final["is_closed"] == 1, "is_closed must transition from 0 to 1"
    assert res_after_final["volume"] == 1401.037, "Volume must be updated to true final volume"
    assert res_after_final["close"] == 61022.0, "Close price must be updated to final close"

    # --- Scenario 6: Duplicate final candle is idempotent ---
    res_duplicate = apply_transition(res_after_final, final_incoming)
    assert res_duplicate == res_after_final, "Duplicate final candle processing must be idempotent"
