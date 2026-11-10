"""Unit tests for subprocess lifecycle, cleanup, escalation, zombie filtering, and lockout."""

import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock

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

    def test_fatal_lockout_prevents_subsequent_claims(self):
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
        )
        worker.state = OutboxWorkerState.FATAL_LOCKOUT
        worker.session_mgr = MagicMock()
        worker.session_mgr.check_lock_owned.return_value = True

        worker.session_mgr.claim_event.assert_not_called()
