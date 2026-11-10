"""Tests for opt-in RetailBot2 outbox supervision and safe shutdown."""

import subprocess

import run_all


def test_outbox_worker_is_disabled_by_default():
    processes = run_all.build_processes({})
    assert "retailbot2_outbox" not in processes


def test_outbox_worker_requires_both_explicit_enable_flags():
    assert "retailbot2_outbox" not in run_all.build_processes({
        "RETAILBOT2_OUTBOX_WORKER_ENABLED": "1",
    })
    assert "retailbot2_outbox" not in run_all.build_processes({
        "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
    })


def test_outbox_worker_command_uses_production_database_only_after_dual_opt_in():
    processes = run_all.build_processes({
        "RETAILBOT2_OUTBOX_WORKER_ENABLED": "1",
        "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
    })
    assert processes["retailbot2_outbox"] == [
        run_all.sys.executable,
        "-m",
        "paper.outbox_worker",
        "--database",
        "crypto_signals",
    ]


def test_supervisor_never_sigkills_outbox_worker_after_shutdown_timeout(monkeypatch):
    class FakeOutboxProcess:
        pid = 98765

        def __init__(self):
            self.terminated = False
            self.killed = False
            self.wait_calls = []

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if timeout is not None:
                raise subprocess.TimeoutExpired("outbox-worker", timeout)
            return 0

        def kill(self):
            self.killed = True

    proc = FakeOutboxProcess()
    monkeypatch.setattr(run_all, "_children", {"retailbot2_outbox": proc})
    run_all._shutdown.clear()
    try:
        run_all._shutdown_all()
        assert proc.terminated is True
        assert proc.killed is False
        assert len(proc.wait_calls) == 2
        assert proc.wait_calls[0] is not None
        assert proc.wait_calls[1] is None
    finally:
        monkeypatch.setattr(run_all, "_children", {})
        run_all._shutdown.clear()
