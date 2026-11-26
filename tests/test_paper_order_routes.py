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
             patch("web.routes.paper_routes.get_paper_configs", return_value=[]), \
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


    def test_existing_active_generator_can_disable_telegram_without_reloading_model(self):
        existing = [{
            "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
            "strategy_name": "ml_signal_generator_v1",
            "ml_generator_model_id": "generator-model-1", "is_active": True,
        }]
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.get_paper_configs", return_value=existing), \
             patch("ml.generator_artifacts.load_generator_artifact") as load, \
             patch("web.routes.paper_routes.set_paper_config", return_value={"status": "success"}) as save:
            response = self.client.post("/api/paper/configs", json={
                "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "strategy_name": "ml_signal_generator_v1", "is_active": True,
                "ml_generator_model_id": "generator-model-1",
                "allocated_capital": 5000, "risk_per_trade_pct": 1,
                "telegram_enabled": False, "report_enabled": True,
            })
        self.assertEqual(response.status_code, 200)
        load.assert_not_called()
        self.assertFalse(save.call_args.kwargs["telegram_enabled"])

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
             patch("web.routes.paper_routes.get_paper_configs", return_value=[]), \
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

    def test_delete_paper_config_success(self):
        with patch("web.app.authenticate_request", return_value=_user("admin")), \
             patch("web.routes.paper_routes.delete_paper_config", return_value=True) as delete_mock:
            response = self.client.delete("/api/paper/configs/15")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success", "id": 15})
        delete_mock.assert_called_once_with(15)

    def test_delete_paper_config_rejects_when_open_position_exists(self):
        with patch("web.app.authenticate_request", return_value=_user("admin")), \
             patch("web.routes.paper_routes.delete_paper_config", side_effect=ValueError("Cannot delete with open position")):
            response = self.client.delete("/api/paper/configs/15")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot delete with open position", response.json()["detail"])

    def test_purge_paper_config_maps_outcomes_and_audits(self):
        from paper.engine import PaperConfigHasHistory
        with patch("web.app.authenticate_request", return_value=_user("admin")), \
             patch("web.routes.paper_routes.purge_paper_config", return_value=True) as purge, \
             patch("web.routes.paper_routes.log_audit") as audit:
            ok = self.client.delete("/api/paper/configs/15/permanent")
        self.assertEqual(ok.status_code, 200)
        purge.assert_called_once_with(15)
        self.assertEqual(audit.call_args.args[0], "PAPER_CONFIG_PURGED")
        for exc, code in ((PaperConfigHasHistory("has trades"), 409), (ValueError("Archive first"), 400)):
            with patch("web.app.authenticate_request", return_value=_user("admin")), \
                 patch("web.routes.paper_routes.purge_paper_config", side_effect=exc):
                self.assertEqual(self.client.delete("/api/paper/configs/15/permanent").status_code, code)
        with patch("web.app.authenticate_request", return_value=_user("admin")), \
             patch("web.routes.paper_routes.purge_paper_config", return_value=False):
            self.assertEqual(self.client.delete("/api/paper/configs/15/permanent").status_code, 404)
        with patch("web.app.authenticate_request", return_value=_user("viewer")), \
             patch("web.routes.paper_routes.purge_paper_config") as blocked:
            self.assertEqual(self.client.delete("/api/paper/configs/15/permanent").status_code, 403)
        blocked.assert_not_called()

    def test_paper_deployment_patch_updates_only_selected_value(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.update_paper_deployment", return_value=True) as update:
            response = self.client.patch("/api/paper/configs/15", json={"telegram_enabled": False})
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with(15, telegram_enabled=False)
        with patch("web.app.authenticate_request", return_value=_user("viewer")):
            denied = self.client.patch("/api/paper/configs/15", json={"telegram_enabled": False})
        self.assertEqual(denied.status_code, 403)

    def test_batch_update_paper_configs_success(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.batch_update_paper_configs", return_value=3) as batch_mock, \
             patch("db.db.set_retailbot2_strategy_lifecycle_setting") as sync_rb2:
            response = self.client.post("/api/paper/configs/batch", json={
                "strategy_name": "BB_REV_INV", "action": "telegram_all_on"
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "success", "strategy": "BB_REV_INV", "action": "telegram_all_on", "updated": 3
        })
        batch_mock.assert_called_once_with("BB_REV_INV", "telegram_all_on")
        sync_rb2.assert_not_called()

    def test_paper_report_preferences_apply_only_to_selected_configurations(self):
        request = {"symbol": "btcusdt", "strategy_name": "BB_REV_INV", "report_enabled": False}
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.batch_update_paper_configs", return_value=1) as update, \
             patch("db.db.set_retailbot2_strategy_lifecycle_setting") as sync:
            response = self.client.post("/api/paper/configs/preferences", json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        update.assert_called_once_with("BB_REV_INV", "report_all_off", symbol="BTCUSDT")
        sync.assert_not_called()

        with patch("web.app.authenticate_request", return_value=_user("viewer")):
            denied = self.client.post("/api/paper/configs/preferences", json=request)
        self.assertEqual(denied.status_code, 403)

    def test_batch_update_paper_configs_rejects_invalid_action(self):
        with patch("web.app.authenticate_request", return_value=_user("trader")), \
             patch("web.routes.paper_routes.batch_update_paper_configs", side_effect=ValueError("Unknown batch action: invalid")):
            response = self.client.post("/api/paper/configs/batch", json={
                "strategy_name": "BB_REV_INV", "action": "invalid"
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown batch action", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
