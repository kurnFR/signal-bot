"""RL model listing must not follow symlinks outside its artifact directory."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web.routes import ml_routes


class TestRLModelListPathSafety(unittest.TestCase):
    def test_external_model_manifest_symlink_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ml_models"
            rl_dir = root / "rl"
            rl_dir.mkdir(parents=True)
            outside = Path(tmp) / "outside.json"
            outside.write_text(json.dumps({
                "model_id": "outside-secret",
                "symbol": "PRIVATE",
                "market": "spot",
                "timeframe": "1h",
                "agent_type": "dqn",
                "out_of_sample_metrics": {"return_pct": 999},
            }), encoding="utf-8")
            (rl_dir / "escape.json").symlink_to(outside)
            (rl_dir / "safe.json").write_text(json.dumps({
                "model_id": "safe-model",
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1h",
                "agent_type": "dqn",
                "out_of_sample_metrics": {"return_pct": 1.5},
            }), encoding="utf-8")

            with patch.object(ml_routes, "ML_MODELS_DIR", root):
                result = ml_routes.list_rl_models()

        self.assertEqual([item["model_id"] for item in result["models"]], ["safe-model"])
        self.assertNotIn("outside-secret", str(result))
        self.assertNotIn("PRIVATE", str(result))

    def test_external_rl_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ml_models"
            root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.json").write_text(
                json.dumps({"model_id": "outside-secret", "symbol": "PRIVATE"}),
                encoding="utf-8",
            )
            (root / "rl").symlink_to(outside, target_is_directory=True)

            with patch.object(ml_routes, "ML_MODELS_DIR", root):
                result = ml_routes.list_rl_models()

        self.assertEqual(result, {"models": []})


if __name__ == "__main__":
    unittest.main()
