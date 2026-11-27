"""Unit tests for subprocess lifecycle, cleanup, escalation, zombie filtering, and lockout."""

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from paper.outbox_transport import (
    STATUS_DELIVERED,
    STATUS_DELIVERY_UNKNOWN,
)
from paper.outbox_worker import (
    STATUS_CLEANUP_FAILED,
    OutboxWorker,
    OutboxWorkerState,
    ProcessInspectionError,
    ProcessInspectionResult,
    ProcessInspectionStatus,
    _child_preexec_pdeathsig,
    dispatch_event_via_subprocess,
    drain_pipes_bounded,
    get_live_pids_in_pgid,
    inspect_process_group,
    reap_child_bounded,
    main as outbox_worker_main,
)


def test_worker_cli_rejects_unsafe_once_mode():
    with patch("sys.argv", ["outbox_worker", "--once"]):
        with pytest.raises(SystemExit) as exc_info:
            outbox_worker_main()
    assert exc_info.value.code == 2


class ControlledSessionManager:
    """Thread-safe controlled session manager simulating held MariaDB advisory lock."""

    def __init__(self, conn_id: int = 101):
        self.conn_id = conn_id
        self.lock_held = True
        self.heartbeat_count = 0
        self.heartbeat_fail = False
        self.heartbeat_raise = False
        self.close_count = 0
        self.claim_count = 0
        self._lock = threading.Lock()

    def connect_and_acquire(self, timeout_sec: float = 0.0) -> bool:
        with self._lock:
            self.lock_held = True
            return True

    def check_lock_owned(self) -> bool:
        with self._lock:
            return self.lock_held

    def connection_alive(self) -> bool:
        with self._lock:
            return self.lock_held

    def heartbeat(self) -> bool:
        with self._lock:
            if not self.lock_held:
                return False
            self.heartbeat_count += 1
            if self.heartbeat_raise:
                raise RuntimeError("simulated cursor close failure")
            return not self.heartbeat_fail

    def claim_event(self, lease_seconds: int = 90):
        with self._lock:
            self.claim_count += 1
            return None

    def mark_sent(self, event_id: int, claim_token: str) -> bool:
        return True

    def mark_retry(self, event_id: int, claim_token: str, attempts: int, error_desc: str, delay: Optional[int] = None) -> bool:
        return True

    def mark_dead(self, event_id: int, claim_token: str, error_desc: str) -> bool:
        return True

    def close(self):
        with self._lock:
            self.lock_held = False
            self.close_count += 1


class TestProcessLifecycleAndCleanup:
    def test_transport_success(self):
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Success test",
            "mock_response": {
                "status_code": 200,
                "body": {"ok": True, "result": {"message_id": 777}},
            },
        }
        res = dispatch_event_via_subprocess(payload, timeout_sec=5.0)
        assert res["status"] == STATUS_DELIVERED
        assert res["result"]["message_id"] == 777

    def test_transport_timeout_and_sigterm_escalation(self):
        # Child simulates hanging past deadline via mock_response sleep
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Sleep test",
            "mock_response": {"sleep": 10.0},
        }
        start = time.monotonic()
        res = dispatch_event_via_subprocess(payload, timeout_sec=0.3, cleanup_grace_sec=0.2)
        elapsed = time.monotonic() - start

        assert res["status"] == STATUS_DELIVERY_UNKNOWN
        assert "exceeded deadline" in res["error"]
        assert elapsed < 2.0  # Finished within bounded escalation period

    def test_direct_child_reaping_bounded(self):
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(42)"])
        exit_code = reap_child_bounded(proc.pid, timeout_sec=2.0)
        assert exit_code == 42

    def test_zombie_filtering(self, tmp_path):
        # Test get_live_pids_in_pgid properly ignores 'Z' (zombie) state
        test_pgid = 55555
        proc_dir = tmp_path / "proc"
        proc_dir.mkdir()

        # Zombie process (state 'Z')
        pid_zombie = proc_dir / "101"
        pid_zombie.mkdir()
        (pid_zombie / "stat").write_text(f"101 (python3) Z 1 {test_pgid} 100 0 0 0\n")

        # Live sleeping process (state 'S')
        pid_live = proc_dir / "102"
        pid_live.mkdir()
        (pid_live / "stat").write_text(f"102 (python3) S 1 {test_pgid} 100 0 0 0\n")

        # Live running process in other process group
        pid_other = proc_dir / "103"
        pid_other.mkdir()
        (pid_other / "stat").write_text("103 (python3) S 1 99999 100 0 0 0\n")

        live = get_live_pids_in_pgid(test_pgid, proc_root=str(proc_dir))
        assert 102 in live
        assert 101 not in live  # Zombie filtered out
        assert 103 not in live  # Other PGID ignored

    def test_inherited_stdout_stderr_pipe_does_not_hang_parent(self):
        # Grandchild holds stdout open, parent drain must not block past deadline
        script = """
import os, subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"], stdout=sys.stdout)
sys.exit(0)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 0.2
            start = time.monotonic()
            out, err = drain_pipes_bounded(proc, deadline)
            elapsed = time.monotonic() - start
            assert elapsed < 1.0
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass

    def test_pdeathsig_setup_and_race_handling(self):
        current_ppid = os.getppid()
        _child_preexec_pdeathsig(current_ppid)

        # Wrong expected parent PID calls os._exit(1); verify via subprocess
        script = """
import os
from paper.outbox_worker import _child_preexec_pdeathsig
_child_preexec_pdeathsig(os.getppid() + 9999)
"""
        res = subprocess.run([sys.executable, "-c", script])
        assert res.returncode == 1

    def test_pdeathsig_prctl_failure_exits_child(self):
        from paper import outbox_worker

        fake_libc = MagicMock()
        fake_libc.prctl.return_value = -1
        with patch.object(outbox_worker.ctypes, "CDLL", return_value=fake_libc):
            with patch.object(outbox_worker.os, "_exit", side_effect=SystemExit) as exit_mock:
                with pytest.raises(SystemExit):
                    _child_preexec_pdeathsig(os.getpid())
        fake_libc.prctl.assert_called_once()
        exit_mock.assert_called_once_with(1)

    def test_transport_child_environment_excludes_application_secrets(self):
        from paper.outbox_worker import _transport_child_environment

        with patch.dict(os.environ, {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/srv/signal-bot",
            "MYSQL_PASSWORD": "db-secret",
            "SIGNAL_BOT_TEST_DB_PASSWORD": "test-secret",
            "BINANCE_API_SECRET": "exchange-secret",
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "TELEGRAM_CHAT_ID": "chat-secret",
            "HTTP_PROXY": "http://user:password@proxy",
        }, clear=True):
            child_env = _transport_child_environment()

        assert child_env == {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/srv/signal-bot",
        }

    # The two escalation tests expect PID 1 to reap adopted grandchildren.
    # A sandbox PID 1 without a reaper can leave a zombie PID visible to
    # kill(pid, 0) even after the process group has no running descendants.
    def test_direct_child_exits_while_grandchild_survives_and_is_terminated_by_sigterm(self, tmp_path):
        # Direct child exits immediately while grandchild stays alive in same PGID
        pid_file = tmp_path / "grandchild_sigterm.pid"
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Grandchild test",
            "mock_response": {
                "fork_descendant": {
                    "sleep": 10.0,
                    "ignore_sigterm": False,
                    "pid_file": str(pid_file),
                }
            },
        }
        res = dispatch_event_via_subprocess(payload, timeout_sec=0.5, cleanup_grace_sec=0.5)
        # Supervisor terminates grandchild on SIGTERM and verifies 0 live processes remain
        assert res["status"] != STATUS_CLEANUP_FAILED
        assert pid_file.exists(), "Grandchild PID file was not created"
        grandchild_pid = int(pid_file.read_text().strip())
        assert grandchild_pid > 0
        assert grandchild_pid in res.get("captured_descendants", []), f"Grandchild PID {grandchild_pid} was not captured in {res}"
        # Verify grandchild process is no longer running
        try:
            os.kill(grandchild_pid, 0)
            assert False, f"Grandchild PID {grandchild_pid} is still alive after SIGTERM cleanup"
        except ProcessLookupError:
            pass

    def test_grandchild_ignoring_sigterm_is_escalated_to_sigkill_and_verified(self, tmp_path):
        # Direct child exits, grandchild ignores SIGTERM; supervisor escalates to SIGKILL
        pid_file = tmp_path / "grandchild_sigkill.pid"
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Grandchild ignore SIGTERM test",
            "mock_response": {
                "fork_descendant": {
                    "sleep": 10.0,
                    "ignore_sigterm": True,
                    "pid_file": str(pid_file),
                }
            },
        }
        res = dispatch_event_via_subprocess(payload, timeout_sec=0.3, cleanup_grace_sec=0.3)
        # Escalates to SIGKILL; grandchild is killed; no live process remains
        assert res["status"] != STATUS_CLEANUP_FAILED
        assert pid_file.exists(), "Grandchild PID file was not created"
        grandchild_pid = int(pid_file.read_text().strip())
        assert grandchild_pid > 0
        assert grandchild_pid in res.get("captured_descendants", []), f"Grandchild PID {grandchild_pid} was not captured in {res}"
        # Verify grandchild process is no longer running after SIGKILL escalation
        try:
            os.kill(grandchild_pid, 0)
            assert False, f"Grandchild PID {grandchild_pid} is still alive after SIGKILL cleanup"
        except ProcessLookupError:
            pass

    def test_unexpected_fatal_lockout_exception_does_not_release_lock_before_descendant_exit(self):
        bg_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pgid = bg_proc.pid
        session_mgr = ControlledSessionManager()
        session_mgr.heartbeat_raise = True
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.02,
        )
        worker.session_mgr = session_mgr
        worker.state = OutboxWorkerState.FATAL_LOCKOUT
        worker._fatal_pgid = pgid
        worker_errors = []

        def run_worker_and_capture_exception():
            try:
                worker.run()
            except RuntimeError as exc:
                worker_errors.append(exc)

        worker_thread = threading.Thread(target=run_worker_and_capture_exception)
        try:
            worker_thread.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and session_mgr.heartbeat_count < 2:
                time.sleep(0.02)

            assert session_mgr.heartbeat_count >= 2
            assert worker_thread.is_alive()
            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
            assert session_mgr.lock_held is True
            assert session_mgr.close_count == 0

            os.killpg(pgid, signal.SIGKILL)
            bg_proc.wait(timeout=3.0)
            worker_thread.join(timeout=3.0)

            assert not worker_thread.is_alive()
            assert worker.state == OutboxWorkerState.STOPPED
            assert session_mgr.close_count == 1
            assert len(worker_errors) == 1
            assert str(worker_errors[0]) == "simulated cursor close failure"
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()
            if worker_thread.is_alive():
                worker._fatal_pgid = None
                worker_thread.join(timeout=2.0)

    def test_failed_heartbeat_with_open_connection_retains_fatal_lockout(self):
        bg_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pgid = bg_proc.pid
        session_mgr = ControlledSessionManager()
        session_mgr.heartbeat_fail = True
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.02,
        )
        worker.session_mgr = session_mgr
        worker.state = OutboxWorkerState.FATAL_LOCKOUT
        worker._fatal_pgid = pgid
        worker_thread = threading.Thread(target=worker.run)
        try:
            worker_thread.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and session_mgr.heartbeat_count < 2:
                time.sleep(0.02)
            assert session_mgr.heartbeat_count >= 2
            assert worker_thread.is_alive()
            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
            assert session_mgr.lock_held is True
            assert session_mgr.close_count == 0

            os.killpg(pgid, signal.SIGKILL)
            bg_proc.wait(timeout=3.0)
            session_mgr.heartbeat_fail = False
            worker._handle_signal(signal.SIGTERM, None)
            worker_thread.join(timeout=3.0)

            assert not worker_thread.is_alive()
            assert worker.state == OutboxWorkerState.STOPPED
            assert session_mgr.close_count == 1
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()
            if worker_thread.is_alive():
                session_mgr.heartbeat_fail = False
                worker._shutdown_requested = True
                worker._fatal_pgid = None
                worker_thread.join(timeout=2.0)

    def test_sigterm_during_fatal_lockout_retains_lock_until_descendant_terminated(self):
        # A. Start a real descendant process in its own session/process group
        bg_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pgid = bg_proc.pid
        assert os.getpgid(bg_proc.pid) == pgid

        session_mgr = ControlledSessionManager()
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.02,
        )
        worker.session_mgr = session_mgr
        worker.state = OutboxWorkerState.FATAL_LOCKOUT
        worker._fatal_pgid = pgid

        worker_thread = threading.Thread(target=worker.run)
        try:
            worker_thread.start()

            # Wait until the worker execution loop runs and executes at least one heartbeat
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and session_mgr.heartbeat_count < 1:
                time.sleep(0.02)
            assert session_mgr.heartbeat_count >= 1

            # Send SIGTERM via worker signal handler
            worker._handle_signal(signal.SIGTERM, None)
            assert worker._shutdown_requested is True

            # Verify that while descendant remains live, the worker continues running in FATAL_LOCKOUT
            time.sleep(0.15)
            assert worker_thread.is_alive() is True
            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
            assert session_mgr.lock_held is True
            assert session_mgr.close_count == 0
            assert session_mgr.heartbeat_count >= 2

            # Now terminate the descendant process
            os.killpg(pgid, signal.SIGKILL)
            bg_proc.wait(timeout=3.0)

            # Once the descendant is terminated, worker loop detects confirmed empty group,
            # clears fatal lockout, performs clean graceful shutdown, and releases S_lock
            worker_thread.join(timeout=3.0)
            assert worker_thread.is_alive() is False
            assert worker.state == OutboxWorkerState.STOPPED
            assert session_mgr.lock_held is False
            assert session_mgr.close_count == 1
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()
            if worker_thread.is_alive():
                worker._shutdown_requested = True
                worker._fatal_pgid = None
                worker_thread.join(timeout=2.0)

    def test_clean_shutdown_after_descendant_termination(self):
        # B. Create the process group correctly using start_new_session=True
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        pgid = proc.pid
        # Ensure the PID being checked is actually the process-group ID
        assert os.getpgid(proc.pid) == pgid

        try:
            # Confirm the inspection detects a live process before termination
            res_live = inspect_process_group(pgid)
            assert res_live.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT
            assert proc.pid in res_live.live_pids
            assert proc.pid in get_live_pids_in_pgid(pgid)

            # Terminate the process group
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=3.0)

            # Confirm the inspection detects no live processes after termination
            res_dead = inspect_process_group(pgid)
            assert res_dead.status == ProcessInspectionStatus.CONFIRMED_EMPTY
            assert res_dead.live_pids == []
            assert get_live_pids_in_pgid(pgid) == []

            # Verify worker releases the lock only after confirmed cleanup
            session_mgr = ControlledSessionManager()
            worker = OutboxWorker(
                db_config={},
                telegram_token="token",
                telegram_chat_id="chat",
                allowed_databases=("signal_bot_test",),
                poll_interval_sec=0.02,
            )
            worker.session_mgr = session_mgr
            worker.state = OutboxWorkerState.FATAL_LOCKOUT
            worker._fatal_pgid = pgid
            worker._shutdown_requested = True

            exit_code = worker.run()
            assert exit_code == 0
            assert worker.state == OutboxWorkerState.STOPPED
            assert session_mgr.lock_held is False
            assert session_mgr.close_count == 1
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()

    def test_fatal_lockout_entered_via_execution_path_prevents_claims_and_shutdown(self):
        # C. Spawn real background process with its own PGID
        bg_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pgid = bg_proc.pid

        session_mgr = ControlledSessionManager()
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.02,
        )
        worker.session_mgr = session_mgr

        # Mock event claim for the first event
        claim_mock = MagicMock(return_value={
            "id": 1001,
            "claim_token": "token-1001",
            "event_type": "entry",
            "payload": {
                "signal": {"symbol": "BTC/USDT", "side": "LONG"},
                "units": 1.0,
                "risk_amt": 50.0,
                "risk_pct": 1.0,
                "mode": "shadow",
            },
            "attempts": 1,
        })
        session_mgr.claim_event = claim_mock

        worker_thread = None
        try:
            # Enter FATAL_LOCKOUT through the ACTUAL worker execution path
            with patch("paper.outbox_worker.dispatch_event_via_subprocess", return_value={
                "status": STATUS_CLEANUP_FAILED,
                "pgid": pgid,
                "error": "Descendant process surviving after SIGKILL",
                "live_pids": [pgid],
            }):
                processed = worker.process_one_event()
                assert processed is True

            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
            assert worker._fatal_pgid == pgid

            # Attempt to continue polling directly: process_one_event must refuse claims
            claim_mock.reset_mock()
            assert worker.process_one_event() is False
            claim_mock.assert_not_called()

            # Start worker execution loop in background thread to verify continuous polling
            worker_thread = threading.Thread(target=worker.run)
            worker_thread.start()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and session_mgr.heartbeat_count < 1:
                time.sleep(0.02)
            assert session_mgr.heartbeat_count >= 1

            # While process group remains unresolved, verify NO claims occur
            claim_mock.assert_not_called()

            # Request SIGTERM
            worker._handle_signal(signal.SIGTERM, None)
            assert worker._shutdown_requested is True

            # Verify the worker does NOT exit merely because SIGTERM was requested
            time.sleep(0.1)
            assert worker_thread.is_alive() is True
            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
            assert session_mgr.lock_held is True
            assert session_mgr.close_count == 0
            claim_mock.assert_not_called()

            # Now terminate descendant and verify clean exit
            os.killpg(pgid, signal.SIGKILL)
            bg_proc.wait(timeout=3.0)

            worker_thread.join(timeout=3.0)
            assert worker_thread.is_alive() is False
            assert worker.state == OutboxWorkerState.STOPPED
            assert session_mgr.close_count == 1
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()
            if worker_thread and worker_thread.is_alive():
                worker._shutdown_requested = True
                worker._fatal_pgid = None
                worker_thread.join(timeout=2.0)

    def test_process_inspection_failure_fails_closed_and_retains_lock(self, tmp_path):
        # D. 1. Missing proc directory
        missing_root = tmp_path / "nonexistent_proc"
        res_missing = inspect_process_group(12345, proc_root=str(missing_root))
        assert res_missing.status == ProcessInspectionStatus.STATE_UNVERIFIED
        assert "does not exist" in res_missing.error
        with pytest.raises(ProcessInspectionError) as exc_info:
            get_live_pids_in_pgid(12345, proc_root=str(missing_root))
        assert "does not exist" in str(exc_info.value)

        # 2. Permission failure on proc directory or stat file
        perm_root = tmp_path / "proc_perm"
        perm_root.mkdir()
        pid_dir = perm_root / "999"
        pid_dir.mkdir()
        stat_file = pid_dir / "stat"
        stat_file.write_text("999 (python3) S 1 12345 100 0 0 0\n")
        # Inject the failed read so the test behaves identically under both
        # ordinary CI users and root inside an isolated network namespace.
        with patch("paper.outbox_worker.open", side_effect=PermissionError("Permission denied")):
            res_perm = inspect_process_group(12345, proc_root=str(perm_root))
            assert res_perm.status == ProcessInspectionStatus.STATE_UNVERIFIED
            assert "Permission denied" in res_perm.error
            with pytest.raises(ProcessInspectionError):
                get_live_pids_in_pgid(12345, proc_root=str(perm_root))

        # 3. Malformed stat file (missing closing parenthesis)
        malformed_root = tmp_path / "proc_malformed"
        malformed_root.mkdir()
        pid_dir2 = malformed_root / "888"
        pid_dir2.mkdir()
        (pid_dir2 / "stat").write_text("888 python3_without_parens S 1 12345 100\n")

        res_malformed = inspect_process_group(12345, proc_root=str(malformed_root))
        assert res_malformed.status == ProcessInspectionStatus.STATE_UNVERIFIED
        assert "missing closing parenthesis" in res_malformed.error
        with pytest.raises(ProcessInspectionError):
            get_live_pids_in_pgid(12345, proc_root=str(malformed_root))

        # 4. Malformed stat file (insufficient fields)
        malformed_root3 = tmp_path / "proc_fields"
        malformed_root3.mkdir()
        pid_dir3 = malformed_root3 / "777"
        pid_dir3.mkdir()
        (pid_dir3 / "stat").write_text("777 (python3) S\n")

        res_fields = inspect_process_group(12345, proc_root=str(malformed_root3))
        assert res_fields.status == ProcessInspectionStatus.STATE_UNVERIFIED
        assert "insufficient fields" in res_fields.error

        # 5. Worker retains lock ownership and remains in fail-closed state under inspection failure
        session_mgr = ControlledSessionManager()
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.02,
            proc_root=str(malformed_root),
        )
        worker.session_mgr = session_mgr
        worker.state = OutboxWorkerState.FATAL_LOCKOUT
        worker._fatal_pgid = 12345
        worker._shutdown_requested = True

        worker_thread = threading.Thread(target=worker.run)
        try:
            worker_thread.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and session_mgr.heartbeat_count < 1:
                time.sleep(0.02)
            assert session_mgr.heartbeat_count >= 1

            # Inspection failed: worker must NOT interpret as empty group
            # Must remain in FATAL_LOCKOUT, retain lock, and refuse to exit
            time.sleep(0.1)
            assert worker_thread.is_alive() is True
            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
            assert worker._fatal_pgid == 12345
            assert session_mgr.lock_held is True
            assert session_mgr.close_count == 0

            # Point to empty proc root to simulate resolved cleanup
            empty_root = tmp_path / "proc_empty"
            empty_root.mkdir()
            worker.proc_root = str(empty_root)

            worker_thread.join(timeout=3.0)
            assert worker_thread.is_alive() is False
            assert worker.state == OutboxWorkerState.STOPPED
            assert session_mgr.lock_held is False
            assert session_mgr.close_count == 1
        finally:
            if worker_thread and worker_thread.is_alive():
                worker._shutdown_requested = True
                worker._fatal_pgid = None
                worker_thread.join(timeout=2.0)

    def test_process_inspection_handles_invalid_utf8_in_comm_field(self, tmp_path):
        test_pgid = 44444
        proc_dir = tmp_path / "proc_utf8"
        proc_dir.mkdir()

        # 1. Process in target PGID with non-UTF-8 bytes in comm field
        pid_dir = proc_dir / "401"
        pid_dir.mkdir()
        # Invalid UTF-8 sequence \xff\xfe\x80 in (comm)
        stat_bytes = b"401 (\xff\xfe\x80proc_name\xaa) S 1 44444 100 0 0 0\n"
        (pid_dir / "stat").write_bytes(stat_bytes)

        # Inspect should safely parse bytes without raising UnicodeDecodeError
        res = inspect_process_group(test_pgid, proc_root=str(proc_dir))
        assert res.status == ProcessInspectionStatus.LIVE_PROCESSES_PRESENT
        assert 401 in res.live_pids
        assert 401 in get_live_pids_in_pgid(test_pgid, proc_root=str(proc_dir))

        # 2. Zombie process with non-UTF-8 bytes in comm field (should be filtered out)
        pid_zombie = proc_dir / "402"
        pid_zombie.mkdir()
        stat_zombie = b"402 (\xff\xfe_zombie) Z 1 44444 100 0 0 0\n"
        (pid_zombie / "stat").write_bytes(stat_zombie)

        res2 = inspect_process_group(test_pgid, proc_root=str(proc_dir))
        assert 401 in res2.live_pids
        assert 402 not in res2.live_pids  # Zombie filtered out

        # 3. If trailing numeric fields are corrupted with invalid bytes, must result in STATE_UNVERIFIED,
        # NEVER CONFIRMED_EMPTY
        pid_corrupt = proc_dir / "403"
        pid_corrupt.mkdir()
        # Corrupted trailing fields after closing parenthesis
        stat_corrupt = b"403 (comm) \xff\xfe 1 44444 100\n"
        (pid_corrupt / "stat").write_bytes(stat_corrupt)

        res_corrupt = inspect_process_group(test_pgid, proc_root=str(proc_dir))
        assert res_corrupt.status == ProcessInspectionStatus.STATE_UNVERIFIED
        assert res_corrupt.status != ProcessInspectionStatus.CONFIRMED_EMPTY
        with pytest.raises(ProcessInspectionError):
            get_live_pids_in_pgid(test_pgid, proc_root=str(proc_dir))
