"""The online retrainer must consume the result-file Path returned by the experiment runner."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerResultPathContract(unittest.TestCase):
    def _payload(self):
        return {
            "winner": {"model_type": "hist_gradient_boosting"},
            "experiment": {
                "threshold": 0.2,
                "baseline_metrics": {
                    "validation": {"total_outcome_r": 0.8},
                    "test": {"total_outcome_r": 1.0},
                },
                "ml_test_metrics": {"total_outcome_r": 1.5},
            },
        }

    def test_loads_result_json_from_runner_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "experiment.json"
            result_path.write_text(json.dumps(self._payload()), encoding="utf-8")

            loaded = online_retrainer._load_experiment_result(result_path)

        self.assertEqual(loaded["winner"]["model_type"], "hist_gradient_boosting")
        self.assertEqual(loaded["experiment"]["threshold"], 0.2)
        self.assertEqual(loaded["experiment"]["baseline_test_metrics"]["total_outcome_r"], 1.0)

    def test_rejects_malformed_result_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "experiment.json"
            result_path.write_text(json.dumps({"winner": {}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "winner.model_type"):
                online_retrainer._load_experiment_result(result_path)

    def test_cycle_checkpoints_when_runner_returns_result_path(self):
        model_key = "BTCUSDT_spot_1h_trend_ema_v1"
        model = {
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": "trend_ema_v1",
            "model_type": "hist_gradient_boosting",
            "registered_at": "2026-01-01T00:00:00+00:00",
            "auto_promote": False,
            "last_retrained_at": None,
            "last_candle_count": 0,
        }
        registry = {
            "enabled": True,
            "min_new_candles": 48,
            "registered_models": {model_key: model},
        }

        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "experiment.json"
            result_path.write_text(json.dumps(self._payload()), encoding="utf-8")
            with patch.object(online_retrainer, "_load_registry", return_value=registry), patch.object(
                online_retrainer, "fetch_ohlcv_with_features_df", return_value=[None] * 200
            ), patch.object(online_retrainer, "run_real_experiment", return_value=result_path), patch.object(
                online_retrainer, "_append_audit_log"
            ) as append_audit, patch.object(
                online_retrainer, "_persist_retraining_updates"
            ) as persist:
                results = online_retrainer._execute_retraining_cycle_unlocked(force=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["winner_model"], "hist_gradient_boosting")
        self.assertEqual(results[0]["status"], "retrained")
        self.assertEqual(results[0]["baseline_test_r"], 1.0)
        append_audit.assert_called_once()
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[0][model_key]["runtime"]["last_candle_count"], 200)


if __name__ == "__main__":
    unittest.main()
