"""Telegram route contracts; all Telegram HTTP/message helpers are mocked."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from notifications import telegram_notifier
from web.app import app


def _user(role, must_change_password=False):
    return {
        "id": 1, "username": "test-user", "email": None, "role": role,
        "is_active": 1, "must_change_password": int(must_change_password),
    }


class _TelegramResponse:
    status_code = 200

    def json(self):
        return {"ok": True, "result": {"username": "signal_test_bot", "first_name": "Signal Test"}}


class TestTelegramRouteContracts(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def _get(self, path, role, must_change_password=False):
        with patch("web.app.authenticate_request", return_value=_user(role, must_change_password)), \
             patch("web.auth.authenticate_request", return_value=_user(role, must_change_password)):
            return self.client.get(path)

    def _post(self, path, role, must_change_password=False, **kwargs):
        with patch("web.app.authenticate_request", return_value=_user(role, must_change_password)), \
             patch("web.auth.authenticate_request", return_value=_user(role, must_change_password)):
            return self.client.post(path, **kwargs)

    def test_status_exposes_chat_id_only_to_admin(self):
        config = {"is_configured": True, "chat_id": "-100123456789", "token": "test-token"}
        with patch("web.routes.telegram_routes.get_telegram_config", return_value=config):
            admin = self._get("/api/telegram/status", "admin")
            trader = self._get("/api/telegram/status", "trader")
            viewer = self._get("/api/telegram/status", "viewer")

        self.assertEqual(admin.status_code, 200)
        self.assertEqual(admin.json(), {
            "configured": True, "chatId": "-100123456789", "hasToken": True,
        })
        for response in (trader, viewer):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "configured": True, "chatId": None, "hasToken": True,
            })

    def test_admin_test_endpoint_returns_mocked_result_without_real_telegram_calls(self):
        result = {
            "success": True,
            "bot": {"username": "signal_test_bot", "first_name": "Signal Test"},
            "chat_id": "-100123456789",
            "error": None,
        }
        with patch("web.routes.telegram_routes.test_telegram_connection", return_value=result) as test_helper:
            response = self._post("/api/telegram/test", "admin", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        test_helper.assert_called_once_with()

    def test_non_admin_test_request_is_blocked_before_outbound_helper(self):
        with patch("web.routes.telegram_routes.test_telegram_connection") as test_helper:
            trader = self._post("/api/telegram/test", "trader", json={})
            viewer = self._post("/api/telegram/test", "viewer", json={})

        self.assertEqual(trader.status_code, 403)
        self.assertEqual(viewer.status_code, 403)
        test_helper.assert_not_called()

    def test_unauthenticated_request_is_denied(self):
        with patch("web.routes.telegram_routes.test_telegram_connection") as test_helper:
            status = self.client.get("/api/telegram/status")
            test = self.client.post("/api/telegram/test", json={})

        self.assertEqual(status.status_code, 401)
        self.assertEqual(test.status_code, 401)
        test_helper.assert_not_called()

    def test_forced_password_change_blocks_telegram_status_and_test(self):
        with patch("web.routes.telegram_routes.get_telegram_config") as get_config, \
             patch("web.routes.telegram_routes.test_telegram_connection") as test_helper:
            status = self._get("/api/telegram/status", "admin", must_change_password=True)
            test = self._post("/api/telegram/test", "admin", must_change_password=True, json={})

        self.assertEqual(status.status_code, 403)
        self.assertEqual(test.status_code, 403)
        get_config.assert_not_called()
        test_helper.assert_not_called()

    def test_notifier_uses_mocked_outbound_helper_for_ping_and_does_not_include_token_in_message(self):
        config = {"is_configured": True, "chat_id": "-100123456789", "token": "secret-test-token"}
        with patch.dict("os.environ", {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}), \
             patch.object(telegram_notifier, "get_telegram_config", return_value=config), \
             patch.object(telegram_notifier.requests, "get", return_value=_TelegramResponse()) as get_me, \
             patch.object(telegram_notifier, "send_telegram_message", return_value={"success": True}) as send_message:
            result = telegram_notifier.test_telegram_connection()

        self.assertTrue(result["success"])
        self.assertEqual(result["bot"]["username"], "signal_test_bot")
        get_me.assert_called_once()
        send_message.assert_called_once()
        self.assertNotIn("secret-test-token", send_message.call_args.args[0])

    def test_notifier_without_chat_id_verifies_token_without_sending_ping(self):
        config = {"is_configured": False, "chat_id": None, "token": "secret-test-token"}
        with patch.dict("os.environ", {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}), \
             patch.object(telegram_notifier, "get_telegram_config", return_value=config), \
             patch.object(telegram_notifier.requests, "get", return_value=_TelegramResponse()), \
             patch.object(telegram_notifier, "send_telegram_message") as send_message:
            result = telegram_notifier.test_telegram_connection()

        self.assertTrue(result["success"])
        self.assertIn("chat_id is missing", result["note"].lower())
        send_message.assert_not_called()

    def test_admin_test_endpoint_fails_closed_when_unauthorized_with_zero_http_calls(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(telegram_notifier.requests, "get") as mock_get, \
             patch.object(telegram_notifier.requests, "post") as mock_post:
            response = self._post("/api/telegram/test", "admin", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Telegram delivery is disabled", response.json()["detail"])
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    def test_notifier_test_connection_fails_closed_when_unauthorized(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(telegram_notifier.requests, "get") as mock_get, \
             patch.object(telegram_notifier.requests, "post") as mock_post:
            result = telegram_notifier.test_telegram_connection()

        self.assertFalse(result["success"])
        self.assertIn("Telegram delivery is disabled", result["error"])
        mock_get.assert_not_called()
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
