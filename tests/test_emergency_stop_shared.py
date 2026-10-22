"""The kill switch must be visible to OTHER processes (the paper engine), not only the web process."""
import unittest
from unittest.mock import MagicMock, patch

import pytest

import paper.circuit_breaker as cb

pytestmark = pytest.mark.real_flag_io
GET_CONN = "paper.circuit_breaker._get_conn"


class FakeFlagDB:
    """A tiny stand-in for the system_flags table shared by 'two processes'."""
    def __init__(self):
        self.row = None  # (is_active, reason)

    def conn(self):
        db = self
        cur = MagicMock()

        def execute(sql, params=None):
            if sql.startswith("INSERT INTO system_flags"):
                db.row = (params[1], params[2])
            elif sql.startswith("SELECT is_active"):
                cur.fetchone.return_value = None if db.row is None else {"is_active": db.row[0], "reason": db.row[1]}
        cur.execute.side_effect = execute
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn


class TestSharedEmergencyStop(unittest.TestCase):
    def setUp(self):
        self.db = FakeFlagDB()
        cb._emergency_stop_active = False
        cb._emergency_stop_reason = ""

    def test_trip_in_one_process_is_seen_by_another(self):
        with patch(GET_CONN, side_effect=lambda: self.db.conn()):
            cb.trip_emergency_stop("web dashboard")
            # simulate the engine process: fresh in-memory state, same database
            cb._emergency_stop_active = False
            cb._emergency_stop_reason = ""
            active, reason = cb.is_emergency_stop_active()
        self.assertTrue(active)
        self.assertEqual(reason, "web dashboard")

    def test_reset_clears_it_for_everyone(self):
        with patch(GET_CONN, side_effect=lambda: self.db.conn()):
            cb.trip_emergency_stop("x")
            cb.reset_emergency_stop()
            cb._emergency_stop_active = False
            self.assertEqual(cb.is_emergency_stop_active(), (False, ""))

    def test_tripped_flag_blocks_new_entries_from_the_engine_process(self):
        with patch(GET_CONN, side_effect=lambda: self.db.conn()):
            cb.trip_emergency_stop("halt")
            cb._emergency_stop_active = False  # engine process memory
            allowed, reason = cb.check_circuit_breakers()
        self.assertFalse(allowed)
        self.assertIn("kill-switch", reason)

    def test_unreadable_flag_table_fails_closed_and_memory_flag_still_works(self):
        with patch(GET_CONN, side_effect=RuntimeError("db down")):
            active, reason = cb.is_emergency_stop_active()
            self.assertTrue(active)
            self.assertIn("Unable to verify shared emergency-stop state", reason)

            # The paper engine must not open a position when it cannot verify
            # whether another process has activated the shared kill switch.
            allowed, entry_reason = cb.check_circuit_breakers()
            self.assertFalse(allowed)
            self.assertIn("new entries blocked", entry_reason)

            cb.trip_emergency_stop("local only")   # persist fails, must not raise
            self.assertEqual(cb.is_emergency_stop_active(), (True, "local only"))

    def tearDown(self):
        cb._emergency_stop_active = False
        cb._emergency_stop_reason = ""


if __name__ == "__main__":
    unittest.main()
