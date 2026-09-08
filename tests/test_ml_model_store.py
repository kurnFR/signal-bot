import tempfile
import unittest

from ml.artifacts import ModelArtifact
from ml.model_store import ModelStore


class TestMLModelStore(unittest.TestCase):
    def artifact(self):
        return ModelArtifact(
            model_id="store-test-v1", experiment_id="exp-1",
            model_type="logistic_regression", base_strategy="trend_ema",
            feature_columns=("rsi", "atr"), strategy_params={}, model_params={},
            threshold=.6, train_metrics={}, validation_metrics={}, test_metrics={},
        )

    def test_save_and_load_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            manifest = store.load_manifest(artifact.model_id)
            self.assertEqual(manifest["model_id"], artifact.model_id)

    def test_feature_schema_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            with self.assertRaises(ValueError):
                store.load(artifact, ["atr", "rsi"])

    def test_duplicate_save_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            with self.assertRaises(FileExistsError):
                store.save(artifact, {"dummy": True})


if __name__ == "__main__":
    unittest.main()
