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
    DELIVERY_UNKNOWN_MARKER,
    DELIVERY_UNKNOWN_PREFIX,
    NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE,
    UNCERTAIN_DELIVERY_CONDITIONS,
    UNCERTAIN_DELIVERY_SQL_PREDICATE,
    OutboxRetentionPlanner,
    SecurityError,
    TEST_CLEANUP_CONFIRMATION_TOKEN,
    format_delivery_unknown_error,
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

    def test_format_delivery_unknown_error_utility(self):
        """Verify format_delivery_unknown_error consistently prefixes and bounds error strings."""
        self.assertEqual(
            format_delivery_unknown_error(None),
            "DELIVERY_UNKNOWN: Delivery state unknown"
        )
        self.assertEqual(
            format_delivery_unknown_error(""),
            "DELIVERY_UNKNOWN: Delivery state unknown"
        )
        self.assertEqual(
            format_delivery_unknown_error("ReadTimeout: socket read timed out"),
            "DELIVERY_UNKNOWN: ReadTimeout: socket read timed out"
        )
        self.assertEqual(
            format_delivery_unknown_error("DELIVERY_UNKNOWN: already prefixed"),
            "DELIVERY_UNKNOWN: already prefixed"
        )
        # Bounded to 1000 chars
        long_err = "X" * 1500
        formatted = format_delivery_unknown_error(long_err)
        self.assertLessEqual(len(formatted), 1000)
        self.assertTrue(formatted.startswith("DELIVERY_UNKNOWN: "))

    def test_ambiguous_read_timeout_protected_after_dead(self):
        """An ambiguous ReadTimeout event remains protected after reaching dead state."""
        cur = self.conn.cursor()
        key_dead_timeout = f"retailbot2:test:read_timeout_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.append(key_dead_timeout)

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY),
                       'ReadTimeout: socket read timed out after request transmission')""",
            (key_dead_timeout,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        plan = planner.plan()

        # The 100-day-old dead ReadTimeout must be excluded from dead_candidates
        cur = self.conn.cursor()
        cur.execute(
            f"""SELECT COUNT(*) FROM rdt_telegram_outbox
               WHERE event_key = %s AND status = 'dead'
                 AND created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 90 DAY)
                 AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
            (key_dead_timeout,)
        )
        candidate_count = cur.fetchone()[0]
        self.assertEqual(candidate_count, 0)

        # Must be reported under uncertain records
        self.assertGreaterEqual(plan["protected_summary"]["uncertain_delivery_requiring_review_count"], 1)
        self.assertGreaterEqual(plan["uncertain_summary"]["uncertain_dead_count"], 1)

        # Cleanup must NOT delete this record
        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_dead_timeout,))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.close()

    def test_ambiguous_http_5xx_protected_after_dead(self):
        """An ambiguous HTTP 5xx event remains protected after reaching dead state."""
        cur = self.conn.cursor()
        key_dead_502 = f"retailbot2:test:http_502_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.append(key_dead_502)

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY),
                       'Server error HTTP 502')""",
            (key_dead_502,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_dead_502,))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.close()

    def test_ambiguous_with_new_marker_excluded_from_candidate_counts(self):
        """An ambiguous event with the new DELIVERY_UNKNOWN marker is excluded from candidate counts."""
        cur = self.conn.cursor()
        key_new_marker = f"retailbot2:test:new_marker_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.append(key_new_marker)

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY),
                       'DELIVERY_UNKNOWN: UnexpectedTransportError: ConnectionResetError')""",
            (key_new_marker,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        plan = planner.plan()

        # Must not be an eligible candidate
        cur = self.conn.cursor()
        cur.execute(
            f"""SELECT COUNT(*) FROM rdt_telegram_outbox
               WHERE event_key = %s AND status = 'dead'
                 AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
            (key_new_marker,)
        )
        self.assertEqual(cur.fetchone()[0], 0)
        cur.close()

        # Must remain in DB after test cleanup
        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_new_marker,))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.close()

    def test_legacy_ambiguous_error_strings_protected(self):
        """Legacy ambiguous error strings without the new marker remain protected."""
        cur = self.conn.cursor()
        legacy_errors = [
            "Delivery state unknown: request timeout",
            "ConnectionError after request may have been transmitted",
            "Transport exceeded deadline 10.0s",
            "delivery lease expired at max attempts",
            "HTTP 200 response missing explicit Telegram ok field",
            "HTTP 200 response did not explicitly confirm delivery",
            "UnexpectedTransportError: SocketError",
            "No HTTP status received",
            "Unexpected HTTP status 503 with unparseable body",
            "Failed to spawn transport child: exec error",
            "Empty stdout from transport child",
            "Failed to parse transport child JSON: expecting value",
        ]

        test_keys = []
        for idx, err in enumerate(legacy_errors):
            k = f"retailbot2:test:legacy_{idx}_{uuid.uuid4().hex[:6]}"
            test_keys.append(k)
            self.tracked_keys.append(k)
            cur.execute(
                """INSERT INTO rdt_telegram_outbox
                   (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
                   VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), %s)""",
                (k, err)
            )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)

        cur = self.conn.cursor()
        for k in test_keys:
            cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (k,))
            self.assertEqual(cur.fetchone()[0], 1, f"Legacy ambiguous record {k} was improperly deleted")
        cur.close()

    def test_confirmed_permanent_rejection_becomes_normal_dead_candidate(self):
        """Confirmed permanent API rejections become normal dead retention candidates and are purgeable."""
        cur = self.conn.cursor()
        perm_errors = [
            "Bad Request: chat not found",
            "Forbidden: bot was blocked by the user",
            "Unauthorized: invalid bot token",
            "Not Found: method not found",
        ]
        perm_keys = []
        for idx, err in enumerate(perm_errors):
            k = f"retailbot2:test:perm_{idx}_{uuid.uuid4().hex[:6]}"
            perm_keys.append(k)
            self.tracked_keys.append(k)
            cur.execute(
                """INSERT INTO rdt_telegram_outbox
                   (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
                   VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), %s)""",
                (k, err)
            )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        plan = planner.plan()
        self.assertGreaterEqual(plan["candidates_summary"]["dead_candidates_count"], len(perm_keys))

        # Cleanup should delete confirmed permanent rejections older than 90 days
        res = planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)
        self.assertGreaterEqual(res["deleted_dead_count"], len(perm_keys))

        cur = self.conn.cursor()
        for k in perm_keys:
            cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (k,))
            self.assertEqual(cur.fetchone()[0], 0, f"Confirmed rejection {k} should have been deleted")
        cur.close()

    def test_cleanup_cannot_delete_ambiguous_events_exceeding_threshold(self):
        """Test cleanup cannot delete ambiguous events, even when older than dead retention threshold."""
        cur = self.conn.cursor()
        key_confirmed = f"retailbot2:test:purge_confirmed_{uuid.uuid4().hex[:8]}"
        key_ambiguous = f"retailbot2:test:keep_ambiguous_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.extend([key_confirmed, key_ambiguous])

        # 1. Confirmed dead (older than 90 days) -> eligible for deletion
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 120 DAY),
                       'Bad Request: chat not found')""",
            (key_confirmed,)
        )
        # 2. Ambiguous dead (older than 90 days) -> protected from deletion
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 120 DAY),
                       'DELIVERY_UNKNOWN: ReadTimeout: read timed out')""",
            (key_ambiguous,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_confirmed,))
        self.assertEqual(cur.fetchone()[0], 0, "Confirmed dead event should have been purged")

        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_ambiguous,))
        self.assertEqual(cur.fetchone()[0], 1, "Ambiguous dead event must NOT be purged")
        cur.close()

    def test_pending_and_processing_remain_protected(self):
        """Pending and processing events are never deleted regardless of age."""
        cur = self.conn.cursor()
        key_old_pending = f"retailbot2:test:old_pend_{uuid.uuid4().hex[:8]}"
        key_old_proc = f"retailbot2:test:old_proc_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.extend([key_old_pending, key_old_proc])

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at)
               VALUES (%s, 0, 'entry', '{}', 'pending', 0, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 150 DAY))""",
            (key_old_pending,)
        )
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, locked_until)
               VALUES (%s, 0, 'entry', '{}', 'processing', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 150 DAY),
                       DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR))""",
            (key_old_proc,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        plan = planner.plan()
        self.assertGreaterEqual(plan["protected_summary"]["pending_protected_count"], 1)
        self.assertGreaterEqual(plan["protected_summary"]["processing_protected_count"], 1)

        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_old_pending,))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (key_old_proc,))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.close()

    def test_candidate_planning_and_cleanup_use_equivalent_predicates(self):
        """Verify candidate planning and actual cleanup use equivalent uncertainty predicates."""
        # Check predicate constants match
        self.assertEqual(OutboxRetentionPlanner.UNCERTAIN_PREDICATE, UNCERTAIN_DELIVERY_SQL_PREDICATE)
        self.assertEqual(OutboxRetentionPlanner.EXCLUDE_UNCERTAIN_PREDICATE, NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE)

        cur = self.conn.cursor()
        k_sent_cand = f"retailbot2:test:eq_sent_cand_{uuid.uuid4().hex[:8]}"
        k_dead_cand = f"retailbot2:test:eq_dead_cand_{uuid.uuid4().hex[:8]}"
        k_dead_uncert = f"retailbot2:test:eq_dead_uncert_{uuid.uuid4().hex[:8]}"
        k_sent_uncert = f"retailbot2:test:eq_sent_uncert_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.extend([k_sent_cand, k_dead_cand, k_dead_uncert, k_sent_uncert])

        # Eligible sent candidate (sent 20 days ago, no error)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at)
               VALUES (%s, 0, 'entry', '{}', 'sent', 1, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY))""",
            (k_sent_cand,)
        )
        # Eligible dead candidate (dead 100 days ago, confirmed error)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), 'Bad Request: chat not found')""",
            (k_dead_cand,)
        )
        # Ineligible dead ambiguous (dead 100 days ago, DELIVERY_UNKNOWN)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'dead', 8, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), 'DELIVERY_UNKNOWN: ReadTimeout')""",
            (k_dead_uncert,)
        )
        # Ineligible sent ambiguous (sent 20 days ago, DELIVERY_UNKNOWN)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at, last_error)
               VALUES (%s, 0, 'entry', '{}', 'sent', 2, DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 20 DAY),
                       'DELIVERY_UNKNOWN: previous ambiguous attempt')""",
            (k_sent_uncert,)
        )
        self.conn.commit()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        plan = planner.plan()

        # Both k_dead_uncert and k_sent_uncert must be excluded from candidates
        cur = self.conn.cursor()
        cur.execute(
            f"""SELECT COUNT(*) FROM rdt_telegram_outbox
               WHERE event_key IN (%s, %s) AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
            (k_dead_uncert, k_sent_uncert)
        )
        self.assertEqual(cur.fetchone()[0], 0)
        cur.close()

        # Execute cleanup
        res = planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)

        cur = self.conn.cursor()
        # Eligible candidates deleted
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key IN (%s, %s)", (k_sent_cand, k_dead_cand))
        self.assertEqual(cur.fetchone()[0], 0)
        # Ineligible uncertain records NOT deleted
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key IN (%s, %s)", (k_dead_uncert, k_sent_uncert))
        self.assertEqual(cur.fetchone()[0], 2)
        cur.close()

    def test_cleanup_does_not_alter_unrelated_rows(self):
        """Cleanup does not delete, alter, or mutate any unrelated rows in rdt_telegram_outbox."""
        cur = self.conn.cursor()
        k_unrelated_sent = f"retailbot2:test:unrel_sent_{uuid.uuid4().hex[:8]}"
        k_unrelated_dead = f"retailbot2:test:unrel_dead_{uuid.uuid4().hex[:8]}"
        k_unrelated_pend = f"retailbot2:test:unrel_pend_{uuid.uuid4().hex[:8]}"
        k_unrelated_proc = f"retailbot2:test:unrel_proc_{uuid.uuid4().hex[:8]}"
        k_unrelated_ambig = f"retailbot2:test:unrel_ambig_{uuid.uuid4().hex[:8]}"
        k_target_purge = f"retailbot2:test:target_purge_{uuid.uuid4().hex[:8]}"

        self.tracked_keys.extend([
            k_unrelated_sent, k_unrelated_dead, k_unrelated_pend,
            k_unrelated_proc, k_unrelated_ambig, k_target_purge,
        ])

        # Young sent (2 days ago)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, sent_at)
               VALUES (%s, 101, 'entry', '{\"symbol\":\"BTC\"}', 'sent', 1,
                       DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 DAY), DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 DAY))""",
            (k_unrelated_sent,)
        )
        # Young dead (5 days ago)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 102, 'exit', '{\"symbol\":\"ETH\"}', 'dead', 8,
                       DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 DAY), 'Bad Request: invalid chat')""",
            (k_unrelated_dead,)
        )
        # Active pending
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at)
               VALUES (%s, 103, 'entry', '{\"symbol\":\"SOL\"}', 'pending', 0,
                       DATE_SUB(UTC_TIMESTAMP(), INTERVAL 50 DAY))""",
            (k_unrelated_pend,)
        )
        # Active processing
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, locked_until)
               VALUES (%s, 104, 'entry', '{\"symbol\":\"BNB\"}', 'processing', 1,
                       DATE_SUB(UTC_TIMESTAMP(), INTERVAL 50 DAY), DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR))""",
            (k_unrelated_proc,)
        )
        # Ambiguous dead (100 days ago, protected)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 105, 'entry', '{\"symbol\":\"XRP\"}', 'dead', 8,
                       DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), 'DELIVERY_UNKNOWN: 504 Gateway Timeout')""",
            (k_unrelated_ambig,)
        )
        # Target candidate (100 days ago, confirmed dead -> should be deleted)
        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, last_error)
               VALUES (%s, 106, 'entry', '{\"symbol\":\"ADA\"}', 'dead', 8,
                       DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY), 'Unauthorized: revoked')""",
            (k_target_purge,)
        )
        self.conn.commit()

        # Snapshot unrelated rows before cleanup
        unrelated_keys = [k_unrelated_sent, k_unrelated_dead, k_unrelated_pend, k_unrelated_proc, k_unrelated_ambig]
        cur.execute(
            f"""SELECT event_key, trade_id, event_type, payload_json, status, attempts, last_error
               FROM rdt_telegram_outbox WHERE event_key IN ({','.join(['%s']*len(unrelated_keys))})
               ORDER BY event_key""",
            tuple(unrelated_keys)
        )
        snapshot_before = cur.fetchall()
        cur.close()

        planner = OutboxRetentionPlanner(self.conn, expected_database="signal_bot_test", sent_retention_days=14, dead_retention_days=90)
        planner.execute_test_cleanup(TEST_CLEANUP_CONFIRMATION_TOKEN)

        cur = self.conn.cursor()
        # Verify target candidate was deleted
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s", (k_target_purge,))
        self.assertEqual(cur.fetchone()[0], 0)

        # Snapshot unrelated rows after cleanup
        cur.execute(
            f"""SELECT event_key, trade_id, event_type, payload_json, status, attempts, last_error
               FROM rdt_telegram_outbox WHERE event_key IN ({','.join(['%s']*len(unrelated_keys))})
               ORDER BY event_key""",
            tuple(unrelated_keys)
        )
        snapshot_after = cur.fetchall()
        cur.close()

        # Every unrelated row must be byte-for-byte identical to before cleanup
        self.assertEqual(snapshot_before, snapshot_after)

    def test_worker_ambiguous_outcome_stores_persistent_marker(self):
        """Worker dispatching an ambiguous outcome updates last_error with DELIVERY_UNKNOWN marker."""
        from paper.outbox_worker import OutboxWorker, ALLOWED_TEST_DATABASES

        cur = self.conn.cursor()
        k_worker_test = f"retailbot2:test:worker_ambig_{uuid.uuid4().hex[:8]}"
        self.tracked_keys.append(k_worker_test)

        cur.execute(
            """INSERT INTO rdt_telegram_outbox
               (event_key, trade_id, event_type, payload_json, status, attempts, created_at, next_attempt_at)
               VALUES (%s, 9999, 'operational', '{\"text\":\"test worker ambiguous\"}', 'pending', 0,
                       UTC_TIMESTAMP(), UTC_TIMESTAMP())""",
            (k_worker_test,)
        )
        self.conn.commit()
        cur.close()

        pwd = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()
        worker = OutboxWorker(
            db_config={
                "host": TEST_DB_HOST,
                "port": TEST_DB_PORT,
                "user": TEST_DB_USER,
                "password": pwd,
                "database": TEST_DB_NAME,
            },
            telegram_token="mock_token",
            telegram_chat_id="mock_chat",
            allowed_databases=ALLOWED_TEST_DATABASES,
            mock_transport_response={"status_code": 500, "body": "Internal Server Error"},
        )
        try:
            self.assertTrue(worker.session_mgr.connect_and_acquire())
            # Process due event
            processed = worker.process_one_event()
            self.assertTrue(processed)

            # Check database row
            cur = self.conn.cursor(dictionary=True)
            cur.execute("SELECT status, attempts, last_error FROM rdt_telegram_outbox WHERE event_key = %s", (k_worker_test,))
            row = cur.fetchone()
            cur.close()

            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["attempts"], 1)
            self.assertIsNotNone(row["last_error"])
            self.assertTrue(row["last_error"].startswith("DELIVERY_UNKNOWN: "), f"Got {row['last_error']}")
            self.assertIn("Server error HTTP 500", row["last_error"])
        finally:
            worker.session_mgr.close()


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
