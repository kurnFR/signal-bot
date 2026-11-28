"""Comprehensive tests for supervisor Phase 2A startup, shutdown, restart, and failure recovery.

Covers:
- Safe Phase 2A default process set (web_server, collectors, screener, wallet_tracker)
- Independent opt-in gates for paper_engine, news_ai, retailbot2, retailbot2_outbox
- Robustness against missing, empty, or malformed environment variables
- Database reachability check and fallback resilience
- Migration runner authorization, fail-closed handling, and supervisor cleanup
- Process restart on crash/exit, backoff, and graceful shutdown responsiveness
- Safe SIGTERM/SIGKILL handling (preserving outbox worker advisory lock safety)
- Main startup flow, signal handling, and empty selection handling
"""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import run_all


class TestSupervisorProcessSet(unittest.TestCase):
    """Test process set construction and CLI opt-in boundaries."""

    def test_default_process_set_is_phase2a_only(self):
        processes = run_all.build_processes({})
        self.assertEqual(
            set(processes.keys()),
            {"web_server", "collectors", "screener", "wallet_tracker"},
        )
        self.assertEqual(processes["collectors"], [sys.executable, "run_collectors.py"])
        self.assertEqual(processes["screener"], [sys.executable, "Screen/screening.py"])
        self.assertEqual(processes["wallet_tracker"], [sys.executable, "run_wallet_tracker.py"])
        self.assertEqual(
            processes["web_server"],
            [sys.executable, "run_web.py", "--port", "8050", "--host", "127.0.0.1"],
        )

    def test_web_server_handles_missing_empty_and_malformed_host_and_port(self):
        # Missing port and host
        p1 = run_all.build_processes({})
        self.assertIn("--port", p1["web_server"])
        self.assertEqual(p1["web_server"][p1["web_server"].index("--port") + 1], "8050")
        self.assertEqual(p1["web_server"][p1["web_server"].index("--host") + 1], "127.0.0.1")

        # Explicit valid port and host
        p2 = run_all.build_processes({"WEB_PORT": "9000", "SIGNAL_BOT_HOST": "0.0.0.0"})
        self.assertEqual(p2["web_server"][p2["web_server"].index("--port") + 1], "9000")
        self.assertEqual(p2["web_server"][p2["web_server"].index("--host") + 1], "0.0.0.0")

        # Malformed / empty / out-of-range / None port and host fall back safely to 8050 and 127.0.0.1
        for bad_port in ("", "   ", "not_a_port", "0", "65536", "-1", "abc", None):
            p = run_all.build_processes({"WEB_PORT": bad_port, "SIGNAL_BOT_HOST": "  "})
            self.assertEqual(p["web_server"][p["web_server"].index("--port") + 1], "8050")
            self.assertEqual(p["web_server"][p["web_server"].index("--host") + 1], "127.0.0.1")

        p_none_host = run_all.build_processes({"WEB_PORT": None, "SIGNAL_BOT_HOST": None})
        self.assertEqual(p_none_host["web_server"][p_none_host["web_server"].index("--port") + 1], "8050")
        self.assertEqual(p_none_host["web_server"][p_none_host["web_server"].index("--host") + 1], "127.0.0.1")

    def test_paper_engine_opt_in_strictness(self):
        for val in ("", "0", "false", "no", "true", "yes", "2", "enabled", None):
            p = run_all.build_processes({"PAPER_ENGINE_ENABLED": val})
            self.assertNotIn("paper_engine", p)

        p = run_all.build_processes({"PAPER_ENGINE_ENABLED": "1"})
        self.assertIn("paper_engine", p)
        self.assertEqual(p["paper_engine"], [sys.executable, "run_paper_engine.py"])

    def test_news_ai_opt_in_strictness(self):
        for val in ("", "0", "false", "no", "true", "yes", "2", "enabled", None):
            p = run_all.build_processes({"NEWS_AI_ENABLED": val})
            self.assertNotIn("news_ai", p)

        p = run_all.build_processes({"NEWS_AI_ENABLED": "1"})
        self.assertIn("news_ai", p)
        self.assertEqual(p["news_ai"], [sys.executable, "run_news_ai.py"])

    def test_retailbot2_opt_in_strictness(self):
        # Step 3: the worker is deleted -- RETAILBOT2_ENABLED no longer opts
        # anything in, whatever its value (including "1").
        for val in ("", "0", "false", "no", "true", "yes", "2", "enabled", None, "1"):
            p = run_all.build_processes({"RETAILBOT2_ENABLED": val})
            self.assertNotIn("retailbot2", p)

    def test_outbox_worker_dual_opt_in_permutations(self):
        # Missing or single flags keep outbox disabled
        cases = [
            ({}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": None}, False),
            ({"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": None}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": None, "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": None}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "1", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": None}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": None, "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "1"}, False),
            ({"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "0", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "1", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "true", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "1", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "true"}, False),
            ({"RETAILBOT2_OUTBOX_WORKER_ENABLED": "1", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, True),
        ]
        for env_dict, expected in cases:
            p = run_all.build_processes(env_dict)
            self.assertEqual("retailbot2_outbox" in p, expected, f"Failed for {env_dict}")

    def test_process_set_resilience_to_all_none_env_values(self):
        all_none = {
            "WEB_PORT": None,
            "SIGNAL_BOT_HOST": None,
            "PAPER_ENGINE_ENABLED": None,
            "NEWS_AI_ENABLED": None,
            "RETAILBOT2_ENABLED": None,
            "RETAILBOT2_OUTBOX_WORKER_ENABLED": None,
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": None,
        }
        processes = run_all.build_processes(all_none)
        self.assertEqual(
            set(processes.keys()),
            {"web_server", "collectors", "screener", "wallet_tracker"},
        )


class TestDatabasePreflightAndResilience(unittest.TestCase):
    """Test ensure_database_running error handling and fallback behavior."""

    @patch("socket.create_connection")
    def test_database_running_when_port_is_open(self, mock_connect):
        mock_connect.return_value.__enter__ = MagicMock()
        mock_connect.return_value.__exit__ = MagicMock()
        with patch.dict(os.environ, {"MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "3306"}):
            proc = run_all.ensure_database_running()
            self.assertIsNone(proc)
            mock_connect.assert_called_once_with(("127.0.0.1", 3306), timeout=1)

    @patch("socket.create_connection", side_effect=OSError("Connection refused"))
    def test_malformed_mysql_port_falls_back_to_3306(self, mock_connect):
        for bad_port in ("", "   ", "abc", "-10", "99999"):
            mock_connect.reset_mock()
            with patch.dict(os.environ, {"MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": bad_port}):
                proc = run_all.ensure_database_running()
                self.assertIsNone(proc)
                mock_connect.assert_called_once_with(("127.0.0.1", 3306), timeout=1)

    @patch("socket.create_connection", side_effect=OSError("Connection refused"))
    def test_empty_mysql_host_falls_back_to_localhost(self, mock_connect):
        with patch.dict(os.environ, {"MYSQL_HOST": "", "MYSQL_PORT": "3306"}):
            proc = run_all.ensure_database_running()
            self.assertIsNone(proc)
            mock_connect.assert_called_once_with(("127.0.0.1", 3306), timeout=1)


class TestSupervisorRestartAndLifecycle(unittest.TestCase):
    """Test process restart on exit, backoff sleep, and clean shutdown response."""

    def setUp(self):
        run_all._shutdown.clear()
        with run_all._children_lock:
            run_all._children.clear()

    def tearDown(self):
        run_all._shutdown.set()
        with run_all._children_lock:
            run_all._children.clear()

    def test_run_and_restart_restarts_exited_process_and_registers_in_children(self):
        iteration = 0
        started_names = []

        class MockProcess:
            pid = 5555

            def __init__(self, idx):
                self.idx = idx

            def wait(self):
                # First iteration exits 0; then trigger shutdown
                if self.idx >= 1:
                    run_all._shutdown.set()
                return 0

        def fake_popen(cmd):
            nonlocal iteration
            p = MockProcess(iteration)
            iteration += 1
            started_names.append(cmd)
            # Verify that while Popen is stored, it is in run_all._children
            return p

        with patch.object(run_all.subprocess, "Popen", side_effect=fake_popen), \
             patch.object(run_all, "RESTART_BACKOFF_SECONDS", 0.01):
            run_all._run_and_restart("test_worker", ["worker_bin"])

        self.assertGreaterEqual(iteration, 2)
        self.assertNotIn("test_worker", run_all._children)

    def test_worker_recovers_after_launch_error(self):
        child = MagicMock()
        child.wait.side_effect = lambda: run_all._shutdown.set()
        with patch.object(run_all.subprocess, "Popen", side_effect=[OSError("busy"), child]) as launch, \
             patch.object(run_all._shutdown, "wait") as backoff:
            run_all._run_and_restart("collectors", ["worker"])
        self.assertEqual(launch.call_count, 2)
        backoff.assert_called_once_with(run_all.RESTART_BACKOFF_SECONDS)
        self.assertNotIn("collectors", run_all._children)

    def test_run_and_restart_exits_immediately_if_shutdown_already_set(self):
        run_all._shutdown.set()
        with patch.object(run_all.subprocess, "Popen") as mock_popen:
            run_all._run_and_restart("test_worker", ["worker_bin"])
            mock_popen.assert_not_called()

    def test_shutdown_all_terminates_registered_children(self):
        class MockChild:
            def __init__(self, pid):
                self.pid = pid
                self.terminated = False
                self.killed = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.killed = True

        child1 = MockChild(101)
        child2 = MockChild(102)
        with run_all._children_lock:
            run_all._children["web_server"] = child1
            run_all._children["screener"] = child2

        run_all._shutdown_all()

        self.assertTrue(run_all._shutdown.is_set())
        self.assertTrue(child1.terminated)
        self.assertFalse(child1.killed)
        self.assertTrue(child2.terminated)
        self.assertFalse(child2.killed)

    def test_shutdown_all_kills_unresponsive_non_outbox_children_after_timeout(self):
        class HangingChild:
            pid = 201

            def __init__(self):
                self.terminated = False
                self.killed = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("hanging", timeout or 0)

            def kill(self):
                self.killed = True

        child = HangingChild()
        with run_all._children_lock:
            run_all._children["web_server"] = child

        with patch("time.time", side_effect=[0, 15, 15, 15]):
            run_all._shutdown_all()

        self.assertTrue(child.terminated)
        self.assertTrue(child.killed)


class TestSupervisorMainFlow(unittest.TestCase):
    """Test main() behavior, arg handling, and migration failure abort."""

    def setUp(self):
        run_all._shutdown.clear()
        with run_all._children_lock:
            run_all._children.clear()

    def tearDown(self):
        run_all._shutdown.set()
        with run_all._children_lock:
            run_all._children.clear()

    def test_main_empty_selection_exits_cleanly(self):
        with patch.object(run_all.argparse.ArgumentParser, "parse_args",
                          return_value=Namespace(skip=["web_server", "collectors", "screener", "wallet_tracker"], only=[])), \
             patch.object(run_all, "PROCESSES", {"web_server": [], "collectors": [], "screener": [], "wallet_tracker": []}), \
             patch.object(run_all, "ensure_database_running") as mock_ensure, \
             patch.object(run_all, "apply_migrations") as mock_migrate:
            run_all.main()
            mock_ensure.assert_not_called()
            mock_migrate.assert_not_called()

    def test_main_migration_failure_calls_shutdown_and_exits_with_code_1(self):
        with patch.object(run_all.argparse.ArgumentParser, "parse_args",
                          return_value=Namespace(skip=[], only=[])), \
             patch.object(run_all, "ensure_database_running"), \
             patch.object(run_all, "apply_migrations", return_value=False), \
             patch.object(run_all, "_shutdown_all") as mock_shutdown:
            with self.assertRaises(SystemExit) as ctx:
                run_all.main()
            self.assertEqual(ctx.exception.code, 1)
            mock_shutdown.assert_called_once()

    def test_signal_handlers_trigger_shutdown_and_clean_exit(self):
        handlers = {}

        def fake_signal(sig, h):
            handlers[sig] = h

        with patch.object(run_all.argparse.ArgumentParser, "parse_args", return_value=Namespace(skip=[], only=[])), \
             patch.object(run_all, "ensure_database_running"), \
             patch.object(run_all, "apply_migrations", return_value=True), \
             patch("signal.signal", side_effect=fake_signal), \
             patch.object(run_all, "_shutdown_all") as mock_shutdown, \
             patch("threading.Thread"), \
             patch.object(run_all.time, "sleep"), \
             patch.object(run_all._shutdown, "is_set", return_value=True):
            run_all.main()

            self.assertIn(signal.SIGINT, handlers)
            self.assertIn(signal.SIGTERM, handlers)

            with self.assertRaises(SystemExit) as ctx:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertEqual(ctx.exception.code, 0)
            mock_shutdown.assert_called_once()

            mock_shutdown.reset_mock()
            with self.assertRaises(SystemExit) as ctx:
                handlers[signal.SIGINT](signal.SIGINT, None)
            self.assertEqual(ctx.exception.code, 0)
            mock_shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
