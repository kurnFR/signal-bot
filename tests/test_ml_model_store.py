import tempfile
import unittest
from pathlib import Path

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

    def test_saved_weights_include_digest_and_load_detects_tampering(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            manifest_path = Path(d) / f"{artifact.model_id}.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertRegex(manifest["weights_sha256"], r"^[a-f0-9]{64}$")
            weight_path = Path(d) / f"{artifact.model_id}.joblib"
            weight_path.write_bytes(weight_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "does not match"):
                store.load(artifact, ["rsi", "atr"])

    def test_feature_schema_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            with self.assertRaises(ValueError):
                store.load(artifact, ["atr", "rsi"])

    def test_save_rejects_weight_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = Path(d)
            target = Path(outside) / "protected.joblib"
            target.write_bytes(b"keep")
            (root / "store-test-v1.joblib").symlink_to(target)
            store = ModelStore(root)
            with self.assertRaises(FileExistsError):
                store.save(self.artifact(), {"dummy": True})
            self.assertEqual(target.read_bytes(), b"keep")

    def test_save_rejects_manifest_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = Path(d)
            target = Path(outside) / "protected.json"
            target.write_text('{"protected": true}', encoding="utf-8")
            (root / "store-test-v1.json").symlink_to(target)
            store = ModelStore(root)
            with self.assertRaises(FileExistsError):
                store.save(self.artifact(), {"dummy": True})
            self.assertEqual(target.read_text(encoding="utf-8"), '{"protected": true}')

    def test_load_rejects_weight_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            weight_path = Path(d) / "store-test-v1.joblib"
            weight_path.unlink()
            target = Path(outside) / "external.joblib"
            target.write_bytes(b"not a model")
            weight_path.symlink_to(target)
            with self.assertRaises(ValueError):
                store.load(artifact, ["rsi", "atr"])

    def test_load_manifest_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            store = ModelStore(d)
            target = Path(outside) / "external.json"
            target.write_text('{"model_id": "external"}', encoding="utf-8")
            (Path(d) / "store-test-v1.json").symlink_to(target)
            with self.assertRaises(ValueError):
                store.load_manifest("store-test-v1")

    def test_duplicate_save_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            store = ModelStore(d)
            artifact = self.artifact()
            store.save(artifact, {"dummy": True})
            with self.assertRaises(FileExistsError):
                store.save(artifact, {"dummy": True})


if __name__ == "__main__":
    unittest.main()
