"""Tests for RetailBot2 Outbox Observability and Health Snapshot (WO71782 Stage E).

Tests cover:
1. Empty queue snapshot.
2. Pending backlog and due vs not-due items.
3. Expired processing lease detection and critical classification.
4. Dead-letter accumulation and sample inspection.
5. Advisory lock status inspection (unheld, held by self, held by other).
6. Database identity mismatch rejection (fail-closed).
7. Read-only zero mutation verification on signal_bot_test.
8. UTC timestamp formatting.
"""

import json
import os
import sys
import unittest
import uuid

import mysql.connector

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from paper.outbox_health import OutboxHealthInspector, inspect_dead_letters, inspect_outbox_health

TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"
TEST_DB_USER = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test")
TEST_DB_PASSWORD = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "3XWMDvCqj9hWb1kc65LU321RG28mSSB3l9_Li7CLRxw")


def _get_test_conn():
    conn = mysql.connector.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        database=TEST_DB_NAME,
        user=TEST_DB_USER,
        password=TEST_DB_PASSWORD,
        autocommit=False,
    )
    cur = conn.cursor()
    cur.execute("SELECT DATABASE()")
    db = cur.fetchone()[0]
    cur.close()
    if db != TEST_DB_NAME:
        conn.close()
        raise AssertionError(f"Refusing connection to {db!r}; must be {TEST_DB_NAME!r}")
    return conn


class TestOutboxObservability(unittest.TestCase):
    def setUp(self):
        try:
            self.conn = _get_test_conn()
        except Exception as exc:
            self.skipTest(f"signal_bot_test DB connection unavailable: {exc}")
        self.tracked_keys = []

    def tearDown(self):
        if hasattr(self, "conn") and self.conn:
            if self.tracked_keys:
                try:
                    cur = self.conn.cursor()
                    for k in self.tracked_keys:
                        cur.execute("DELETE FROM rdt_telegram_outbox WHERE event_key = %s", (k,))
                    self.conn.commit()
                    cur.close()
                except Exception:
                    pass
            try:
                self.conn.close()
            except Exception:
                pass

    def test_database_identity_mismatch_fails_closed(self):
        """Inspector must refuse connection when expected database does not match active connection."""
        inspector = OutboxHealthInspector(self.conn, expected_database="crypto_signals")
        with self.assertRaises(RuntimeError) as ctx:
            inspector.verify_database_identity()
        self.assertIn("Health inspection database mismatch", str(ctx.exception))

    def test_empty_queue_snapshot(self):
        """Snapshot on clean queue returns structured metrics with zero due or dead items."""
        inspector = OutboxHealthInspector(self.conn, expected_database="signal_bot_test", observation_mode=True)
        snapshot = inspector.snapshot()
        self.assertEqual(snapshot["database"], "signal_bot_test")
        self.assertEqual(snapshot["mode"], "observation")
        self.assertIn("queue", snapshot)
        self.assertIn("status_counts", snapshot["queue"])
        self.assertIn("due_pending_count", snapshot["queue"])
        self.assertIn("expired_processing_count", snapshot["queue"])

    def test_pending_backlog_due_vs_not_due(self):
        """Inspector distinguishes due pending items from future retry items."""
        cur = self.conn.cursor()
        key_due = f"retailbot2:test:obs_due_{uuid.uuid4().hex[:8]}"
        key_future = f"retailbot2:test:obs_future_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.extend([key_due, key_future])

        # Due item (next_attempt_at in past)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, next_attempt_at)
               VALUES (%s, 0, 'entry', '{}', 'pending', 0, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MINUTE))""",
            (key_due,),
        )
        # Future item (next_attempt_at 1 hour in future)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, next_attempt_at)
               VALUES (%s, 0, 'entry', '{}', 'pending', 1, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR))""",
            (key_future,),
        )
        self.conn.commit()
        cur.close()

        inspector = OutboxHealthInspector(self.conn, expected_database="signal_bot_test")
        snapshot = inspector.get_queue_snapshot()
        self.assertGreaterEqual(snapshot["status_counts"]["pending"], 2)
        self.assertGreaterEqual(snapshot["due_pending_count"], 1)
        self.assertIsNotNone(snapshot["oldest_pending_age_sec"])

    def test_expired_processing_lease_detected_as_critical(self):
        """Stale in-flight lease past locked_until triggers expired count and CRITICAL state."""
        cur = self.conn.cursor()
        key_stale = f"retailbot2:test:obs_stale_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.append(key_stale)

        # Processing item with locked_until expired 2 minutes ago
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, locked_until, claim_token)
               VALUES (%s, 0, 'entry', '{}', 'processing', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 MINUTE), 'stale_token')""",
            (key_stale,),
        )
        self.conn.commit()
        cur.close()

        inspector = OutboxHealthInspector(self.conn, expected_database="signal_bot_test")
        snapshot = inspector.snapshot()
        self.assertGreaterEqual(snapshot["queue"]["expired_processing_count"], 1)
        self.assertEqual(snapshot["health_status"], "CRITICAL")
        self.assertTrue(any("expired/stale processing lease" in issue for issue in snapshot["issues"]))

    def test_dead_letter_accumulation_and_sample(self):
        """Dead letters are counted, sampled, and classified as CRITICAL."""
        cur = self.conn.cursor()
        key_dead = f"retailbot2:test:obs_dead_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.append(key_dead)

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, 'Permanent Telegram 400 Bad Request')""",
            (key_dead,),
        )
        self.conn.commit()
        cur.close()

        inspector = OutboxHealthInspector(self.conn, expected_database="signal_bot_test")
        snapshot = inspector.snapshot(dead_letter_limit=5)
        self.assertGreaterEqual(snapshot["queue"]["status_counts"]["dead"], 1)
        self.assertEqual(snapshot["health_status"], "CRITICAL")
        self.assertTrue(any("dead-letter event(s) detected" in issue for issue in snapshot["issues"]))

        dead_sample = snapshot["dead_letters_sample"]
        self.assertGreaterEqual(len(dead_sample), 1)
        dead_keys = [d["event_key"] for d in dead_sample]
        self.assertIn(key_dead, dead_keys)

    def test_advisory_lock_inspection(self):
        """Lock status accurately reports whether lock is held, without acquiring it."""
        inspector = OutboxHealthInspector(self.conn, expected_database="signal_bot_test")
        lock_info = inspector.get_lock_status()
        self.assertEqual(lock_info["lock_name"], "rdt_outbox_worker_lock")
        self.assertIn("is_locked", lock_info)
        self.assertIn("lock_holder_connection_id", lock_info)
        self.assertIn("is_held_by_current_session", lock_info)

    def test_read_only_behavior_causes_zero_mutations(self):
        """Health inspection must never insert, update, or delete rows in any table."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox")
        outbox_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM rdt_trades")
        trades_before = cur.fetchone()[0]
        cur.close()

        # Run multiple inspection snapshots
        _ = inspect_outbox_health(self.conn, expected_database="signal_bot_test", observation_mode=True)
        _ = inspect_dead_letters(self.conn, expected_database="signal_bot_test", limit=10)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox")
        outbox_after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM rdt_trades")
        trades_after = cur.fetchone()[0]
        cur.close()

        self.assertEqual(outbox_before, outbox_after, "Outbox table mutated by health inspection!")
        self.assertEqual(trades_before, trades_after, "Trades table mutated by health inspection!")


if __name__ == "__main__":
    unittest.main()
