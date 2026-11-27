"""Targeted regression tests for Work Order 71695 and Work Order 71696:
- Section 1: Telegram delivery authorization dual-flag policy (RETAILBOT2_ALLOW_TELEGRAM_DELIVERY and RETAILBOT2_ALLOW_DIRECT_TELEGRAM).
- Section 2 & 4: Worker activation contract in run_all.py supervisor.
- Section 5: Outbox worker startup schema preflight fail-closed diagnostic.

Tested against local mock environments and the isolated test schema signal_bot_test.
Never connects to, inspects, or modifies the production database crypto_signals.
"""

import json
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import run_all
from paper.outbox_worker import (
    OUTBOX_LOCK_NAME,
    REQUIRED_OUTBOX_COLUMNS,
    OutboxSessionManager,
    OutboxWorker,
    OutboxWorkerState,
    SchemaVerificationError,
    verify_outbox_schema,
)
# Step 3: the worker (paper/retailbot2.py) is deleted. TelegramAlert's
# dual-flag policy is now asserted through notifications.telegram_notifier
# in tests/test_telegram_delivery_ceiling.py; the report formatting tests
# below target paper/paper_report.py directly.
from paper.paper_report import format_paper_report, paper_performance_lines


# ==============================================================================
# SECTION 1: TELEGRAM DELIVERY DUAL-FLAG AUTHORIZATION
# ==============================================================================

# ==============================================================================
# SECTION 1 (CONT): OPERATIONAL NOTIFICATION ROUTING & FAIL-CLOSED BEHAVIOR
# ==============================================================================

class TestOperationalNotificationRouting:
    """Verifies operational notifications prefer outbox queue and fail closed when unauthorized."""

    def test_paper_report_formats_closed_trades_and_hides_zero_trade_rows(self):
        mock_metrics = {
            "totalTrades": 10,
            "wins": 7,
            "losses": 3,
            "winRatePct": 70.0,
            "netPnl": -450.50,
            "byStrategy": [
                {"symbol": "BTCUSDT", "strategy": "trend_v1", "totalTrades": 6,
                 "wins": 4, "losses": 2, "netPnl": -450.50},
                {"symbol": "ETHUSDT", "strategy": "trend_v1", "totalTrades": 4,
                 "wins": 3, "losses": 1, "netPnl": 0.0},
                {"symbol": "ETHUSDT", "strategy": "idle_v1", "totalTrades": 0,
                 "wins": 0, "losses": 0, "netPnl": 0.0},
                {"symbol": "XAUTUSDT", "strategy": "trend_v1", "totalTrades": 0,
                 "wins": 0, "losses": 0, "netPnl": 0.0},
            ],
        }
        with patch("paper.engine.get_paper_metrics", return_value=mock_metrics):
            text = format_paper_report()

        assert "Net P&amp;L: <b>−$450.50</b>" in text
        assert "10 closed · Wins 7 · Losses 3 · 70% win rate" in text
        assert "<b>BTCUSDT</b> · −$450.50\n6 closed · 67% win rate" in text
        assert "<b>ETHUSDT</b> · $0.00\n4 closed · 75% win rate" in text
        assert "• <b>trend_v1</b> · $0.00\n  4 closed · Wins 3 · Losses 1 · 75% win rate" in text
        assert text.count("<b>trend_v1</b>") == 2
        assert "idle_v1" not in text
        assert "XAUTUSDT" not in text

    def test_paper_performance_lines_standalone_execution_resolves_paper_engine(self):
        """paper_report.paper_performance_lines resolves paper.engine imports
        without ModuleNotFoundError.

        Step 3: the old version ran `from retailbot2 import TelegramAlert`
        with cwd=paper/ -- that only worked because retailbot2.py inserted
        the repo root into sys.path at import time. paper_report.py is a
        plain package module and does not do that, so the supported path is
        the package import from the repo root. The header line is asserted
        (not the metrics body) because get_paper_metrics needs MySQL, which
        a bare import-resolution check must not require; an unresolvable
        import still fails the subprocess with a nonzero exit.
        """
        import subprocess
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            "import sys\n"
            "from paper.paper_report import paper_performance_lines\n"
            "lines = paper_performance_lines()\n"
            "assert any('Paper Trading Engine' in l for l in lines), f'Missing header: {lines}'\n"
            "print('RESOLVED_OK')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        assert "RESOLVED_OK" in res.stdout

    def test_paper_performance_lines_logs_detailed_exception_on_failure(self, caplog):
        """When paper.engine raises an exception, the exception name and string are logged."""
        with patch("paper.engine.get_paper_metrics", side_effect=RuntimeError("db connection failed")):
            with caplog.at_level(logging.WARNING):
                lines = paper_performance_lines()
        assert "Paper trade totals unavailable" in lines
        assert any("Paper performance report unavailable (RuntimeError: db connection failed)" in record.message for record in caplog.records)

    def test_worker_formats_operational_event_payload(self):
        """OutboxWorker.format_event_text properly formats operational event type."""
        worker = object.__new__(OutboxWorker)
        event = {
            "id": 42,
            "event_type": "operational",
            "trade_id": 0,
            "payload": {"text": "📊 <b>PERFORMANCE REPORT</b>\nNet=$+500"},
        }
        text = worker.format_event_text(event)
        assert text == "📊 <b>PERFORMANCE REPORT</b>\nNet=$+500"

    def test_worker_rejects_malformed_operational_event(self):
        """OutboxWorker.format_event_text raises ValueError on operational event lacking text."""
        worker = object.__new__(OutboxWorker)
        event = {
            "id": 42,
            "event_type": "operational",
            "trade_id": 0,
            "payload": {},
        }
        with pytest.raises(ValueError, match="missing 'text' payload"):
            worker.format_event_text(event)


# ==============================================================================
# SECTION 2 & 4: WORKER ACTIVATION CONTRACT IN SUPERVISOR
# ==============================================================================

class TestWorkerActivationSupervisorContract:
    """Verifies that run_all.py supervisor activates the outbox worker only with exact dual flags."""

    def test_worker_disabled_by_default(self):
        """Supervisor disables outbox worker by default when no flags are passed."""
        processes = run_all.build_processes({})
        assert "retailbot2_outbox" not in processes

    def test_retailbot2_disabled_by_default(self):
        """RetailBot2 is excluded from the default supervisor process set."""
        processes = run_all.build_processes({})
        assert "retailbot2" not in processes

    def test_retailbot2_requires_explicit_independent_opt_in(self):
        """Step 3: RETAILBOT2_ENABLED=1 no longer opts anything in -- the
        worker is deleted, so the supervisor never has a retailbot2 entry."""
        processes = run_all.build_processes({"RETAILBOT2_ENABLED": "1"})
        assert "retailbot2" not in processes
        assert "retailbot2_outbox" not in processes

    def test_telegram_flags_do_not_implicitly_enable_retailbot2(self):
        """Telegram authorization flags alone must not start RetailBot2."""
        processes = run_all.build_processes({
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
            "RETAILBOT2_ALLOW_DIRECT_TELEGRAM": "1",
            "RETAILBOT2_OUTBOX_ENQUEUE_ENABLED": "1",
        })
        assert "retailbot2" not in processes

    def test_trading_authorization_flag_alone_does_not_enable_retailbot2_in_supervisor(self):
        """RETAILBOT2_ALLOW_TRADING=1 alone must not start RetailBot2 in supervisor."""
        processes = run_all.build_processes({
            "RETAILBOT2_ALLOW_TRADING": "1",
        })
        assert "retailbot2" not in processes

    def test_retailbot2_enabled_does_not_implicitly_authorize_trading_or_outbox(self):
        """Step 3: RETAILBOT2_ENABLED=1 is ignored entirely now (no worker,
        no trading gate); it must not start the outbox worker either."""
        processes = run_all.build_processes({
            "RETAILBOT2_ENABLED": "1",
        })
        assert "retailbot2" not in processes
        assert "retailbot2_outbox" not in processes

    def test_worker_requires_both_flags(self):
        """Supervisor requires BOTH RETAILBOT2_OUTBOX_WORKER_ENABLED and RETAILBOT2_ALLOW_TELEGRAM_DELIVERY."""
        assert "retailbot2_outbox" not in run_all.build_processes({
            "RETAILBOT2_OUTBOX_WORKER_ENABLED": "1",
        })
        assert "retailbot2_outbox" not in run_all.build_processes({
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        })

    def test_unsupported_flag_does_not_enable_worker(self):
        """Hypothetical or misspelled flag RETAILBOT2_TELEGRAM_WORKER_ENABLED is not recognized."""
        processes = run_all.build_processes({
            "RETAILBOT2_TELEGRAM_WORKER_ENABLED": "1",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        })
        assert "retailbot2_outbox" not in processes

    def test_worker_command_when_both_flags_present(self):
        """When both flags are set, supervisor configures standalone worker with crypto_signals."""
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


# ==============================================================================
# SECTION 5: OUTBOX WORKER STARTUP SCHEMA PREFLIGHT & DIAGNOSTICS
# ==============================================================================

class TestOutboxWorkerSchemaPreflight:
    """Verifies that the standalone worker validates schema and produces fail-closed diagnostics."""

    def test_required_outbox_columns_specification(self):
        """Ensure all 13 required columns are formally specified."""
        expected = {
            "id", "event_key", "trade_id", "event_type", "payload_json",
            "status", "attempts", "next_attempt_at", "locked_until",
            "claim_token", "last_error", "created_at", "sent_at",
        }
        assert set(REQUIRED_OUTBOX_COLUMNS) == expected

    def test_preflight_fails_when_table_missing_with_fail_closed_diagnostic(self):
        """Missing table raises SchemaVerificationError with strict fail-closed operator diagnostic."""
        cursor = Mock()
        cursor.fetchone.return_value = None  # Table not found
        cursor.fetchall.return_value = []
        conn = Mock()
        conn.cursor.return_value = cursor

        with pytest.raises(SchemaVerificationError) as exc_info:
            verify_outbox_schema(conn)

        msg = str(exc_info.value)
        assert "Required outbox table 'rdt_telegram_outbox' does not exist in database" in msg
        assert "Worker startup halted fail-closed" in msg
        assert "Operator investigation required against approved migration history" in msg
        assert "(migrations 025-027 must not be blindly re-run)" in msg
        assert "do not automatically modify production schema" in msg
        # Must NOT casually recommend running db.migrate
        assert "run 'python -m db.migrate'" not in msg
        cursor.close.assert_called_once()

    def test_preflight_fails_when_claim_token_column_missing_with_strict_diagnostic(self):
        """Missing claim_token column raises SchemaVerificationError with strict diagnostic."""
        cursor = Mock()
        cursor.fetchone.return_value = ("rdt_telegram_outbox",)
        columns_without_claim_token = [
            (c,) for c in REQUIRED_OUTBOX_COLUMNS if c != "claim_token"
        ]
        cursor.fetchall.return_value = columns_without_claim_token
        conn = Mock()
        conn.cursor.return_value = cursor

        with pytest.raises(SchemaVerificationError) as exc_info:
            verify_outbox_schema(conn)

        msg = str(exc_info.value)
        assert "Required outbox column(s) missing from 'rdt_telegram_outbox': claim_token" in msg
        assert "Worker startup halted fail-closed" in msg
        assert "Operator investigation required against approved migration history" in msg
        assert "(migrations 025-027 must not be blindly re-run)" in msg
        assert "run 'python -m db.migrate'" not in msg
        cursor.close.assert_called_once()

    def test_preflight_fails_when_multiple_columns_missing(self):
        """Missing other required columns raises SchemaVerificationError listing them."""
        cursor = Mock()
        cursor.fetchone.return_value = ("rdt_telegram_outbox",)
        partial_columns = [(c,) for c in REQUIRED_OUTBOX_COLUMNS if c not in ("status", "locked_until")]
        cursor.fetchall.return_value = partial_columns
        conn = Mock()
        conn.cursor.return_value = cursor

        with pytest.raises(SchemaVerificationError) as exc_info:
            verify_outbox_schema(conn)

        err_msg = str(exc_info.value)
        assert "locked_until" in err_msg
        assert "status" in err_msg
        assert "Worker startup halted fail-closed" in err_msg
        cursor.close.assert_called_once()

    def test_preflight_queries_only_information_schema_without_mutations(self):
        """Preflight issues only read-only SELECT queries against information_schema."""
        cursor = Mock()
        cursor.fetchone.return_value = ("rdt_telegram_outbox",)
        cursor.fetchall.return_value = [(c,) for c in REQUIRED_OUTBOX_COLUMNS]
        conn = Mock()
        conn.cursor.return_value = cursor

        verify_outbox_schema(conn)

        executed_queries = [call[0][0].upper() for call in cursor.execute.call_args_list]
        assert len(executed_queries) == 2
        for query in executed_queries:
            assert query.strip().startswith("SELECT")
            assert "INFORMATION_SCHEMA" in query
            assert "INSERT" not in query
            assert "UPDATE" not in query
            assert "DELETE" not in query
            assert "DROP" not in query
            assert "ALTER" not in query

    def test_no_advisory_lock_acquired_when_schema_preflight_fails(self):
        """Session manager closes connection and raises SchemaVerificationError without acquiring lock."""
        mgr = object.__new__(OutboxSessionManager)
        mgr.db_config = {"host": "127.0.0.1", "port": 3306, "user": "test", "password": "", "database": "signal_bot_test"}
        mgr.s_lock = None
        mgr.allowed_databases = ("signal_bot_test",)
        mgr.db_manager = Mock()
        mgr.close = Mock()

        mock_conn = Mock()
        with patch("paper.outbox_worker.mysql.connector.connect", return_value=mock_conn), \
             patch("paper.outbox_worker.verify_database_identity", return_value="signal_bot_test"), \
             patch("paper.outbox_worker.verify_outbox_schema", side_effect=SchemaVerificationError("Missing table")):
            with pytest.raises(SchemaVerificationError, match="Missing table"):
                mgr.connect_and_acquire()

        mgr.db_manager.acquire_outbox_lock.assert_not_called()
        mgr.close.assert_called_once()

    def test_worker_run_exits_code_1_on_schema_verification_error(self):
        """Worker run loop exits with code 1 and STOPPED state on schema preflight failure."""
        worker = object.__new__(OutboxWorker)
        worker.session_mgr = Mock()
        worker.session_mgr.check_lock_owned.return_value = False
        worker.session_mgr.connect_and_acquire.side_effect = SchemaVerificationError("Missing table")
        worker._fatal_pgid = None
        worker.state = OutboxWorkerState.STOPPED

        exit_code = worker.run()

        assert exit_code == 1
        assert worker.state == OutboxWorkerState.STOPPED

    @pytest.mark.skipif(
        not os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD"),
        reason="Requires local test database credentials for signal_bot_test",
    )
    def test_preflight_passes_against_signal_bot_test_schema(self):
        """Integration test: Real preflight against signal_bot_test schema succeeds."""
        import mysql.connector
        user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms").strip()
        conn = mysql.connector.connect(
            host="127.0.0.1",
            port=3306,
            database="signal_bot_test",
            user=user,
            password=os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            connection_timeout=5,
            autocommit=False,
        )
        try:
            # Must verify connection is strictly signal_bot_test
            cursor = conn.cursor()
            cursor.execute("SELECT DATABASE()")
            actual_db = cursor.fetchone()[0]
            cursor.close()
            assert actual_db == "signal_bot_test", f"Aborting: Unexpected database {actual_db!r}"

            # Preflight must succeed without exception
            verify_outbox_schema(conn)
        finally:
            conn.close()
