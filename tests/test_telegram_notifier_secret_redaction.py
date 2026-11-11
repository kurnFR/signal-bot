"""Telegram Bot API errors must not expose token-bearing request URLs."""
import unittest
from unittest.mock import patch

import requests

from notifications import telegram_notifier


class TestTelegramTokenRedaction(unittest.TestCase):
    def test_send_failure_redacts_token_from_result_and_logs(self):
        token = "123456:secret-bot-token"
        error = requests.ConnectionError(
            f"failed to connect to https://api.telegram.org/bot{token}/sendMessage"
        )
        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "post", side_effect=error), \
             patch("time.sleep"), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = telegram_notifier.send_telegram_message("test", max_retries=1)

        self.assertFalse(result["success"])
        self.assertNotIn(token, str(result))
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertNotIn("/bot" + token, "\n".join(captured.output))

    def test_connection_check_redacts_token_from_result_and_logs(self):
        token = "123456:secret-bot-token"
        error = requests.ConnectionError(
            f"failed to connect to https://api.telegram.org/bot{token}/getMe"
        )
        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get", side_effect=error), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = telegram_notifier.test_telegram_connection()

        self.assertFalse(result["success"])
        self.assertNotIn(token, str(result))
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertNotIn("/bot" + token, "\n".join(captured.output))

    def test_unauthorized_send_and_connection_suppresses_requests(self):
        token = "123456:secret-bot-token"
        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get") as mock_get, \
             patch.object(telegram_notifier.requests, "post") as mock_post:
            res_send = telegram_notifier.send_telegram_message("test msg")
            res_conn = telegram_notifier.test_telegram_connection()

        self.assertFalse(res_send["success"])
        self.assertTrue(res_send.get("suppressed"))
        self.assertFalse(res_conn["success"])
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    def test_send_unsuccessful_response_redacts_description_and_token(self):
        token = "123456:secret-bot-token"
        mock_res = requests.Response()
        mock_res.status_code = 400
        raw_desc = f"Bad Request: invalid token {token} in url https://api.telegram.org/bot{token}/sendMessage"
        mock_res._content = f'{{"ok": false, "error_code": 400, "description": "{raw_desc}"}}'.encode("utf-8")

        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "post", return_value=mock_res), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = telegram_notifier.send_telegram_message("test message", max_retries=1)

        self.assertFalse(result["success"])
        self.assertNotIn(token, str(result))
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertNotIn("/bot", "\n".join(captured.output))
        self.assertNotIn("/bot", str(result))
        self.assertNotIn("Bad Request: invalid token", str(result))
        self.assertNotIn("Bad Request: invalid token", "\n".join(captured.output))
        self.assertEqual(result["error"], "Telegram API error (HTTP 400)")
        self.assertIn("HTTP 400", "\n".join(captured.output))

    def test_connection_check_unsuccessful_response_redacts_description_and_token(self):
        token = "123456:secret-bot-token"
        mock_res = requests.Response()
        mock_res.status_code = 401
        raw_desc = f"Unauthorized: invalid bot token {token} in https://api.telegram.org/bot{token}/getMe"
        mock_res._content = f'{{"ok": false, "error_code": 401, "description": "{raw_desc}"}}'.encode("utf-8")

        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get", return_value=mock_res), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = telegram_notifier.test_telegram_connection()

        self.assertFalse(result["success"])
        self.assertNotIn(token, str(result))
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertNotIn("/bot", "\n".join(captured.output))
        self.assertNotIn("/bot", str(result))
        self.assertNotIn("Unauthorized: invalid bot token", str(result))
        self.assertNotIn("Unauthorized: invalid bot token", "\n".join(captured.output))
        self.assertEqual(result["error"], "Telegram API error (HTTP 401)")
        self.assertIn("HTTP 401", "\n".join(captured.output))

    def test_notifier_invalid_status_representation_falls_back_to_unknown(self):
        token = "123456:secret-bot-token"
        mock_res = requests.Response()
        mock_res.status_code = 999  # Out-of-range HTTP status
        mock_res._content = b'{"ok": false, "description": "unsupported"}'

        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "post", return_value=mock_res), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured_send:
            res_send = telegram_notifier.send_telegram_message("test", max_retries=1)

        self.assertEqual(res_send["error"], "Telegram API error (HTTP unknown)")
        self.assertIn("HTTP unknown", "\n".join(captured_send.output))

        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get", return_value=mock_res), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured_conn:
            res_conn = telegram_notifier.test_telegram_connection()

        self.assertEqual(res_conn["error"], "Telegram API error (HTTP unknown)")
        self.assertIn("HTTP unknown", "\n".join(captured_conn.output))


class TestLegacyTelegramTokenRedaction(unittest.TestCase):
    def test_legacy_sender_redacts_token_from_logs_on_connection_error(self):
        from unittest.mock import MagicMock
        from Screen.smart_money_engine import SmartMoneyTelegram, SmartMoneySignal

        token = "987654:secret-legacy-token"
        error = requests.ConnectionError(
            f"failed to connect to https://api.telegram.org/bot{token}/sendMessage"
        )
        signal = SmartMoneySignal(
            priority_score=-90.0,
            symbol="BTCUSDT",
            signal_type="STRONG_BUYING",
            rvol=3.5,
            buy_ratio=0.8,
            velocity=2.0,
            price=65000.0,
            price_change_pct=1.5,
            quote_volume=100000000.0,
            timestamp=1700000000.0,
            candle_time="2026-11-11 12:00:00",
        )
        session = MagicMock()
        session.post.side_effect = error

        with patch.dict("os.environ", {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-123")
            tg.session = session
            with patch("time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                success = tg.send_alert(signal)

            self.assertFalse(success)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot" + token, output)
            self.assertIn("error_type=ConnectionError", output)
            self.assertIn("http_status=unknown", output)

    def test_legacy_sender_rejects_unsafe_status_representations(self):
        from unittest.mock import MagicMock
        from Screen.smart_money_engine import SmartMoneyTelegram, SmartMoneySignal

        token = "987654:secret-legacy-token"
        signal = SmartMoneySignal(
            priority_score=-90.0,
            symbol="BTCUSDT",
            signal_type="STRONG_BUYING",
            rvol=3.5,
            buy_ratio=0.8,
            velocity=2.0,
            price=65000.0,
            price_change_pct=1.5,
            quote_volume=100000000.0,
            timestamp=1700000000.0,
            candle_time="2026-11-11 12:00:00",
        )

        # Test helper directly
        mock_resp = MagicMock()
        exc = requests.HTTPError(response=mock_resp)

        mock_resp.status_code = 502
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "502")

        mock_resp.status_code = 999
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = "502"
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = True
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        mock_resp.status_code = None
        self.assertEqual(SmartMoneyTelegram._safe_http_status(exc), "unknown")

        self.assertEqual(SmartMoneyTelegram._safe_http_status(requests.ConnectionError()), "unknown")

        # Test within send_alert logging
        mock_resp.status_code = 999
        error = requests.HTTPError(
            f"999 Custom Error for url: https://api.telegram.org/bot{token}/sendMessage",
            response=mock_resp,
        )
        session = MagicMock()
        session.post.return_value = mock_resp
        mock_resp.raise_for_status.side_effect = error

        with patch.dict("os.environ", {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-123")
            tg.session = session
            with patch("time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                success = tg.send_alert(signal)

            self.assertFalse(success)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("999", output)
            self.assertIn("error_type=HTTPError", output)
            self.assertIn("http_status=unknown", output)


class TestScreenerTelegramTokenRedaction(unittest.TestCase):
    def test_screener_early_alert_redacts_token_and_body_on_http_error(self):
        from unittest.mock import MagicMock
        from Screen.screening import SmartMoneyTelegram, SmartMoneySignal

        token = "987654:secret-screening-token"
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = f'{{"ok": false, "error_code": 403, "description": "Forbidden: bot was blocked by the user {token}"}}'
        error = requests.HTTPError(
            f"403 Client Error: Forbidden for url: https://api.telegram.org/bot{token}/sendMessage",
            response=mock_response,
        )
        signal = SmartMoneySignal(
            priority_score=90.0,
            symbol="BTCUSDT",
            signal_type="RVOL_SPIKE",
            rvol=3.0,
            volume=150.0,
            quote_volume=10200000.0,
            price=68000.0,
            price_change_pct=1.2,
            volume_velocity=2.0,
            timestamp=1700000000.0,
            candle_time="2026-11-11 12:00:00",
            quality_score=90.0,
            direction="LONG",
        )
        session = MagicMock()
        session.post.return_value = mock_response
        mock_response.raise_for_status.side_effect = error

        with patch.dict("os.environ", {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-123")
            tg.session = session
            with patch("Screen.screening.time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                success = tg.send_early_alert(signal)

            self.assertFalse(success)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertNotIn("Forbidden: bot was blocked", output)
            self.assertIn("error_type=HTTPError", output)
            self.assertIn("http_status=403", output)

    def test_screener_send_redacts_token_on_connection_error(self):
        from unittest.mock import MagicMock
        from Screen.screening import SmartMoneyTelegram

        token = "987654:secret-screening-token"
        error = requests.ConnectionError(
            f"failed to connect to https://api.telegram.org/bot{token}/sendMessage"
        )
        session = MagicMock()
        session.post.side_effect = error

        with patch.dict("os.environ", {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, clear=True):
            tg = SmartMoneyTelegram(token=token, chat_id="chat-123")
            tg.session = session
            with patch("Screen.screening.time.sleep"), \
                 self.assertLogs("SmartMoneyDetector", level="ERROR") as captured:
                success = tg.send("🎛️ Screener payload")

            self.assertFalse(success)
            output = "\n".join(captured.output)
            self.assertNotIn(token, output)
            self.assertNotIn("/bot", output)
            self.assertIn("error_type=ConnectionError", output)
            self.assertIn("http_status=unknown", output)


class TestTelegramGetMeStrictValidation(unittest.TestCase):
    def setUp(self):
        self.token = "123456:secret-bot-token"
        self.env = {
            "TELEGRAM_BOT_TOKEN": self.token,
            "TELEGRAM_CHAT_ID": "",  # Empty chat ID to isolate /getMe check without ping message
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }

    def _call_test_connection(self, status_code, ok_val, desc=None):
        import json
        mock_res = requests.Response()
        mock_res.status_code = status_code
        payload = {"ok": ok_val, "result": {"username": "test_bot"}}
        if desc is not None:
            payload["description"] = desc
        mock_res._content = json.dumps(payload).encode("utf-8")

        with patch.dict("os.environ", self.env, clear=True), \
             patch.object(telegram_notifier.requests, "get", return_value=mock_res):
            return telegram_notifier.test_telegram_connection()

    def test_valid_integer_200_with_ok_true_accepted(self):
        result = self._call_test_connection(200, True)
        self.assertTrue(result["success"])
        self.assertEqual(result["bot"]["username"], "test_bot")

    def test_integer_401_with_ok_true_rejected(self):
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection(401, True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP 401)")
        self.assertIn("HTTP 401", "\n".join(captured.output))

    def test_integer_500_with_ok_true_rejected(self):
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection(500, True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP 500)")
        self.assertIn("HTTP 500", "\n".join(captured.output))

    def test_none_status_with_ok_true_rejected_with_http_unknown(self):
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection(None, True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP unknown)")
        self.assertIn("HTTP unknown", "\n".join(captured.output))

    def test_string_200_status_with_ok_true_rejected_with_http_unknown(self):
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection("200", True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP unknown)")
        self.assertIn("HTTP unknown", "\n".join(captured.output))

    def test_boolean_true_status_with_ok_true_rejected_with_http_unknown(self):
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection(True, True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP unknown)")
        self.assertIn("HTTP unknown", "\n".join(captured.output))

    def test_integer_200_with_ok_false_rejected(self):
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection(200, False)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP 200)")
        self.assertIn("HTTP 200", "\n".join(captured.output))

    def test_invalid_status_with_malicious_description_redacts_token_and_desc(self):
        malicious_desc = f"Fake error containing token {self.token} and url https://api.telegram.org/bot{self.token}/getMe"
        with patch.dict("os.environ", self.env, clear=True), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = self._call_test_connection(999, False, desc=malicious_desc)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Telegram API error (HTTP unknown)")
        self.assertNotIn(self.token, str(result))
        self.assertNotIn(self.token, "\n".join(captured.output))
        self.assertNotIn("/bot", str(result))
        self.assertNotIn("/bot", "\n".join(captured.output))
        self.assertNotIn(malicious_desc, str(result))
        self.assertNotIn(malicious_desc, "\n".join(captured.output))


class TestTelegramSendStrictValidation(unittest.TestCase):
    def setUp(self):
        self.env = {
            "TELEGRAM_BOT_TOKEN": "123456:secret-bot-token",
            "TELEGRAM_CHAT_ID": "chat-123",
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
        }

    def _send(self, status_code, payload):
        from unittest.mock import MagicMock
        response = MagicMock()
        if status_code is not ...:
            response.status_code = status_code
        response.json.return_value = payload
        with patch.dict("os.environ", self.env, clear=True), \
             patch.object(telegram_notifier.requests, "post", return_value=response), \
             patch("time.sleep"):
            return telegram_notifier.send_telegram_message("test", max_retries=1)

    def test_exact_integer_200_and_boolean_true_is_success(self):
        self.assertTrue(self._send(200, {"ok": True})["success"])

    def test_integer_200_and_non_boolean_truthy_ok_is_rejected(self):
        for ok_value in (1, "true", ["yes"]):
            with self.subTest(ok=ok_value):
                self.assertFalse(self._send(200, {"ok": ok_value})["success"])

    def test_non_200_status_with_ok_true_is_rejected(self):
        for status in (401, 500):
            with self.subTest(status=status):
                self.assertFalse(self._send(status, {"ok": True})["success"])

    def test_boolean_float_string_and_none_status_are_rejected(self):
        for status in (True, 200.0, "200", None):
            with self.subTest(status=status):
                result = self._send(status, {"ok": True})
                self.assertFalse(result["success"])
                self.assertIn("HTTP", result["error"])

    def test_missing_status_is_rejected(self):
        result = self._send(..., {"ok": True})
        self.assertFalse(result["success"])

    def test_non_dictionary_json_is_rejected(self):
        for payload in (None, [], "ok", 1):
            with self.subTest(payload=payload):
                self.assertFalse(self._send(200, payload)["success"])

    def test_ambiguous_request_exception_is_not_retried(self):
        from unittest.mock import MagicMock
        with patch.dict("os.environ", self.env, clear=True), \
             patch.object(
                 telegram_notifier.requests,
                 "post",
                 side_effect=telegram_notifier.requests.exceptions.ReadTimeout("read timed out"),
             ) as post, patch("time.sleep"):
            result = telegram_notifier.send_telegram_message("test", max_retries=3)
        self.assertFalse(result["success"])
        self.assertEqual(post.call_count, 1)

    def test_malformed_response_json_is_not_retried(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("invalid JSON")
        with patch.dict("os.environ", self.env, clear=True), \
             patch.object(telegram_notifier.requests, "post", return_value=response) as post, \
             patch("time.sleep"):
            result = telegram_notifier.send_telegram_message("test", max_retries=3)
        self.assertFalse(result["success"])
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
