"""Invalid online retraining registry files must disable execution safely."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerRegistryValidation(unittest.TestCase):
    def test_missing_registry_keeps_documented_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            with patch.object(online_retrainer, "REGISTRY_PATH", path):
                registry = online_retrainer._load_registry()
            self.assertIs(registry["enabled"], True)
            self.assertEqual(registry["registered_models"], {})
            self.assertEqual(registry["retrain_interval_hours"], 24)

    def test_malformed_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text('{"enabled": true,', encoding="utf-8")
            with patch.object(online_retrainer, "REGISTRY_PATH", path):
                registry = online_retrainer._load_registry()
            self.assertIs(registry["enabled"], False)
            self.assertEqual(registry["registered_models"], {})

    def test_non_object_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(["not", "a", "registry"]), encoding="utf-8")
            with patch.object(online_retrainer, "REGISTRY_PATH", path):
                registry = online_retrainer._load_registry()
            self.assertIs(registry["enabled"], False)

    def test_invalid_model_entry_disables_entire_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps({
                "enabled": True,
                "registered_models": {"bad": {"symbol": "BTCUSDT"}},
            }), encoding="utf-8")
            with patch.object(online_retrainer, "REGISTRY_PATH", path):
                registry = online_retrainer._load_registry()
            self.assertIs(registry["enabled"], False)
            self.assertEqual(registry["registered_models"], {})

    def test_valid_legacy_registry_remains_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps({
                "registered_models": {"btc": {
                    "symbol": "BTCUSDT", "market": "spot",
                    "timeframe": "1h", "strategy_name": "trend_ema_v1",
                }},
            }), encoding="utf-8")
            with patch.object(online_retrainer, "REGISTRY_PATH", path):
                registry = online_retrainer._load_registry()
            self.assertIs(registry["enabled"], True)
            self.assertIn("btc", registry["registered_models"])


if __name__ == "__main__":
    unittest.main()
