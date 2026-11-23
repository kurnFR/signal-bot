"""Comprehensive integration and regression tests for Work Order 71951."""
import os
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi import HTTPException

from ml.generator_artifacts import GeneratorArtifact, list_generator_artifacts
import notifications.telegram_notifier as telegram_notifier
from notifications.telegram_notifier import (
    get_telegram_config,
    is_telegram_delivery_authorized,
    test_telegram_connection as run_telegram_test,
)
from paper.engine import get_paper_configs, set_paper_config
from web.routes import backtest, paper_routes, telegram_routes


def sample_ohlcv_df(rows: int = 60) -> pd.DataFrame:
    return pd.DataFrame({
        "open_time": [1_700_000_000_000 + i * 3_600_000 for i in range(rows)],
        "open": [100.0 + i for i in range(rows)],
        "high": [102.0 + i for i in range(rows)],
        "low": [99.0 + i for i in range(rows)],
        "close": [101.0 + i for i in range(rows)],
        "volume": [10.0] * rows,
        "is_closed": [1] * rows,
    })


class TestWorkOrder71951Integration(unittest.TestCase):
    # -------------------------------------------------------------------------
    # PR 1: Telegram Status, Delivery Authorization & Custom Message
    # -------------------------------------------------------------------------
    def test_telegram_status_delivery_authorized_flag(self):
        req = MagicMock()
        req.state.user = {"role": "admin"}
        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}):
            self.assertTrue(is_telegram_delivery_authorized())
            res = telegram_routes.get_status(req)
            self.assertTrue(res["deliveryAuthorized"])

        with patch.dict(os.environ, {"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}):
            self.assertFalse(is_telegram_delivery_authorized())
            res = telegram_routes.get_status(req)
            self.assertFalse(res["deliveryAuthorized"])

    def test_telegram_test_custom_message_delivery_authorized(self):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"ok": True, "result": {"username": "test_bot"}}

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}

        with patch.dict(os.environ, {
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "TELEGRAM_CHAT_ID": "-100123456789",
        }), patch.object(telegram_notifier.requests, "get", return_value=mock_get_resp), \
           patch.object(telegram_notifier.requests, "post", return_value=mock_post_resp) as mock_post:

            res = run_telegram_test(custom_message="<b>Custom Test Alert</b>")
            self.assertTrue(res["success"])
            self.assertEqual(mock_post.call_count, 1)  # At most 1 message sent
            # Verify HTML escaping
            posted_data = mock_post.call_args[1].get("data") or mock_post.call_args[1].get("json")
            self.assertIn("&lt;b&gt;Custom Test Alert&lt;/b&gt;", str(posted_data))

    def test_telegram_test_custom_message_suppressed_when_delivery_unauthorized(self):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"ok": True, "result": {"username": "test_bot"}}

        with patch.dict(os.environ, {
            "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0",
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "TELEGRAM_CHAT_ID": "-100123456789",
        }), patch.object(telegram_notifier.requests, "get", return_value=mock_get_resp), \
           patch.object(telegram_notifier.requests, "post") as mock_post:

            res = run_telegram_test(custom_message="Hello World")
            self.assertFalse(res["success"])
            self.assertIn("delivery is disabled", res["error"])
            mock_post.assert_not_called()

    def test_telegram_test_rejects_oversized_custom_message(self):
        res = run_telegram_test(custom_message="A" * 1001)
        self.assertFalse(res["success"])
        self.assertIn("1000", res["error"])

    def test_telegram_credential_canonical_precedence(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "canonical-token",
            "TELEGRAM_TOKEN": "alias-token",
            "TELEGRAM_CHAT_ID": "-100canonical",
            "TELEGRAM_TARGET_CHAT_ID": "-100alias",
        }):
            cfg = get_telegram_config()
            self.assertEqual(cfg["token"], "canonical-token")
            self.assertEqual(cfg["chat_id"], "-100canonical")

    # -------------------------------------------------------------------------
    # PR 2: Pair Backtest Rejection
    # -------------------------------------------------------------------------
    def test_pair_backtest_rejects_target_symbol_equals_comparator(self):
        req = backtest.BacktestRunRequest(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1d",
            strategy="pairs_ratio_v1",
        )
        with patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=sample_ohlcv_df(60)):
            with self.assertRaises(HTTPException) as ctx:
                backtest.execute_backtest(req)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Invalid pair comparison", ctx.exception.detail)
            self.assertIn("BTCUSDT", ctx.exception.detail)

    def test_pair_backtest_rejects_missing_comparator_data(self):
        req = backtest.BacktestRunRequest(
            symbol="ETHUSDT",
            market="spot",
            timeframe="1d",
            strategy="pairs_ratio_v1",
        )

        def mock_fetch(symbol, market, timeframe, closed_only=True, **kwargs):
            if symbol == "BTCUSDT":
                return pd.DataFrame()  # Empty comparator data
            return sample_ohlcv_df(60)

        with patch.object(backtest, "fetch_ohlcv_with_features_df", side_effect=mock_fetch):
            with self.assertRaises(HTTPException) as ctx:
                backtest.execute_backtest(req)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Comparator data unavailable", ctx.exception.detail)

    def test_walk_forward_pair_rejects_btcusdt(self):
        req = backtest.WalkForwardBacktestRequest(
            symbol="BTCUSDT",
            strategy="pairs_ratio_v1",
        )
        with self.assertRaises(HTTPException) as ctx:
            backtest.execute_walk_forward_backtest(req)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid pair comparison", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # PR 2: Saved Custom Strategy Registration in Paper Routes
    # -------------------------------------------------------------------------
    def test_paper_routes_accept_saved_custom_strategy(self):
        definitions = [{
            "name": "custom_strat_alpha",
            "longConditions": [],
            "shortConditions": [],
            "slAtrMult": 1.5,
            "riskRewardRatio": 2.5,
        }]
        with patch("backtest.custom_strategy.read_definitions", return_value=definitions):
            self.assertTrue(paper_routes._is_known_paper_strategy("custom_strat_alpha"))

            # Deploying this strategy is accepted by the validation gate
            with patch("web.routes.paper_routes.set_paper_config") as mock_set:
                mock_set.return_value = {"status": "success", "id": 101}
                req = paper_routes.DeployStrategyRequest(
                    symbol="ETHUSDT",
                    market="spot",
                    timeframe="1d",
                    strategy_name="custom_strat_alpha",
                    allocated_capital=5000.0,
                    risk_per_trade_pct=1.0,
                    send_telegram=False,
                )
                res = paper_routes.deploy_strategy(req)
                self.assertEqual(res["status"], "success")

    # -------------------------------------------------------------------------
    # PR 2: Strategy Parameter Overrides Persistence in Paper Configs
    # -------------------------------------------------------------------------
    def test_paper_strategy_parameter_overrides_persistence(self):
        overrides = {"CONFLUENCE_RISK_REWARD_RATIO": 3.0, "MIN_CONFLUENCE_SCORE": 4.5}
        # Call set_paper_config directly with strategy_params
        res = set_paper_config(
            symbol="ETHUSDT",
            market="spot",
            timeframe="1d",
            strategy_name="confluence_ensemble_v1",
            is_active=True,
            allocated_capital=5000.0,
            risk_per_trade_pct=1.0,
            strategy_params=overrides,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["strategy_params"], overrides)

        # Retrieve and verify it is preserved
        configs = get_paper_configs()
        target = next((c for c in configs if c["symbol"] == "ETHUSDT" and c["strategy_name"] == "confluence_ensemble_v1"), None)
        self.assertIsNotNone(target)
        self.assertEqual(target.get("strategy_params"), overrides)

    # -------------------------------------------------------------------------
    # PR 2: ML Generator Artifact Boundaries Exposure
    # -------------------------------------------------------------------------
    def test_list_generator_artifacts_exposes_symbol_market_timeframe(self):
        import tempfile
        from ml.generator_artifacts import save_generator_artifact
        from tests.test_ml_generator_artifacts import _artifact, _model

        with tempfile.TemporaryDirectory() as tmp_dir:
            art = _artifact(
                data_boundaries={
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "timeframe": "1d",
                    "train_start": 100,
                    "train_end": 200,
                    "validation_start": 300,
                    "validation_end": 400,
                    "test_start": 500,
                    "test_end": 600,
                }
            )
            model = _model()
            save_generator_artifact(art, model, tmp_dir)
            artifacts = list_generator_artifacts(tmp_dir)

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["model_id"], art.model_id)
        self.assertEqual(artifacts[0]["symbol"], "BTCUSDT")
        self.assertEqual(artifacts[0]["market"], "spot")
        self.assertEqual(artifacts[0]["timeframe"], "1d")


if __name__ == "__main__":
    unittest.main()
