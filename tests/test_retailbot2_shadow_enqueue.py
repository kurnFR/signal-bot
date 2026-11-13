"""Comprehensive unit and integration tests for RetailBot2 shadow enqueue decoupling (WO 71778).

Validates:
A. Pure payload builders (build_entry_alert_payload, build_exit_alert_payload).
B. Test-only shadow enqueue authorization (is_shadow_outbox_enqueue_authorized).
C. Outbox persistence atomicity without trade mutation (DatabaseManager.enqueue_trade_signal_event).
D. Full integration with RetailDeathTrapBot and OutboxWorker on signal_bot_test.
"""

import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mysql.connector

from paper.outbox_worker import OutboxWorker
from paper.retailbot2 import (
    BotConfig,
    DatabaseManager,
    RetailDeathTrapBot,
    SignalMode,
    build_entry_alert_payload,
    build_exit_alert_payload,
    format_entry_message,
    format_exit_message,
    is_shadow_outbox_enqueue_authorized,
)

logger = logging.getLogger("test.shadow_enqueue")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A: Pure Payload Builder Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPayloadBuilders(unittest.TestCase):
    def setUp(self):
        self.valid_signal = {
            "symbol": "BTC/USDT",
            "side": "LONG",
            "entry": 65000.0,
            "stop": 64000.0,
            "target": 67000.0,
            "rr": 2.0,
            "candle_close_time": "2026-11-13 14:00:00",
            "strategies_triggered": ["BB_REV", "RSI_OVERSOLD"],
            "strategy": "BB_REV",
            "confluence_score": 2,
            "reason": "Oversold bounce at support",
        }
        self.valid_trade = {
            "id": 42,
            "symbol": "ETH/USDT",
            "side": "SHORT",
            "strategy": "MA_CROSS",
            "mode": "shadow",
            "entry": 3500.0,
            "stop": 3600.0,
            "target": 3300.0,
            "units": 1.5,
        }

    def test_build_entry_alert_payload_success(self):
        payload = build_entry_alert_payload(
            signal=self.valid_signal,
            units=0.5,
            risk_amt=500.0,
            risk_pct=2.0,
            mode="shadow",
        )
        self.assertEqual(payload["mode"], "shadow")
        self.assertEqual(payload["units"], 0.5)
        self.assertEqual(payload["risk_amt"], 500.0)
        self.assertEqual(payload["risk_pct"], 2.0)
        sig = payload["signal"]
        self.assertEqual(sig["symbol"], "BTC/USDT")
        self.assertEqual(sig["side"], "LONG")
        self.assertEqual(sig["entry"], 65000.0)
        self.assertEqual(sig["stop"], 64000.0)
        self.assertEqual(sig["target"], 67000.0)
        self.assertEqual(sig["rr"], 2.0)
        self.assertEqual(sig["confluence_score"], 2)
        # JSON serializable
        serialized = json.dumps(payload)
        self.assertIn("BTC/USDT", serialized)

    def test_build_entry_alert_payload_immutability(self):
        original_copy = dict(self.valid_signal)
        _ = build_entry_alert_payload(
            signal=self.valid_signal,
            units=1.0,
            risk_amt=100.0,
            risk_pct=1.0,
        )
        self.assertEqual(self.valid_signal, original_copy)

    def test_build_entry_alert_payload_missing_required_fields(self):
        for field in ("symbol", "side", "entry", "stop", "target"):
            bad_signal = dict(self.valid_signal)
            del bad_signal[field]
            with self.assertRaises(ValueError) as ctx:
                build_entry_alert_payload(bad_signal, 1.0, 100.0, 1.0)
            self.assertIn("Missing required signal fields", str(ctx.exception))

    def test_build_entry_alert_payload_invalid_numbers(self):
        bad_cases = [
            ({"entry": -10.0}, 1.0, 100.0, 1.0, "Prices must be positive"),
            ({"stop": 0.0}, 1.0, 100.0, 1.0, "Prices must be positive"),
            ({"target": -1.0}, 1.0, 100.0, 1.0, "Prices must be positive"),
            ({}, 0.0, 100.0, 1.0, "Units must be positive"),
            ({}, -5.0, 100.0, 1.0, "Units must be positive"),
            ({}, 1.0, -50.0, 1.0, "Risk amounts must be non-negative"),
            ({}, 1.0, 50.0, -2.0, "Risk amounts must be non-negative"),
        ]
        for signal_override, units, risk_amt, risk_pct, expected_err in bad_cases:
            test_signal = dict(self.valid_signal)
            test_signal.update(signal_override)
            with self.assertRaises(ValueError) as ctx:
                build_entry_alert_payload(test_signal, units, risk_amt, risk_pct)
            self.assertIn(expected_err, str(ctx.exception))

    def test_build_entry_alert_payload_invalid_side(self):
        bad_signal = dict(self.valid_signal, side="SIDEWAYS")
        with self.assertRaises(ValueError) as ctx:
            build_entry_alert_payload(bad_signal, 1.0, 100.0, 1.0)
        self.assertIn("Invalid signal side", str(ctx.exception))

    def test_build_entry_alert_payload_formatter_compatibility(self):
        payload = build_entry_alert_payload(
            signal=self.valid_signal,
            units=0.5,
            risk_amt=500.0,
            risk_pct=2.0,
            mode="shadow",
        )
        formatted = format_entry_message(payload, trade_id=0)
        self.assertIn("🟢 LONG", formatted)
        self.assertIn("👁️ SHADOW #0", formatted)
        self.assertIn("BTC/USDT", formatted)
        self.assertIn("$65000.000000", formatted)
        self.assertIn("BB_REV", formatted)

    def test_build_exit_alert_payload_success(self):
        payload = build_exit_alert_payload(
            trade=self.valid_trade,
            exit_price=3350.0,
            reason="TAKE_PROFIT",
            pnl=225.0,
            pnl_pct=6.43,
        )
        self.assertEqual(payload["exit_price"], 3350.0)
        self.assertEqual(payload["reason"], "TAKE_PROFIT")
        self.assertEqual(payload["pnl"], 225.0)
        self.assertEqual(payload["pnl_pct"], 6.43)
        tr = payload["trade"]
        self.assertEqual(tr["id"], 42)
        self.assertEqual(tr["symbol"], "ETH/USDT")
        self.assertEqual(tr["side"], "SHORT")
        self.assertEqual(tr["strategy"], "MA_CROSS")

    def test_build_exit_alert_payload_immutability(self):
        original_copy = dict(self.valid_trade)
        _ = build_exit_alert_payload(
            trade=self.valid_trade,
            exit_price=3400.0,
            reason="STOP_LOSS",
            pnl=-150.0,
            pnl_pct=-4.2,
        )
        self.assertEqual(self.valid_trade, original_copy)

    def test_build_exit_alert_payload_invalid_values(self):
        with self.assertRaises(ValueError):
            build_exit_alert_payload(self.valid_trade, exit_price=-10.0, reason="TP", pnl=10.0, pnl_pct=1.0)
        with self.assertRaises(ValueError):
            build_exit_alert_payload(self.valid_trade, exit_price=100.0, reason="", pnl=10.0, pnl_pct=1.0)
        bad_trade = dict(self.valid_trade)
        del bad_trade["symbol"]
        with self.assertRaises(ValueError):
            build_exit_alert_payload(bad_trade, exit_price=100.0, reason="TP", pnl=10.0, pnl_pct=1.0)

    def test_build_exit_alert_payload_formatter_compatibility(self):
        payload = build_exit_alert_payload(
            trade=self.valid_trade,
            exit_price=3300.0,
            reason="TAKE_PROFIT",
            pnl=300.0,
            pnl_pct=8.57,
        )
        formatted = format_exit_message(payload)
        self.assertIn("🎯✅ <b>EXIT [SHADOW] #42</b>", formatted)
        self.assertIn("ETH/USDT", formatted)
        self.assertIn("$+300.00", formatted)
        self.assertIn("$3300.000000", formatted)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B: Shadow Enqueue Authorization Gate Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestShadowEnqueueAuthorization(unittest.TestCase):
    def setUp(self):
        self.config_test = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
        self.config_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)
        self.mock_conn_test = MagicMock()
        cursor_test = MagicMock()
        cursor_test.fetchone.return_value = ("signal_bot_test",)
        self.mock_conn_test.cursor.return_value = cursor_test

        self.mock_conn_prod = MagicMock()
        cursor_prod = MagicMock()
        cursor_prod.fetchone.return_value = ("crypto_signals",)
        self.mock_conn_prod.cursor.return_value = cursor_prod

    def test_flag_unset_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(self.config_test, conn=self.mock_conn_test)
            )

    def test_flag_malformed_rejected(self):
        for bad_val in ("0", "true", "2", "yes", "enabled", ""):
            with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": bad_val}):
                self.assertFalse(
                    is_shadow_outbox_enqueue_authorized(self.config_test, conn=self.mock_conn_test)
                )

    def test_flag_enabled_signal_bot_test_accepted(self):
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            self.assertTrue(
                is_shadow_outbox_enqueue_authorized(self.config_test, conn=self.mock_conn_test)
            )

    def test_flag_enabled_crypto_signals_strictly_rejected(self):
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            # Production database in config must be rejected
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(self.config_prod, conn=self.mock_conn_test)
            )
            # Production database in active connection must be rejected
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(self.config_test, conn=self.mock_conn_prod)
            )

    def test_database_mismatch_rejected(self):
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(
                    self.config_test, conn=self.mock_conn_test, database="other_db"
                )
            )

    def test_missing_connection_rejected(self):
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(self.config_test, conn=None)
            )

    def test_missing_configuration_rejected(self):
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(config=None, conn=self.mock_conn_test)
            )

    def test_observation_mode_rejected(self):
        obs_config = SimpleNamespace(db_name="signal_bot_test", observation_only=True)
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            self.assertFalse(
                is_shadow_outbox_enqueue_authorized(obs_config, conn=self.mock_conn_test)
            )

    def test_independent_of_trading_authorization(self):
        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1", "RETAILBOT2_ALLOW_TRADING": "0"}):
            # Must succeed even when trading is strictly disabled
            self.assertTrue(
                is_shadow_outbox_enqueue_authorized(self.config_test, conn=self.mock_conn_test)
            )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C & D: Persistence & Integration Tests on signal_bot_test
# ═══════════════════════════════════════════════════════════════════════════════

TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"

def _get_test_db_connection():
    user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip()
    pwd = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()
    if not user or not pwd:
        return None
    try:
        conn = mysql.connector.connect(
            host=TEST_DB_HOST,
            port=TEST_DB_PORT,
            database=TEST_DB_NAME,
            user=user,
            password=pwd,
            connection_timeout=5,
            autocommit=False,
        )
        cur = conn.cursor()
        cur.execute("SELECT DATABASE()")
        actual = cur.fetchone()[0]
        cur.close()
        if actual != TEST_DB_NAME:
            conn.close()
            raise AssertionError(f"Refusing unexpected test database {actual!r}")
        return conn
    except Exception as exc:
        logger.warning("Could not connect to test db: %s", exc)
        return None


class TestShadowEnqueuePersistence(unittest.TestCase):
    def setUp(self):
        self.conn = _get_test_db_connection()
        if not self.conn:
            self.skipTest("Isolated test database 'signal_bot_test' unavailable")
        self.created_keys = []

    def tearDown(self):
        if self.conn and self.created_keys:
            try:
                cur = self.conn.cursor()
                for key in self.created_keys:
                    cur.execute("DELETE FROM rdt_telegram_outbox WHERE event_key = %s;", (key,))
                self.conn.commit()
                cur.close()
            except Exception:
                pass
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def test_shadow_enqueue_inserts_outbox_row_without_trade_mutation(self):
        # 1. Check initial counts of rdt_trades and rdt_strategy_stats
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_trades;")
        initial_trades_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM rdt_strategy_stats;")
        initial_stats_count = cur.fetchone()[0]
        cur.close()

        # 2. Setup DatabaseManager pointing to signal_bot_test
        db = object.__new__(DatabaseManager)
        db.logger = logger
        db.get_conn = lambda: _get_test_db_connection()
        db.config = SimpleNamespace(db_name="signal_bot_test", observation_only=False, allow_trading=False)

        test_key = "retailbot2:shadow:entry:TEST_SOL_BB_REV_LONG_20261113"
        self.created_keys.append(test_key)
        payload = {
            "signal": {"symbol": "SOL/USDT", "side": "LONG", "entry": 150.0, "stop": 140.0, "target": 170.0},
            "units": 2.0,
            "risk_amt": 20.0,
            "risk_pct": 2.0,
            "mode": "shadow",
        }

        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            ok = db.enqueue_trade_signal_event(
                event_key=test_key,
                event_type="entry",
                payload=payload,
                trade_id=0,
            )
        self.assertTrue(ok, "enqueue_trade_signal_event should return True on signal_bot_test")
        self.conn.commit()

        # 3. Verify outbox row exists with trade_id=0 and status='pending'
        cur = self.conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM rdt_telegram_outbox WHERE event_key = %s;", (test_key,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["event_key"], test_key)
        self.assertEqual(row["trade_id"], 0)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["event_type"], "entry")
        parsed_payload = json.loads(row["payload_json"])
        self.assertEqual(parsed_payload["signal"]["symbol"], "SOL/USDT")

        # 4. Verify ZERO trade rows were inserted or modified
        cur.execute("SELECT COUNT(*) FROM rdt_trades;")
        final_trades_count = cur.fetchone()["COUNT(*)"]
        self.assertEqual(final_trades_count, initial_trades_count, "Zero trade rows must be created by shadow enqueue")

        cur.execute("SELECT COUNT(*) FROM rdt_strategy_stats;")
        final_stats_count = cur.fetchone()["COUNT(*)"]
        self.assertEqual(final_stats_count, initial_stats_count, "Zero strategy stats rows must be mutated")
        cur.close()

    def test_shadow_enqueue_idempotency_duplicate_key(self):
        db = object.__new__(DatabaseManager)
        db.logger = logger
        db.get_conn = lambda: _get_test_db_connection()
        db.config = SimpleNamespace(db_name="signal_bot_test", observation_only=False, allow_trading=False)

        test_key = "retailbot2:shadow:entry:IDEMPOTENT_TEST_KEY"
        self.created_keys.append(test_key)
        payload = {"signal": {"symbol": "ADA/USDT", "side": "SHORT", "entry": 0.5, "stop": 0.55, "target": 0.4}}

        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            ok1 = db.enqueue_trade_signal_event(test_key, "entry", payload, trade_id=0)
            ok2 = db.enqueue_trade_signal_event(test_key, "entry", payload, trade_id=0)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rdt_telegram_outbox WHERE event_key = %s;", (test_key,))
        count = cur.fetchone()[0]
        cur.close()
        self.assertEqual(count, 1, "Duplicate event_key must not create multiple pending rows")

    def test_mock_worker_claims_and_delivers_shadow_outbox_event(self):
        # 1. Enqueue an event using shadow enqueue
        db = object.__new__(DatabaseManager)
        db.logger = logger
        db.get_conn = lambda: _get_test_db_connection()
        db.config = SimpleNamespace(db_name="signal_bot_test", observation_only=False, allow_trading=False)

        test_key = "retailbot2:shadow:entry:WORKER_DELIVERY_TEST"
        self.created_keys.append(test_key)
        payload = build_entry_alert_payload(
            signal={
                "symbol": "BTC/USDT", "side": "LONG", "entry": 60000.0, "stop": 59000.0, "target": 62000.0,
                "strategy": "BB_REV", "rr": 2.0, "candle_close_time": "2026-11-13 14:30:00",
            },
            units=0.1,
            risk_amt=100.0,
            risk_pct=1.0,
            mode="shadow",
        )

        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            ok = db.enqueue_trade_signal_event(test_key, "entry", payload, trade_id=0)
        self.assertTrue(ok)

        # 2. Setup OutboxWorker with mock transport response (HTTP 200 ok=True)
        worker_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "database": TEST_DB_NAME,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD"),
        }
        mock_response = {
            "status_code": 200,
            "body": json.dumps({"ok": True, "result": {"message_id": 9999}}),
        }
        worker = OutboxWorker(
            db_config=worker_cfg,
            telegram_token="mock_token",
            telegram_chat_id="mock_chat",
            allowed_databases=("signal_bot_test",),
            poll_interval_sec=0.1,
            mock_transport_response=mock_response,
            observation_mode=False,
        )
        self.assertTrue(worker.session_mgr.connect_and_acquire())

        # 3. Worker processes the event
        processed = worker.process_one_event()
        self.assertTrue(processed, "OutboxWorker should claim and process the shadow entry event")
        worker.session_mgr.close()

        # 4. Verify event in DB is now 'sent'
        cur = self.conn.cursor(dictionary=True)
        cur.execute("SELECT status, attempts, sent_at FROM rdt_telegram_outbox WHERE event_key = %s;", (test_key,))
        row = cur.fetchone()
        cur.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "sent", "Shadow event should be marked 'sent' on delivery")
        self.assertEqual(row["attempts"], 1)
        self.assertIsNotNone(row["sent_at"])


class TestRetailDeathTrapBotShadowIntegration(unittest.TestCase):
    def setUp(self):
        self.conn = _get_test_db_connection()
        if not self.conn:
            self.skipTest("Isolated test database 'signal_bot_test' unavailable")
        self.created_keys = []

    def tearDown(self):
        if self.conn and self.created_keys:
            try:
                cur = self.conn.cursor()
                for key in self.created_keys:
                    cur.execute("DELETE FROM rdt_telegram_outbox WHERE event_key = %s;", (key,))
                self.conn.commit()
                cur.close()
            except Exception:
                pass
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def test_enqueue_shadow_entry_signal_lifecycle_gating(self):
        bot_cfg = BotConfig(
            db_name="signal_bot_test",
            allow_trading=False,
            observation_only=False,
        )
        bot = object.__new__(RetailDeathTrapBot)
        bot.logger = logger
        bot.config = bot_cfg
        bot.mode = SignalMode.SHADOW
        bot.equity_shadow = 10000.0
        bot.equity_inverse = 10000.0
        bot.observation_only = False
        bot.db = object.__new__(DatabaseManager)
        bot.db.logger = logger
        bot.db.config = bot_cfg
        bot.db.get_conn = lambda: _get_test_db_connection()

        signal = {
            "symbol": "BTC/USDT",
            "side": "LONG",
            "entry": 65000.0,
            "stop": 64000.0,
            "target": 67000.0,
            "strategy": "BB_REV",
            "candle_close_time": "2026-11-13 14:00:00",
        }
        expected_key = "retailbot2:shadow:entry:BTC/USDT_BB_REV_shadow_LONG_2026-11-13_14-00-00"
        self.created_keys.append(expected_key)

        with patch.dict(os.environ, {"RETAILBOT2_SHADOW_ENQUEUE_ENABLED": "1"}):
            # 1. When telegram_enabled is False in lifecycle -> False, no row
            with patch.object(bot, "_get_strategy_lifecycle", return_value={"telegram_enabled": False, "paper_enabled": True}):
                ok = bot.enqueue_shadow_entry_signal(signal, mode="shadow")
                self.assertFalse(ok)

            # 2. When paper_enabled and evaluation_enabled are False -> False, no row
            with patch.object(bot, "_get_strategy_lifecycle", return_value={"telegram_enabled": True, "paper_enabled": False, "evaluation_enabled": False}):
                ok = bot.enqueue_shadow_entry_signal(signal, mode="shadow")
                self.assertFalse(ok)

            # 3. When eligible -> True, outbox row created
            with patch.object(bot, "_get_strategy_lifecycle", return_value={"telegram_enabled": True, "paper_enabled": True}):
                ok = bot.enqueue_shadow_entry_signal(signal, mode="shadow")
                self.assertTrue(ok)

        # Confirm outbox row exists in signal_bot_test
        self.conn.commit()
        cur = self.conn.cursor()
        cur.execute("SELECT status, trade_id FROM rdt_telegram_outbox WHERE event_key = %s;", (expected_key,))
        row = cur.fetchone()
        cur.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "pending")
        self.assertEqual(row[1], 0)


class TestShadowEnqueueDocumentation(unittest.TestCase):
    def test_release_gates_doc_shadow_enqueue_section(self):
        doc_path = os.path.join(
            os.path.dirname(__file__), "..", "docs", "RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md"
        )
        self.assertTrue(os.path.isfile(doc_path), "Release gates doc must exist")
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Section 4.4 presence
        self.assertIn("### 4.4 Shadow enqueue decoupling, payload builders, and testing architecture", content)

        # Difference between persisted trade notifications and shadow signal notifications
        self.assertIn("Persisted Trade Notifications", content)
        self.assertIn("Shadow Signal Notifications (Decoupled)", content)
        self.assertIn("trade_id = 0", content)

        # Authorization flag and database restriction
        self.assertIn("RETAILBOT2_SHADOW_ENQUEUE_ENABLED=1", content)
        self.assertIn("signal_bot_test", content)
        self.assertIn("crypto_signals", content)

        # Event key namespaces
        self.assertIn("retailbot2:shadow:entry:{symbol}_{strategy}_{mode}_{side}_{candle_close}", content)
        self.assertIn("retailbot2:shadow:exit:{symbol}_{strategy}_{mode}_{side}_{candle_close}", content)

        # Idempotency guarantee
        self.assertIn("ON DUPLICATE KEY UPDATE", content)

        # Exit correlation limitation
        self.assertIn("Exit Correlation Limitations", content)

        # Production boundary warning
        self.assertIn("Production Boundary Warning", content)


if __name__ == "__main__":
    unittest.main()
