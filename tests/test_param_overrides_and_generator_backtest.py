"""Strict strategy-param validation + generator-aware backtest runs.

Covers two review findings:
1. Paper parameter validation accepted unknown keys and invalid value
   types. Overrides are now validated against BASE_PARAMS (the flat knob
   dict the paper engine merges into) at every write path.
2. Backtest Studio had no generator-aware run path: ml_signal_generator_v1
   could be selected but no model could be supplied, so it silently
   produced zero trades. /api/backtest/run now mirrors the paper rules.
"""
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

from backtest.params import validate_param_overrides
from web.routes import backtest


class TestValidateParamOverrides(unittest.TestCase):
    def test_valid_overrides_pass_through(self):
        out = validate_param_overrides(
            {"RSI_OVERSOLD": 28, "MIN_CONFLUENCE_SCORE": 4.5, "USE_TRAILING_STOP": True}
        )
        self.assertEqual(out["RSI_OVERSOLD"], 28)
        self.assertEqual(out["MIN_CONFLUENCE_SCORE"], 4.5)
        self.assertIs(out["USE_TRAILING_STOP"], True)

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_param_overrides({"RSI_OVERSOLD": 28, "rsi_oversold": 28})
        self.assertIn("rsi_oversold", str(ctx.exception))

    def test_engine_injected_keys_are_not_user_input(self):
        for key in ("symbol", "ml_generator_model_id", "htf_df"):
            with self.assertRaises(ValueError):
                validate_param_overrides({key: "x"})

    def test_wrong_types_rejected(self):
        for bad in (
            {"RSI_OVERSOLD": "28"},          # string for int knob
            {"RSI_OVERSOLD": 28.5},          # non-integral float for int knob
            {"RSI_OVERSOLD": True},          # bool is not an int here
            {"USE_TRAILING_STOP": 1},        # int for bool knob
            {"VOLUME_MULTIPLIER": "high"},   # string for float knob
            {"PAIRS_BASE_SYMBOL": 42},       # number for str knob
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_param_overrides(bad)

    def test_non_finite_rejected_and_int_coercion(self):
        with self.assertRaises(ValueError):
            validate_param_overrides({"VOLUME_MULTIPLIER": float("nan")})
        with self.assertRaises(ValueError):
            validate_param_overrides({"VOLUME_MULTIPLIER": float("inf")})
        # Integral floats for int knobs are coerced; ints for float knobs pass.
        self.assertEqual(validate_param_overrides({"RSI_OVERSOLD": 28.0})["RSI_OVERSOLD"], 28)
        self.assertEqual(validate_param_overrides({"MIN_CONFLUENCE_SCORE": 4})["MIN_CONFLUENCE_SCORE"], 4)


class TestPaperWritePathsValidate(unittest.TestCase):
    """Validation fires before any DB access in both paper write paths."""

    def test_set_paper_config_rejects_unknown_param(self):
        from paper.engine import set_paper_config

        with self.assertRaises(ValueError):
            set_paper_config(
                "BTCUSDT", "spot", "1d", "confluence_ensemble_v1", True,
                strategy_params={"BOGUS_KEY": 1},
            )

    def test_update_paper_deployment_rejects_bad_type(self):
        from paper.engine import update_paper_deployment

        with self.assertRaises(ValueError):
            update_paper_deployment(1, strategy_params={"RSI_OVERSOLD": "low"})


def _candles(rows=60):
    return pd.DataFrame({
        "open_time": [1_700_000_000_000 + i * 3_600_000 for i in range(rows)],
        "open": [100.0 + i for i in range(rows)],
        "high": [102.0 + i for i in range(rows)],
        "low": [99.0 + i for i in range(rows)],
        "close": [101.0 + i for i in range(rows)],
        "volume": [10.0] * rows,
    })


_METRICS = {
    "total_trades": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0,
    "profit_factor": 0.0, "max_drawdown_r": 0.0,
}


class TestGeneratorAwareBacktestRun(unittest.TestCase):
    def _run(self, request, strategy_fn=None):
        captured = {}

        def stub(frame, params):
            captured.update(params)
            return []

        with patch.dict(backtest.STRATEGIES, {"ml_signal_generator_v1": strategy_fn or stub}), \
             patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=_candles()), \
             patch.object(backtest, "compute_metrics", return_value=_METRICS), \
             patch.object(backtest, "simulate_equity_curve", return_value={"equity": []}), \
             patch.object(backtest, "insert_backtest_run", return_value=1), \
             patch.object(backtest, "insert_backtest_trades"):
            result = backtest.execute_backtest(request)
        return result, captured

    def test_generator_strategy_requires_a_model(self):
        request = backtest.BacktestRunRequest(
            symbol="BTCUSDT", strategy="ml_signal_generator_v1", save_to_db=False
        )
        with patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=_candles()):
            with self.assertRaises(HTTPException) as raised:
                backtest.execute_backtest(request)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Select an independent ML generator model", raised.exception.detail)

    def test_model_id_rejected_for_other_strategies(self):
        request = backtest.BacktestRunRequest(
            symbol="BTCUSDT", strategy="confluence_ensemble_v1",
            ml_generator_model_id="gen-1", save_to_db=False,
        )
        with patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=_candles()):
            with self.assertRaises(HTTPException) as raised:
                backtest.execute_backtest(request)
        self.assertEqual(raised.exception.status_code, 400)

    def test_ineligible_model_conflicts(self):
        request = backtest.BacktestRunRequest(
            symbol="BTCUSDT", strategy="ml_signal_generator_v1",
            ml_generator_model_id="gen-1", save_to_db=False,
        )
        with patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=_candles()), \
             patch("ml.generator_artifacts.load_generator_artifact", side_effect=RuntimeError("nope")):
            with self.assertRaises(HTTPException) as raised:
                backtest.execute_backtest(request)
        self.assertEqual(raised.exception.status_code, 409)

    def test_eligible_model_reaches_strategy_params(self):
        request = backtest.BacktestRunRequest(
            symbol="BTCUSDT", strategy="ml_signal_generator_v1",
            ml_generator_model_id="gen-1", save_to_db=False,
        )
        with patch("ml.generator_artifacts.load_generator_artifact", return_value=object()):
            _, captured = self._run(request)
        self.assertEqual(captured.get("ml_generator_model_id"), "gen-1")
        self.assertIs(captured.get("_require_generator_eligible"), True)

    def test_run_rejects_unknown_param_key(self):
        request = backtest.BacktestRunRequest(
            symbol="BTCUSDT", strategy="confluence_ensemble_v1",
            params={"not_a_knob": 1}, save_to_db=False,
        )
        with patch.object(backtest, "fetch_ohlcv_with_features_df", return_value=_candles()):
            with self.assertRaises(HTTPException) as raised:
                backtest.execute_backtest(request)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("not_a_knob", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
