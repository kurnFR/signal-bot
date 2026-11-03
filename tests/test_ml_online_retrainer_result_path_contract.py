"""The online retrainer must consume the result-file Path returned by the experiment runner."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer
from ml.artifacts import CURRENT_EVALUATION_PROTOCOL_VERSION


class TestOnlineRetrainerResultPathContract(unittest.TestCase):
    def _payload(self):
        return {
            "experiment_id": "exp-retrain-verified",
            "winner": {"model_type": "hist_gradient_boosting"},
            "experiment": {
                "experiment_id": "exp-retrain-verified",
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

    def test_accepts_legacy_flat_baseline_test_metrics(self):
        payload = self._payload()
        experiment = dict(payload["experiment"])
        experiment["baseline_test_metrics"] = experiment["baseline_metrics"]["test"]
        experiment.pop("baseline_metrics")
        payload["experiment"] = experiment

        loaded = online_retrainer._load_experiment_result(payload)

        self.assertEqual(loaded["experiment"]["baseline_test_metrics"]["total_outcome_r"], 1.0)

    def test_rejects_malformed_result_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "experiment.json"
            result_path.write_text(json.dumps({"winner": {}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "winner.model_type"):
                online_retrainer._load_experiment_result(result_path)

    @staticmethod
    def _write_model_artifact(root: Path, model_id: str):
        root.mkdir(parents=True, exist_ok=True)
        weights = b"verified test model weights"
        (root / f"{model_id}.joblib").write_bytes(weights)
        manifest = {
            "model_id": model_id,
            "weights_sha256": hashlib.sha256(weights).hexdigest(),\n            "evaluation_protocol_version": CURRENT_EVALUATION_PROTOCOL_VERSION,
        }
        (root / f"{model_id}.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_rejects_missing_or_mismatched_model_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "artifact"):
                online_retrainer._verify_persisted_model_artifact("exp-retrain-verified", root)

            self._write_model_artifact(root, "exp-retrain-verified")
            (root / "exp-retrain-verified.joblib").write_bytes(b"tampered weights")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                online_retrainer._verify_persisted_model_artifact("exp-retrain-verified", root)

    def test_cycle_does_not_checkpoint_when_model_artifact_is_missing(self):
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
            models_root = Path(tmp) / "missing_models"
            with patch.object(online_retrainer, "MODEL_ARTIFACTS_DIR", models_root), patch.object(
                online_retrainer, "_load_registry", return_value=registry
            ), patch.object(
                online_retrainer, "fetch_ohlcv_with_features_df", return_value=[None] * 200
            ), patch.object(
                online_retrainer, "run_real_experiment", return_value=result_path
            ), patch.object(online_retrainer, "_append_audit_log") as append_audit, patch.object(
                online_retrainer, "_persist_retraining_updates"
            ) as persist:
                results = online_retrainer._execute_retraining_cycle_unlocked(force=True)

        self.assertEqual(results, [])
        persist.assert_not_called()
        append_audit.assert_called_once()
        self.assertEqual(append_audit.call_args.args[0]["status"], "failed")

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
            models_root = Path(tmp) / "ml_models"
            self._write_model_artifact(models_root, "exp-retrain-verified")
            with patch.object(online_retrainer, "MODEL_ARTIFACTS_DIR", models_root), patch.object(
                online_retrainer, "_load_registry", return_value=registry
            ), patch.object(
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
