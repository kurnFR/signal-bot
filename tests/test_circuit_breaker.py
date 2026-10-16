import unittest
from unittest.mock import patch, MagicMock

from paper.circuit_breaker import (
    trip_emergency_stop, reset_emergency_stop, check_circuit_breakers,
    get_circuit_breaker_status, get_daily_realized_loss_usd,
    DEFAULT_MAX_CONCURRENT_POSITIONS,
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
        conn, _cur = _mock_conn({"active_count": 0}, {"total_net_pnl": 0.0})
        with patch(GET_CONN, return_value=conn):
            allowed, reason = check_circuit_breakers()
        self.assertTrue(allowed, reason)
        self.assertEqual(reason, "All circuit breakers nominal")

    def test_missing_rows_are_treated_as_zero_on_fresh_start(self):
        conn, _cur = _mock_conn(None, None)  # driver returned no row at all
        with patch(GET_CONN, return_value=conn):
            allowed, reason = check_circuit_breakers()
        self.assertTrue(allowed, reason)

    def test_daily_pnl_helper_returns_the_summed_value(self):
        conn, _cur = _mock_conn({"total_net_pnl": -42.5})
        with patch(GET_CONN, return_value=conn):
            self.assertEqual(get_daily_realized_loss_usd(), -42.5)

    def test_connections_are_closed_after_each_check(self):
        conn, cur = _mock_conn({"active_count": 0}, {"total_net_pnl": 0.0})
        with patch(GET_CONN, return_value=conn):
            check_circuit_breakers()
        self.assertGreaterEqual(conn.close.call_count, 2)  # position query + PnL query
        self.assertGreaterEqual(cur.close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
