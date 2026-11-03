"""Paper runtime must deserialize the exact model bytes whose digest was checked."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.artifacts import ModelArtifact
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


if __name__ == "__main__":
    unittest.main()
