"""Paper runtime must deserialize the exact model bytes whose digest was checked."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.artifacts import CURRENT_EVALUATION_PROTOCOL_VERSION, ModelArtifact
from ml.model_store import ModelStore
from paper.engine import _load_verified_ml_model


class TestPaperMLModelLoading(unittest.TestCase):
    def test_runtime_uses_same_open_file_for_hash_and_deserialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ModelStore(tmp)
            artifact = ModelArtifact(
                model_id="paper-loader-test",
                experiment_id="experiment-1",
                model_type="logistic_regression",
                base_strategy="trend_ema",
                feature_columns=("rsi", "atr"),
                strategy_params={},
                model_params={},
                threshold=0.6,
                train_metrics={},
                validation_metrics={},
                test_metrics={
                    "trade_count": 30,
                    "profit_factor": 1.2,
                    "max_drawdown_pct": 10.0,
                    "net_pnl": 12.0,
                },
                eligible_for_paper=True,
                evaluation_protocol_version=CURRENT_EVALUATION_PROTOCOL_VERSION,
            )
            store.save(artifact, {"source": "verified"})
            weight_path = Path(tmp) / "paper-loader-test.joblib"

            import joblib
            original_load = joblib.load

            def replace_path_then_load(handle):
                weight_path.unlink()
                weight_path.write_bytes(b"unverified replacement")
                return original_load(handle)

            with patch("ml.model_store.joblib.load", side_effect=replace_path_then_load):
                loaded_artifact, loaded_model = _load_verified_ml_model(
                    "paper-loader-test", models_dir=tmp
                )

        self.assertEqual(loaded_artifact.model_id, "paper-loader-test")
        self.assertEqual(loaded_model, {"source": "verified"})

        from ml.strategy import MLSignalFilter
        loaded_filter = MLSignalFilter.from_artifact(
            artifact=loaded_artifact,
            model=loaded_model,
            base_strategy=lambda *_args, **_kwargs: [],
        )
        self.assertIs(loaded_filter.model, loaded_model)
        self.assertEqual(loaded_filter.model_id, "paper-loader-test")


    def test_runtime_uses_the_manifest_that_passed_eligibility_validation(self):
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            store = ModelStore(tmp)
            artifact = ModelArtifact(
                model_id="paper-manifest-test",
                experiment_id="experiment-1",
                model_type="logistic_regression",
                base_strategy="trend_ema",
                feature_columns=("rsi", "atr"),
                strategy_params={},
                model_params={},
                threshold=0.6,
                train_metrics={},
                validation_metrics={},
                test_metrics={
                    "trade_count": 30,
                    "profit_factor": 1.2,
                    "max_drawdown_pct": 10.0,
                    "net_pnl": 12.0,
                },
                eligible_for_paper=True,
                evaluation_protocol_version=CURRENT_EVALUATION_PROTOCOL_VERSION,
            )
            store.save(artifact, {"source": "validated-manifest"})
            manifest_path = Path(tmp) / "paper-manifest-test.json"
            original_load = ModelStore.load

            def replace_manifest_then_load(instance, loaded_artifact, expected_features, *, manifest=None):
                replacement = json.loads(manifest_path.read_text(encoding="utf-8"))
                replacement["weights_sha256"] = "0" * 64
                manifest_path.write_text(json.dumps(replacement), encoding="utf-8")
                return original_load(
                    instance, loaded_artifact, expected_features, manifest=manifest
                )

            with patch.object(ModelStore, "load", replace_manifest_then_load):
                loaded_artifact, loaded_model = _load_verified_ml_model(
                    "paper-manifest-test", models_dir=tmp
                )

        self.assertEqual(loaded_artifact.model_id, "paper-manifest-test")
        self.assertEqual(loaded_model, {"source": "validated-manifest"})

if __name__ == "__main__":
    unittest.main()
