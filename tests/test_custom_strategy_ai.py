import os
import unittest
from unittest.mock import patch, Mock
import pandas as pd
from fastapi.testclient import TestClient

from web.app import app
from web.routes.custom_strategy import (
    _eval_condition,
    _create_strategy_runner,
    _parse_llm_json,
    _heuristic_strategy_generator,
    INDICATOR_DEFINITIONS,
)


class TestCustomStrategyConditionEval(unittest.TestCase):
    def setUp(self):
        self.row = {
            "rsi": 28.5,
            "volume_ratio": 2.2,
            "macd": 15.0,
            "macd_signal": 10.0,
            "macd_hist": 5.0,
            "close": 62000.0,
            "support": 60000.0,
            "resistance": 65000.0,
        }
        self.prev_row = {}

    def test_numeric_less_than_operator(self):
        cond = {"indicator": "rsi", "operator": "<", "value": 30.0}
        self.assertTrue(_eval_condition(self.row, self.prev_row, cond))

        cond_false = {"indicator": "rsi", "operator": "<", "value": 25.0}
        self.assertFalse(_eval_condition(self.row, self.prev_row, cond_false))

    def test_numeric_greater_than_operator(self):
        cond = {"indicator": "volume_ratio", "operator": ">", "value": 1.8}
        self.assertTrue(_eval_condition(self.row, self.prev_row, cond))

    def test_indicator_to_indicator_comparison(self):
        # macd > macd_signal
        cond = {"indicator": "macd", "operator": ">", "value": "macd_signal"}
        self.assertTrue(_eval_condition(self.row, self.prev_row, cond))

        # close > support
        cond2 = {"indicator": "close", "operator": ">", "value": "support"}
        self.assertTrue(_eval_condition(self.row, self.prev_row, cond2))

        # close > resistance (false)
        cond3 = {"indicator": "close", "operator": ">", "value": "resistance"}
        self.assertFalse(_eval_condition(self.row, self.prev_row, cond3))

    def test_alias_normalization(self):
        # RSI alias for rsi
        cond = {"indicator": "RSI", "operator": "<", "value": 30.0}
        self.assertTrue(_eval_condition(self.row, self.prev_row, cond))

        # volume alias for volume_ratio
        cond2 = {"indicator": "volume", "operator": ">", "value": 1.5}
        self.assertTrue(_eval_condition(self.row, self.prev_row, cond2))


class TestCustomStrategyRunner(unittest.TestCase):
    def test_runner_simulation(self):
        strat_def = {
            "name": "unit_test_rsi_v1",
            "displayName": "Unit Test RSI",
            "description": "Test strategy",
            "longConditions": [{"indicator": "rsi", "operator": "<", "value": 30.0}],
            "shortConditions": [{"indicator": "rsi", "operator": ">", "value": 70.0}],
            "slAtrMult": 1.5,
            "riskRewardRatio": 2.5,
            "useTrailingStop": True,
            "trailActivationR": 1.0,
            "trailDistanceAtrMult": 1.5,
        }
        run_fn, run_inv_fn = _create_strategy_runner(strat_def)

        # Mock dataframe with 10 bars
        n = 10
        df = pd.DataFrame({
            "open_time": [1000 + i * 60000 for i in range(n)],
            "open": [100.0] * n,
            "high": [105.0] * n,
            "low": [95.0] * n,
            "close": [102.0] * n,
            "volume": [1000.0] * n,
            "rsi": [25.0, 25.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0],
            "atr": [2.0] * n,
        })
        params = {
            "BACKTEST_FEE_PCT": 0.001,
            "BACKTEST_SLIPPAGE_PCT": 0.0005,
            "BACKTEST_MAX_HOLD_BARS": 200,
            "CUSTOM_SL_ATR_MULT": 1.5,
            "CUSTOM_RISK_REWARD_RATIO": 2.5,
        }
        trades = run_fn(df, params)
        self.assertIsInstance(trades, list)


class TestAIStrategyGeneration(unittest.TestCase):
    def test_heuristic_generator_with_keywords(self):
        prompt = "Create a strategy that buys when RSI < 32 and volume surge over 2.0 with 2.0 ATR stop and 3.0 RR"
        res = _heuristic_strategy_generator(prompt)
        self.assertIn("name", res)
        self.assertIn("longConditions", res)
        self.assertEqual(res["slAtrMult"], 2.0)
        self.assertEqual(res["riskRewardRatio"], 3.0)
        indicators = [c["indicator"] for c in res["longConditions"]]
        self.assertIn("rsi", indicators)
        self.assertIn("volume_ratio", indicators)

    def test_parse_llm_json_clean(self):
        raw = '```json\n{"name": "test_strat_v1", "displayName": "Test Strat", "slAtrMult": 1.8, "riskRewardRatio": 2.2}\n```'
        parsed = _parse_llm_json(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "test_strat_v1")
        self.assertEqual(parsed["slAtrMult"], 1.8)


class TestCustomStrategyRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_headers = {"Authorization": "Bearer test-admin-token"}

    def test_get_indicators(self):
        with patch("web.app.authenticate_request", return_value={"username": "admin", "role": "admin"}):
            resp = self.client.get("/api/custom-strategy/indicators", headers=self.auth_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("indicators", data)
            self.assertIn("operators", data)
            names = [i["name"] for i in data["indicators"]]
            self.assertIn("rsi", names)
            self.assertIn("volume_ratio", names)

    def test_generate_ai_strategy_endpoint(self):
        with patch("web.app.authenticate_request", return_value={"username": "admin", "role": "admin"}):
            payload = {
                "prompt": "Create an oversold bounce strategy using RSI under 30 and MACD momentum",
            }
            resp = self.client.post("/api/custom-strategy/generate-ai", json=payload, headers=self.auth_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("strategy", data)
            self.assertIn("chatResponse", data)
            self.assertIn("name", data["strategy"])
            self.assertIn("longConditions", data["strategy"])

    def test_create_and_delete_custom_strategy(self):
        with patch("web.app.authenticate_request", return_value={"username": "admin", "role": "admin"}):
            payload = {
                "name": "auto_test_strat_v1",
                "displayName": "Auto Test Strategy",
                "description": "Testing save and delete",
                "longConditions": [{"indicator": "rsi", "operator": "<", "value": 30.0}],
                "shortConditions": [{"indicator": "rsi", "operator": ">", "value": 70.0}],
                "slAtrMult": 1.5,
                "riskRewardRatio": 2.5,
                "useTrailingStop": False,
            }
            create_resp = self.client.post("/api/custom-strategy/create", json=payload, headers=self.auth_headers)
            self.assertEqual(create_resp.status_code, 200)
            self.assertEqual(create_resp.json()["status"], "success")

            # Check list endpoint
            list_resp = self.client.get("/api/custom-strategy/list", headers=self.auth_headers)
            self.assertEqual(list_resp.status_code, 200)
            names = [s["name"] for s in list_resp.json()["strategies"]]
            self.assertIn("auto_test_strat_v1", names)

            # Delete
            del_resp = self.client.delete("/api/custom-strategy/auto_test_strat_v1", headers=self.auth_headers)
            self.assertEqual(del_resp.status_code, 200)
            self.assertEqual(del_resp.json()["status"], "success")

    def test_quick_test_endpoint(self):
        with patch("web.app.authenticate_request", return_value={"username": "admin", "role": "admin"}):
            # Create synthetic df of 60 rows with integer epoch ms open_time
            import numpy as np
            base_ts = 1700000000000
            timestamps = [base_ts + i * 3600000 for i in range(60)]
            df = pd.DataFrame({
                "open_time": timestamps,
                "open": np.linspace(100, 110, 60),
                "high": np.linspace(101, 112, 60),
                "low": np.linspace(99, 109, 60),
                "close": np.linspace(100, 111, 60),
                "volume": 1000.0,
                "rsi": [25.0 if i % 10 == 0 else 50.0 for i in range(60)],
                "atr": 1.0,
                "volume_ratio": 1.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "macd_hist": 0.0,
            })
            with patch("web.routes.custom_strategy.fetch_ohlcv_with_features_df", return_value=df):
                payload = {
                    "strategy": {
                        "name": "quick_test_strat",
                        "displayName": "Quick Test",
                        "longConditions": [{"indicator": "rsi", "operator": "<", "value": 30.0}],
                        "shortConditions": [],
                        "slAtrMult": 1.5,
                        "riskRewardRatio": 2.5,
                        "useTrailingStop": False,
                    },
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "timeframe": "1h",
                }
                resp = self.client.post("/api/custom-strategy/quick-test", json=payload, headers=self.auth_headers)
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["status"], "success")
                self.assertIn("totalCandles", data)
                self.assertIn("totalTrades", data)
                self.assertIn("winRate", data)
                self.assertIn("netR", data)
