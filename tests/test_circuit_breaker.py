import unittest
from unittest.mock import patch, MagicMock

from paper.circuit_breaker import (
    trip_emergency_stop, reset_emergency_stop, check_circuit_breakers,
    get_circuit_breaker_status, DEFAULT_MAX_CONCURRENT_POSITIONS
)


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
        with patch("paper.circuit_breaker.get_pool") as mock_get_pool:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_get_pool.return_value.get_connection.return_value = mock_conn
            mock_cur.fetchone.return_value = {"active_count": 0}
            mock_conn.cursor.return_value = mock_cur
            with patch("paper.circuit_breaker.get_daily_realized_loss_usd", return_value=0.0):
                status = get_circuit_breaker_status()
        self.assertFalse(status["emergency_stop_active"])

    @patch("paper.circuit_breaker.get_pool")
    def test_max_concurrent_positions_gate(self, mock_get_pool):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_pool.return_value.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur

        # Return 5 active positions (at max limit)
        mock_cur.fetchone.return_value = {"active_count": 5}

        allowed, reason = check_circuit_breakers(max_concurrent=5)
        self.assertFalse(allowed)
        self.assertIn("max concurrent positions reached", reason)

    @patch("paper.circuit_breaker.get_daily_realized_loss_usd")
    @patch("paper.circuit_breaker.get_pool")
    def test_max_daily_loss_gate(self, mock_get_pool, mock_daily_loss):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_pool.return_value.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"active_count": 1}

        # Simulate losing $600 today against a $500 limit
        mock_daily_loss.return_value = -600.0

        allowed, reason = check_circuit_breakers(max_daily_loss=500.0)
        self.assertFalse(allowed)
        self.assertIn("max daily loss exceeded", reason)

    @patch("paper.circuit_breaker.get_pool", side_effect=RuntimeError("database unavailable"))
    def test_active_position_query_failure_blocks_new_entries(self, _mock_get_pool):
        allowed, reason = check_circuit_breakers()
        self.assertFalse(allowed)
        self.assertIn("cannot verify active positions", reason)

    @patch("paper.circuit_breaker.get_daily_realized_loss_usd", return_value=None)
    @patch("paper.circuit_breaker.get_pool")
    def test_daily_pnl_query_failure_blocks_new_entries(self, mock_get_pool, _mock_daily_pnl):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_pool.return_value.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"active_count": 0}

        allowed, reason = check_circuit_breakers()
        self.assertFalse(allowed)
        self.assertIn("cannot verify daily realized PnL", reason)

    @patch("paper.circuit_breaker.get_pool", side_effect=RuntimeError("database unavailable"))
    def test_daily_pnl_helper_reports_unknown_instead_of_zero_on_db_failure(self, _mock_get_pool):
        from paper.circuit_breaker import get_daily_realized_loss_usd
        self.assertIsNone(get_daily_realized_loss_usd())


if __name__ == "__main__":
    unittest.main()
