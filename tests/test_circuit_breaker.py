import unittest
from unittest.mock import patch, MagicMock

from paper.circuit_breaker import (
    trip_emergency_stop, reset_emergency_stop, check_circuit_breakers,
    get_circuit_breaker_status, get_daily_realized_loss_usd,
    DEFAULT_MAX_CONCURRENT_POSITIONS, check_exposure_limit, get_open_notional_usd,
)

# The breaker opens a direct connection through paper.circuit_breaker._get_conn()
# (the shared pool was bypassed on purpose), so that is the one name to patch.
GET_CONN = "paper.circuit_breaker._get_conn"


def _mock_conn(*fetchone_results):
    """A fake connection whose cursor returns the given rows, one per fetchone()."""
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value = cur
    if len(fetchone_results) == 1:
        cur.fetchone.return_value = fetchone_results[0]
    else:
        cur.fetchone.side_effect = list(fetchone_results)
    return conn, cur


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        reset_emergency_stop()

    def tearDown(self):
        reset_emergency_stop()

    def test_emergency_stop_trips_and_blocks_entries(self):
        trip_emergency_stop("Manual test emergency stop")
        allowed, reason = check_circuit_breakers()
        self.assertFalse(allowed)
        self.assertIn("Emergency kill-switch is ACTIVE", reason)

        # Reset re-enables entries. Keep the status test isolated from MySQL.
        reset_emergency_stop()
        conn, _cur = _mock_conn({"active_count": 0})
        with patch(GET_CONN, return_value=conn), \
                patch("paper.circuit_breaker.get_daily_realized_loss_usd", return_value=0.0):
            status = get_circuit_breaker_status()
        self.assertFalse(status["emergency_stop_active"])

    def test_max_concurrent_positions_gate(self):
        conn, _cur = _mock_conn({"active_count": 5})  # at the limit
        with patch(GET_CONN, return_value=conn):
            allowed, reason = check_circuit_breakers(max_concurrent=5)
        self.assertFalse(allowed)
        self.assertIn("max concurrent positions reached", reason)

    def test_max_daily_loss_gate(self):
        conn, _cur = _mock_conn({"active_count": 1})
        # Simulate losing $600 today against a $500 limit
        with patch(GET_CONN, return_value=conn), \
                patch("paper.circuit_breaker.get_daily_realized_loss_usd", return_value=-600.0):
            allowed, reason = check_circuit_breakers(max_daily_loss=500.0)
        self.assertFalse(allowed)
        self.assertIn("max daily loss exceeded", reason)

    def test_active_position_query_failure_blocks_new_entries(self):
        with patch(GET_CONN, side_effect=RuntimeError("database unavailable")):
            allowed, reason = check_circuit_breakers()
        self.assertFalse(allowed)
        self.assertIn("cannot verify active positions", reason)

    def test_daily_pnl_query_failure_blocks_new_entries(self):
        conn, _cur = _mock_conn({"active_count": 0})
        with patch(GET_CONN, return_value=conn), \
                patch("paper.circuit_breaker.get_daily_realized_loss_usd", return_value=None):
            allowed, reason = check_circuit_breakers()
        self.assertFalse(allowed)
        self.assertIn("cannot verify daily realized PnL", reason)

    def test_daily_pnl_helper_reports_unknown_instead_of_zero_on_db_failure(self):
        with patch(GET_CONN, side_effect=RuntimeError("database unavailable")):
            self.assertIsNone(get_daily_realized_loss_usd())

    # --- fresh start (empty tables) must not block trading ------------------

    def test_empty_tables_allow_entries(self):
        # COUNT(*) -> 0 and COALESCE(SUM(...), 0.0) -> 0.0 on a brand-new database.
        conn, _cur = _mock_conn({"active_count": 0}, {"total_net_pnl": 0.0}, {"total": 0.0})
        with patch(GET_CONN, return_value=conn):
            allowed, reason = check_circuit_breakers()
        self.assertTrue(allowed, reason)
        self.assertEqual(reason, "All circuit breakers nominal")

    def test_missing_rows_are_treated_as_zero_on_fresh_start(self):
        conn, _cur = _mock_conn(None, None, None)  # driver returned no row at all
        with patch(GET_CONN, return_value=conn):
            allowed, reason = check_circuit_breakers()
        self.assertTrue(allowed, reason)

    def test_daily_pnl_helper_returns_the_summed_value(self):
        conn, _cur = _mock_conn({"total_net_pnl": -42.5})
        with patch(GET_CONN, return_value=conn):
            self.assertEqual(get_daily_realized_loss_usd(), -42.5)

    def test_connections_are_closed_after_each_check(self):
        conn, cur = _mock_conn({"active_count": 0}, {"total_net_pnl": 0.0}, {"total": 0.0})
        with patch(GET_CONN, return_value=conn):
            check_circuit_breakers()
        self.assertGreaterEqual(conn.close.call_count, 3)  # positions + PnL + exposure queries
        self.assertGreaterEqual(cur.close.call_count, 3)


class TestExposureLimit(unittest.TestCase):
    def setUp(self):
        reset_emergency_stop()

    def test_within_limit_allows(self):
        conn, _ = _mock_conn({"total": 10000.0})
        with patch(GET_CONN, return_value=conn):
            ok, _msg = check_exposure_limit(5000.0, max_total_notional=25000.0)
        self.assertTrue(ok)

    def test_new_position_that_would_exceed_the_cap_is_blocked(self):
        conn, _ = _mock_conn({"total": 22000.0})
        with patch(GET_CONN, return_value=conn):
            ok, msg = check_exposure_limit(5000.0, max_total_notional=25000.0)
        self.assertFalse(ok)
        self.assertIn("exposure limit", msg)

    def test_exactly_at_cap_is_allowed_one_cent_over_is_not(self):
        conn, _ = _mock_conn({"total": 20000.0})
        with patch(GET_CONN, return_value=conn):
            self.assertTrue(check_exposure_limit(5000.0, max_total_notional=25000.0)[0])
        conn, _ = _mock_conn({"total": 20000.0})
        with patch(GET_CONN, return_value=conn):
            self.assertFalse(check_exposure_limit(5000.01, max_total_notional=25000.0)[0])

    def test_unverifiable_exposure_fails_closed(self):
        with patch(GET_CONN, side_effect=RuntimeError("db down")):
            self.assertIsNone(get_open_notional_usd())
            ok, msg = check_exposure_limit(1.0)
        self.assertFalse(ok)
        self.assertIn("cannot verify", msg)

    def test_invalid_sizes_fail_closed(self):
        for bad in (-1.0, float("nan"), "abc", None):
            ok, _ = check_exposure_limit(bad)
            self.assertFalse(ok, bad)

    def test_already_over_cap_trips_the_general_breaker(self):
        conn, _ = _mock_conn({"active_count": 1}, {"total_net_pnl": 0.0}, {"total": 99999999.0})
        with patch(GET_CONN, return_value=conn):
            ok, msg = check_circuit_breakers()
        self.assertFalse(ok)
        self.assertIn("exposure", msg)


if __name__ == "__main__":
    unittest.main()
