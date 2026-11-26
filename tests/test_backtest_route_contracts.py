"""Focused contract tests for the Backtest Studio API response and failure behavior."""
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

from web.routes import backtest


def candle_frame(rows: int) -> pd.DataFrame:
    return pd.DataFrame({
        "open_time": [1_700_000_000_000 + i * 3_600_000 for i in range(rows)],
        "open": [100.0 + i for i in range(rows)],
        "high": [102.0 + i for i in range(rows)],
        "low": [99.0 + i for i in range(rows)],
        "close": [101.0 + i for i in range(rows)],
        "volume": [10.0] * rows,
    })


class TestBacktestRouteContracts(unittest.TestCase):
    def test_strategy_registry_uses_actual_react_contract_field_names(self):
        payload = backtest.list_strategies()
        self.assertTrue(payload["strategies"])
        row = payload["strategies"][0]
        self.assertTrue({
            "id", "baseName", "isInverse", "displayName", "category",
            "validated", "description", "params", "sharedParams",
        }.issubset(row))
        self.assertNotIn("name", row)
        self.assertNotIn("type", row)
        self.assertNotIn("holdout_passed", row)

    def test_single_run_request_defaults_match_documented_api_defaults(self):
        request = backtest.BacktestRunRequest(symbol="BTCUSDT")
        self.assertEqual(request.market, "spot")
        self.assertEqual(request.timeframe, "1d")
        self.assertEqual(request.strategy, "confluence_ensemble_v1")
        self.assertEqual(request.data_segment, "full")
        self.assertEqual(request.initial_capital, 5000.0)
        self.assertEqual(request.risk_per_trade_pct, 1.0)
        self.assertTrue(request.save_to_db)
        self.assertEqual(request.params, {})

    def test_run_rejects_fewer_than_50_source_candles_before_simulation(self):
        request = backtest.BacktestRunRequest(symbol="BTCUSDT")
        with patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=candle_frame(49)), \
             patch.object(backtest, "compute_metrics") as compute_metrics, \
             patch.object(backtest, "insert_backtest_run") as insert_run:
            with self.assertRaises(HTTPException) as raised:
                backtest.execute_backtest(request)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("found 49, need >= 50", raised.exception.detail)
        compute_metrics.assert_not_called()
        insert_run.assert_not_called()

    def test_saved_run_failure_returns_results_without_claiming_a_saved_id(self):
        request = backtest.BacktestRunRequest(symbol="BTCUSDT", strategy="test_strategy", save_to_db=True)
        metrics = {
            "total_trades": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0,
            "profit_factor": 0.0, "max_drawdown_r": 0.0,
        }
        with patch.dict(backtest.STRATEGIES, {"test_strategy": lambda frame, params: []}), \
             patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=candle_frame(60)), \
             patch.object(backtest, "compute_metrics", return_value=metrics), \
             patch.object(backtest, "simulate_equity_curve", return_value={"equity": []}), \
             patch.object(backtest, "insert_backtest_run", side_effect=RuntimeError("simulated DB outage")) as insert_run, \
             patch.object(backtest, "insert_backtest_trades") as insert_trades, \
             self.assertLogs("web.backtest", level="ERROR") as captured:
            result = backtest.execute_backtest(request)

        self.assertIsNone(result["runId"])
        self.assertEqual(result["totalCandles"], 60)
        self.assertEqual(result["metrics"], metrics)
        self.assertEqual(result["trades"], [])
        insert_run.assert_called_once()
        insert_trades.assert_not_called()
        self.assertIn("Failed to save backtest to DB (RuntimeError)", "\n".join(captured.output))
        self.assertNotIn("simulated DB outage", "\n".join(captured.output))

    def test_research_benchmark_keeps_rankings_without_attempting_promotion(self):
        request = backtest.RunAllStrategiesRequest(
            symbol="BTCUSDT", data_segment="train", allow_auto_promotion=False
        )
        metrics = {
            "total_trades": 100, "win_rate_pct": 100.0, "expectancy_r": 0.5,
            "profit_factor": float("inf"), "max_drawdown_r": 0.0,
        }
        with patch.object(backtest, "STRATEGIES", {"test_strategy": lambda frame, params: []}), \
             patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=candle_frame(100)), \
             patch("backtest.metrics.compute_metrics", return_value=metrics), \
             patch("backtest.equity.simulate_equity_curve", return_value={"equity": []}), \
             patch("ai.insight_engine.generate_ai_insight", return_value={}), \
             patch.object(backtest, "_auto_promote_top_candidate") as auto_promote:
            result = backtest.run_all_strategies(request)

        auto_promote.assert_not_called()
        self.assertEqual(result["autoPromotion"]["status"], "skipped")
        self.assertEqual(result["top3"][0]["strategy"], "test_strategy")
        self.assertIsNone(result["top3"][0]["metrics"]["profit_factor"])


if __name__ == "__main__":
    unittest.main()
