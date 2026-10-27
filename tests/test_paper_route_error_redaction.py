"""Paper route errors must not expose raw exception details to API clients."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from web.routes import paper_routes


class TestPaperRouteErrorRedaction(unittest.TestCase):
    def test_sync_failure_returns_generic_json_detail(self):
        secret = "https://api.telegram.org/bot123456:secret-token/sendMessage"
        with patch.object(paper_routes, "sync_and_evaluate_paper_trading", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.paper_routes", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                paper_routes.sync_market_tick()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("secret-token", raised.exception.detail)
        self.assertNotIn(secret, raised.exception.detail)
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_deploy_notification_failure_does_not_return_raw_exception(self):
        secret = "https://api.telegram.org/bot123456:secret-token/sendMessage"
        request = paper_routes.DeployStrategyRequest(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1h",
            strategy_name="safe_test",
            send_telegram=True,
        )
        with patch.object(paper_routes, "STRATEGIES", {"safe_test": object()}), \
             patch.object(paper_routes, "OVERLAY_STRATEGIES", set()), \
             patch.object(paper_routes, "set_paper_config", return_value={"status": "ok"}), \
             patch("notifications.telegram_notifier.send_deployment_alert", side_effect=RuntimeError(secret)), \
             patch.object(paper_routes, "sync_and_evaluate_paper_trading", return_value={}), \
             self.assertLogs("web.paper_routes", level="ERROR") as captured:
            result = paper_routes.deploy_strategy(request)

        self.assertFalse(result["telegramAlert"]["success"])
        self.assertNotIn("secret-token", str(result))
        self.assertNotIn(secret, str(result))
        self.assertNotIn("secret-token", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
