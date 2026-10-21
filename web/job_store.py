"""Durable, best-effort storage for dashboard background jobs.

Jobs run in daemon threads inside the web process, so a restart kills them. Their
state is mirrored to ``background_jobs`` (migration 014) so the dashboard still shows
history afterwards, and any job left ``queued``/``running`` by a previous process is
reported as ``interrupted`` instead of running forever.

Persistence is best effort: a database error is logged and never breaks the job itself.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger("web.job_store")

ACTIVE_STATES = ("queued", "running")
_PROMOTED = ("symbol", "market", "timeframe", "years", "candles_fetched")


def _connect():
    from db.db import get_pool
    return get_pool().get_connection()


def save_job(job: dict[str, Any], connect: Callable = _connect) -> bool:
    """Insert or update one job. Returns False (and logs) on any error."""
    try:
        extra = {k: job[k] for k in _PROMOTED if k in job}
        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO background_jobs (job_id, job_type, status, progress, message, payload, owner_pid)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status), progress = VALUES(progress),
                    message = VALUES(message), payload = VALUES(payload), owner_pid = VALUES(owner_pid)
                """,
                (str(job["id"]), str(job.get("type", "job"))[:40], str(job.get("status", "queued"))[:20],
                 int(float(job.get("progress") or 0)), str(job.get("message") or "")[:500],
                 json.dumps(extra), os.getpid()),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return True
    except Exception as exc:
        logger.warning("Could not persist job %s: %s", job.get("id"), exc)
        return False


def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    job = {
        "id": row["job_id"], "type": row["job_type"], "status": row["status"],
        "progress": float(row.get("progress") or 0), "message": row.get("message") or "",
        "created_at": _ts(row.get("created_at")), "updated_at": _ts(row.get("updated_at")),
    }
    job.update(payload or {})
    return job


def _ts(value: Any) -> float:
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    return float(value or 0)


def load_job(job_id: str, connect: Callable = _connect) -> Optional[dict[str, Any]]:
    try:
        conn = connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM background_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        return _row_to_job(row) if row else None
    except Exception as exc:
        logger.warning("Could not load job %s: %s", job_id, exc)
        return None


def list_jobs(limit: int = 30, connect: Callable = _connect) -> list[dict[str, Any]]:
    try:
        conn = connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM background_jobs ORDER BY created_at DESC LIMIT %s", (int(limit),))
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
        return [_row_to_job(r) for r in rows]
    except Exception as exc:
        logger.warning("Could not list jobs: %s", exc)
        return []


def mark_interrupted(connect: Callable = _connect, pid: Optional[int] = None) -> int:
    """At startup: jobs another process left active can never finish. Returns rows changed."""
    pid = os.getpid() if pid is None else pid
    try:
        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE background_jobs SET status = 'interrupted', "
                "message = 'Interrupted by a server restart; run it again.' "
                "WHERE status IN ('queued','running') AND (owner_pid IS NULL OR owner_pid <> %s)",
                (pid,),
            )
            changed = cur.rowcount
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return int(changed or 0)
    except Exception as exc:
        logger.warning("Could not mark interrupted jobs: %s", exc)
        return 0


def merge_jobs(live: list[dict[str, Any]], stored: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    """Live in-memory jobs win over stored copies of the same id; newest first."""
    by_id = {j["id"]: j for j in stored}
    by_id.update({j["id"]: j for j in live})
    merged = sorted(by_id.values(), key=lambda j: j.get("created_at", 0), reverse=True)
    return merged[:limit]
