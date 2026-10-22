import tempfile
import unittest
from pathlib import Path

from ml.artifacts import ModelArtifact, paper_eligibility, resolve_model_artifact_paths


class TestMLArtifacts(unittest.TestCase):
    def test_manifest_round_trip(self):
        artifact = ModelArtifact(
            model_id="ml-test-v1", experiment_id="exp-1",
            model_type="random_forest", base_strategy="trend_ema",
            feature_columns=("rsi", "atr"), strategy_params={"x": 1},
            model_params={"n_estimators": 10}, threshold=0.65,
            train_metrics={"accuracy": .6}, validation_metrics={"accuracy": .61},
            test_metrics={"trade_count": 40, "profit_factor": 1.2,
                          "max_drawdown_pct": 10, "net_pnl": 100},
        )
        with tempfile.TemporaryDirectory() as d:
            path = artifact.save_manifest(d)
            self.assertTrue(Path(path).exists())

    def test_eligibility_is_conservative(self):
        good = {"trade_count": 30, "profit_factor": 1.0,
                "max_drawdown_pct": 25, "net_pnl": 0}
        self.assertTrue(paper_eligibility(test_metrics=good))
        bad = dict(good, trade_count=29)
        self.assertFalse(paper_eligibility(test_metrics=bad))


    def test_runtime_paths_require_files_inside_artifact_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ml_models"
            root.mkdir()
            (root / "model-v1.json").write_text("{}", encoding="utf-8")
            (root / "model-v1.joblib").write_bytes(b"model")
            manifest, weights = resolve_model_artifact_paths("model-v1", root)
            self.assertEqual(manifest.parent, root.resolve())
            self.assertEqual(weights.parent, root.resolve())

    def test_runtime_paths_reject_traversal_model_ids(self):
        with tempfile.TemporaryDirectory() as d:
            for model_id in ("../outside", "..", "model/../../outside", ""):
                with self.subTest(model_id=model_id):
                    with self.assertRaises(ValueError):
                        resolve_model_artifact_paths(model_id, d)

    def test_runtime_paths_reject_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = Path(d) / "ml_models"
            root.mkdir()
            outside_manifest = Path(outside) / "model-v1.json"
            outside_weights = Path(outside) / "model-v1.joblib"
            outside_manifest.write_text("{}", encoding="utf-8")
            outside_weights.write_bytes(b"model")
            (root / "model-v1.json").symlink_to(outside_manifest)
            (root / "model-v1.joblib").symlink_to(outside_weights)
            with self.assertRaises(ValueError):
                resolve_model_artifact_paths("model-v1", root)

    def test_runtime_paths_require_both_regular_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "model-v1.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                resolve_model_artifact_paths("model-v1", root)


if __name__ == "__main__":
    unittest.main()
