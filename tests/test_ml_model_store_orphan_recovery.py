import tempfile
import unittest
from pathlib import Path

from ml.artifacts import ModelArtifact
from ml.model_store import ModelStore


class TestMLModelStoreOrphanRecovery(unittest.TestCase):
    def artifact(self):
        return ModelArtifact(
            model_id="orphan-recovery-v1", experiment_id="exp-1",
            model_type="logistic_regression", base_strategy="trend_ema",
            feature_columns=("rsi", "atr"), strategy_params={}, model_params={},
            threshold=0.6, train_metrics={}, validation_metrics={}, test_metrics={},
        )

    def test_save_recovers_regular_weights_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orphan = root / "orphan-recovery-v1.joblib"
            orphan.write_bytes(b"incomplete weights from interrupted save")
            store = ModelStore(root)

            model_path, manifest_path = store.save(self.artifact(), {"recovered": True})

            self.assertTrue(model_path.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(store.load(self.artifact(), ["rsi", "atr"]), {"recovered": True})
            self.assertEqual(list(root.glob(".model-weights-*.tmp")), [])

    def test_manifest_without_weights_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "orphan-recovery-v1.json"
            manifest.write_text('{"model_id":"existing"}', encoding="utf-8")
            store = ModelStore(root)

            with self.assertRaises(FileExistsError):
                store.save(self.artifact(), {"replacement": True})

            self.assertEqual(manifest.read_text(encoding="utf-8"), '{"model_id":"existing"}')
            self.assertFalse((root / "orphan-recovery-v1.joblib").exists())

    def test_symlink_weights_without_manifest_are_not_followed_or_removed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            target = Path(outside) / "protected.joblib"
            target.write_bytes(b"protected")
            (root / "orphan-recovery-v1.joblib").symlink_to(target)
            store = ModelStore(root)

            with self.assertRaises(FileExistsError):
                store.save(self.artifact(), {"replacement": True})

            self.assertEqual(target.read_bytes(), b"protected")
            self.assertTrue((root / "orphan-recovery-v1.joblib").is_symlink())


if __name__ == "__main__":
    unittest.main()
