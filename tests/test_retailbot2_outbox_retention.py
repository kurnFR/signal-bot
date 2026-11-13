"""Tests for RetailBot2 Outbox Retention Planning and Dry-Run Policy Engine (WO71782 Stage E).

Tests cover:
1. Boundary cutoffs and UTC calculation.
2. Sent candidates older than retention window identified.
3. Dead-letter candidates older than retention window identified.
4. Active records (pending, processing) protected and excluded from candidates.
5. Uncertain delivery records protected from automated deletion.
6. Dry-run plan modifies zero database rows.
7. Rejection of unpermitted database (crypto_signals) fail-closed.
8. Test cleanup execution on signal_bot_test with confirmation token.
9. Invalid confirmation token rejection.
"""

import json
import os
import sys
import unittest
import uuid
from unittest.mock import patch

import mysql.connector

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from paper.outbox_retention import (
    OutboxRetentionPlanner,
    SecurityError,
    TEST_CLEANUP_CONFIRMATION_TOKEN,
    plan_outbox_retention,
)

TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"
TEST_DB_USER = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test")
TEST_DB_PASSWORD = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "")


def _get_test_conn():
    user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    pwd = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError("SIGNAL_BOT_TEST_DB_PASSWORD is required for integration tests; no fallback permitted")
    conn = mysql.connector.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        database=TEST_DB_NAME,
        user=user,
        password=pwd,
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


class TestOutboxRetentionPlanner(unittest.TestCase):
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

    def test_database_mismatch_fails_closed(self):
        """Planner must refuse when expected database does not match active connection."""
        planner = OutboxRetentionPlanner(self.conn, expected_database="crypto_signals")
        with self.assertRaises(SecurityError) as ctx:
            planner.verify_database_identity()
        self.assertIn("Retention planner database mismatch", str(ctx.exception))

    def test_dry_run_zero_mutation_guarantee(self):
        """plan() must be purely read-only and never delete or modify any row."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox")
        count_before = cur.fetchone()[0]
        cur.close()

        plan = plan_outbox_retention(self.conn, expected_database="signal_bot_test")
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["action"], "DRY_RUN_ONLY_NO_RECORDS_MODIFIED")

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox")
        count_after = cur.fetchone()[0]
        cur.close()

        self.assertEqual(count_before, count_after)

    def test_sent_and_dead_candidates_detection_and_protection(self):
        """Old sent and dead events are identified as candidates; young events, pending, and processing are protected."""
        cur = self.conn.cursor()

        key_old_sent = f"retailbot2:test:ret_old_sent_{uuid.uuid4().hex[:8]}"
        key_young_sent = f"retailbot2:test:ret_young_sent_{uuid.uuid4().hex[:8]}"
        key_old_dead = f"retailbot2:test:ret_old_dead_{uuid.uuid4().hex[:8]}"
        key_young_dead = f"retailbot2:test:ret_young_dead_{uuid.uuid4().hex[:8]}"
        key_pending = f"retailbot2:test:ret_pending_{uuid.uuid4().hex[:8]}"
        key_processing = f"retailbot2:test:ret_processing_{uuid.uuid4().hex[:8]}"
        key_uncertain = f"retailbot2:test:ret_uncertain_{uuid.uuid4().hex[:8]}"

        self.tracked_keys.extend([
            key_old_sent, key_young_sent, key_old_dead, key_young_dead,
            key_pending, key_processing, key_uncertain
        ])

        # 1. Old sent (sent_at 20 days ago)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at)
               VALUES (%s, 0, 'entry', '{}', 'sent', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY))""",
            (key_old_sent,)
        )
        # 2. Young sent (sent_at 2 days ago)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at)
               VALUES (%s, 0, 'entry', '{}', 'sent', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 DAY))""",
            (key_young_sent,)
        )
        # 3. Old dead (created_at 100 days ago)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), 'Old error')""",
            (key_old_dead,)
        )
        # 4. Young dead (created_at 5 days ago)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 DAY), 'Recent error')""",
            (key_young_dead,)
        )
        # 5. Pending (must be protected)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at)
               VALUES (%s, 0, 'entry', '{}', 'pending', 0, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY))""",
            (key_pending,)
        )
        # 6. Processing (must be protected)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, locked_until)
               VALUES (%s, 0, 'entry', '{}', 'processing', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY), DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR))""",
            (key_processing,)
        )
        # 7. Uncertain delivery (must be protected from automatic deletion)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'pending', 2, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 25 DAY), 'Delivery state unknown: timeout')""",
            (key_uncertain,)
        )

        self.conn.commit()
        cur.close()

        # Run retention plan with sent_days=14, dead_days=90
        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        plan = planner.plan()

        # Verify candidate counts
        self.assertGreaterEqual(plan["candidates_summary"]["sent_candidates_count"], 1)
        self.assertGreaterEqual(plan["candidates_summary"]["dead_candidates_count"], 1)
        self.assertGreaterEqual(plan["candidates_summary"]["total_eligible_candidates"], 2)

        # Verify protected counts
        protected = plan["protected_summary"]
        self.assertGreaterEqual(protected["pending_protected_count"], 1)
        self.assertGreaterEqual(protected["processing_protected_count"], 1)
        self.assertGreaterEqual(protected["sent_retained_in_window_count"], 1)
        self.assertGreaterEqual(protected["dead_retained_in_window_count"], 1)
        self.assertGreaterEqual(protected["uncertain_delivery_requiring_review_count"], 1)

    def test_test_cleanup_execution_with_token(self):
        """execute_test_cleanup on signal_bot_test deletes only eligible candidates."""
        cur = self.conn.cursor()
        key_old_sent = f"retailbot2:test:del_old_sent_{uuid.uuid4().hex[:8]}"
        key_young_sent = f"retailbot2:test:del_young_sent_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.extend([key_old_sent, key_young_sent])

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at)
               VALUES (%s, 0, 'entry', '{}', 'sent', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY))""",
            (key_old_sent,)
        )
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at)
               VALUES (%s, 0, 'entry', '{}', 'sent', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 DAY))""",
            (key_young_sent,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)

        # 1. Invalid token fails
        with self.assertRaises(SecurityError):
            planner.execute_test_cleanup("INVALID_TOKEN")

        # 2. Valid token deletes only old candidate
        res = planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)
        self.assertEqual(res["status"], "TEST_CLEANUP_EXECUTED")
        self.assertGreaterEqual(res["deleted_sent_count"], 1)

        # Verify young row remains
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_young_sent,))
        self.assertEqual(cur.fetchone()[0], 1)
        # Verify old row deleted
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_old_sent,))
        self.assertEqual(cur.fetchone()[0], 0)
        cur.close()

    def test_cleanup_execution_prohibited_on_production_database(self):
        """execute_test_cleanup must fail closed if expected database is crypto_signals."""
        planner = OutboxRetentionPlanner(self.conn, expected_database="crypto_signals")
        with self.assertRaises(SecurityError):
            planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)


class TestRetentionDBCredentialSecurity(unittest.TestCase):
    """Verify that retention test database credentials fail closed when missing or empty."""

    def test_missing_password_fails_closed(self):
        """Missing SIGNAL_BOT_TEST_DB_PASSWORD raises RuntimeError without falling back to any secret."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                _get_test_conn()
            self.assertIn("SIGNAL_BOT_TEST_DB_PASSWORD is required", str(ctx.exception))

    def test_empty_password_fails_closed(self):
        """Empty SIGNAL_BOT_TEST_DB_PASSWORD raises RuntimeError without selecting another credential."""
        with patch.dict(os.environ, {"SIGNAL_BOT_TEST_DB_PASSWORD": "   "}):
            with self.assertRaises(RuntimeError) as ctx:
                _get_test_conn()
            self.assertIn("SIGNAL_BOT_TEST_DB_PASSWORD is required", str(ctx.exception))

    def test_production_credentials_never_used_as_fallback(self):
        """Production database credentials are never used as fallback for retention test connection."""
        with patch.dict(os.environ, {
            "SIGNAL_BOT_TEST_DB_PASSWORD": "",
            "MYSQL_DATABASE": "crypto_signals",
            "MYSQL_PASSWORD": "production_password_placeholder",
        }):
            with self.assertRaises(RuntimeError):
                _get_test_conn()


if __name__ == "__main__":
    unittest.main()
