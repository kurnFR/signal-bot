"""Targeted regression tests for Work Order 71695:
- Blocker A: Operational Telegram delivery authorization and fail-closed routing.
- Blocker B: Outbox worker startup schema preflight and advisory lock protection.

Tested against local mock environments and the isolated test schema signal_bot_test.
Never connects to, inspects, or modifies the production database crypto_signals.
"""

import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from paper.outbox_worker import (
    OUTBOX_LOCK_NAME,
    REQUIRED_OUTBOX_COLUMNS,
    OutboxSessionManager,
    OutboxWorker,
    OutboxWorkerState,
    SchemaVerificationError,
    verify_outbox_schema,
)
from paper.retailbot2 import (
    DatabaseManager,
    RetailDeathTrapBot,
    TelegramAlert,
)


# ==============================================================================
# BLOCKER A: OPERATIONAL TELEGRAM DELIVERY AUTHORIZATION
# ==============================================================================

class TestOperationalTelegramAuthorization:
    """Verifies that no operational or direct Telegram delivery bypasses authorization."""

    def test_direct_delivery_disabled_by_default(self, monkeypatch):
        """Without RETAILBOT2_ALLOW_DIRECT_TELEGRAM=1, direct delivery is unauthorized and fails closed."""
        monkeypatch.delenv("RETAILBOT2_ALLOW_DIRECT_TELEGRAM", raising=False)
        with patch("paper.retailbot2.requests.get") as mock_get:
            tg = TelegramAlert("123456:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "123456789")
            # Network getMe must never be called when unauthorized
            mock_get.assert_not_called()

        assert tg.direct_authorized is False
        assert tg.enabled is False

        # Direct send returns False with zero HTTP post requests
        with patch("paper.retailbot2.requests.post") as mock_post:
            assert tg.send("Test message", retries=0) is False
            mock_post.assert_not_called()

    def test_direct_delivery_authorized_via_parameter(self):
        """Passing allow_direct=True authorizes direct delivery and enables getMe validation."""
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"id": 123456, "is_bot": True, "first_name": "TestBot"}}
        with patch("paper.retailbot2.requests.get", return_value=response) as mock_get:
            tg = TelegramAlert(
                "123456:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "123456789",
                allow_direct=True,
            )
            mock_get.assert_called_once()
            assert tg.direct_authorized is True
            assert tg.enabled is True

    def test_direct_delivery_authorized_via_env(self, monkeypatch):
        """Setting RETAILBOT2_ALLOW_DIRECT_TELEGRAM=1 authorizes direct delivery."""
        monkeypatch.setenv("RETAILBOT2_ALLOW_DIRECT_TELEGRAM", "1")
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"id": 123456, "is_bot": True, "first_name": "TestBot"}}
        with patch("paper.retailbot2.requests.get", return_value=response):
            tg = TelegramAlert(
                "123456:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "123456789",
            )
            assert tg.direct_authorized is True
            assert tg.enabled is True

    def test_notify_operational_prefers_outbox_queue(self):
        """Operational notifications are enqueued to rdt_telegram_outbox as durable events."""
        bot = object.__new__(RetailDeathTrapBot)
        bot.logger = logging.getLogger("test.retailbot2")
        bot.db = Mock()
        bot.db.enqueue_telegram_outbox_event.return_value = True
        bot.tg = Mock()
        bot.tg.direct_authorized = False
        bot.tg.enabled = False

        msg = "🤖 Bot started successfully"
        result = bot.notify_operational(msg, event_key="test:startup:1")

        assert result is True
        bot.db.enqueue_telegram_outbox_event.assert_called_once_with(
            event_key="test:startup:1",
            event_type="operational",
            payload={"text": msg},
            trade_id=0,
        )
        bot.tg.send.assert_not_called()

    def test_notify_operational_falls_back_to_direct_only_when_authorized(self):
        """When outbox enqueue fails, direct send is used ONLY if direct delivery is authorized."""
        bot = object.__new__(RetailDeathTrapBot)
        bot.logger = logging.getLogger("test.retailbot2")
        bot.db = Mock()
        bot.db.enqueue_telegram_outbox_event.return_value = False  # Enqueue fails
        bot.tg = Mock()
        bot.tg.direct_authorized = True
        bot.tg.enabled = True
        bot.tg.send.return_value = True

        msg = "⚠️ Operational alert"
        assert bot.notify_operational(msg, event_key="test:op:2") is True
        bot.tg.send.assert_called_once_with(msg)

    def test_notify_operational_fails_closed_when_unauthorized(self):
        """When outbox enqueue fails and direct delivery is unauthorized, notification fails closed."""
        bot = object.__new__(RetailDeathTrapBot)
        bot.logger = logging.getLogger("test.retailbot2")
        bot.db = Mock()
        bot.db.enqueue_telegram_outbox_event.return_value = False  # Enqueue fails
        bot.tg = Mock()
        bot.tg.direct_authorized = False
        bot.tg.enabled = False

        msg = "🚨 Operational emergency"
        assert bot.notify_operational(msg, event_key="test:op:3") is False
        bot.tg.send.assert_not_called()

    def test_format_stats_produces_valid_text(self):
        """TelegramAlert.format_stats formats performance metrics into clean HTML text."""
        stats = {
            "SR_TRAP": {"total": 5, "wins": 4, "losses": 1, "net_pnl": 350.0},
            "BB_SQUEEZE": {"total": 5, "wins": 3, "losses": 2, "net_pnl": 150.0},
        }
        text = TelegramAlert.format_stats(stats, equity_shadow=10500.0, equity_inverse=9800.0)
        assert "PERFORMANCE REPORT" in text
        assert "Shadow Equity:  <code>$10,500.00</code>" in text
        assert "Inverse Equity: <code>$9,800.00</code>" in text
        assert "SR_TRAP" in text
        assert "BB_SQUEEZE" in text
        assert "WR=80% Net=$+350" in text

    def test_startup_message_routes_through_notify_operational(self):
        """_startup_msg uses notify_operational instead of direct send."""
        bot = object.__new__(RetailDeathTrapBot)
        bot.logger = logging.getLogger("test.retailbot2")
        bot.config = SimpleNamespace(
            symbols=["BTC/USDT"],
            leverage=5,
            initial_capital=10000.0,
            max_open_trades_per_mode=3,
        )
        bot.symbols = ["BTC/USDT"]
        bot.mode = SimpleNamespace(value="shadow")
        bot.notify_operational = Mock(return_value=True)

        bot._startup_msg()
        bot.notify_operational.assert_called_once()
        msg_arg = bot.notify_operational.call_args[0][0]
        assert "RETAIL DEATH TRAP BOT v2.1" in msg_arg
        assert "Symbols: 1" in msg_arg

    def test_control_toggle_routes_through_notify_operational(self):
        """_reload_control uses notify_operational when trading is toggled."""
        bot = object.__new__(RetailDeathTrapBot)
        bot.logger = logging.getLogger("test.retailbot2")
        bot.trading_enabled = True
        bot._last_control_reload = 0.0
        bot._control_reload_interval = 10.0
        bot._tunable_fields = set()
        bot.db = Mock()
        bot.db.get_bot_control.return_value = {"enabled": False, "overrides": {}}
        bot.notify_operational = Mock(return_value=True)

        bot._reload_control(force=True)
        assert bot.trading_enabled is False
        bot.notify_operational.assert_called_once()
        msg_arg = bot.notify_operational.call_args[0][0]
        assert "PAUSED" in msg_arg

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
# BLOCKER B: OUTBOX WORKER STARTUP SCHEMA PREFLIGHT
# ==============================================================================

class TestOutboxWorkerSchemaPreflight:
    """Verifies that the standalone worker validates schema before advisory lock acquisition."""

    def test_required_outbox_columns_specification(self):
        """Ensure all 13 required columns are formally specified."""
        expected = {
            "id", "event_key", "trade_id", "event_type", "payload_json",
            "status", "attempts", "next_attempt_at", "locked_until",
            "claim_token", "last_error", "created_at", "sent_at",
        }
        assert set(REQUIRED_OUTBOX_COLUMNS) == expected

    def test_preflight_fails_when_table_missing(self):
        """Missing rdt_telegram_outbox table raises SchemaVerificationError immediately."""
        cursor = Mock()
        cursor.fetchone.return_value = None  # Table not found
        cursor.fetchall.return_value = []
        conn = Mock()
        conn.cursor.return_value = cursor

        with pytest.raises(SchemaVerificationError) as exc_info:
            verify_outbox_schema(conn)

        assert "Required outbox table 'rdt_telegram_outbox' does not exist" in str(exc_info.value)
        cursor.close.assert_called_once()

    def test_preflight_fails_when_claim_token_column_missing(self):
        """Missing claim_token column raises SchemaVerificationError."""
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

        assert "claim_token" in str(exc_info.value)
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
        # Mock database identity check passes, but schema check fails
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
