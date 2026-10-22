import json
import tempfile
import unittest
from pathlib import Path

from ml.artifacts import ModelArtifact, load_eligible_paper_artifact, paper_eligibility, resolve_model_artifact_paths, resolve_model_manifest_path


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


    def test_save_manifest_replaces_symlink_without_modifying_target(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = Path(d) / "ml_models"
            root.mkdir()
            target = Path(outside) / "protected.json"
            target.write_text('{"protected": true}', encoding="utf-8")
            link = root / "model-v1.json"
            link.symlink_to(target)
            artifact = ModelArtifact(
                model_id="model-v1", experiment_id="exp-1",
                model_type="random_forest", base_strategy="trend_ema",
                feature_columns=("rsi",), strategy_params={}, model_params={},
                threshold=0.5, train_metrics={}, validation_metrics={},
                test_metrics={}, eligible_for_paper=False,
            )
            saved = artifact.save_manifest(root)
            self.assertEqual(saved, link)
            self.assertFalse(link.is_symlink())
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"protected": True})
            self.assertEqual(json.loads(link.read_text(encoding="utf-8"))["model_id"], "model-v1")
            self.assertEqual(list(root.glob(".model-manifest-*.tmp")), [])

    def test_runtime_manifest_must_still_pass_eligibility_gate(self):
        manifest = ModelArtifact(
            model_id="model-v1", experiment_id="exp-1",
            model_type="random_forest", base_strategy="trend_ema",
            feature_columns=("rsi",), strategy_params={}, model_params={},
            threshold=0.5, train_metrics={}, validation_metrics={},
            test_metrics={"trade_count": 40, "profit_factor": 1.2,
                          "max_drawdown_pct": 10, "net_pnl": 10},
            eligible_for_paper=True,
        ).manifest()
        self.assertEqual(load_eligible_paper_artifact(manifest, "model-v1").model_id, "model-v1")

        bad_metrics = dict(manifest, test_metrics={"trade_count": 4, "profit_factor": 2,
                                                  "max_drawdown_pct": 2, "net_pnl": 100})
        with self.assertRaisesRegex(ValueError, "eligibility"):
            load_eligible_paper_artifact(bad_metrics, "model-v1")

        flagged_off = dict(manifest, eligible_for_paper=False)
        with self.assertRaisesRegex(ValueError, "not marked eligible"):
            load_eligible_paper_artifact(flagged_off, "model-v1")

        mismatched_id = dict(manifest, model_id="other-model")
        with self.assertRaisesRegex(ValueError, "ID does not match"):
            load_eligible_paper_artifact(mismatched_id, "model-v1")


    def test_model_artifact_rejects_unsafe_model_ids(self):
        for model_id in ("../outside", "..", "nested/model", ""):
            with self.subTest(model_id=model_id):
                with self.assertRaises(ValueError):
                    ModelArtifact(
                        model_id=model_id, experiment_id="exp-1",
                        model_type="random_forest", base_strategy="trend_ema",
                        feature_columns=("rsi",), strategy_params={},
                        model_params={}, threshold=0.5, train_metrics={},
                        validation_metrics={}, test_metrics={},
                    )

    def test_eligibility_is_conservative(self):
        good = {"trade_count": 30, "profit_factor": 1.0,
                "max_drawdown_pct": 25, "net_pnl": 0}
        self.assertTrue(paper_eligibility(test_metrics=good))
        bad = dict(good, trade_count=29)
        self.assertFalse(paper_eligibility(test_metrics=bad))


    def test_manifest_only_resolver_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = Path(d)
            target = Path(outside) / "external.json"
            target.write_text("{}", encoding="utf-8")
            (root / "model-v1.json").symlink_to(target)
            with self.assertRaises(ValueError):
                resolve_model_manifest_path("model-v1", root)

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
