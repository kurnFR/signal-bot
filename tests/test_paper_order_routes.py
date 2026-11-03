"""API tests for paper order history and cancellation authorization."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from paper.orders import OrderTransitionConflict, OrderValidationError
from web.app import app


def _user(role="trader"):
    return {
        "id": 42, "username": "test-user", "email": None, "role": role,
        "is_active": 1, "must_change_password": 0,
    }


class TestPaperOrderRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_list_orders_returns_service_rows_and_filter(self):
        with patch("web.app.authenticate_request", return_value=_user("viewer")), \
             patch("web.routes.paper_routes.list_orders", return_value=[{"id": 7, "status": "PENDING"}]) as list_orders:
            response = self.client.get("/api/paper/orders?status=PENDING&limit=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders"][0]["id"], 7)
        list_orders.assert_called_once_with(status="PENDING", limit=10)

    def test_invalid_order_state_returns_400(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.list_orders", side_effect=OrderValidationError("invalid order state")):
            response = self.client.get("/api/paper/orders?status=UNKNOWN")
        self.assertEqual(response.status_code, 400)

    def test_cancel_uses_authenticated_actor_id(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.cancel_pending_order", return_value=True) as cancel:
            response = self.client.post("/api/paper/orders/7/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "cancelled", "order_id": 7})
        cancel.assert_called_once_with(7, actor_user_id=42)

    def test_terminal_order_cancel_returns_conflict(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.cancel_pending_order", side_effect=OrderTransitionConflict("order is not pending")):
            response = self.client.post("/api/paper/orders/7/cancel")
        self.assertEqual(response.status_code, 409)


    def test_add_paper_config_accepts_registered_strategy(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config", return_value={"status": "success"}) as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "trend_ema_v1", "is_active": True,
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
                "telegram_enabled": False, "report_enabled": True,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.args[3], "trend_ema_v1")
        self.assertFalse(save.call_args.kwargs["telegram_enabled"])
        self.assertTrue(save.call_args.kwargs["report_enabled"])


    def test_add_generator_config_requires_typed_eligible_model_when_active(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("ml.generator_artifacts.load_generator_artifact", return_value=(object(), object())) as load, \
             patch("web.routes.paper_routes.set_paper_config", return_value={"status": "success"}) as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "ml_signal_generator_v1", "is_active": True,
                "ml_generator_model_id": "generator-model-1",
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
                "telegram_enabled": False, "report_enabled": True,
            })
        self.assertEqual(response.status_code, 200)
        load.assert_called_once_with("generator-model-1", require_eligible=True)
        self.assertEqual(save.call_args.kwargs["ml_generator_model_id"], "generator-model-1")
        self.assertIsNone(save.call_args.kwargs.get("ml_model_id"))

    def test_generator_config_without_model_is_rejected(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config") as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "ml_signal_generator_v1", "is_active": False,
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
            })
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_generator_config_cannot_use_legacy_filter_model(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config") as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "ml_signal_generator_v1", "is_active": False,
                "ml_model_id": "legacy-filter", "ml_generator_model_id": "generator-model-1",
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
            })
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_ineligible_generator_cannot_be_activated(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("ml.generator_artifacts.load_generator_artifact", side_effect=ValueError("not eligible")), \
             patch("web.routes.paper_routes.set_paper_config") as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "ml_signal_generator_v1", "is_active": True,
                "ml_generator_model_id": "research-only-model",
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
            })
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()

    def test_generator_models_have_separate_listing_endpoint(self):
        with patch("web.app.authenticate_request", return_value=_user("viewer")), \
             patch("web.routes.ml_routes.list_generator_artifacts", return_value=[{"model_id": "gen-1"}]):
            response = self.client.get("/api/ml/generator-models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"], [{"model_id": "gen-1"}])

    def test_add_paper_config_rejects_unregistered_strategy(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config") as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "not_registered", "is_active": True,
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
            })
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_add_paper_config_rejects_unsupported_timeframe(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config") as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "5m",
                "strategy_name": "trend_ema_v1", "is_active": True,
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
            })
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()


    def test_deploy_rejects_unregistered_strategy_before_any_side_effect(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config") as save, \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading") as sync:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "not_registered", "allocated_capital": 5000,
                "risk_per_trade_pct": 1, "send_telegram": True,
            })
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()
        sync.assert_not_called()

    def test_deploy_rejects_unsupported_timeframe_before_any_side_effect(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.set_paper_config") as save, \
             patch("web.routes.paper_routes.sync_and_evaluate_paper_trading") as sync:
            response = self.client.post("/api/paper/deploy", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "5m",
                "strategy_name": "trend_ema_v1", "allocated_capital": 5000,
                "risk_per_trade_pct": 1, "send_telegram": True,
            })
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()
        sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
