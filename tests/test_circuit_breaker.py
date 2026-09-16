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

        # Reset re-enables entries
        reset_emergency_stop()
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


if __name__ == "__main__":
    unittest.main()
