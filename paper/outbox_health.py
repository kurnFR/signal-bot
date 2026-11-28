"""RetailBot2 Telegram Outbox Observability and Health Snapshot.

Provides read-only inspection of outbox health, queue backlogs, attempt distributions,
dead-letter accumulation, and worker advisory lock status without modifying database rows.

Strictly read-only:
- Executes SELECT queries only.
- Never executes INSERT, UPDATE, DELETE, or DDL.
- Never claims events or alters locked_until/claim_token.
- Uses UTC semantics consistently.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from paper.outbox_store import OUTBOX_LOCK_NAME

logger = logging.getLogger("telegram.outbox.observability")

# Default monitoring threshold proposals (not production mandate; configurable)
DEFAULT_PENDING_WARN_COUNT = 50
DEFAULT_PENDING_CRITICAL_COUNT = 200
DEFAULT_PENDING_AGE_WARN_SEC = 300.0  # 5 minutes
DEFAULT_PENDING_AGE_CRITICAL_SEC = 1800.0  # 30 minutes


class OutboxHealthInspector:
    """Read-only health inspector for the RetailBot2 outbox queue."""

    def __init__(self, conn, expected_database: str, observation_mode: bool = False):
        if conn is None:
            raise ValueError("A database connection is required for outbox health inspection")
        if not expected_database or not isinstance(expected_database, str):
            raise ValueError("expected_database must be a non-empty string")
        self.conn = conn
        self.expected_database = expected_database.strip()
        self.observation_mode = observation_mode

    def verify_database_identity(self) -> Tuple[str, str]:
        """Verify active connection database and user against expected configuration."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT DATABASE(), CURRENT_USER()")
            row = cur.fetchone()
            actual_db = row[0] if row else None
            current_user = row[1] if row else None
            if actual_db != self.expected_database:
                raise RuntimeError(
                    f"Health inspection database mismatch: configured for {self.expected_database!r}, "
                    f"but active connection is connected to {actual_db!r}"
                )
            return str(actual_db), str(current_user)
        finally:
            cur.close()

    def get_lock_status(self) -> Dict[str, Any]:
        """Inspect MariaDB advisory lock ownership without acquiring or modifying it."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT IS_USED_LOCK(%s), CONNECTION_ID()",
                (OUTBOX_LOCK_NAME,)
            )
            row = cur.fetchone()
            lock_conn_id = row[0] if row else None
            my_conn_id = row[1] if row else None
            is_held = lock_conn_id is not None
            held_by_me = is_held and (lock_conn_id == my_conn_id)
            return {
                "lock_name": OUTBOX_LOCK_NAME,
                "is_locked": is_held,
                "lock_holder_connection_id": lock_conn_id,
                "is_held_by_current_session": held_by_me,
            }
        finally:
            cur.close()

    def get_queue_snapshot(self) -> Dict[str, Any]:
        """Collect status counts, age metrics, and retry distribution."""
        cur = self.conn.cursor(dictionary=True)
        try:
            # 1. Status aggregates (pending, processing, sent, dead)
            cur.execute(
                """SELECT
                       status,
                       COUNT(*) AS count,
                       MIN(created_at) AS oldest_created_at,
                       MAX(created_at) AS newest_created_at
                   FROM rdt_telegram_outbox
                   GROUP BY status"""
            )
            status_rows = cur.fetchall()

            status_counts = {"pending": 0, "processing": 0, "sent": 0, "dead": 0}
            oldest_created = {}
            for r in status_rows:
                s = r["status"]
                status_counts[s] = int(r["count"])
                if r.get("oldest_created_at"):
                    oldest_created[s] = r["oldest_created_at"]

            # Current UTC time on MariaDB server
            cur.execute("SELECT UTC_TIMESTAMP()")
            db_utc_now = cur.fetchone()["UTC_TIMESTAMP()"]

            # Calculate ages for oldest pending and oldest processing
            oldest_pending_age_sec = None
            if "pending" in oldest_created and oldest_created["pending"]:
                delta = db_utc_now - oldest_created["pending"]
                oldest_pending_age_sec = max(0.0, delta.total_seconds())

            oldest_processing_age_sec = None
            if "processing" in oldest_created and oldest_created["processing"]:
                delta = db_utc_now - oldest_created["processing"]
                oldest_processing_age_sec = max(0.0, delta.total_seconds())

            # 2. Due items ready for dispatch or retry
            # Uses idx_rdt_telegram_outbox_due (status, next_attempt_at, locked_until, id)
            cur.execute(
                """SELECT COUNT(*) AS due_count
                   FROM rdt_telegram_outbox
                   WHERE status = 'pending'
                     AND next_attempt_at <= UTC_TIMESTAMP()
                     AND attempts < 8"""
            )
            due_pending_count = int(cur.fetchone()["due_count"])

            # 3. Expired / stale processing leases
            cur.execute(
                """SELECT COUNT(*) AS expired_count, MIN(locked_until) AS oldest_expired_until
                   FROM rdt_telegram_outbox
                   WHERE status = 'processing'
                     AND locked_until <= UTC_TIMESTAMP()"""
            )
            expired_row = cur.fetchone()
            expired_processing_count = int(expired_row["expired_count"])
            oldest_expired_until = expired_row.get("oldest_expired_until")

            # 4. Attempt distribution
            cur.execute(
                """SELECT attempts, COUNT(*) AS count
                   FROM rdt_telegram_outbox
                   GROUP BY attempts
                   ORDER BY attempts"""
            )
            attempt_rows = cur.fetchall()
            attempt_distribution = {int(r["attempts"]): int(r["count"]) for r in attempt_rows}

            return {
                "server_utc_timestamp": db_utc_now.isoformat() if hasattr(db_utc_now, "isoformat") else str(db_utc_now),
                "status_counts": status_counts,
                "due_pending_count": due_pending_count,
                "expired_processing_count": expired_processing_count,
                "oldest_expired_lease_until": oldest_expired_until.isoformat() if hasattr(oldest_expired_until, "isoformat") else str(oldest_expired_until) if oldest_expired_until else None,
                "oldest_pending_age_sec": oldest_pending_age_sec,
                "oldest_processing_age_sec": oldest_processing_age_sec,
                "attempt_distribution": attempt_distribution,
            }
        finally:
            cur.close()

    def get_dead_letters(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent dead-lettered events for diagnosis without updating state."""
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT
                       id,
                       event_key,
                       trade_id,
                       event_type,
                       attempts,
                       last_error,
                       created_at,
                       sent_at
                   FROM rdt_telegram_outbox
                   WHERE status = 'dead'
                   ORDER BY id DESC
                   LIMIT %s""",
                (max(1, int(limit)),)
            )
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": int(r["id"]),
                    "event_key": str(r["event_key"]),
                    "trade_id": int(r["trade_id"]),
                    "event_type": str(r["event_type"]),
                    "attempts": int(r["attempts"]),
                    "last_error": str(r["last_error"] or ""),
                    "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                    "sent_at": r["sent_at"].isoformat() if r.get("sent_at") and hasattr(r["sent_at"], "isoformat") else str(r["sent_at"]) if r.get("sent_at") else None,
                })
            return result
        finally:
            cur.close()

    def evaluate_health_status(
        self,
        snapshot: Dict[str, Any],
        lock_status: Dict[str, Any],
        pending_warn_count: int = DEFAULT_PENDING_WARN_COUNT,
        pending_age_warn_sec: float = DEFAULT_PENDING_AGE_WARN_SEC,
    ) -> Tuple[str, List[str]]:
        """Evaluate operational health status based on snapshot metrics."""
        issues = []
        is_critical = False
        is_warning = False

        status_counts = snapshot.get("status_counts", {})
        dead_count = status_counts.get("dead", 0)
        expired_proc = snapshot.get("expired_processing_count", 0)
        pending_count = status_counts.get("pending", 0)
        oldest_pending_age = snapshot.get("oldest_pending_age_sec")

        # Critical conditions
        if dead_count > 0:
            is_critical = True
            issues.append(f"CRITICAL: {dead_count} dead-letter event(s) detected in queue")

        if expired_proc > 0:
            is_critical = True
            issues.append(f"CRITICAL: {expired_proc} expired/stale processing lease(s) detected")

        # Warning conditions
        if pending_count >= pending_warn_count:
            is_warning = True
            issues.append(f"WARN: Pending backlog ({pending_count}) exceeds threshold ({pending_warn_count})")

        if oldest_pending_age is not None and oldest_pending_age >= pending_age_warn_sec:
            is_warning = True
            issues.append(f"WARN: Oldest pending event age ({oldest_pending_age:.1f}s) exceeds threshold ({pending_age_warn_sec:.1f}s)")

        # Lock observation evaluation
        if not self.observation_mode:
            if not lock_status.get("is_locked"):
                is_warning = True
                issues.append("WARN: Outbox worker advisory lock is currently not held (worker daemon may be down)")

        if is_critical:
            status = "CRITICAL"
        elif is_warning:
            status = "WARN"
        else:
            status = "HEALTHY"

        return status, issues

    def snapshot(
        self,
        dead_letter_limit: int = 10,
        pending_warn_count: int = DEFAULT_PENDING_WARN_COUNT,
        pending_age_warn_sec: float = DEFAULT_PENDING_AGE_WARN_SEC,
    ) -> Dict[str, Any]:
        """Produce a complete, machine-readable operational health snapshot."""
        actual_db, current_user = self.verify_database_identity()
        lock_status = self.get_lock_status()
        queue_snapshot = self.get_queue_snapshot()
        dead_letters = self.get_dead_letters(limit=dead_letter_limit)
        health_status, issues = self.evaluate_health_status(
            queue_snapshot,
            lock_status,
            pending_warn_count=pending_warn_count,
            pending_age_warn_sec=pending_age_warn_sec,
        )

        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "database": actual_db,
            "current_user": current_user,
            "mode": "observation" if self.observation_mode else "delivery",
            "health_status": health_status,
            "issues": issues,
            "lock": lock_status,
            "queue": queue_snapshot,
            "dead_letters_sample": dead_letters,
        }


def inspect_outbox_health(
    conn,
    expected_database: str,
    observation_mode: bool = False,
    dead_letter_limit: int = 10,
) -> Dict[str, Any]:
    """Convenience helper to obtain a health snapshot from an active connection."""
    inspector = OutboxHealthInspector(conn, expected_database, observation_mode=observation_mode)
    return inspector.snapshot(dead_letter_limit=dead_letter_limit)


def inspect_dead_letters(
    conn,
    expected_database: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Convenience helper to retrieve dead-letter records."""
    inspector = OutboxHealthInspector(conn, expected_database)
    inspector.verify_database_identity()
    return inspector.get_dead_letters(limit=limit)


def main():
    parser = argparse.ArgumentParser(description="RetailBot2 Outbox Queue Observability Inspector")
    parser.add_argument("--database", choices=["signal_bot_test", "crypto_signals"], default="signal_bot_test")
    parser.add_argument("--observation", action="store_true", help="Observation-only mode inspection")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--dead-letters", action="store_true", help="Inspect detailed dead-letter records only")
    parser.add_argument("--limit", type=int, default=20, help="Dead-letter limit")
    args = parser.parse_args()

    # Import credential resolver safely
    from paper.outbox_worker import resolve_worker_db_credentials
    try:
        creds = resolve_worker_db_credentials(args.database, observation=args.observation)
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
        if args.dead_letters:
            dead_list = inspect_dead_letters(conn, args.database, limit=args.limit)
            if args.json:
                print(json.dumps(dead_list, indent=2))
            else:
                print(f"=== Dead Letters Sample (count={len(dead_list)}) ===")
                for dl in dead_list:
                    print(f"ID: {dl['id']} | Key: {dl['event_key']} | Attempts: {dl['attempts']} | Error: {dl['last_error']}")
        else:
            snapshot = inspect_outbox_health(
                conn, args.database, observation_mode=args.observation, dead_letter_limit=args.limit
            )
            if args.json:
                print(json.dumps(snapshot, indent=2))
            else:
                print(f"=== Outbox Health Snapshot [{snapshot['health_status']}] ===")
                print(f"Database: {snapshot['database']} | User: {snapshot['current_user']} | Mode: {snapshot['mode']}")
                print(f"Lock: {'HELD' if snapshot['lock']['is_locked'] else 'FREE'} (holder={snapshot['lock']['lock_holder_connection_id']})")
                print(f"Counts: {snapshot['queue']['status_counts']}")
                print(f"Due Pending: {snapshot['queue']['due_pending_count']} | Expired Leases: {snapshot['queue']['expired_processing_count']}")
                if snapshot["issues"]:
                    print("Issues:")
                    for issue in snapshot["issues"]:
                        print(f"  - {issue}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
