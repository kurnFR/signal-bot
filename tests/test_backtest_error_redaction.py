"""Backtest API failures must not return raw exception details."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from web.routes import backtest


class TestBacktestErrorRedaction(unittest.TestCase):
    def test_policy_load_failure_returns_generic_error(self):
        secret = "database-password=secret-token"
        with patch.object(backtest, "get_strategy_auto_promotion_policy", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.backtest", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                backtest.get_promotion_policy()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Unable to load promotion policy")
        self.assertNotIn("secret-token", str(raised.exception.detail))
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_policy_save_failure_returns_generic_error(self):
        secret = "database-password=secret-token"
        request = backtest.StrategyAutoPromotionPolicyRequest()
        with patch.object(backtest, "set_strategy_auto_promotion_policy", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.backtest", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                backtest.update_promotion_policy(request)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Unable to save promotion policy")
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_promotion_audit_failure_returns_generic_error(self):
        secret = "database-password=secret-token"
        with patch.object(backtest, "get_strategy_promotion_audit", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.backtest", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                backtest.list_promotion_audit()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Unable to load promotion audit")
        self.assertNotIn("secret-token", "\n".join(captured.output))


    def test_rolling_walk_forward_error_response_is_generic(self):
        from backtest import walk_forward

        secret = "database-password=secret-token /srv/private/backtest-cache"
        request = backtest.WalkForwardBacktestRequest(rolling=True)
        with patch.object(walk_forward, "run_rolling_walk_forward", side_effect=RuntimeError(secret)), self.assertLogs("web.backtest", level="ERROR"):
            with self.assertRaises(HTTPException) as raised:
                backtest.execute_walk_forward_backtest(request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Rolling walk-forward analysis failed; check server logs for diagnostics")
        self.assertNotIn("secret-token", raised.exception.detail)
        self.assertNotIn("/srv/private", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
