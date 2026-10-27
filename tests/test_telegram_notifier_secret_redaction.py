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
        }, clear=True), \
             patch.object(telegram_notifier.requests, "get", side_effect=error), \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            result = telegram_notifier.test_telegram_connection()

        self.assertFalse(result["success"])
        self.assertNotIn(token, str(result))
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertNotIn("/bot" + token, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
