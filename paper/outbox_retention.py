"""RetailBot2 Outbox Retention Planning and Dry-Run Policy Engine.

Provides safe, read-only retention planning, candidate row analysis, and policy enforcement
without deleting production records.

Policy Principles (Stage E):
1. Sent events: Recommended retention 14 days for audit reconciliation and duplicate detection.
2. Dead-lettered events: Recommended retention 90 days for forensic error investigation.
3. Pending events: Strictly EXCLUDED from retention purging (never delete unserved work).
4. Processing events: Strictly EXCLUDED from retention purging (never delete in-flight leases).
5. Uncertain delivery events: Strictly EXCLUDED from retention purging (requires manual review).
6. Dry-run by default: Queries candidate rows without modifying or deleting records.
7. Test-only execution path: Purge execution is strictly gated to 'signal_bot_test' and
   requires an explicit confirmation token. Production execution is completely blocked.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from paper.outbox_transport import (
    DELIVERY_UNKNOWN_MARKER,
    DELIVERY_UNKNOWN_PREFIX,
    format_delivery_unknown_error,
)

logger = logging.getLogger("retailbot2.retention")

DEFAULT_SENT_RETENTION_DAYS = 14
DEFAULT_DEAD_RETENTION_DAYS = 90
TEST_CLEANUP_CONFIRMATION_TOKEN = "AUTHORIZE_TEST_DATABASE_PURGE_ONLY"

# Explicit criteria for uncertain/ambiguous delivery outcomes (WO71797, WO71798).
# Covers both newly tagged outcomes (with DELIVERY_UNKNOWN marker) and historical/legacy error strings.
UNCERTAIN_DELIVERY_CONDITIONS = (
    "last_error LIKE '%DELIVERY_UNKNOWN%'",
    "last_error LIKE '%Delivery state unknown%'",
    "LOWER(last_error) LIKE '%transport child exception%'",
    "last_error LIKE '%ReadTimeout%'",
    "last_error LIKE '%HTTP 5%'",
    "last_error LIKE '%Server error HTTP 5%'",
    "last_error LIKE '%ConnectionError after request%'",
    "last_error LIKE '%Transport exceeded deadline%'",
    "last_error LIKE '%delivery lease expired%'",
    "last_error LIKE '%did not explicitly confirm delivery%'",
    "last_error LIKE '%missing explicit Telegram ok field%'",
    "last_error LIKE '%HTTP 200 response%'",
    "last_error LIKE '%UnexpectedTransportError%'",
    "last_error LIKE '%No HTTP status received%'",
    "last_error LIKE '%unparseable body%'",
    "last_error LIKE '%Failed to spawn transport child%'",
    "last_error LIKE '%Empty stdout from transport child%'",
    "last_error LIKE '%Failed to parse transport child JSON%'",
)

UNCERTAIN_DELIVERY_SQL_PREDICATE = (
    "(last_error IS NOT NULL AND (" + " OR ".join(UNCERTAIN_DELIVERY_CONDITIONS) + "))"
)

NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE = (
    "(last_error IS NULL OR NOT (" + " OR ".join(UNCERTAIN_DELIVERY_CONDITIONS) + "))"
)


class SecurityError(Exception):
    """Raised when an unauthorized or unsafe retention operation is attempted."""
    pass


class OutboxRetentionPlanner:
    """Read-only retention analysis and policy planning engine."""

    UNCERTAIN_PREDICATE = UNCERTAIN_DELIVERY_SQL_PREDICATE
    EXCLUDE_UNCERTAIN_PREDICATE = NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE

    def __init__(
        self,
        conn,
        expected_database: str,
        sent_retention_days: int = DEFAULT_SENT_RETENTION_DAYS,
        dead_retention_days: int = DEFAULT_DEAD_RETENTION_DAYS,
    ):
        if conn is None:
            raise ValueError("A database connection is required for retention planning")
        if not expected_database or not isinstance(expected_database, str):
            raise ValueError("expected_database must be a non-empty string")
        if sent_retention_days < 1:
            raise ValueError("sent_retention_days must be at least 1")
        if dead_retention_days < 1:
            raise ValueError("dead_retention_days must be at least 1")

        self.conn = conn
        self.expected_database = expected_database.strip()
        self.sent_retention_days = int(sent_retention_days)
        self.dead_retention_days = int(dead_retention_days)

    def verify_database_identity(self) -> Tuple[str, str]:
        """Verify active connection database and user against expected configuration."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT DATABASE(), CURRENT_USER()")
            row = cur.fetchone()
            actual_db = row[0] if row else None
            current_user = row[1] if row else None
            if actual_db != self.expected_database:
                raise SecurityError(
                    f"Retention planner database mismatch: expected {self.expected_database!r}, "
                    f"but active connection reports {actual_db!r}"
                )
            return str(actual_db), str(current_user)
        finally:
            cur.close()

    def plan(self) -> Dict[str, Any]:
        """Produce a read-only retention plan reporting candidate rows and protected categories.

        Modifies zero database rows.
        """
        actual_db, current_user = self.verify_database_identity()
        cur = self.conn.cursor(dictionary=True)
        try:
            # Server UTC time
            cur.execute("SELECT UTC_TIMESTAMP()")
            db_utc_now = cur.fetchone()["UTC_TIMESTAMP()"]

            # Calculate UTC cutoffs
            cur.execute(
                "SELECT DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY) AS sent_cutoff, "
                "       DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY) AS dead_cutoff",
                (self.sent_retention_days, self.dead_retention_days)
            )
            cutoffs = cur.fetchone()
            sent_cutoff = cutoffs["sent_cutoff"]
            dead_cutoff = cutoffs["dead_cutoff"]

            # 1. Eligible Candidates: Sent events older than sent_cutoff (strictly excluding uncertain records)
            # Uses sent_at if present; falls back to created_at if sent_at is NULL
            cur.execute(
                f"""SELECT
                       COUNT(*) AS count,
                       MIN(COALESCE(sent_at, created_at)) AS oldest_timestamp,
                       MAX(COALESCE(sent_at, created_at)) AS newest_timestamp
                   FROM rdt_telegram_outbox
                   WHERE status = 'sent'
                     AND COALESCE(sent_at, created_at) < %s
                     AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
                (sent_cutoff,)
            )
            sent_cand_row = cur.fetchone()
            sent_candidates = int(sent_cand_row["count"])
            oldest_sent_cand = sent_cand_row.get("oldest_timestamp")
            newest_sent_cand = sent_cand_row.get("newest_timestamp")

            # 2. Eligible Candidates: Dead-letter events older than dead_cutoff (strictly excluding uncertain records)
            cur.execute(
                f"""SELECT
                       COUNT(*) AS count,
                       MIN(created_at) AS oldest_timestamp,
                       MAX(created_at) AS newest_timestamp
                   FROM rdt_telegram_outbox
                   WHERE status = 'dead'
                     AND created_at < %s
                     AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
                (dead_cutoff,)
            )
            dead_cand_row = cur.fetchone()
            dead_candidates = int(dead_cand_row["count"])
            oldest_dead_cand = dead_cand_row.get("oldest_timestamp")
            newest_dead_cand = dead_cand_row.get("newest_timestamp")

            # 3. Protected Categories: Active items (pending, processing)
            cur.execute("SELECT COUNT(*) AS count FROM rdt_telegram_outbox WHERE status = 'pending'")
            pending_count = int(cur.fetchone()["count"])

            cur.execute("SELECT COUNT(*) AS count FROM rdt_telegram_outbox WHERE status = 'processing'")
            processing_count = int(cur.fetchone()["count"])

            # 4. Protected Categories: Retained within retention window (and not uncertain)
            cur.execute(
                f"""SELECT COUNT(*) AS count FROM rdt_telegram_outbox
                   WHERE status = 'sent' AND COALESCE(sent_at, created_at) >= %s
                     AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
                (sent_cutoff,)
            )
            sent_retained = int(cur.fetchone()["count"])

            cur.execute(
                f"""SELECT COUNT(*) AS count FROM rdt_telegram_outbox
                   WHERE status = 'dead' AND created_at >= %s
                     AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
                (dead_cutoff,)
            )
            dead_retained = int(cur.fetchone()["count"])

            # 5. Uncertain delivery events (matching the comprehensive uncertain delivery predicate)
            cur.execute(
                f"""SELECT
                       COUNT(*) AS count,
                       MIN(created_at) AS oldest_timestamp,
                       MAX(created_at) AS newest_timestamp
                   FROM rdt_telegram_outbox
                   WHERE {UNCERTAIN_DELIVERY_SQL_PREDICATE}"""
            )
            uncertain_row = cur.fetchone()
            uncertain_delivery_count = int(uncertain_row["count"])
            oldest_uncertain = uncertain_row.get("oldest_timestamp")
            newest_uncertain = uncertain_row.get("newest_timestamp")

            # Breakdown of uncertain records by status
            cur.execute(
                f"""SELECT
                       SUM(CASE WHEN status = 'dead' THEN 1 ELSE 0 END) AS dead_count,
                       SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN status IN ('pending', 'processing') THEN 1 ELSE 0 END) AS active_count
                   FROM rdt_telegram_outbox
                   WHERE {UNCERTAIN_DELIVERY_SQL_PREDICATE}"""
            )
            breakdown_row = cur.fetchone()
            uncertain_dead_count = int(breakdown_row.get("dead_count") or 0)
            uncertain_sent_count = int(breakdown_row.get("sent_count") or 0)
            uncertain_active_count = int(breakdown_row.get("active_count") or 0)

            total_candidates = sent_candidates + dead_candidates
            total_protected = (
                pending_count + processing_count + sent_retained + dead_retained +
                uncertain_dead_count + uncertain_sent_count
            )

            return {
                "dry_run": True,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "server_utc_timestamp": db_utc_now.isoformat() if hasattr(db_utc_now, "isoformat") else str(db_utc_now),
                "database": actual_db,
                "current_user": current_user,
                "policy_parameters": {
                    "sent_retention_days": self.sent_retention_days,
                    "dead_retention_days": self.dead_retention_days,
                    "sent_cutoff_utc": sent_cutoff.isoformat() if hasattr(sent_cutoff, "isoformat") else str(sent_cutoff),
                    "dead_cutoff_utc": dead_cutoff.isoformat() if hasattr(dead_cutoff, "isoformat") else str(dead_cutoff),
                    "pending_retention_policy": "EXCLUDED_PROTECTED",
                    "processing_retention_policy": "EXCLUDED_PROTECTED",
                    "uncertain_delivery_policy": "EXCLUDED_MANUAL_REVIEW_REQUIRED",
                },
                "candidates_summary": {
                    "total_eligible_candidates": total_candidates,
                    "sent_candidates_count": sent_candidates,
                    "sent_oldest_timestamp": oldest_sent_cand.isoformat() if oldest_sent_cand and hasattr(oldest_sent_cand, "isoformat") else str(oldest_sent_cand) if oldest_sent_cand else None,
                    "sent_newest_timestamp": newest_sent_cand.isoformat() if newest_sent_cand and hasattr(newest_sent_cand, "isoformat") else str(newest_sent_cand) if newest_sent_cand else None,
                    "dead_candidates_count": dead_candidates,
                    "dead_oldest_timestamp": oldest_dead_cand.isoformat() if oldest_dead_cand and hasattr(oldest_dead_cand, "isoformat") else str(oldest_dead_cand) if oldest_dead_cand else None,
                    "dead_newest_timestamp": newest_dead_cand.isoformat() if newest_dead_cand and hasattr(newest_dead_cand, "isoformat") else str(newest_dead_cand) if newest_dead_cand else None,
                },
                "protected_summary": {
                    "total_protected_records": total_protected,
                    "pending_protected_count": pending_count,
                    "processing_protected_count": processing_count,
                    "sent_retained_in_window_count": sent_retained,
                    "dead_retained_in_window_count": dead_retained,
                    "uncertain_dead_protected_count": uncertain_dead_count,
                    "uncertain_sent_protected_count": uncertain_sent_count,
                    "uncertain_delivery_requiring_review_count": uncertain_delivery_count,
                },
                "uncertain_summary": {
                    "total_uncertain_records": uncertain_delivery_count,
                    "uncertain_dead_count": uncertain_dead_count,
                    "uncertain_sent_count": uncertain_sent_count,
                    "uncertain_active_count": uncertain_active_count,
                    "oldest_uncertain_timestamp": oldest_uncertain.isoformat() if oldest_uncertain and hasattr(oldest_uncertain, "isoformat") else str(oldest_uncertain) if oldest_uncertain else None,
                    "newest_uncertain_timestamp": newest_uncertain.isoformat() if newest_uncertain and hasattr(newest_uncertain, "isoformat") else str(newest_uncertain) if newest_uncertain else None,
                    "policy": "EXCLUDED_FROM_PURGE_MANUAL_REVIEW_REQUIRED",
                },
                "action": "DRY_RUN_ONLY_NO_RECORDS_MODIFIED",
            }
        finally:
            cur.close()

    def execute_test_cleanup(self, confirmation_token: str) -> Dict[str, Any]:
        """Execute cleanup strictly on signal_bot_test with required token.

        FAIL-CLOSED SECURITY INVARIANT:
        This execution path is strictly prohibited against any production database
        and immediately aborts if database is not 'signal_bot_test'.
        """
        actual_db, current_user = self.verify_database_identity()
        if actual_db != "signal_bot_test" or self.expected_database != "signal_bot_test":
            raise SecurityError(
                f"Automated retention execution rejected: database {actual_db!r} is not permitted. "
                "Retention execution is strictly restricted to 'signal_bot_test' only."
            )
        if confirmation_token != TEST_CLEANUP_CONFIRMATION_TOKEN:
            raise SecurityError(
                "Automated retention execution rejected: invalid confirmation token. "
                f"Expected {TEST_CLEANUP_CONFIRMATION_TOKEN!r}."
            )

        cur = self.conn.cursor()
        try:
            # Calculate cutoffs
            cur.execute(
                "SELECT DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY) AS sent_cutoff, "
                "       DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY) AS dead_cutoff",
                (self.sent_retention_days, self.dead_retention_days)
            )
            cutoffs = cur.fetchone()
            sent_cutoff = cutoffs[0]
            dead_cutoff = cutoffs[1]

            # 1. Delete sent candidates older than sent_cutoff (strictly excluding uncertain)
            cur.execute(
                f"""DELETE FROM rdt_telegram_outbox
                   WHERE status = 'sent'
                     AND COALESCE(sent_at, created_at) < %s
                     AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
                (sent_cutoff,)
            )
            deleted_sent = cur.rowcount

            # 2. Delete dead candidates older than dead_cutoff (strictly excluding uncertain)
            cur.execute(
                f"""DELETE FROM rdt_telegram_outbox
                   WHERE status = 'dead'
                     AND created_at < %s
                     AND {NOT_UNCERTAIN_DELIVERY_SQL_PREDICATE}""",
                (dead_cutoff,)
            )
            deleted_dead = cur.rowcount

            self.conn.commit()
            return {
                "database": actual_db,
                "current_user": current_user,
                "deleted_sent_count": deleted_sent,
                "deleted_dead_count": deleted_dead,
                "total_deleted_count": deleted_sent + deleted_dead,
                "status": "TEST_CLEANUP_EXECUTED",
            }
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()


def plan_outbox_retention(
    conn,
    expected_database: str,
    sent_retention_days: int = DEFAULT_SENT_RETENTION_DAYS,
    dead_retention_days: int = DEFAULT_DEAD_RETENTION_DAYS,
) -> Dict[str, Any]:
    """Convenience helper to plan retention dry-run."""
    planner = OutboxRetentionPlanner(
        conn,
        expected_database=expected_database,
        sent_retention_days=sent_retention_days,
        dead_retention_days=dead_retention_days,
    )
    return planner.plan()


def main():
    parser = argparse.ArgumentParser(description="RetailBot2 Outbox Retention Planner (Dry-Run)")
    parser.add_argument("--database", choices=["signal_bot_test", "crypto_signals"], default="signal_bot_test")
    parser.add_argument("--sent-days", type=int, default=DEFAULT_SENT_RETENTION_DAYS, help="Sent event retention days")
    parser.add_argument("--dead-days", type=int, default=DEFAULT_DEAD_RETENTION_DAYS, help="Dead event retention days")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    from paper.outbox_worker import resolve_worker_db_credentials
    try:
        creds = resolve_worker_db_credentials(args.database, observation=True)
    except ValueError as exc:
        print(f"ERROR: Credential resolution failed: {exc}", file=sys.stderr)
        sys.exit(1)

    import mysql.connector
    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=creds["user"],
        password=creds["password"],
        database=args.database,
        autocommit=False,
    )

    try:
        plan = plan_outbox_retention(
            conn,
            expected_database=args.database,
            sent_retention_days=args.sent_days,
            dead_retention_days=args.dead_days,
        )
        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            print(f"=== RetailBot2 Outbox Retention Plan (DRY RUN) ===")
            print(f"Database: {plan['database']} | User: {plan['current_user']}")
            print(f"Sent Retention: {plan['policy_parameters']['sent_retention_days']} days (Cutoff: {plan['policy_parameters']['sent_cutoff_utc']})")
            print(f"Dead Retention: {plan['policy_parameters']['dead_retention_days']} days (Cutoff: {plan['policy_parameters']['dead_cutoff_utc']})")
            print(f"Eligible Candidates: {plan['candidates_summary']['total_eligible_candidates']} (Sent: {plan['candidates_summary']['sent_candidates_count']}, Dead: {plan['candidates_summary']['dead_candidates_count']})")
            print(f"Protected Records: {plan['protected_summary']['total_protected_records']} (Pending: {plan['protected_summary']['pending_protected_count']}, Processing: {plan['protected_summary']['processing_protected_count']}, Uncertain: {plan['protected_summary']['uncertain_delivery_requiring_review_count']})")
            print("Notice: Dry-run only; no records were modified or deleted.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
