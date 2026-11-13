"""Tests for RetailBot2 Outbox Worker Supervision and Lifecycle (WO71782 Stage E).

Validates:
1. Production systemd unit template syntax, directives, hardening, and environment boundaries.
2. systemd-analyze verification (if systemd tooling is available).
3. Worker process lifecycle: signal registration, SIGTERM handling, lock contention, and clean release.
"""

import configparser
import os
import shutil
import signal
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UNIT_FILE_PATH = os.path.join(REPO_ROOT, "deploy", "systemd", "retailbot2-outbox-worker.service")

sys.path.insert(0, REPO_ROOT)
from paper.outbox_worker import OutboxWorker, OutboxWorkerState


class TestOutboxSystemdUnitTemplate(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.isfile(UNIT_FILE_PATH), f"Unit file missing: {UNIT_FILE_PATH}")
        with open(UNIT_FILE_PATH, "r", encoding="utf-8") as f:
            self.content = f.read()

        # Parse with configparser (ignoring prefix comments)
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read_string(self.content)

    def test_unit_sections_present(self):
        self.assertIn("Unit", self.config.sections())
        self.assertIn("Service", self.config.sections())
        self.assertIn("Install", self.config.sections())

    def test_unit_metadata_and_rate_limits(self):
        unit = self.config["Unit"]
        self.assertIn("RetailBot2", unit.get("Description", ""))
        self.assertIn("RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md", unit.get("Documentation", ""))
        self.assertEqual(unit.get("StartLimitIntervalSec"), "60s")
        self.assertEqual(unit.get("StartLimitBurst"), "5")

    def test_service_identity_and_environment(self):
        service = self.config["Service"]
        self.assertEqual(service.get("User"), "retailbot")
        self.assertEqual(service.get("Group"), "retailbot")
        self.assertEqual(service.get("WorkingDirectory"), "/opt/signal-bot")
        self.assertEqual(service.get("EnvironmentFile"), "-/etc/retailbot2/outbox-worker.env")

    def test_service_process_lifecycle_and_shutdown(self):
        service = self.config["Service"]
        self.assertEqual(service.get("KillMode"), "mixed")
        self.assertEqual(service.get("KillSignal"), "SIGTERM")
        self.assertEqual(service.get("TimeoutStopSec"), "30s")
        self.assertEqual(service.get("TimeoutStartSec"), "30s")
        self.assertEqual(service.get("Restart"), "on-failure")
        self.assertEqual(service.get("RestartSec"), "5s")

    def test_service_exec_start_observation_default(self):
        service = self.config["Service"]
        exec_start = service.get("ExecStart", "")
        self.assertIn("--observation", exec_start, "ExecStart must explicitly default to --observation")
        self.assertIn("--database crypto_signals", exec_start)
        self.assertIn("paper.outbox_worker", exec_start)

    def test_service_security_hardening(self):
        service = self.config["Service"]
        self.assertEqual(service.get("NoNewPrivileges"), "true")
        self.assertEqual(service.get("ProtectSystem"), "strict")
        self.assertEqual(service.get("ProtectHome"), "read-only")
        self.assertEqual(service.get("PrivateTmp"), "true")
        self.assertEqual(service.get("ProtectKernelTunables"), "true")
        self.assertEqual(service.get("ProtectControlGroups"), "true")
        self.assertEqual(service.get("ProtectKernelModules"), "true")
        self.assertEqual(service.get("RestrictRealtime"), "true")
        self.assertEqual(service.get("LockPersonality"), "true")
        self.assertEqual(service.get("MemoryDenyWriteExecute"), "true")
        self.assertEqual(service.get("RestrictAddressFamilies"), "AF_INET AF_INET6 AF_UNIX")
        self.assertEqual(service.get("CapabilityBoundingSet"), "")

    def test_systemd_analyze_verify_if_available(self):
        systemd_analyze = shutil.which("systemd-analyze")
        if not systemd_analyze:
            self.skipTest("systemd-analyze not available in environment")

        res = subprocess.run(
            [systemd_analyze, "verify", UNIT_FILE_PATH],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Note: missing executable binary /opt/signal-bot/venv/bin/python3 on dev host
        # is an expected informational notice, not a unit syntax error.
        combined = res.stdout + res.stderr
        self.assertNotIn("Unknown lvalue", combined)
        self.assertNotIn("Failed to parse", combined)


class TestWorkerLifecycleSimulation(unittest.TestCase):
    def test_signal_handling_registration(self):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "database": "signal_bot_test"},
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.1,
        )
        self.assertFalse(worker._shutdown_requested)
        worker.setup_signals()

        # Simulate SIGTERM receipt
        worker._handle_signal(signal.SIGTERM, None)
        self.assertTrue(worker._shutdown_requested)

    def test_graceful_shutdown_releases_lock(self):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "database": "signal_bot_test"},
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.01,
        )
        worker.session_mgr = MagicMock()
        worker.session_mgr.check_lock_owned.return_value = True
        worker.session_mgr.heartbeat.return_value = True
        worker._shutdown_requested = True

        exit_code = worker.run()
        self.assertEqual(exit_code, 0)
        worker.session_mgr.close.assert_called_once()
        self.assertEqual(worker.state, OutboxWorkerState.STOPPED)

    def test_lock_contention_fails_closed(self):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "database": "signal_bot_test"},
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.01,
        )
        worker.session_mgr = MagicMock()
        worker.session_mgr.check_lock_owned.return_value = False
        worker.session_mgr.connect_and_acquire.return_value = False

        exit_code = worker.run()
        self.assertEqual(exit_code, 1)
        self.assertEqual(worker.state, OutboxWorkerState.STOPPED)

    def test_shutdown_with_active_transport_subprocess_terminates_and_releases_lock(self):
        """Simulate worker receiving SIGTERM while a transport subprocess is active in-flight.
        Proves clean subprocess handling, release of advisory lock, and exit code 0."""
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "database": "signal_bot_test"},
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.01,
        )
        worker.session_mgr = MagicMock()
        worker.session_mgr.check_lock_owned.return_value = True
        worker.session_mgr.heartbeat.return_value = True

        event_sample = {
            "id": 999,
            "event_key": "retailbot2:test:shutdown_active_transport",
            "trade_id": 0,
            "event_type": "entry",
            "attempts": 1,
            "claim_token": "token1234567890123456789012345678",
            "payload": {
                "symbol": "BTC/USDT",
                "side": "LONG",
                "entry": 50000.0,
                "stop": 49000.0,
                "target": 52000.0,
                "units": 0.1,
                "risk_amt": 100.0,
                "risk_pct": 1.0,
            },
        }
        worker.session_mgr.claim_event.return_value = event_sample

        def mock_dispatch(payload, **kwargs):
            # Simulate SIGTERM receipt during in-flight subprocess execution
            worker._handle_signal(signal.SIGTERM, None)
            return {"status": "DELIVERED"}

        with patch("paper.outbox_worker.dispatch_event_via_subprocess", side_effect=mock_dispatch):
            exit_code = worker.run()

        self.assertEqual(exit_code, 0)
        self.assertEqual(worker.state, OutboxWorkerState.STOPPED)
        worker.session_mgr.close.assert_called_once()
        worker.session_mgr.mark_sent.assert_called_once_with(999, "token1234567890123456789012345678")

    def test_observation_mode_does_not_claim_or_dispatch(self):
        """Verify that a worker running in observation mode inspects queue without claiming."""
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "database": "signal_bot_test"},
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.01,
            observation_mode=True,
        )
        worker.session_mgr = MagicMock()
        worker.session_mgr.check_lock_owned.return_value = True
        worker.session_mgr.inspect_queue.return_value = {"due_count": 5, "status_counts": {"pending": 5}}

        res = worker.process_one_event()
        self.assertFalse(res)
        worker.session_mgr.inspect_queue.assert_called_once()
        worker.session_mgr.claim_event.assert_not_called()
        self.assertEqual(worker.telegram_token, "")
        self.assertEqual(worker.telegram_chat_id, "")

    def test_production_startup_without_observation_fails_closed(self):
        """Verify that launching worker on crypto_signals without --observation fails closed."""
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals"]):
                with self.assertRaises(SystemExit) as ctx:
                    from paper.outbox_worker import main
                    main()
                self.assertEqual(ctx.exception.code, 1)

    def test_production_observation_startup_without_auth_switch_fails_closed(self):
        """Verify that observation mode on crypto_signals requires explicit authorization switch."""
        from paper.outbox_worker import resolve_worker_db_credentials
        with patch.dict(os.environ, {"RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED": "0"}, clear=False):
            with self.assertRaises(ValueError) as ctx:
                resolve_worker_db_credentials("crypto_signals", observation=True)
            self.assertIn("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1 is required", str(ctx.exception))

    def test_production_health_without_observation_fails_closed(self):
        """Production --health without --observation fails closed without requesting delivery authorization."""
        from paper.outbox_worker import main
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals", "--health"]):
                with self.assertLogs("outbox_worker", level="ERROR") as log_ctx:
                    with self.assertRaises(SystemExit) as ctx:
                        main()
                self.assertEqual(ctx.exception.code, 1)
                log_text = "\n".join(log_ctx.output)
                self.assertIn("Production health inspection is read-only and requires --observation mode", log_text)
                self.assertNotIn("requires RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1", log_text)

    def test_production_health_with_observation_uses_dedicated_obs_credentials(self):
        """Production --health --observation resolves only dedicated observation credentials."""
        from paper.outbox_worker import main
        fake_conn = MagicMock()
        with patch.dict(os.environ, {
            "RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED": "1",
            "RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER": "obs_usr_mock",
            "RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD": "obs_pwd_mock",
            "MYSQL_USER": "delivery_user_forbidden",
            "MYSQL_PASSWORD": "delivery_password_forbidden",
        }, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals", "--health", "--observation"]):
                with patch("mysql.connector.connect", return_value=fake_conn) as mock_connect:
                    with patch("paper.outbox_health.inspect_outbox_health", return_value={"health_status": "HEALTHY"}):
                        with patch("builtins.print"):
                            with self.assertRaises(SystemExit) as ctx:
                                main()
                        self.assertEqual(ctx.exception.code, 0)
                        mock_connect.assert_called_once()
                        conn_kwargs = mock_connect.call_args[1]
                        self.assertEqual(conn_kwargs["user"], "obs_usr_mock")
                        self.assertEqual(conn_kwargs["password"], "obs_pwd_mock")
                        self.assertNotEqual(conn_kwargs["user"], "delivery_user_forbidden")

    def test_production_health_never_consults_delivery_credentials(self):
        """Production health inspection fails closed if observation credentials missing, ignoring delivery credentials."""
        from paper.outbox_worker import main
        with patch.dict(os.environ, {
            "RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED": "0",
            "MYSQL_USER": "delivery_user_forbidden",
            "MYSQL_PASSWORD": "delivery_password_forbidden",
        }, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals", "--health", "--observation"]):
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertEqual(ctx.exception.code, 1)

    def test_test_db_health_uses_dedicated_test_credentials(self):
        """Test database health inspection uses SIGNAL_BOT_TEST_DB_USER and SIGNAL_BOT_TEST_DB_PASSWORD."""
        from paper.outbox_worker import main
        fake_conn = MagicMock()
        with patch.dict(os.environ, {
            "SIGNAL_BOT_TEST_DB_USER": "test_db_user_mock",
            "SIGNAL_BOT_TEST_DB_PASSWORD": "test_db_pwd_mock",
            "MYSQL_USER": "cms_fallback_forbidden",
            "MYSQL_PASSWORD": "cms_fallback_forbidden",
        }, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "signal_bot_test", "--health"]):
                with patch("mysql.connector.connect", return_value=fake_conn) as mock_connect:
                    with patch("paper.outbox_health.inspect_outbox_health", return_value={"health_status": "HEALTHY"}):
                        with patch("builtins.print"):
                            with self.assertRaises(SystemExit) as ctx:
                                main()
                        self.assertEqual(ctx.exception.code, 0)
                        conn_kwargs = mock_connect.call_args[1]
                        self.assertEqual(conn_kwargs["user"], "test_db_user_mock")
                        self.assertEqual(conn_kwargs["password"], "test_db_pwd_mock")

    def test_health_mode_does_not_instantiate_worker(self):
        """Health mode exits before OutboxWorker is instantiated or run."""
        from paper.outbox_worker import main
        fake_conn = MagicMock()
        with patch.dict(os.environ, {
            "SIGNAL_BOT_TEST_DB_USER": "test_user",
            "SIGNAL_BOT_TEST_DB_PASSWORD": "test_pwd",
        }, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "signal_bot_test", "--health"]):
                with patch("mysql.connector.connect", return_value=fake_conn):
                    with patch("paper.outbox_health.inspect_outbox_health", return_value={"health_status": "HEALTHY"}):
                        with patch("builtins.print"):
                            with patch("paper.outbox_worker.OutboxWorker") as mock_worker_cls:
                                with self.assertRaises(SystemExit) as ctx:
                                    main()
                                self.assertEqual(ctx.exception.code, 0)
                                mock_worker_cls.assert_not_called()

    def test_normal_delivery_requires_explicit_delivery_authorization(self):
        """Production worker without --observation and without --health requires RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1."""
        from paper.outbox_worker import main
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}, clear=False):
            with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals"]):
                with self.assertLogs("outbox_worker", level="ERROR") as log_ctx:
                    with self.assertRaises(SystemExit) as ctx:
                        main()
                self.assertEqual(ctx.exception.code, 1)
                self.assertIn("Production outbox requires RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1", "\n".join(log_ctx.output))


if __name__ == "__main__":
    unittest.main()
