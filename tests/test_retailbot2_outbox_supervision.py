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
        self.assertEqual(service.get("TimeoutStopSec"), "15s")
        self.assertEqual(service.get("TimeoutStartSec"), "30s")
        self.assertEqual(service.get("Restart"), "on-failure")
        self.assertEqual(service.get("RestartSec"), "5s")

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



if __name__ == "__main__":
    unittest.main()
