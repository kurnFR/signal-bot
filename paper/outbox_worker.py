"""Standalone RetailBot2 Telegram Outbox Worker.

Operates independently of the RetailBot2 trading loop:
- Does NOT initialize Binance, load markets, scan strategies, or manage trades.
- Dedicated, unpooled MariaDB connection S_lock holds MariaDB advisory lock 'rdt_outbox_worker_lock'.
- Uses isolated child process group for Telegram transport with PR_SET_PDEATHSIG(SIGKILL).
- Inspects entire process group on cleanup and filters out inert zombies.
- Enters STATE_FATAL_LOCKOUT if process group cleanup cannot be confirmed, retaining
  the advisory lock to prevent replacement workers from claiming while an unverified process exists.

Important:
- At-least-once delivery semantics: duplicate Telegram messages remain possible when
  Telegram accepts a request but the acknowledgement cannot be persisted.
- Do not claim a universal 15-second maximum process lifetime. D-state processes can outlive
  SIGKILL and the cleanup deadline. The 90-second lease is a normal-path recovery margin,
  not proof of safety under every kernel or network failure.
"""

import argparse
import ctypes
import json
import logging
import os
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector

from paper.outbox_store import (
    OUTBOX_LOCK_NAME,
    OutboxStore,
    format_entry_message,
    format_exit_message,
)
from notifications.telegram_notifier import is_telegram_delivery_authorized
from paper.outbox_transport import (
    DELIVERY_UNKNOWN_MARKER,
    DELIVERY_UNKNOWN_PREFIX,
    STATUS_DELIVERED,
    STATUS_DELIVERY_UNKNOWN,
    STATUS_FAILED_CONFIRMED,
    format_delivery_unknown_error,
)


logger = logging.getLogger("outbox_worker")

STATUS_CLEANUP_FAILED = "CLEANUP_FAILED"
ALLOWED_TEST_DATABASES = ("signal_bot_test",)
ALLOWED_PROD_DATABASES = ("crypto_signals",)


class OutboxWorkerState(Enum):
    INITIALIZING = "INITIALIZING"
    ACQUIRING_LOCK = "ACQUIRING_LOCK"
    RUNNING = "RUNNING"
    OBSERVING = "OBSERVING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FATAL_LOCKOUT = "FATAL_LOCKOUT"


class DatabaseAuthorizationError(RuntimeError):
    """Raised when the connected database is not authorized for the requested mode."""
    pass


class LockLostError(RuntimeError):
    """Raised when ownership of the MariaDB advisory lock is lost."""
    pass


class ProcessInspectionError(RuntimeError):
    """Raised when process group state cannot be reliably verified."""
    pass


class ProcessInspectionStatus(str, Enum):
    CONFIRMED_EMPTY = "confirmed_empty"
    LIVE_PROCESSES_PRESENT = "live_processes_present"
    STATE_UNVERIFIED = "state_unverified"


@dataclass(frozen=True)
class ProcessInspectionResult:
    status: ProcessInspectionStatus
    live_pids: List[int] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def is_confirmed_empty(self) -> bool:
        return self.status == ProcessInspectionStatus.CONFIRMED_EMPTY

    @property
    def is_live(self) -> bool:
        return self.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT

    @property
    def is_unverified(self) -> bool:
        return self.status == ProcessInspectionStatus.STATE_UNVERIFIED


def verify_database_identity(conn, allowed_databases: Tuple[str, ...]) -> str:
    """Query SELECT DATABASE() on conn and assert it matches allowed_databases."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DATABASE()")
        row = cursor.fetchone()
        actual_db = row[0] if row else ""
    finally:
        cursor.close()

    if actual_db not in allowed_databases:
        raise DatabaseAuthorizationError(
            f"Database authorization rejected: connected to {actual_db!r}; "
            f"expected one of {allowed_databases!r}"
        )
    return actual_db


REQUIRED_OUTBOX_COLUMNS = (
    "id",
    "event_key",
    "trade_id",
    "event_type",
    "payload_json",
    "status",
    "attempts",
    "next_attempt_at",
    "locked_until",
    "claim_token",
    "last_error",
    "created_at",
    "sent_at",
)


class SchemaVerificationError(RuntimeError):
    """Raised when required outbox schema tables or columns are missing."""
    pass


def verify_outbox_schema(conn) -> None:
    """Preflight validation of required outbox table and columns before lock acquisition.

    Queries information_schema to verify rdt_telegram_outbox and all required columns exist.
    Fails closed without modifying any database state.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT TABLE_NAME
               FROM information_schema.tables
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = 'rdt_telegram_outbox'"""
        )
        row = cursor.fetchone()
        if not row:
            raise SchemaVerificationError(
                "Required outbox table 'rdt_telegram_outbox' does not exist in database. "
                "Worker startup halted fail-closed. Operator investigation required against "
                "approved migration history (migrations 025-027 must not be blindly re-run); "
                "do not automatically modify production schema."
            )

        cursor.execute(
            """SELECT COLUMN_NAME
               FROM information_schema.columns
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = 'rdt_telegram_outbox'"""
        )
        existing = {r[0] for r in cursor.fetchall()}
        missing = sorted(set(REQUIRED_OUTBOX_COLUMNS) - existing)
        if missing:
            raise SchemaVerificationError(
                f"Required outbox column(s) missing from 'rdt_telegram_outbox': {', '.join(missing)}. "
                "Worker startup halted fail-closed. Operator investigation required against "
                "approved migration history (migrations 025-027 must not be blindly re-run); "
                "do not automatically modify production schema."
            )
    finally:
        cursor.close()


def inspect_process_group(pgid: int, proc_root: str = "/proc") -> ProcessInspectionResult:
    """Inspect process group `pgid` via procfs.

    Returns:
    - CONFIRMED_EMPTY if no live processes in `pgid` exist (zombies filtered out).
    - LIVE_PROCESSES_PRESENT if one or more live non-zombie processes in `pgid` exist.
    - STATE_UNVERIFIED if inspection fails (unreadable procfs, permission error on
      potential target, or malformed stat data).
    """
    if not os.path.exists(proc_root):
        return ProcessInspectionResult(
            status=ProcessInspectionStatus.STATE_UNVERIFIED,
            error=f"Process filesystem {proc_root!r} does not exist",
        )

    try:
        entries = os.listdir(proc_root)
    except Exception as exc:
        return ProcessInspectionResult(
            status=ProcessInspectionStatus.STATE_UNVERIFIED,
            error=f"Cannot list process filesystem {proc_root!r}: {exc}",
        )

    live_pids: List[int] = []
    unverified_reasons: List[str] = []

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        stat_path = os.path.join(proc_root, str(pid), "stat")
        try:
            with open(stat_path, "rb") as f:
                stat_bytes = f.read()
        except (FileNotFoundError, ProcessLookupError):
            # Process exited during directory traversal; safe and expected
            continue
        except PermissionError as exc:
            # Check if this process belongs to our target PGID
            if proc_root == "/proc":
                try:
                    actual_pgid = os.getpgid(pid)
                    if actual_pgid != pgid:
                        # Confirmed unrelated process group; safe to ignore
                        continue
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except (PermissionError, OSError):
                    unverified_reasons.append(
                        f"Permission denied reading stat for PID {pid} and getpgid failed: {exc}"
                    )
                    continue
            unverified_reasons.append(
                f"Permission denied reading stat for PID {pid}: {exc}"
            )
            continue
        except OSError as exc:
            unverified_reasons.append(
                f"OS error reading stat for PID {pid}: {exc}"
            )
            continue
        except Exception as exc:
            unverified_reasons.append(
                f"Unexpected error reading stat for PID {pid}: {exc}"
            )
            continue

        rparen_idx = stat_bytes.rfind(b")")
        if rparen_idx == -1:
            if proc_root == "/proc":
                try:
                    actual_pgid = os.getpgid(pid)
                    if actual_pgid != pgid:
                        continue
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except (PermissionError, OSError):
                    pass
            unverified_reasons.append(
                f"Malformed stat data for PID {pid}: missing closing parenthesis"
            )
            continue

        fields = stat_bytes[rparen_idx + 2:].split()
        if len(fields) < 3:
            if proc_root == "/proc":
                try:
                    actual_pgid = os.getpgid(pid)
                    if actual_pgid != pgid:
                        continue
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except (PermissionError, OSError):
                    pass
            unverified_reasons.append(
                f"Malformed stat data for PID {pid}: insufficient fields ({len(fields)} < 3)"
            )
            continue

        try:
            state_str = fields[0].decode("ascii")
            if len(state_str) != 1 or state_str not in "RSDZTtWXxKPI":
                unverified_reasons.append(
                    f"Malformed stat data for PID {pid}: invalid state field {fields[0]!r}"
                )
                continue
            state = state_str
        except Exception:
            unverified_reasons.append(
                f"Malformed stat data for PID {pid}: non-ASCII state field {fields[0]!r}"
            )
            continue

        try:
            pgrp = int(fields[2])  # Field 5: process group ID
        except (ValueError, UnicodeDecodeError):
            if proc_root == "/proc":
                try:
                    actual_pgid = os.getpgid(pid)
                    if actual_pgid != pgid:
                        continue
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except (PermissionError, OSError):
                    pass
            unverified_reasons.append(
                f"Malformed stat data for PID {pid}: non-integer pgrp {fields[2]!r}"
            )
            continue

        if pgrp == pgid and state != "Z":
            live_pids.append(pid)

    if unverified_reasons:
        return ProcessInspectionResult(
            status=ProcessInspectionStatus.STATE_UNVERIFIED,
            live_pids=live_pids,
            error="; ".join(unverified_reasons),
        )

    if live_pids:
        return ProcessInspectionResult(
            status=ProcessInspectionStatus.LIVE_PROCESSES_PRESENT,
            live_pids=live_pids,
        )

    return ProcessInspectionResult(
        status=ProcessInspectionStatus.CONFIRMED_EMPTY,
        live_pids=[],
    )


def get_live_pids_in_pgid(pgid: int, proc_root: str = "/proc") -> List[int]:
    """Scan /proc to find all PIDs belonging to process group `pgid` that are NOT zombies ('Z').

    Returns a list of live PIDs (empty list if confirmed empty).
    Raises ProcessInspectionError if process group state cannot be verified.
    """
    res = inspect_process_group(pgid, proc_root=proc_root)
    if res.status == ProcessInspectionStatus.STATE_UNVERIFIED:
        raise ProcessInspectionError(res.error or "Process state cannot be verified")
    return list(res.live_pids)


def _child_preexec_pdeathsig(expected_parent_pid: int):
    """Set parent death signal to SIGKILL and fail closed if prctl cannot be installed."""
    try:
        PR_SET_PDEATHSIG = 1
        SIGKILL = 9
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0:
            os._exit(1)
        # Race-condition check: if parent died before prctl, exit immediately
        if os.getppid() != expected_parent_pid:
            os._exit(1)
    except Exception:
        os._exit(1)


def _transport_child_environment() -> Dict[str, str]:
    """Build a minimal child environment that cannot inherit application secrets."""
    allowed = {
        "PATH", "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def reap_child_bounded(child_pid: int, timeout_sec: float) -> Optional[int]:
    """Bounded non-blocking reap of direct child using waitpid(WNOHANG)."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while time.monotonic() <= deadline:
        try:
            pid, status = os.waitpid(child_pid, os.WNOHANG)
            if pid == child_pid:
                if os.WIFEXITED(status):
                    return os.WEXITSTATUS(status)
                if os.WIFSIGNALED(status):
                    return -os.WTERMSIG(status)
                return status
        except ChildProcessError:
            return 0
        time.sleep(0.02)
    return None


def drain_pipes_bounded(child_proc: subprocess.Popen, deadline: float) -> Tuple[bytes, bytes]:
    """Drain stdout and stderr non-blocking up to deadline, then explicitly close pipes."""
    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []

    sel = selectors.DefaultSelector()
    for pipe, chunks in [(child_proc.stdout, stdout_chunks), (child_proc.stderr, stderr_chunks)]:
        if pipe:
            os.set_blocking(pipe.fileno(), False)
            sel.register(pipe, selectors.EVENT_READ, chunks)

    while sel.get_map() and time.monotonic() < deadline:
        timeout = max(0.0, min(0.2, deadline - time.monotonic()))
        events = sel.select(timeout=timeout)
        for key, _ in events:
            try:
                chunk = key.fileobj.read(4096)
                if chunk:
                    key.data.append(chunk)
                else:
                    sel.unregister(key.fileobj)
                    try:
                        key.fileobj.close()
                    except OSError:
                        pass
            except (OSError, ValueError):
                sel.unregister(key.fileobj)
                try:
                    key.fileobj.close()
                except OSError:
                    pass

    # Explicitly unregister and close remaining parent pipe descriptors
    for key in list(sel.get_map().values()):
        sel.unregister(key.fileobj)
        try:
            key.fileobj.close()
        except OSError:
            pass
    sel.close()

    return b"".join(stdout_chunks), b"".join(stderr_chunks)


def dispatch_event_via_subprocess(
    transport_payload: Dict[str, Any],
    timeout_sec: float = 10.0,
    cleanup_grace_sec: float = 1.5,
    proc_root: str = "/proc",
) -> Dict[str, Any]:
    """Execute Telegram transport in an isolated subprocess group with bounded cleanup."""
    parent_pid = os.getpid()
    payload_bytes = json.dumps(transport_payload).encode("utf-8")

    try:
        child_proc = subprocess.Popen(
            [sys.executable, "-m", "paper.outbox_transport"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # PGID == child_proc.pid
            preexec_fn=lambda: _child_preexec_pdeathsig(parent_pid),
            env=_transport_child_environment(),
        )
    except Exception as exc:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": format_delivery_unknown_error(f"Failed to spawn transport child: {exc}"),
        }

    pgid = child_proc.pid

    # Write payload to stdin and close immediately
    try:
        if child_proc.stdin:
            child_proc.stdin.write(payload_bytes)
            child_proc.stdin.flush()
            child_proc.stdin.close()
    except (OSError, BrokenPipeError):
        pass

    start_time = time.monotonic()
    deadline = start_time + max(0.1, timeout_sec)

    # Drain pipes until EOF or deadline
    stdout_bytes, stderr_bytes = drain_pipes_bounded(child_proc, deadline)

    # Check child exit status
    child_exit = reap_child_bounded(child_proc.pid, 0.05)
    timed_out = child_exit is None

    if timed_out:
        logger.warning(
            "Transport subprocess (PID %d, PGID %d) exceeded deadline %.1fs; initiating SIGTERM",
            child_proc.pid, pgid, timeout_sec
        )
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

        child_exit = reap_child_bounded(child_proc.pid, cleanup_grace_sec)
        if child_exit is None:
            logger.warning(
                "Transport process group %d did not terminate on SIGTERM; escalating to SIGKILL",
                pgid
            )
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            child_exit = reap_child_bounded(child_proc.pid, cleanup_grace_sec)

    # Reap direct child if not yet reaped
    if child_exit is None:
        reap_child_bounded(child_proc.pid, 0.2)

    # A direct child may exit while a descendant in the same process group survives.
    # Do not merely lock out in that case: explicitly terminate the remaining group.
    captured_descendants: List[int] = []
    inspection = inspect_process_group(pgid, proc_root=proc_root)
    if inspection.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT:
        captured_descendants.extend(inspection.live_pids)
        logger.warning(
            "Direct transport child exited but live descendants %r remain in PGID %d; terminating group",
            inspection.live_pids, pgid
        )
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        group_deadline = time.monotonic() + max(0.0, cleanup_grace_sec)
        while time.monotonic() < group_deadline:
            inspection = inspect_process_group(pgid, proc_root=proc_root)
            if inspection.status != ProcessInspectionStatus.LIVE_PROCESSES_PRESENT:
                break
            time.sleep(0.02)
        if inspection.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            group_deadline = time.monotonic() + max(0.0, cleanup_grace_sec)
            while time.monotonic() < group_deadline:
                inspection = inspect_process_group(pgid, proc_root=proc_root)
                if inspection.status != ProcessInspectionStatus.LIVE_PROCESSES_PRESENT:
                    break
                time.sleep(0.02)

    # Process-group verification: ensure NO live descendants remain in PGID.
    final_inspection = inspect_process_group(pgid, proc_root=proc_root)
    if final_inspection.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT:
        logger.critical(
            "Transport cleanup failed: live descendants %r still active in PGID %d after SIGKILL escalation",
            final_inspection.live_pids, pgid
        )
        return {
            "status": STATUS_CLEANUP_FAILED,
            "error": f"Live descendants remaining after SIGKILL: {final_inspection.live_pids}",
            "live_pids": final_inspection.live_pids,
            "pgid": pgid,
        }
    elif final_inspection.status == ProcessInspectionStatus.STATE_UNVERIFIED:
        logger.critical(
            "Transport cleanup failed: process state cannot be verified for PGID %d: %s",
            pgid, final_inspection.error
        )
        return {
            "status": STATUS_CLEANUP_FAILED,
            "error": f"Process state cannot be verified for PGID {pgid}: {final_inspection.error}",
            "live_pids": final_inspection.live_pids,
            "pgid": pgid,
        }
    else:
        logger.info("Confirmed no live processes remain in PGID %d", pgid)

    if timed_out:
        out = {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": format_delivery_unknown_error(f"Transport exceeded deadline {timeout_sec:.1f}s"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace")[:500],
        }
        if captured_descendants:
            out["captured_descendants"] = captured_descendants
        return out

    # Child completed before deadline; parse JSON result
    try:
        stdout_text = stdout_bytes.decode("utf-8").strip()
        if not stdout_text:
            out = {
                "status": STATUS_DELIVERY_UNKNOWN,
                "error": format_delivery_unknown_error("Empty stdout from transport child"),
                "stderr": stderr_bytes.decode("utf-8", errors="replace")[:500],
            }
            if captured_descendants:
                out["captured_descendants"] = captured_descendants
            return out
        parsed = json.loads(stdout_text)
        if isinstance(parsed, dict) and captured_descendants:
            parsed["captured_descendants"] = captured_descendants
        return parsed
    except Exception as exc:
        out = {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": format_delivery_unknown_error(f"Failed to parse transport child JSON: {exc}"),
            "raw_stdout": stdout_bytes.decode("utf-8", errors="replace")[:500],
        }
        if captured_descendants:
            out["captured_descendants"] = captured_descendants
        return out


class OutboxSessionManager:
    """Manages the persistent S_lock connection, advisory lock, and outbox operations."""

    def __init__(self, db_config: Dict[str, Any], allowed_databases: Tuple[str, ...]):
        self.db_config = db_config
        self.allowed_databases = allowed_databases
        self.s_lock = None
        self.db_manager = object.__new__(OutboxStore)
        self.db_manager.logger = logger
        self.connected_database = ""

    @property
    def conn_id(self) -> Optional[int]:
        """Return the underlying MariaDB connection ID of S_lock, or None if disconnected."""
        if self.s_lock and self.s_lock.is_connected():
            return getattr(self.s_lock, "connection_id", None)
        return None

    def connect_and_acquire(self) -> bool:
        """Connect unpooled S_lock, verify database authorization, and acquire advisory lock."""
        try:
            self.s_lock = mysql.connector.connect(
                host=self.db_config.get("host", "127.0.0.1"),
                port=int(self.db_config.get("port", 3306)),
                user=self.db_config.get("user", ""),
                password=self.db_config.get("password", ""),
                database=self.db_config.get("database", ""),
                connection_timeout=5,
                autocommit=False,
            )
            self.connected_database = verify_database_identity(self.s_lock, self.allowed_databases)
            logger.info("S_lock connected to authorized database: %r", self.connected_database)

            # Preflight: Validate required outbox schema BEFORE attempting lock acquisition
            verify_outbox_schema(self.s_lock)
            logger.info("Schema preflight verified for 'rdt_telegram_outbox'")

            # Acquire MariaDB advisory lock
            acquired = self.db_manager.acquire_outbox_lock(self.s_lock, timeout_seconds=0)
            if not acquired:
                logger.warning(
                    "Advisory lock %r is held by another session; acquisition failed",
                    OUTBOX_LOCK_NAME
                )
                self.close()
                return False

            if not self.check_lock_owned():
                logger.error("Acquired advisory lock but ownership verification failed")
                self.close()
                return False

            logger.info("Advisory lock %r successfully acquired and verified on S_lock", OUTBOX_LOCK_NAME)
            return True
        except SchemaVerificationError as exc:
            logger.critical("Schema preflight validation failed: %s", exc)
            self.close()
            raise
        except Exception as exc:
            logger.error("Failed to connect or acquire advisory lock: %s", exc)
            self.close()
            return False

    def check_lock_owned(self) -> bool:
        """Check if S_lock still owns the advisory lock."""
        if not self.s_lock or not self.s_lock.is_connected():
            return False
        return self.db_manager.is_outbox_lock_owned(self.s_lock)

    def connection_alive(self) -> bool:
        """Return the connector's local socket state without issuing another query."""
        return bool(self.s_lock and self.s_lock.is_connected())

    def heartbeat(self) -> bool:
        """Execute SELECT 1 keep-alive ping on S_lock."""
        if not self.s_lock:
            return False
        cursor = None
        try:
            cursor = self.s_lock.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return True
        except Exception as exc:
            logger.warning("S_lock heartbeat ping failed: %s", exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def claim_event(self, lease_seconds: int = 90) -> Optional[Dict]:
        """Claim one outbox event on S_lock; commits transaction before return."""
        if not self.check_lock_owned():
            raise LockLostError("Cannot claim outbox event: advisory lock ownership lost")
        return self.db_manager.claim_telegram_outbox_event(lease_seconds=lease_seconds, conn=self.s_lock)

    def mark_sent(self, event_id: int, claim_token: str) -> bool:
        """Acknowledge sent event on S_lock using claim_token fencing."""
        if not self.check_lock_owned():
            raise LockLostError("Cannot acknowledge outbox event: advisory lock ownership lost")
        return self.db_manager.mark_telegram_outbox_sent(event_id, claim_token, conn=self.s_lock)

    def mark_retry(self, event_id: int, claim_token: str, attempts: int, error: str,
                   delay: Optional[int] = None) -> bool:
        """Transition event to retry on S_lock using claim_token fencing."""
        if not self.check_lock_owned():
            raise LockLostError("Cannot retry outbox event: advisory lock ownership lost")
        return self.db_manager.retry_telegram_outbox_event(
            event_id, claim_token, attempts, error, conn=self.s_lock, delay=delay
        )

    def mark_dead(self, event_id: int, claim_token: str, error: str) -> bool:
        """Transition event to dead on S_lock using claim_token fencing."""
        if not self.check_lock_owned():
            raise LockLostError("Cannot dead-letter outbox event: advisory lock ownership lost")
        return self.db_manager.dead_letter_telegram_outbox_event(
            event_id, claim_token, error, conn=self.s_lock
        )

    def inspect_queue(self) -> Dict[str, Any]:
        """Perform read-only inspection of outbox queue on S_lock without claiming or mutating.

        Queries table status counts and due items using only verified schema columns.
        Does not initiate transactions, acquire row locks, or modify any database state.
        """
        if not self.check_lock_owned():
            raise LockLostError("Cannot inspect outbox queue: advisory lock ownership lost")
        cursor = self.s_lock.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT
                       status,
                       COUNT(*) AS count,
                       MIN(created_at) AS oldest_created_at
                   FROM rdt_telegram_outbox
                   GROUP BY status"""
            )
            rows = cursor.fetchall()
            cursor.execute(
                """SELECT COUNT(*) AS due_count
                   FROM rdt_telegram_outbox
                   WHERE attempts < 8 AND (
                       (status='pending' AND next_attempt_at <= UTC_TIMESTAMP())
                       OR (status='processing' AND locked_until <= UTC_TIMESTAMP())
                   )"""
            )
            due_row = cursor.fetchone()
            due_count = due_row["due_count"] if due_row else 0
            return {
                "status_counts": {r["status"]: int(r["count"]) for r in rows},
                "oldest_by_status": {r["status"]: r["oldest_created_at"] for r in rows if r.get("oldest_created_at")},
                "due_count": int(due_count),
            }
        finally:
            cursor.close()

    def close(self):
        """Release advisory lock and cleanly close S_lock."""
        if self.s_lock:
            try:
                if self.s_lock.is_connected():
                    self.db_manager.release_outbox_lock(self.s_lock)
            except Exception:
                pass
            try:
                self.s_lock.close()
            except Exception:
                pass
            self.s_lock = None


class OutboxWorker:
    """Outbox dispatcher daemon managing lock, polling, and isolated transport."""

    def __init__(
        self,
        db_config: Dict[str, Any],
        telegram_token: str = "",
        telegram_chat_id: str = "",
        allowed_databases: Tuple[str, ...] = ALLOWED_TEST_DATABASES,
        poll_interval_sec: float = 1.0,
        transport_timeout_sec: float = 10.0,
        cleanup_grace_sec: float = 1.5,
        lease_seconds: int = 90,
        max_attempts: int = 8,
        mock_transport_response: Optional[Dict[str, Any]] = None,
        proc_root: str = "/proc",
        observation_mode: bool = False,
    ):
        self.session_mgr = OutboxSessionManager(db_config, allowed_databases)
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.poll_interval_sec = poll_interval_sec
        self.transport_timeout_sec = transport_timeout_sec
        self.cleanup_grace_sec = cleanup_grace_sec
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.mock_transport_response = mock_transport_response
        self.proc_root = proc_root
        self.observation_mode = observation_mode
        self.state = OutboxWorkerState.INITIALIZING
        self._shutdown_requested = False
        self._fatal_pgid: Optional[int] = None

    def _handle_signal(self, signum, frame):
        logger.info("Signal %d received; requesting graceful outbox worker shutdown", signum)
        self._shutdown_requested = True

    def setup_signals(self):
        """Register graceful shutdown handlers for SIGINT and SIGTERM."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def format_event_text(self, event: Dict[str, Any]) -> str:
        """Format entry, exit, or operational payload into Telegram HTML text."""
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        trade_id = int(event.get("trade_id") or 0)

        if event_type == "entry":
            return format_entry_message(payload, trade_id)
        if event_type == "exit":
            return format_exit_message(payload)
        if event_type == "operational":
            text = payload.get("text") or payload.get("message")
            if text:
                return str(text)
            raise ValueError(f"Operational outbox event {event.get('id')} missing 'text' payload")
        raise ValueError(f"Unsupported outbox event_type: {event_type!r}")

    def process_one_event(self) -> bool:
        """Claim and process at most one due event. Returns True if an event was processed."""
        if self.state == OutboxWorkerState.FATAL_LOCKOUT:
            logger.critical("process_one_event called while in FATAL_LOCKOUT; refusing claims")
            return False

        if not self.session_mgr.check_lock_owned():
            logger.error("Advisory lock ownership lost during event loop; stopping worker")
            self.state = OutboxWorkerState.STOPPED
            raise LockLostError("Advisory lock lost")

        if self.observation_mode:
            summary = self.session_mgr.inspect_queue()
            logger.info(
                "Observation mode active: read-only queue summary (due=%d, counts=%s)",
                summary.get("due_count", 0),
                summary.get("status_counts", {}),
            )
            return False

        event = self.session_mgr.claim_event(lease_seconds=self.lease_seconds)
        if not event:
            return False

        event_id = int(event["id"])
        claim_token = str(event["claim_token"])
        attempts = int(event.get("attempts") or 1)

        try:
            message_text = self.format_event_text(event)
        except Exception as exc:
            logger.error("Failed to format outbox event %d: %s; marking dead", event_id, exc)
            self.session_mgr.mark_dead(event_id, claim_token, f"Payload formatting error: {exc}")
            return True

        transport_input = {
            "token": self.telegram_token,
            "chat_id": self.telegram_chat_id,
            "text": message_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "connect_timeout": 5.0,
            "read_timeout": self.transport_timeout_sec,
            "mock_response": self.mock_transport_response,
        }

        # Dispatch via isolated subprocess
        outcome = dispatch_event_via_subprocess(
            transport_input,
            timeout_sec=self.transport_timeout_sec,
            cleanup_grace_sec=self.cleanup_grace_sec,
            proc_root=self.proc_root,
        )

        status = outcome.get("status")

        # 1. Cleanup failure: Enter fatal lockout, retain lock, do not release or claim
        if status == STATUS_CLEANUP_FAILED:
            self.state = OutboxWorkerState.FATAL_LOCKOUT
            self._fatal_pgid = int(outcome.get("pgid") or 0) or None
            logger.critical(
                "FATAL: Transport cleanup failed for event %d (PGID=%s, error=%s); entering STATE_FATAL_LOCKOUT. "
                "Retaining advisory lock on S_lock. Shutdown requests will not release ownership while "
                "live transport processes remain. Operator intervention required.",
                event_id, self._fatal_pgid, outcome.get("error")
            )
            return True

        # 2. Delivered: Acknowledge sent
        if status == STATUS_DELIVERED:
            ok = self.session_mgr.mark_sent(event_id, claim_token)
            if ok:
                logger.info("Successfully delivered and acknowledged outbox event %d", event_id)
            else:
                logger.warning(
                    "Delivered event %d to Telegram but database ack was rejected (token mismatch/expired lease)",
                    event_id
                )
            return True

        # 3. Confirmed rejection
        if status == STATUS_FAILED_CONFIRMED:
            is_perm = bool(outcome.get("permanent", False))
            retry_after = outcome.get("retry_after")
            err_desc = outcome.get("description") or outcome.get("error") or "Confirmed rejection"

            if is_perm or attempts >= self.max_attempts:
                logger.warning(
                    "Permanent rejection for outbox event %d (attempts=%d): %s; moving to dead",
                    event_id, attempts, err_desc
                )
                self.session_mgr.mark_dead(event_id, claim_token, err_desc)
            else:
                logger.info(
                    "Transient rejection for outbox event %d (attempts=%d): %s; scheduling retry (delay=%s)",
                    event_id, attempts, err_desc, retry_after
                )
                self.session_mgr.mark_retry(event_id, claim_token, attempts, err_desc, delay=retry_after)
            return True

        # 4. Unknown delivery state: Never mark sent; schedule retry with backoff
        raw_error = outcome.get("error") or "Delivery state unknown"
        err_desc = format_delivery_unknown_error(raw_error)
        logger.warning(
            "Ambiguous delivery status for outbox event %d (attempts=%d): %s; scheduling backoff retry",
            event_id, attempts, err_desc
        )
        self.session_mgr.mark_retry(event_id, claim_token, attempts, err_desc)
        return True

    def run(self):
        """Main worker execution loop."""
        if not self.session_mgr.check_lock_owned():
            self.state = OutboxWorkerState.ACQUIRING_LOCK
            try:
                acquired = self.session_mgr.connect_and_acquire()
            except SchemaVerificationError as exc:
                self.state = OutboxWorkerState.STOPPED
                logger.critical("Outbox worker startup aborted by schema preflight: %s", exc)
                return 1
            if not acquired:
                self.state = OutboxWorkerState.STOPPED
                logger.error("Could not acquire advisory lock; exiting worker")
                return 1

        if self._fatal_pgid or self.state == OutboxWorkerState.FATAL_LOCKOUT:
            self.state = OutboxWorkerState.FATAL_LOCKOUT
        elif self.observation_mode:
            self.state = OutboxWorkerState.OBSERVING
        else:
            self.state = OutboxWorkerState.RUNNING
        logger.info("Outbox worker running in state %s", self.state.value)

        try:
            while True:
                if self.state == OutboxWorkerState.FATAL_LOCKOUT:
                    # Never release S_lock while an unverified live transport process remains.
                    logger.critical(
                        "Worker is in FATAL_LOCKOUT (PGID=%s); claims are disabled and lock heartbeats continue.",
                        self._fatal_pgid
                    )
                    if not self.session_mgr.heartbeat():
                        # A failed query is not proof that the underlying socket/session is gone.
                        # If the connector still considers it connected, retain S_lock and retry;
                        # never voluntarily release a potentially-held advisory lock while a
                        # transport process may still be alive.
                        if self.session_mgr.connection_alive():
                            logger.critical(
                                "S_lock heartbeat query failed but the connection remains open; "
                                "retaining FATAL_LOCKOUT and retrying without releasing the lock"
                            )
                            time.sleep(min(self.poll_interval_sec, 5.0))
                            continue
                        # The local connection is gone; MariaDB will release its advisory lock
                        # when it detects session loss. No local action can preserve ownership.
                        logger.critical(
                            "S_lock connection is no longer open during FATAL_LOCKOUT; "
                            "transport exclusion cannot be guaranteed"
                        )
                        return 75

                    if self._fatal_pgid:
                        try:
                            inspection = inspect_process_group(self._fatal_pgid, proc_root=self.proc_root)
                        except Exception as exc:
                            logger.critical(
                                "Unexpected exception during inspect_process_group for PGID %s: %s; failing closed",
                                self._fatal_pgid, exc
                            )
                            inspection = ProcessInspectionResult(
                                status=ProcessInspectionStatus.STATE_UNVERIFIED,
                                error=f"Unexpected inspection error: {exc}",
                            )
                        if inspection.status == ProcessInspectionStatus.CONFIRMED_EMPTY:
                            logger.info(
                                "Confirmed no live transport processes remain in PGID %s; fatal lockout cleared",
                                self._fatal_pgid
                            )
                            self._fatal_pgid = None
                            self.state = (
                                OutboxWorkerState.STOPPING
                                if self._shutdown_requested
                                else (
                                    OutboxWorkerState.OBSERVING
                                    if self.observation_mode
                                    else OutboxWorkerState.RUNNING
                                )
                            )
                        elif inspection.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT:
                            logger.warning(
                                "Live transport processes %r still present in PGID %s; retaining FATAL_LOCKOUT and S_lock",
                                inspection.live_pids, self._fatal_pgid
                            )
                            time.sleep(min(self.poll_interval_sec, 5.0))
                            continue
                        else:  # ProcessInspectionStatus.STATE_UNVERIFIED
                            logger.critical(
                                "Process state cannot be verified for PGID %s (%s); retaining FATAL_LOCKOUT and S_lock (fail-closed)",
                                self._fatal_pgid, inspection.error
                            )
                            time.sleep(min(self.poll_interval_sec, 5.0))
                            continue
                    else:
                        if not self._shutdown_requested:
                            time.sleep(min(self.poll_interval_sec, 5.0))
                            continue

                if self._shutdown_requested:
                    logger.info("Graceful shutdown finished; releasing advisory lock")
                    return 0

                processed = self.process_one_event()
                if not processed:
                    time.sleep(self.poll_interval_sec)
                    if not self.session_mgr.heartbeat():
                        raise LockLostError("S_lock heartbeat failed; advisory-lock ownership is no longer reliable")
        finally:
            # A cleanup failure is a hard process-lifetime constraint. Even an
            # unexpected exception elsewhere in run() must not close S_lock
            # while a transport process group remains live or unverified.
            while self._fatal_pgid is not None:
                self.state = OutboxWorkerState.FATAL_LOCKOUT
                try:
                    inspection = inspect_process_group(
                        self._fatal_pgid, proc_root=self.proc_root
                    )
                except Exception as exc:
                    inspection = ProcessInspectionResult(
                        status=ProcessInspectionStatus.STATE_UNVERIFIED,
                        error=f"Unexpected finalizer inspection error: {exc}",
                    )

                if inspection.status == ProcessInspectionStatus.CONFIRMED_EMPTY:
                    logger.info(
                        "Finalizer confirmed no live transport processes remain in PGID %s",
                        self._fatal_pgid,
                    )
                    self._fatal_pgid = None
                    break

                # Keep the lock-owning session alive where possible. A lost
                # database session cannot preserve the server-side advisory
                # lock, but the parent must still not exit while descendants
                # remain unverified.
                try:
                    if self.session_mgr.connection_alive():
                        self.session_mgr.heartbeat()
                except Exception:
                    logger.exception(
                        "Finalizer could not heartbeat S_lock during transport lockout"
                    )

                logger.critical(
                    "Finalizer retaining worker process and advisory-lock session: "
                    "transport PGID %s is not confirmed empty (%s)",
                    self._fatal_pgid,
                    inspection.error or inspection.status.value,
                )
                time.sleep(min(max(self.poll_interval_sec, 0.1), 5.0))

            self.state = OutboxWorkerState.STOPPING
            self.session_mgr.close()
            self.state = OutboxWorkerState.STOPPED


def is_production_outbox_observation_authorized() -> bool:
    """Check if production outbox observation mode is authorized.

    Requires explicit RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1.
    Missing, empty, or any value other than '1' fails closed.
    This flag authorizes read-only outbox queue inspection ONLY;
    it never authorizes outbox enqueue, live trading, worker delivery, or Telegram transmission.
    """
    return os.getenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", "").strip() == "1"


def resolve_worker_db_credentials(database: str, observation: bool = False) -> Dict[str, str]:
    """Resolve database credentials with strict fail-closed requirements.

    Credential Isolation Rules (Work Order 71765):
    1. For 'signal_bot_test':
       Requires explicit SIGNAL_BOT_TEST_DB_USER and SIGNAL_BOT_TEST_DB_PASSWORD.
       Generic or default ('cms') fallbacks are strictly prohibited.
    2. For 'crypto_signals' in observation mode (observation=True):
       Requires independent explicit RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1.
       Requires explicit RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER and
       RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD.
       Generic or default ('cms') fallbacks are strictly prohibited.
       The production observation account must be provisioned separately by DB administrators
       with SELECT-only privileges for table inspection and advisory locks.
    3. For 'crypto_signals' in delivery mode (observation=False):
       Resolves standard MYSQL_USER / MYSQL_PASSWORD.
    """
    if database == "signal_bot_test":
        user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip()
        password = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()
        if not user or not password:
            raise ValueError(
                f"Missing dedicated test credentials for {database!r}: "
                "SIGNAL_BOT_TEST_DB_USER and SIGNAL_BOT_TEST_DB_PASSWORD are both required; "
                "generic or default credential fallbacks are strictly prohibited"
            )
        return {"user": user, "password": password}

    if database == "crypto_signals" and observation:
        if not is_production_outbox_observation_authorized():
            raise ValueError(
                "Production outbox observation authorization rejected: "
                "RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1 is required for database 'crypto_signals'"
            )
        user = os.getenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", "").strip()
        password = os.getenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", "").strip()
        if not user or not password:
            raise ValueError(
                f"Missing dedicated production observation credentials for {database!r}: "
                "RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER and "
                "RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD are both required; "
                "generic or default credential fallbacks are strictly prohibited"
            )
        return {"user": user, "password": password}

    return {
        "user": os.getenv("MYSQL_USER", "signal_bot"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Standalone RetailBot2 Telegram Outbox Worker")
    parser.add_argument("--database", choices=["signal_bot_test", "crypto_signals"], default="signal_bot_test")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--observation",
        action="store_true",
        help="Run in read-only observation mode (inspect queue without claiming or delivering)",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Perform read-only operational health snapshot and exit",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 1. Health-only operational inspection branch (Finding A - WO71795)
    if getattr(args, "health", False):
        if args.database == "crypto_signals" and not args.observation:
            logger.error(
                "Production health inspection is read-only and requires --observation mode "
                "with dedicated observation credentials"
            )
            sys.exit(1)

        try:
            creds = resolve_worker_db_credentials(
                args.database,
                observation=True if args.database == "crypto_signals" else args.observation,
            )
        except ValueError as exc:
            logger.critical("Database credential resolution failed: %s", exc)
            sys.exit(1)

        db_cfg = {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", 3306)),
            "user": creds["user"],
            "password": creds["password"],
            "database": args.database,
        }

        import mysql.connector
        from paper.outbox_health import inspect_outbox_health
        health_conn = mysql.connector.connect(
            host=db_cfg["host"],
            port=db_cfg["port"],
            user=db_cfg["user"],
            password=db_cfg["password"],
            database=db_cfg["database"],
            autocommit=False,
        )
        try:
            snapshot = inspect_outbox_health(
                health_conn, args.database, observation_mode=args.observation
            )
            print(json.dumps(snapshot, indent=2))
            sys.exit(0 if snapshot["health_status"] != "CRITICAL" else 2)
        finally:
            health_conn.close()

    # 2. Worker daemon execution branch
    allowed = ALLOWED_PROD_DATABASES if args.database == "crypto_signals" else ALLOWED_TEST_DATABASES
    if args.database == "crypto_signals" and not args.observation:
        if not is_telegram_delivery_authorized():
            logger.error("Production outbox requires TELEGRAM_DELIVERY_ENABLED=1 (or legacy RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1)")
            sys.exit(1)

    try:
        creds = resolve_worker_db_credentials(args.database, observation=args.observation)
    except ValueError as exc:
        logger.critical("Database credential resolution failed: %s", exc)
        sys.exit(1)

    db_cfg = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", 3306)),
        "user": creds["user"],
        "password": creds["password"],
        "database": args.database,
    }

    telegram_token = "" if args.observation else (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
    telegram_chat_id = "" if args.observation else (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_TARGET_CHAT_ID") or "").strip()

    worker = OutboxWorker(
        db_config=db_cfg,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        allowed_databases=allowed,
        poll_interval_sec=args.poll_interval,
        transport_timeout_sec=args.timeout,
        observation_mode=args.observation,
    )

    worker.setup_signals()
    exit_code = worker.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
