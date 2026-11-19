"""ML artifact listing endpoints must reject external JSON symlinks."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web.routes import ml_routes


class TestMLArtifactListingPathSafety(unittest.TestCase):
    def test_experiment_listing_skips_external_json_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ml_results"
            root.mkdir()
            outside = Path(tmp) / "outside-experiment.json"
            outside.write_text(json.dumps({
                "experiment_id": "outside-secret",
                "experiment": {
                    "symbol": "PRIVATE",
                    "market": "spot",
                    "timeframe": "1h",
                    "base_strategy": "private-strategy",
                    "ml_test_metrics": {"total_outcome_r": 999},
                },
                "winner": {"model_type": "private-model"},
            }), encoding="utf-8")
            (root / "escape.json").symlink_to(outside)
            (root / "safe.json").write_text(json.dumps({
                "experiment_id": "safe-experiment",
                "experiment": {"symbol": "BTCUSDT", "market": "spot", "threshold": 0.4,
                               "baseline_test_metrics": {"total_outcome_r": 1},
                               "ml_test_metrics": {"trade_count": 2, "total_outcome_r": 1, "win_rate_pct": 50}},
                "winner": {"model_type": "logistic_regression"},
            }), encoding="utf-8")

            with patch.object(ml_routes, "ML_RESULTS_DIR", root):
                result = ml_routes.list_experiments()

        self.assertEqual([item["experiment_id"] for item in result["experiments"]], ["safe-experiment"])
        self.assertNotIn("outside-secret", str(result))
        self.assertNotIn("PRIVATE", str(result))

    def test_saved_model_listing_skips_external_manifest_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ml_models"
            root.mkdir()
            outside = Path(tmp) / "outside-model.json"
            outside.write_text(json.dumps({
                "model_id": "outside-secret",
                "symbol": "PRIVATE",
                "eligible_for_paper": False,
                "test_metrics": {},
            }), encoding="utf-8")
            (root / "escape.json").symlink_to(outside)
            (root / "safe.json").write_text(json.dumps({
                "model_id": "safe-model",
                "eligible_for_paper": False,
                "test_metrics": {},
            }), encoding="utf-8")

            with patch.object(ml_routes, "ML_MODELS_DIR", root):
                result = ml_routes.list_saved_models()

        self.assertEqual([item["model_id"] for item in result["models"]], ["safe-model"])
        self.assertNotIn("outside-secret", str(result))
        self.assertNotIn("PRIVATE", str(result))


if __name__ == "__main__":
    unittest.main()
