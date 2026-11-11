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


if __name__ == "__main__":
    unittest.main()
