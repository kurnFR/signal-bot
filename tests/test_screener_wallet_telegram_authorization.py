"""Regression tests for Telegram delivery authorization boundaries.

Verifies that Smart Money Screener and Wallet Tracker:
1. Fail closed when RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is unset or not '1'.
2. Make zero Telegram HTTP calls when unauthorized (absent, empty, 0, invalid).
3. Safely fail when bot token or chat ID is missing.
4. Allow mock-only delivery when RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1.
5. Generate and log notifications locally even when Telegram delivery is suppressed.
6. Make zero network calls during startup/initialization.
7. Worker dispatchers preserve durable queued state without burning retry attempts when unauthorized.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

from Screen.screening import SmartMoneySignal, SmartMoneyTelegram
import Screen.screening as screening
from wallet_tracker import worker
from wallet_tracker.telegram import WalletTelegramNotifier


class TestWalletTelegramAuthorization(unittest.TestCase):
    def setUp(self):
        self.alert = {
            "swap_id": 101,
            "chain": "bsc",
            "chain_label": "BNB Chain",
            "wallet": "0x" + "a" * 40,
            "label": "Whale accumulator",
            "pair": "BNB / USDT",
            "side": "buy",
            "usd_value": 55000.0,
            "token_amount": 90.5,
            "price_usd": 607.73,
            "tx_url": "https://bscscan.com/tx/" + "c" * 64,
            "explorer_url": "https://bscscan.com/address/" + "a" * 40,
            "block_time": "2026-11-11 10:00:00",
        }

    def test_unauthorized_by_default_makes_zero_http_calls_and_returns_false(self):
        session = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            notifier = WalletTelegramNotifier(token="123:abc", chat_id="chat-1", session=session)
            self.assertFalse(notifier.delivery_authorized)
            result = notifier.send(self.alert)
            self.assertFalse(result)
            session.post.assert_not_called()

    def test_invalid_authorization_values_fail_closed(self):
        invalid_values = ["", "0", "false", "no", "invalid", "true", "2", "None", " 0 "]
        for val in invalid_values:
            session = MagicMock()
            with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": val}, clear=True):
                notifier = WalletTelegramNotifier(token="123:abc", chat_id="chat-1", session=session)
                self.assertFalse(
                    notifier.delivery_authorized,
                    f"Expected delivery_authorized=False for RETAILBOT2_ALLOW_TELEGRAM_DELIVERY={val!r}",
                )
                self.assertFalse(notifier.send(self.alert))
                session.post.assert_not_called()

    def test_authorized_when_flag_is_strictly_one(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 789}}
        session.post.return_value = response

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            notifier = WalletTelegramNotifier(token="123:abc", chat_id="chat-1", session=session)
            self.assertTrue(notifier.delivery_authorized)
            with patch("wallet_tracker.telegram.time.sleep"):
                result = notifier.send(self.alert)
            self.assertTrue(result)
            session.post.assert_called_once()
            args, kwargs = session.post.call_args
            self.assertIn("/bot123:abc/sendMessage", args[0])
            self.assertEqual(kwargs["json"]["chat_id"], "chat-1")

    def test_missing_token_or_chat_id_fails_safely_even_when_authorized(self):
        session = MagicMock()
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            # Missing token
            notifier_no_token = WalletTelegramNotifier(token="", chat_id="chat-1", session=session)
            self.assertFalse(notifier_no_token.configured)
            self.assertFalse(notifier_no_token.send(self.alert))
            session.post.assert_not_called()

            # Missing chat ID
            notifier_no_chat = WalletTelegramNotifier(token="123:abc", chat_id="", session=session)
            self.assertFalse(notifier_no_chat.configured)
            self.assertFalse(notifier_no_chat.send(self.alert))
            session.post.assert_not_called()

    def test_notification_generated_and_logged_locally_when_unauthorized(self):
        session = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            notifier = WalletTelegramNotifier(token="123:abc", chat_id="chat-1", session=session)
            with self.assertLogs("wallet_tracker.telegram", level="INFO") as captured:
                result = notifier.send(self.alert)
            self.assertFalse(result)
            session.post.assert_not_called()
            output = "\n".join(captured.output)
            self.assertIn("ℹ️ Wallet Telegram alert suppressed (delivery unauthorized):", output)
            self.assertIn("BNB / USDT", output)
            self.assertIn("$55,000.00", output)
            self.assertIn("0x" + "a" * 40, output)

    def test_initialization_makes_zero_network_calls(self):
        with patch("requests.Session.get") as mock_sget, \
             patch("requests.Session.post") as mock_spost, \
             patch("requests.get") as mock_rget, \
             patch("requests.post") as mock_rpost:
            notifier = WalletTelegramNotifier(token="123:abc", chat_id="chat-1")
            self.assertFalse(notifier.delivery_authorized)
            mock_sget.assert_not_called()
            mock_spost.assert_not_called()
            mock_rget.assert_not_called()
            mock_rpost.assert_not_called()


class TestScreenerTelegramAuthorization(unittest.TestCase):
    def setUp(self):
        self.signal = SmartMoneySignal(
            priority_score=85.0,
            symbol="BTCUSDT",
            signal_type="RVOL_SPIKE",
            rvol=3.45,
            volume=500.0,
            quote_volume=45000000.0,
            price=90000.0,
            price_change_pct=1.25,
            volume_velocity=2.1,
            timestamp=1700000000.0,
            candle_time="2026-11-11T10:00:00Z",
            quality_score=88.0,
            direction="BULLISH",
            taker_buy_ratio=0.68,
            htf_alignment=1,
            htf_context="HTF_UPTREND",
        )

    def test_unauthorized_by_default_makes_zero_http_calls_and_returns_false(self):
        session = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            tg = SmartMoneyTelegram(token="123:bot", chat_id="chat-2")
            tg.session = session
            self.assertFalse(tg.delivery_authorized)

            # Early alert
            self.assertFalse(tg.send_early_alert(self.signal))
            session.post.assert_not_called()

            # Operational control message
            self.assertFalse(tg.send("🎛️ Screener ENABLED"))
            session.post.assert_not_called()

    def test_invalid_authorization_values_fail_closed(self):
        invalid_values = ["", "0", "false", "no", "invalid", "true", "2", "None", " 0 "]
        for val in invalid_values:
            session = MagicMock()
            with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": val}, clear=True):
                tg = SmartMoneyTelegram(token="123:bot", chat_id="chat-2")
                tg.session = session
                self.assertFalse(
                    tg.delivery_authorized,
                    f"Expected delivery_authorized=False for RETAILBOT2_ALLOW_TELEGRAM_DELIVERY={val!r}",
                )
                self.assertFalse(tg.send_early_alert(self.signal))
                self.assertFalse(tg.send("Test message"))
                session.post.assert_not_called()

    def test_authorized_when_flag_is_strictly_one(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 456}}
        session.post.return_value = response

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token="123:bot", chat_id="chat-2")
            tg.session = session
            self.assertTrue(tg.delivery_authorized)

            with patch("Screen.screening.time.sleep"):
                self.assertTrue(tg.send_early_alert(self.signal))
                self.assertTrue(tg.send("🎛️ Screener ENABLED"))

            self.assertEqual(session.post.call_count, 2)
            args0, kwargs0 = session.post.call_args_list[0]
            self.assertIn("/bot123:bot/sendMessage", args0[0])
            self.assertEqual(kwargs0["json"]["chat_id"], "chat-2")
            self.assertIn("SMART MONEY EARLY SIGNAL", kwargs0["json"]["text"])

            args1, kwargs1 = session.post.call_args_list[1]
            self.assertIn("/bot123:bot/sendMessage", args1[0])
            self.assertEqual(kwargs1["json"]["text"], "🎛️ Screener ENABLED")

    def test_missing_token_or_chat_id_fails_safely_even_when_authorized(self):
        session = MagicMock()
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            # Missing token
            tg_no_token = SmartMoneyTelegram(token="", chat_id="chat-2")
            tg_no_token.session = session
            self.assertFalse(tg_no_token.send_early_alert(self.signal))
            self.assertFalse(tg_no_token.send("msg"))
            session.post.assert_not_called()

            # Missing chat ID
            tg_no_chat = SmartMoneyTelegram(token="123:bot", chat_id="")
            tg_no_chat.session = session
            self.assertFalse(tg_no_chat.send_early_alert(self.signal))
            self.assertFalse(tg_no_chat.send("msg"))
            session.post.assert_not_called()

    def test_notification_generated_and_logged_locally_even_when_unauthorized(self):
        session = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            tg = SmartMoneyTelegram(token="123:bot", chat_id="chat-2")
            tg.session = session
            with self.assertLogs("SmartMoneyDetector", level="INFO") as captured:
                self.assertFalse(tg.send_early_alert(self.signal))
                self.assertFalse(tg.send("🎛️ Screener state changed to ENABLED"))

            session.post.assert_not_called()
            output = "\n".join(captured.output)
            self.assertIn("ℹ️ Screener early alert suppressed (delivery unauthorized):", output)
            self.assertIn("BTCUSDT", output)
            self.assertIn("RVOL_SPIKE", output)
            self.assertIn("Quality 88", output)
            self.assertIn("ℹ️ Screener message suppressed (delivery unauthorized): 🎛️ Screener state changed to ENABLED", output)

    def test_initialization_makes_zero_network_calls(self):
        with patch("requests.Session.get") as mock_sget, \
             patch("requests.Session.post") as mock_spost, \
             patch("requests.get") as mock_rget, \
             patch("requests.post") as mock_rpost:
            tg = SmartMoneyTelegram(token="123:bot", chat_id="chat-2")
            self.assertFalse(tg.delivery_authorized)
            mock_sget.assert_not_called()
            mock_spost.assert_not_called()
            mock_rget.assert_not_called()
            mock_rpost.assert_not_called()


class TestWorkerDispatchAuthorization(unittest.TestCase):
    @patch.object(worker.store, "get_settings", return_value={"min_trade_usd": 10000})
    @patch.object(worker.store, "queue_new_wallet_alerts", return_value=3)
    @patch.object(worker.store, "get_pending_wallet_alerts")
    def test_wallet_dispatch_preserves_queue_and_pauses_delivery_when_unauthorized(
        self, mock_pending, mock_queue, mock_settings
    ):
        with patch.dict(os.environ, {}, clear=True):
            notifier = WalletTelegramNotifier(token="token", chat_id="chat")
            self.assertFalse(notifier.delivery_authorized)
            sent = worker.dispatch_telegram_alerts(notifier)
            self.assertEqual(sent, 0)
            # Queues new alerts to preserve durable local state
            mock_queue.assert_called_once_with(10000, max_age_minutes=30)
            # Never drains or burns retry attempts while unauthorized
            mock_pending.assert_not_called()

    def test_screener_dispatch_pauses_without_claiming_signals_when_unauthorized(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(screening.telegram, "delivery_authorized", False):
                with patch("Screen.screening.claim_next_pending_signal") as mock_claim:
                    with patch("Screen.screening.time.sleep") as mock_sleep:
                        # Emulate one iteration of dispatch_alerts loop
                        # When state.shutdown_flag becomes True, loop ends
                        screening.state.shutdown_flag = False

                        def stop_after_sleep(sec):
                            screening.state.shutdown_flag = True

                        mock_sleep.side_effect = stop_after_sleep
                        screening.dispatch_alerts()
                        mock_claim.assert_not_called()
                        mock_sleep.assert_called_once()
                        self.assertEqual(mock_sleep.call_args[0][0], 1.0)


if __name__ == "__main__":
    unittest.main()
