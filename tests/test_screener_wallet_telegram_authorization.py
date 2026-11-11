"""Comprehensive regression tests for Telegram delivery authorization boundaries.

Verifies that ALL repository Telegram notification paths:
1. Fail closed when RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is unset or not strictly '1'.
2. Make zero Telegram HTTP calls when unauthorized (absent, empty, 0, invalid, malformed, padded).
3. Safely fail when bot token or chat ID is missing.
4. Allow mock-only delivery when RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1.
5. Generate and log notifications locally even when Telegram delivery is suppressed.
6. Make zero network calls during startup/initialization.
7. Worker dispatchers preserve durable queued state without burning retry attempts when unauthorized.
8. Enforce admin authentication on /api/telegram/test and require global authorization before /getMe.
9. Enforce global authorization on the legacy Smart Money engine (Screen/smart_money_engine.py).
10. Ensure no constructor or setter override bypasses environment authorization.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient

from notifications import telegram_notifier
from Screen.screening import SmartMoneySignal, SmartMoneyTelegram
import Screen.screening as screening
from Screen.smart_money_engine import (
    SmartMoneySignal as LegacySignal,
    SmartMoneyTelegram as LegacyTelegram,
)
from wallet_tracker import worker
from wallet_tracker.telegram import WalletTelegramNotifier
from web.app import app


def _user(role, must_change_password=False):
    return {
        "id": 1,
        "username": "test-user",
        "email": None,
        "role": role,
        "is_active": 1,
        "must_change_password": int(must_change_password),
    }


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
        invalid_values = ["", "0", "false", "no", "invalid", "true", "2", "None", " 0 ", " false ", " -1 "]
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
        invalid_values = ["", "0", "false", "no", "invalid", "true", "2", "None", " 0 ", " false ", " -1 "]
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

    def test_send_early_alert_redacts_token_and_raw_url_on_generic_exception(self):
        token = "987654:secret-screening-token"
        error = requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: /bot{token}/sendMessage"
        )
        session = MagicMock()
        session.post.side_effect = error

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-2")
            tg.session = session
            with patch("Screen.screening.time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                result = tg.send_early_alert(self.signal)

            self.assertFalse(result)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertNotIn("api.telegram.org", output)
            self.assertIn("error_type=ConnectionError", output)
            self.assertIn("http_status=unknown", output)

    def test_send_early_alert_redacts_token_and_response_body_on_http_error(self):
        token = "987654:secret-screening-token"
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = f'{{"ok": false, "error_code": 401, "description": "Unauthorized: invalid token {token}"}}'
        error = requests.HTTPError(
            f"401 Client Error: Unauthorized for url: https://api.telegram.org/bot{token}/sendMessage",
            response=mock_response,
        )
        session = MagicMock()
        session.post.return_value = mock_response
        mock_response.raise_for_status.side_effect = error

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-2")
            tg.session = session
            with patch("Screen.screening.time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                result = tg.send_early_alert(self.signal)

            self.assertFalse(result)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertNotIn("Unauthorized: invalid token", output)
            self.assertIn("error_type=HTTPError", output)
            self.assertIn("http_status=401", output)

    def test_send_redacts_token_and_raw_url_on_generic_exception(self):
        token = "987654:secret-screening-token"
        error = requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: /bot{token}/sendMessage"
        )
        session = MagicMock()
        session.post.side_effect = error

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-2")
            tg.session = session
            with patch("Screen.screening.time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                result = tg.send("🎛️ Screener alert payload")

            self.assertFalse(result)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertNotIn("api.telegram.org", output)
            self.assertIn("error_type=ConnectionError", output)
            self.assertIn("http_status=unknown", output)

    def test_send_redacts_token_and_response_body_on_http_error(self):
        token = "987654:secret-screening-token"
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = f'<html><body>Bad gateway to bot {token}</body></html>'
        error = requests.HTTPError(
            f"502 Server Error: Bad Gateway for url: https://api.telegram.org/bot{token}/sendMessage",
            response=mock_response,
        )
        session = MagicMock()
        session.post.return_value = mock_response
        mock_response.raise_for_status.side_effect = error

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-2")
            tg.session = session
            with patch("Screen.screening.time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                result = tg.send("🎛️ Screener alert payload")

            self.assertFalse(result)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertNotIn("Bad gateway to bot", output)
            self.assertIn("error_type=HTTPError", output)
            self.assertIn("http_status=502", output)

    def test_safe_http_status_validation_and_fallback(self):
        # Valid status codes
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        exc = requests.HTTPError(response=mock_resp)
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "200")

        mock_resp.status_code = 599
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "599")

        # Out-of-range integer status codes
        mock_resp.status_code = 99
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = 600
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        # Non-integer status codes
        mock_resp.status_code = "200"
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = None
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = True
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        # Missing response attribute
        self.assertEqual(SmartMoneyTelegram._safe_http_status(requests.ConnectionError()), "unknown")


class TestLegacySmartMoneyEngineAuthorization(unittest.TestCase):
    def setUp(self):
        self.signal = LegacySignal(
            priority_score=-85.0,
            symbol="ETHUSDT",
            signal_type="STRONG_BUYING",
            rvol=4.2,
            buy_ratio=0.75,
            velocity=2.5,
            price=3200.0,
            price_change_pct=2.1,
            quote_volume=80000000.0,
            timestamp=1700000000.0,
            candle_time="2026-11-11 10:00:00",
        )

    def test_unauthorized_by_default_makes_zero_http_calls_and_returns_false(self):
        session = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            tg = LegacyTelegram(token="123:bot", chat_id="chat-3")
            tg.session = session
            self.assertFalse(tg.delivery_authorized)
            result = tg.send_alert(self.signal)
            self.assertFalse(result)
            session.post.assert_not_called()

    def test_invalid_authorization_values_fail_closed(self):
        invalid_values = ["", "0", "false", "no", "invalid", "true", "2", "None", " 0 ", " false ", " -1 "]
        for val in invalid_values:
            session = MagicMock()
            with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": val}, clear=True):
                tg = LegacyTelegram(token="123:bot", chat_id="chat-3")
                tg.session = session
                self.assertFalse(tg.delivery_authorized)
                self.assertFalse(tg.send_alert(self.signal))
                session.post.assert_not_called()

    def test_authorized_when_flag_is_strictly_one(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 999}}
        session.post.return_value = response

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = LegacyTelegram(token="123:bot", chat_id="chat-3")
            tg.session = session
            self.assertTrue(tg.delivery_authorized)
            with patch("time.sleep"):
                result = tg.send_alert(self.signal)
            self.assertTrue(result)
            session.post.assert_called_once()
            args, kwargs = session.post.call_args
            self.assertIn("/bot123:bot/sendMessage", args[0])
            self.assertEqual(kwargs["json"]["chat_id"], "chat-3")
            self.assertIn("STRONG BUYING PRESSURE", kwargs["json"]["text"])

    def test_missing_token_or_chat_id_fails_safely_even_when_authorized(self):
        session = MagicMock()
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg_no_token = LegacyTelegram(token="", chat_id="chat-3")
            tg_no_token.session = session
            self.assertFalse(tg_no_token.send_alert(self.signal))

            tg_no_chat = LegacyTelegram(token="123:bot", chat_id="")
            tg_no_chat.session = session
            self.assertFalse(tg_no_chat.send_alert(self.signal))
            session.post.assert_not_called()

    def test_notification_generated_and_logged_locally_when_unauthorized(self):
        session = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            tg = LegacyTelegram(token="123:bot", chat_id="chat-3")
            tg.session = session
            with self.assertLogs("SmartMoneyDetector", level="INFO") as captured:
                result = tg.send_alert(self.signal)
            self.assertFalse(result)
            session.post.assert_not_called()
            output = "\n".join(captured.output)
            self.assertIn("ℹ️ Legacy Screener alert suppressed (delivery unauthorized):", output)
            self.assertIn("ETHUSDT", output)
            self.assertIn("STRONG_BUYING", output)

    def test_initialization_makes_zero_network_calls(self):
        with patch("requests.Session.get") as mock_sget, \
             patch("requests.Session.post") as mock_spost, \
             patch("requests.get") as mock_rget, \
             patch("requests.post") as mock_rpost:
            tg = LegacyTelegram(token="123:bot", chat_id="chat-3")
            self.assertFalse(tg.delivery_authorized)
            mock_sget.assert_not_called()
            mock_spost.assert_not_called()
            mock_rget.assert_not_called()
            mock_rpost.assert_not_called()

    def test_send_failure_redacts_token_and_raw_exception_from_logs(self):
        token = "987654:secret-legacy-token"
        error = requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: /bot{token}/sendMessage"
        )
        session = MagicMock()
        session.post.side_effect = error

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = LegacyTelegram(token=token, chat_id="chat-3")
            tg.session = session
            with patch("time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                result = tg.send_alert(self.signal)

            self.assertFalse(result)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertIn("error_type=ConnectionError", output)
            self.assertIn("http_status=unknown", output)

    def test_http_error_logs_safe_status_code_without_token_or_raw_url(self):
        token = "987654:secret-legacy-token"
        mock_response = MagicMock()
        mock_response.status_code = 502
        error = requests.HTTPError(
            f"502 Server Error: Bad Gateway for url: https://api.telegram.org/bot{token}/sendMessage",
            response=mock_response,
        )
        session = MagicMock()
        session.post.return_value = mock_response
        mock_response.raise_for_status.side_effect = error

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = LegacyTelegram(token=token, chat_id="chat-3")
            tg.session = session
            with patch("time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                result = tg.send_alert(self.signal)

            self.assertFalse(result)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertIn("error_type=HTTPError", output)
            self.assertIn("http_status=502", output)

    def test_legacy_safe_http_status_validation_and_fallback(self):
        mock_resp = MagicMock()
        exc = requests.HTTPError(response=mock_resp)

        mock_resp.status_code = 200
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "200")

        mock_resp.status_code = 500
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "500")

        mock_resp.status_code = 99
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = 600
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = "500"
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = None
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = True
        self.assertEqual(LegacyTelegram._safe_http_status(exc), "unknown")

        self.assertEqual(LegacyTelegram._safe_http_status(requests.ConnectionError()), "unknown")


class TestSharedTelegramNotifierAuthorization(unittest.TestCase):
    def test_send_telegram_message_unauthorized_by_default_makes_zero_http_calls(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("requests.post") as mock_post:
            result = telegram_notifier.send_telegram_message("test text")
            self.assertFalse(result["success"])
            self.assertTrue(result.get("suppressed"))
            self.assertIn("Telegram delivery is disabled", result["error"])
            mock_post.assert_not_called()

    def test_send_telegram_message_invalid_flags_fail_closed(self):
        invalid_values = ["", "0", "false", "no", "invalid", "true", "2", "None", " 0 ", " false ", " -1 "]
        for val in invalid_values:
            with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": val}, clear=True), \
                 patch("requests.post") as mock_post:
                result = telegram_notifier.send_telegram_message("test text")
                self.assertFalse(
                    result["success"],
                    f"Expected success=False for RETAILBOT2_ALLOW_TELEGRAM_DELIVERY={val!r}",
                )
                self.assertTrue(result.get("suppressed"))
                mock_post.assert_not_called()

    def test_send_telegram_message_authorized_with_mock_http(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 555}}

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "token-1",
            "TELEGRAM_CHAT_ID": "chat-1",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            result = telegram_notifier.send_telegram_message("hello")
            self.assertTrue(result["success"])
            self.assertEqual(result["message_id"], 555)
            mock_post.assert_called_once()

    def test_send_telegram_message_missing_credentials_fails_safely_when_authorized(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "chat-1",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch("requests.post") as mock_post:
            result = telegram_notifier.send_telegram_message("hello")
            self.assertFalse(result["success"])
            self.assertIn("not configured", result["error"].lower())
            mock_post.assert_not_called()

    def test_signal_and_deployment_alerts_suppressed_when_unauthorized(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("requests.post") as mock_post:
            res_signal = telegram_notifier.send_signal_alert(
                symbol="BTCUSDT", market="spot", timeframe="1h", strategy_name="strat",
                direction="LONG", entry_price=50000.0, stop_loss=49000.0, take_profit=52000.0,
            )
            res_deploy = telegram_notifier.send_deployment_alert(
                symbol="BTCUSDT", market="spot", timeframe="1h", strategy_name="strat",
                allocated_capital=5000.0, risk_per_trade_pct=1.0,
            )
            res_news = telegram_notifier.send_news_signal_alert(
                symbol="BTCUSDT", market="spot", timeframe="1h", direction="LONG",
                entry_price=50000.0, stop_loss=49000.0, take_profit=52000.0, confidence=80,
            )
            res_close = telegram_notifier.send_trade_close_alert(
                symbol="BTCUSDT", strategy_name="strat", direction="LONG",
                entry_price=50000.0, exit_price=52000.0, exit_reason="TP",
                net_return_pct=4.0, r_multiple=2.0, holding_bars=5,
            )

        self.assertFalse(res_signal["success"])
        self.assertTrue(res_signal.get("suppressed"))
        self.assertFalse(res_deploy["success"])
        self.assertTrue(res_deploy.get("suppressed"))
        self.assertFalse(res_news["success"])
        self.assertTrue(res_news.get("suppressed"))
        self.assertFalse(res_close["success"])
        self.assertTrue(res_close.get("suppressed"))
        mock_post.assert_not_called()


class TestTelegramConnectionTestEndpointAuthorization(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def _post(self, path, role, must_change_password=False, **kwargs):
        with patch("web.app.authenticate_request", return_value=_user(role, must_change_password)), \
             patch("web.auth.authenticate_request", return_value=_user(role, must_change_password)):
            return self.client.post(path, **kwargs)

    def test_direct_connection_check_unauthorized_by_default(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(telegram_notifier.requests, "get") as mock_get, \
             patch.object(telegram_notifier.requests, "post") as mock_post:
            result = telegram_notifier.test_telegram_connection()
            self.assertFalse(result["success"])
            self.assertFalse(result.get("delivery_authorized"))
            self.assertIn("Telegram delivery is disabled", result["error"])
            mock_get.assert_not_called()
            mock_post.assert_not_called()

    def test_direct_connection_check_authorized_calls_getme_and_send(self):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"ok": True, "result": {"username": "bot_user", "first_name": "TestBot"}}

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"ok": True, "result": {"message_id": 111}}

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "bot-tok",
            "TELEGRAM_CHAT_ID": "chat-id",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get", return_value=mock_get_resp) as mock_get, \
             patch.object(telegram_notifier.requests, "post", return_value=mock_post_resp) as mock_post:
            result = telegram_notifier.test_telegram_connection()
            self.assertTrue(result["success"])
            mock_get.assert_called_once()
            mock_post.assert_called_once()

    def test_api_test_endpoint_denies_unauthenticated_and_non_admin(self):
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True), \
             patch.object(telegram_notifier.requests, "get") as mock_get, \
             patch.object(telegram_notifier.requests, "post") as mock_post:
            anon = self.client.post("/api/telegram/test", json={})
            trader = self._post("/api/telegram/test", "trader", json={})
            viewer = self._post("/api/telegram/test", "viewer", json={})

        self.assertEqual(anon.status_code, 401)
        self.assertEqual(trader.status_code, 403)
        self.assertEqual(viewer.status_code, 403)
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    def test_api_test_endpoint_admin_unauthorized_fails_closed_with_zero_http_calls(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(telegram_notifier.requests, "get") as mock_get, \
             patch.object(telegram_notifier.requests, "post") as mock_post:
            response = self._post("/api/telegram/test", "admin", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Telegram delivery is disabled", response.json()["detail"])
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    def test_api_test_endpoint_admin_authorized_dispatches_mock_only(self):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"ok": True, "result": {"username": "admin_bot", "first_name": "AdminBot"}}

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"ok": True, "result": {"message_id": 222}}

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "bot-tok",
            "TELEGRAM_CHAT_ID": "chat-id",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get", return_value=mock_get_resp) as mock_get, \
             patch.object(telegram_notifier.requests, "post", return_value=mock_post_resp) as mock_post:
            response = self._post("/api/telegram/test", "admin", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_get.assert_called_once()
        mock_post.assert_called_once()


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
            self.assertFalse(screening.telegram.delivery_authorized)
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
