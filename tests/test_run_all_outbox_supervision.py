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


def test_shutdown_cannot_miss_child_spawned_during_startup(monkeypatch):
    import threading
    import time

    spawn_entered = threading.Event()
    allow_spawn_to_finish = threading.Event()
    terminated = threading.Event()

    class FakeProcess:
        pid = 24680

        def terminate(self):
            terminated.set()

        def wait(self, timeout=None):
            if terminated.wait(timeout):
                return 0
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake-child", timeout)
            terminated.wait()
            return 0

        def kill(self):
            raise AssertionError("This test must not need SIGKILL")

    def blocked_popen(command):
        spawn_entered.set()
        assert allow_spawn_to_finish.wait(timeout=3)
        return FakeProcess()

    monkeypatch.setattr(run_all.subprocess, "Popen", blocked_popen)
    monkeypatch.setattr(run_all, "_children", {})
    run_all._shutdown.clear()

    starter = threading.Thread(
        target=run_all._run_and_restart,
        args=("retailbot2_outbox", ["fake-outbox-worker"]),
    )
    shutdown = threading.Thread(target=run_all._shutdown_all)

    try:
        starter.start()
        assert spawn_entered.wait(timeout=3)

        # Shutdown sets the event while Popen is still in progress and then
        # waits for the same lock before taking its child snapshot.
        shutdown.start()
        deadline = time.monotonic() + 3
        while not run_all._shutdown.is_set() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert run_all._shutdown.is_set()

        allow_spawn_to_finish.set()
        starter.join(timeout=3)
        shutdown.join(timeout=3)

        assert not starter.is_alive()
        assert not shutdown.is_alive()
        assert terminated.is_set(), "shutdown missed the child created during startup"
        assert run_all._children == {}
    finally:
        allow_spawn_to_finish.set()
        if starter.is_alive():
            starter.join(timeout=3)
        if shutdown.is_alive():
            shutdown.join(timeout=3)
        run_all._shutdown.clear()
        run_all._children.clear()


def test_phase2a_is_the_safe_default_process_set():
    processes = run_all.build_processes({})
    assert set(processes) == {"web_server", "collectors", "screener", "wallet_tracker"}


def test_paper_engine_and_news_ai_require_independent_opt_in():
    assert "paper_engine" not in run_all.build_processes({})
    assert "news_ai" not in run_all.build_processes({})
    assert "paper_engine" in run_all.build_processes({"PAPER_ENGINE_ENABLED": "1"})
    assert "news_ai" in run_all.build_processes({"NEWS_AI_ENABLED": "1"})


def test_telegram_flags_do_not_enable_held_back_services():
    processes = run_all.build_processes({
        "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        "RETAILBOT2_ALLOW_DIRECT_TELEGRAM": "1",
        "RETAILBOT2_OUTBOX_ENQUEUE_ENABLED": "1",
    })
    assert "paper_engine" not in processes
    assert "news_ai" not in processes
