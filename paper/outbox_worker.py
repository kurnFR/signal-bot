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
  SIGKILL and the cleanup deadline. The 60-second lease is a normal-path recovery margin,
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
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector

from paper.retailbot2 import (
    OUTBOX_LOCK_NAME,
    DatabaseManager,
    format_entry_message,
    format_exit_message,
)
from paper.outbox_transport import (
    STATUS_DELIVERED,
    STATUS_DELIVERY_UNKNOWN,
    STATUS_FAILED_CONFIRMED,
)


logger = logging.getLogger("outbox_worker")

STATUS_CLEANUP_FAILED = "CLEANUP_FAILED"
ALLOWED_TEST_DATABASES = ("signal_bot_test",)
ALLOWED_PROD_DATABASES = ("crypto_signals",)


class OutboxWorkerState(Enum):
    INITIALIZING = "INITIALIZING"
    ACQUIRING_LOCK = "ACQUIRING_LOCK"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FATAL_LOCKOUT = "FATAL_LOCKOUT"


class DatabaseAuthorizationError(RuntimeError):
    """Raised when the connected database is not authorized for the requested mode."""
    pass


class LockLostError(RuntimeError):
    """Raised when ownership of the MariaDB advisory lock is lost."""
    pass


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


def get_live_pids_in_pgid(pgid: int, proc_root: str = "/proc") -> List[int]:
    """Scan /proc to find all PIDs belonging to process group `pgid` that are NOT zombies ('Z')."""
    live_pids = []
    if not os.path.exists(proc_root):
        # Fallback if procfs not mounted: check pgid with killpg
        try:
            os.killpg(pgid, 0)
            return [pgid]
        except (ProcessLookupError, PermissionError):
            return []

    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            stat_path = os.path.join(proc_root, str(pid), "stat")
            with open(stat_path, "r") as f:
                stat_content = f.read()
            rparen_idx = stat_content.rfind(")")
            if rparen_idx == -1:
                continue
            fields = stat_content[rparen_idx + 2:].split()
            state = fields[0]      # Field 3: process state
            pgrp = int(fields[2])  # Field 5: process group ID
            if pgrp == pgid and state != "Z":
                live_pids.append(pid)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return live_pids


def _child_preexec_pdeathsig(expected_parent_pid: int):
    """Set parent death signal to SIGKILL and verify parent did not exit beforehand."""
    try:
        PR_SET_PDEATHSIG = 1
        SIGKILL = 9
        libc = ctypes.CDLL("libc.so.6")
        libc.prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0)
        # Race-condition check: if parent died before prctl, exit immediately
        if os.getppid() != expected_parent_pid:
            os._exit(1)
    except Exception:
        os._exit(1)


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
        )
    except Exception as exc:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": f"Failed to spawn transport child: {exc}",
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

    # Process-group verification: ensure NO live descendants remain in PGID
    live_descendants = get_live_pids_in_pgid(pgid)
    if live_descendants:
        logger.critical(
            "Transport cleanup failed: live descendants %r still active in PGID %d after SIGKILL escalation",
            live_descendants, pgid
        )
        return {
            "status": STATUS_CLEANUP_FAILED,
            "error": f"Live descendants remaining after SIGKILL: {live_descendants}",
            "live_pids": live_descendants,
            "pgid": pgid,
        }

    if timed_out:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": f"Transport exceeded deadline {timeout_sec:.1f}s",
            "stderr": stderr_bytes.decode("utf-8", errors="replace")[:500],
        }

    # Child completed before deadline; parse JSON result
    try:
        stdout_text = stdout_bytes.decode("utf-8").strip()
        if not stdout_text:
            return {
                "status": STATUS_DELIVERY_UNKNOWN,
                "error": "Empty stdout from transport child",
                "stderr": stderr_bytes.decode("utf-8", errors="replace")[:500],
            }
        return json.loads(stdout_text)
    except Exception as exc:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": f"Failed to parse transport child JSON: {exc}",
            "raw_stdout": stdout_bytes.decode("utf-8", errors="replace")[:500],
        }


class OutboxSessionManager:
    """Manages the persistent S_lock connection, advisory lock, and outbox operations."""

    def __init__(self, db_config: Dict[str, Any], allowed_databases: Tuple[str, ...]):
        self.db_config = db_config
        self.allowed_databases = allowed_databases
        self.s_lock = None
        self.db_manager = object.__new__(DatabaseManager)
        self.db_manager.logger = logger
        self.connected_database = ""

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
        except Exception as exc:
            logger.error("Failed to connect or acquire advisory lock: %s", exc)
            self.close()
            return False

    def check_lock_owned(self) -> bool:
        """Check if S_lock still owns the advisory lock."""
        if not self.s_lock or not self.s_lock.is_connected():
            return False
        return self.db_manager.is_outbox_lock_owned(self.s_lock)

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
        telegram_token: str,
        telegram_chat_id: str,
        allowed_databases: Tuple[str, ...] = ALLOWED_TEST_DATABASES,
        poll_interval_sec: float = 1.0,
        transport_timeout_sec: float = 10.0,
        cleanup_grace_sec: float = 1.5,
        lease_seconds: int = 90,
        max_attempts: int = 8,
        mock_transport_response: Optional[Dict[str, Any]] = None,
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
        self.state = OutboxWorkerState.INITIALIZING
        self._shutdown_requested = False
        self._fatal_pgid: Optional[int] = None

    def setup_signals(self):
        """Register graceful shutdown handlers for SIGINT and SIGTERM."""
        def _handler(signum, frame):
            logger.info("Signal %d received; requesting graceful outbox worker shutdown", signum)
            self._shutdown_requested = True

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def format_event_text(self, event: Dict[str, Any]) -> str:
        """Format entry or exit payload into Telegram HTML text."""
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        trade_id = int(event.get("trade_id") or 0)

        if event_type == "entry":
            return format_entry_message(payload, trade_id)
        if event_type == "exit":
            return format_exit_message(payload)
        raise ValueError(f"Unsupported outbox event_type: {event_type!r}")

    def process_one_event(self) -> bool:
        """Claim and process at most one due event. Returns True if an event was processed."""
        if not self.session_mgr.check_lock_owned():
            logger.error("Advisory lock ownership lost during event loop; stopping worker")
            self.state = OutboxWorkerState.STOPPED
            raise LockLostError("Advisory lock lost")

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
        )

        status = outcome.get("status")

        # 1. Cleanup failure: Enter fatal lockout, retain lock, do not release or claim
        if status == STATUS_CLEANUP_FAILED:
            self.state = OutboxWorkerState.FATAL_LOCKOUT
            self._fatal_pgid = int(outcome.get("pgid") or 0) or None
            logger.critical(
                "FATAL: Transport cleanup failed for event %d (PGID=%s); entering STATE_FATAL_LOCKOUT. "
                "Retaining advisory lock on S_lock. Shutdown requests will not release ownership while "
                "live transport processes remain. Operator intervention required.",
                event_id, self._fatal_pgid
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
        err_desc = outcome.get("error") or "Delivery state unknown"
        logger.warning(
            "Ambiguous delivery status for outbox event %d (attempts=%d): %s; scheduling backoff retry",
            event_id, attempts, err_desc
        )
        self.session_mgr.mark_retry(event_id, claim_token, attempts, err_desc)
        return True

    def run(self):
        """Main worker execution loop."""
        self.state = OutboxWorkerState.ACQUIRING_LOCK
        acquired = self.session_mgr.connect_and_acquire()
        if not acquired:
            self.state = OutboxWorkerState.STOPPED
            logger.error("Could not acquire advisory lock; exiting worker")
            return 1

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
                        # The DB session has already been lost; advisory-lock retention is no longer possible.
                        logger.critical("S_lock connection lost during FATAL_LOCKOUT; transport exclusion is no longer guaranteed")
                        return 75
                    if self._fatal_pgid and not get_live_pids_in_pgid(self._fatal_pgid):
                        logger.warning("No live transport processes remain in PGID %s; fatal lockout can be cleared", self._fatal_pgid)
                        self._fatal_pgid = None
                        self.state = OutboxWorkerState.STOPPING if self._shutdown_requested else OutboxWorkerState.RUNNING
                    else:
                        # Even SIGTERM/SIGINT must not release the lock while transport survival is unresolved.
                        time.sleep(5.0)
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
            self.state = OutboxWorkerState.STOPPING
            self.session_mgr.close()
            self.state = OutboxWorkerState.STOPPED


def main():
    parser = argparse.ArgumentParser(description="Standalone RetailBot2 Telegram Outbox Worker")
    parser.add_argument("--database", choices=["signal_bot_test", "crypto_signals"], default="signal_bot_test")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--once", action="store_true", help="Process at most one event and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    allowed = ALLOWED_PROD_DATABASES if args.database == "crypto_signals" else ALLOWED_TEST_DATABASES
    if args.database == "crypto_signals":
        if os.getenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY") != "1":
            logger.error("Production outbox requires RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1")
            sys.exit(1)

    if args.database == "signal_bot_test":
        db_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", os.getenv("MYSQL_USER", "cms"))
        db_password = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
    else:
        db_user = os.getenv("MYSQL_USER", "cms")
        db_password = os.getenv("MYSQL_PASSWORD", "")
    db_cfg = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", 3306)),
        "user": db_user,
        "password": db_password,
        "database": args.database,
    }

    worker = OutboxWorker(
        db_config=db_cfg,
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        allowed_databases=allowed,
        poll_interval_sec=args.poll_interval,
        transport_timeout_sec=args.timeout,
    )

    if args.once:
        worker.session_mgr.connect_and_acquire()
        try:
            worker.process_one_event()
            sys.exit(0)
        finally:
            worker.session_mgr.close()

    worker.setup_signals()
    exit_code = worker.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
