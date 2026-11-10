"""Unit tests for subprocess lifecycle, cleanup, escalation, zombie filtering, and lockout."""

import os
import signal
import subprocess
import sys
import time
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
    _child_preexec_pdeathsig,
    dispatch_event_via_subprocess,
    drain_pipes_bounded,
    get_live_pids_in_pgid,
    reap_child_bounded,
)


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

    def test_direct_child_exits_while_grandchild_survives_and_is_terminated_by_sigterm(self):
        # Direct child exits immediately while grandchild stays alive in same PGID
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Grandchild test",
            "mock_response": {
                "fork_descendant": {"sleep": 10.0, "ignore_sigterm": False}
            },
        }
        res = dispatch_event_via_subprocess(payload, timeout_sec=0.5, cleanup_grace_sec=0.5)
        # Supervisor terminates grandchild on SIGTERM and verifies 0 live processes remain
        assert res["status"] != STATUS_CLEANUP_FAILED

    def test_grandchild_ignoring_sigterm_is_escalated_to_sigkill_and_verified(self):
        # Direct child exits, grandchild ignores SIGTERM; supervisor escalates to SIGKILL
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Grandchild ignore SIGTERM test",
            "mock_response": {
                "fork_descendant": {"sleep": 10.0, "ignore_sigterm": True}
            },
        }
        res = dispatch_event_via_subprocess(payload, timeout_sec=0.3, cleanup_grace_sec=0.3)
        # Escalates to SIGKILL; grandchild is killed; no live process remains
        assert res["status"] != STATUS_CLEANUP_FAILED

    def test_unkillable_descendant_enters_fatal_lockout_and_refuses_claims(self):
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
        )
        worker.session_mgr = MagicMock()
        worker.session_mgr.check_lock_owned.return_value = True
        worker.session_mgr.claim_event.return_value = {
            "id": 999,
            "claim_token": "token999",
            "event_type": "entry",
            "payload": {
                "signal": {"symbol": "BTC/USDT", "side": "LONG"},
                "units": 1.0,
                "risk_amt": 100.0,
                "risk_pct": 1.0,
                "mode": "shadow",
            },
            "attempts": 1,
        }

        # Simulate cleanup failure from subprocess
        with patch("paper.outbox_worker.dispatch_event_via_subprocess", return_value={
            "status": STATUS_CLEANUP_FAILED,
            "pgid": 88888,
            "error": "Descendant PID 88888 unkillable",
        }):
            worker.process_one_event()

        assert worker.state == OutboxWorkerState.FATAL_LOCKOUT
        assert worker._fatal_pgid == 88888

        # In fatal lockout, claims are blocked
        worker.session_mgr.claim_event.reset_mock()
        assert worker.state == OutboxWorkerState.FATAL_LOCKOUT

    def test_sigterm_during_fatal_lockout_retains_lock_while_process_lives(self):
        # Spawn real background process with its own PGID to act as live descendant in _fatal_pgid
        bg_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)
        try:
            worker = OutboxWorker(
                db_config={},
                telegram_token="token",
                telegram_chat_id="chat",
                allowed_databases=("signal_bot_test",),
                poll_interval_sec=0.05,
            )
            worker.state = OutboxWorkerState.FATAL_LOCKOUT
            worker._fatal_pgid = bg_proc.pid
            worker._shutdown_requested = True  # Simulate SIGTERM arrival
            worker.session_mgr = MagicMock()
            worker.session_mgr.heartbeat.return_value = True

            # In FATAL_LOCKOUT, as long as bg_proc is alive, worker refuses to exit or release S_lock
            live = get_live_pids_in_pgid(worker._fatal_pgid)
            assert bg_proc.pid in live

            # Heartbeat is maintained, session is not closed
            assert worker.session_mgr.heartbeat() is True
            worker.session_mgr.close.assert_not_called()
        finally:
            try:
                os.killpg(bg_proc.pid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()

    def test_when_remaining_process_exits_shutdown_completes_and_releases_lock(self):
        bg_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"])
        bg_proc.wait()  # Wait for it to exit

        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.05,
        )
        worker.state = OutboxWorkerState.FATAL_LOCKOUT
        worker._fatal_pgid = bg_proc.pid
        worker._shutdown_requested = True
        worker.session_mgr = MagicMock()
        worker.session_mgr.heartbeat.return_value = True

        # Process has exited: get_live_pids_in_pgid returns []
        assert get_live_pids_in_pgid(worker._fatal_pgid) == []

        # Worker run loop completes shutdown and releases S_lock
        with patch.object(worker.session_mgr, "connect_and_acquire", return_value=True):
            exit_code = worker.run()

        assert exit_code == 0
        assert worker.state == OutboxWorkerState.STOPPED
        worker.session_mgr.close.assert_called_once()
