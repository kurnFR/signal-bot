"""Regression tests ensuring Telegram notifications for paper deployments are strictly opt-in."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import requests

from ml.artifacts import CURRENT_EVALUATION_PROTOCOL_VERSION
from notifications.telegram_notifier import (
    send_deployment_alert,
    send_signal_alert,
    send_telegram_message,
    send_trade_close_alert,
)
from web.app import app
from web.routes.ml_routes import DeployMLToPaperRequest
from web.routes.paper_routes import DeployStrategyRequest


def _user(role="trader"):
    return {
        "id": 42,
        "username": "trader-bob",
        "email": None,
        "role": role,
        "is_active": 1,
        "must_change_password": 0,
    }


class TestDeployTelegramOptInModels(unittest.TestCase):
    """Pydantic model-level defaults must require explicit opt-in."""

    def test_deploy_strategy_request_defaults_send_telegram_to_false(self):
        req_omitted = DeployStrategyRequest(symbol="BTCUSDT", strategy_name="trend_ema_v1")
        self.assertIs(req_omitted.send_telegram, False)

        req_false = DeployStrategyRequest(symbol="BTCUSDT", strategy_name="trend_ema_v1", send_telegram=False)
        self.assertIs(req_false.send_telegram, False)

        req_true = DeployStrategyRequest(symbol="BTCUSDT", strategy_name="trend_ema_v1", send_telegram=True)
        self.assertIs(req_true.send_telegram, True)

    def test_deploy_ml_to_paper_request_defaults_send_telegram_to_false(self):
        req_omitted = DeployMLToPaperRequest(model_id="rf_model_v1")
        self.assertIs(req_omitted.send_telegram, False)

        req_false = DeployMLToPaperRequest(model_id="rf_model_v1", send_telegram=False)
        self.assertIs(req_false.send_telegram, False)

        req_true = DeployMLToPaperRequest(model_id="rf_model_v1", send_telegram=True)
        self.assertIs(req_true.send_telegram, True)


class TestPaperDeployTelegramOptInRoute(unittest.TestCase):
    """Route-level tests for POST /api/paper/deploy Telegram opt-in behavior."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_omitted_send_telegram_does_not_call_notifier(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("notifications.telegram_notifier.send_deployment_alert") as alert_mock:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
            })

        self.assertEqual(response.status_code, 200, response.text)
        alert_mock.assert_not_called()
        data = response.json()
        self.assertIsNone(data.get("telegramAlert"))
        self.assertIsNone(data.get("telegram"))

    def test_explicit_false_send_telegram_does_not_call_notifier(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("notifications.telegram_notifier.send_deployment_alert") as alert_mock:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": False,
            })

        self.assertEqual(response.status_code, 200, response.text)
        alert_mock.assert_not_called()
        data = response.json()
        self.assertIsNone(data.get("telegramAlert"))
        self.assertIsNone(data.get("telegram"))

    def test_explicit_true_invokes_notifier_exactly_once_and_reports_delivery(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("notifications.telegram_notifier.send_deployment_alert", return_value={"success": True, "message_id": 7890}) as alert_mock:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })

        self.assertEqual(response.status_code, 200, response.text)
        alert_mock.assert_called_once_with(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1d",
            strategy_name="trend_ema_v1",
            allocated_capital=5000.0,
            risk_per_trade_pct=1.0,
            rank=None,
            rank_score=None,
            max_retries=1,
        )
        data = response.json()
        self.assertEqual(data.get("telegramAlert"), {"success": True, "message_id": 7890})
        self.assertEqual(data.get("telegram"), {"success": True, "message_id": 7890})

    def test_paper_deploy_telegram_failure_does_not_leak_secrets_and_reports_failure(self):
        secret = "https://api.telegram.org/bot987654:secret_token_abc/sendMessage"
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("notifications.telegram_notifier.send_deployment_alert", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.paper_routes", level="ERROR") as captured:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        telegram_alert = data.get("telegramAlert")
        self.assertIsInstance(telegram_alert, dict)
        self.assertFalse(telegram_alert.get("success"))
        self.assertEqual(telegram_alert.get("error"), "Telegram deployment alert failed")
        # Ensure secret token is not leaked in response body or error logs
        self.assertNotIn("secret_token_abc", response.text)
        self.assertNotIn(secret, response.text)
        self.assertNotIn("secret_token_abc", "\n".join(captured.output))


class TestMLDeployTelegramOptInRoute(unittest.TestCase):
    """Route-level tests for POST /api/ml/deploy-to-paper Telegram opt-in behavior."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.models_dir = Path(self.tmp.name)
        manifest = {
            "model_id": "test_rf_model",
            "base_strategy": "trend_ema_v1",
            "model_type": "random_forest",
            "threshold": 0.6,
            "eligible_for_paper": True,
            "test_metrics": {
                "sharpe_ratio": 1.5,
                "win_rate": 0.6,
                "profit_factor": 1.8,
                "max_drawdown_pct": 12.0,
                "trade_count": 45,
                "net_pnl": 500.0,
            },
            "evaluation_protocol_version": CURRENT_EVALUATION_PROTOCOL_VERSION,
            "strategy_params": {"symbol": "BTCUSDT", "market": "spot", "timeframe": "1d"},
        }
        (self.models_dir / "test_rf_model.json").write_text(json.dumps(manifest))
        (self.models_dir / "test_rf_model.joblib").write_bytes(b"dummy-weights")

    def tearDown(self):
        self.tmp.cleanup()

    def test_omitted_send_telegram_does_not_call_notifier(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("web.routes.ml_routes.send_telegram_message") as send_mock:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "test_rf_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
            })

        self.assertEqual(response.status_code, 200, response.text)
        send_mock.assert_not_called()
        data = response.json()
        self.assertIsNone(data.get("telegram"))
        self.assertIsNone(data.get("telegramAlert"))

    def test_explicit_false_send_telegram_does_not_call_notifier(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("web.routes.ml_routes.send_telegram_message") as send_mock:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "test_rf_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": False,
            })

        self.assertEqual(response.status_code, 200, response.text)
        send_mock.assert_not_called()
        data = response.json()
        self.assertIsNone(data.get("telegram"))
        self.assertIsNone(data.get("telegramAlert"))

    def test_explicit_true_invokes_notifier_exactly_once_and_reports_delivery(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("web.routes.ml_routes.send_telegram_message", return_value={"success": True, "message_id": 4321}) as send_mock:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "test_rf_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })

        self.assertEqual(response.status_code, 200, response.text)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs.get("max_retries"), 1)
        data = response.json()
        self.assertEqual(data.get("telegram"), {"success": True, "message_id": 4321})
        self.assertEqual(data.get("telegramAlert"), {"success": True, "message_id": 4321})

    def test_ml_deploy_telegram_failure_does_not_leak_secrets_and_reports_failure(self):
        secret = "https://api.telegram.org/bot112233:secret_token_xyz/sendMessage"
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch("web.routes.ml_routes.send_telegram_message", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.ml_routes", level="ERROR") as captured:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "test_rf_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        telegram_status = data.get("telegram")
        self.assertIsInstance(telegram_status, dict)
        self.assertFalse(telegram_status.get("success"))
        self.assertEqual(telegram_status.get("error"), "Telegram deployment alert failed")
        # Ensure secret token is not leaked in response body or error logs
        self.assertNotIn("secret_token_xyz", response.text)
        self.assertNotIn(secret, response.text)
        self.assertNotIn("secret_token_xyz", "\n".join(captured.output))


class TestDeploymentTelegramSingleAttempt(unittest.TestCase):
    """Proves single-attempt delivery for deployments while preserving ordinary retry behavior."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.models_dir = Path(self.tmp.name)
        manifest = {
            "model_id": "single_attempt_model",
            "base_strategy": "trend_ema_v1",
            "model_type": "random_forest",
            "threshold": 0.6,
            "eligible_for_paper": True,
            "test_metrics": {
                "sharpe_ratio": 1.5,
                "win_rate": 0.6,
                "profit_factor": 1.8,
                "max_drawdown_pct": 12.0,
                "trade_count": 45,
                "net_pnl": 500.0,
            },
            "evaluation_protocol_version": CURRENT_EVALUATION_PROTOCOL_VERSION,
            "strategy_params": {"symbol": "BTCUSDT", "market": "spot", "timeframe": "1d"},
        }
        (self.models_dir / "single_attempt_model.json").write_text(json.dumps(manifest))
        (self.models_dir / "single_attempt_model.joblib").write_bytes(b"dummy-weights")

    def tearDown(self):
        self.tmp.cleanup()

    def test_send_deployment_alert_defaults_to_single_attempt_on_transport_failure(self):
        token = "123456:secret_bot_token"
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_123"}, clear=True), \
             patch("requests.post", side_effect=requests.ConnectionError(f"connection error {token}")) as post_mock, \
             patch("time.sleep") as sleep_mock, \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            res = send_deployment_alert(
                symbol="BTCUSDT",
                market="spot",
                timeframe="1d",
                strategy_name="trend_ema_v1",
                allocated_capital=5000.0,
                risk_per_trade_pct=1.0,
            )
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "ConnectionError while contacting Telegram API")
        self.assertNotIn(token, str(res))
        self.assertNotIn(token, "\n".join(captured.output))

    def test_send_deployment_alert_dispatches_once_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 9999}}
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token123", "TELEGRAM_CHAT_ID": "chat123"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock:
            res = send_deployment_alert(
                symbol="BTCUSDT",
                market="spot",
                timeframe="1d",
                strategy_name="trend_ema_v1",
                allocated_capital=5000.0,
                risk_per_trade_pct=1.0,
            )
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        self.assertTrue(res["success"])
        self.assertEqual(res["message_id"], 9999)

    def test_ordinary_telegram_message_retains_three_retries_on_transport_failure(self):
        token = "123456:secret_bot_token"
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_123"}, clear=True), \
             patch("requests.post", side_effect=requests.ConnectionError(f"connection error {token}")) as post_mock, \
             patch("time.sleep") as sleep_mock:
            res = send_telegram_message("ordinary signal notification")
        self.assertEqual(post_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertFalse(res["success"])
        self.assertNotIn(token, str(res))

    def test_ordinary_signal_and_close_alerts_retain_default_retries(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token123", "TELEGRAM_CHAT_ID": "chat123"}, clear=True), \
             patch("requests.post", side_effect=requests.ConnectionError("network error")) as post_mock, \
             patch("time.sleep") as sleep_mock:
            res_signal = send_signal_alert(
                symbol="BTCUSDT",
                market="spot",
                timeframe="1h",
                strategy_name="trend_ema_v1",
                direction="LONG",
                entry_price=50000.0,
                stop_loss=49000.0,
                take_profit=52000.0,
            )
        self.assertEqual(post_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertFalse(res_signal["success"])

    def test_paper_deploy_route_makes_only_one_http_attempt_on_telegram_failure(self):
        token = "bot987654:secret_token_paper"
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_paper"}, clear=True), \
             patch("requests.post", side_effect=requests.ConnectionError(f"failed to connect to https://api.telegram.org/bot{token}/sendMessage")) as post_mock, \
             patch("time.sleep") as sleep_mock:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertFalse(data["telegramAlert"]["success"])
        self.assertFalse(data["telegram"]["success"])
        self.assertNotIn(token, response.text)

    def test_paper_deploy_route_dispatches_once_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 3333}}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token_paper", "TELEGRAM_CHAT_ID": "chat_paper"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertTrue(data["telegramAlert"]["success"])
        self.assertEqual(data["telegramAlert"]["message_id"], 3333)

    def test_ml_deploy_route_makes_only_one_http_attempt_on_telegram_failure(self):
        token = "bot987654:secret_token_ml"
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_ml"}, clear=True), \
             patch("requests.post", side_effect=requests.ConnectionError(f"failed to connect to https://api.telegram.org/bot{token}/sendMessage")) as post_mock, \
             patch("time.sleep") as sleep_mock:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "single_attempt_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertFalse(data["telegram"]["success"])
        self.assertFalse(data["telegramAlert"]["success"])
        self.assertNotIn(token, response.text)

    def test_ml_deploy_route_dispatches_once_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 4444}}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token_ml", "TELEGRAM_CHAT_ID": "chat_ml"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "single_attempt_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertTrue(data["telegram"]["success"])
        self.assertEqual(data["telegram"]["message_id"], 4444)

    def test_trade_close_alert_retains_three_retries_on_transport_failure(self):
        token = "secret_close_token_999"
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_close"}, clear=True), \
             patch("requests.post", side_effect=requests.ConnectionError(f"connection error {token}")) as post_mock, \
             patch("time.sleep") as sleep_mock:
            res_close = send_trade_close_alert(
                symbol="BTCUSDT",
                strategy_name="trend_ema_v1",
                direction="LONG",
                entry_price=50000.0,
                exit_price=52000.0,
                exit_reason="TP",
                net_return_pct=4.0,
                r_multiple=2.0,
                holding_bars=10,
            )
        self.assertEqual(post_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertFalse(res_close["success"])
        self.assertNotIn(token, str(res_close))

    def test_paper_deploy_route_makes_only_one_http_attempt_on_telegram_500_response(self):
        token = "bot987654:secret_token_paper_500"
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"ok": False, "error_code": 500, "description": "Internal Server Error"}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_paper"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock, \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["telegramAlert"]["success"])
        self.assertFalse(data["telegram"]["success"])
        self.assertNotIn(token, response.text)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_paper_deploy_route_makes_only_one_http_attempt_on_telegram_502_response(self):
        token = "bot987654:secret_token_paper_502"
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.return_value = {"ok": False, "error_code": 502, "description": "Bad Gateway"}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"error": "evaluation timed out"}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_paper"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock, \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1d",
                "strategy_name": "trend_ema_v1",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertEqual(data["status"], "partial")
        self.assertFalse(data["telegramAlert"]["success"])
        self.assertFalse(data["telegram"]["success"])
        self.assertNotIn(token, response.text)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_ml_deploy_route_makes_only_one_http_attempt_on_telegram_500_response(self):
        token = "bot987654:secret_token_ml_500"
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"ok": False, "error_code": 500, "description": "Internal Server Error"}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_ml"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock, \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "single_attempt_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["telegram"]["success"])
        self.assertFalse(data["telegramAlert"]["success"])
        self.assertNotIn(token, response.text)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_ml_deploy_route_makes_only_one_http_attempt_on_telegram_502_response(self):
        token = "bot987654:secret_token_ml_502"
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.return_value = {"ok": False, "error_code": 502, "description": "Bad Gateway"}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", self.models_dir), \
             patch("web.routes.ml_routes.set_paper_config", return_value={"id": 1, "status": "ok"}), \
             patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={"evaluated": True}), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "chat_ml"}, clear=True), \
             patch("requests.post", return_value=mock_resp) as post_mock, \
             patch("time.sleep") as sleep_mock, \
             self.assertLogs("notifications.telegram", level="WARNING") as captured:
            response = self.client.post("/api/ml/deploy-to-paper", json={
                "model_id": "single_attempt_model",
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "send_telegram": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post_mock.call_count, 1)
        sleep_mock.assert_not_called()
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["telegram"]["success"])
        self.assertFalse(data["telegramAlert"]["success"])
        self.assertNotIn(token, response.text)
        self.assertNotIn(token, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
