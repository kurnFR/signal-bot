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

    def test_invalid_policy_values_disable_registry(self):
        invalid_values = [
            {"retrain_interval_hours": "not-a-number"},
            {"retrain_interval_hours": float("inf")},
            {"retrain_interval_hours": -1},
            {"min_new_candles": 1.5},
            {"min_new_candles": True},
            {"min_new_candles": -1},
            {"max_drift_psi": float("nan")},
            {"max_drift_psi": -0.1},
        ]
        for policy in invalid_values:
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "registry.json"
                path.write_text(json.dumps({
                    "enabled": True,
                    "registered_models": {},
                    **policy,
                }), encoding="utf-8")
                with patch.object(online_retrainer, "REGISTRY_PATH", path):
                    registry = online_retrainer._load_registry()
                self.assertIs(registry["enabled"], False)

    def test_invalid_optional_runtime_fields_disable_registry(self):
        valid_model = {
            "symbol": "BTCUSDT", "market": "spot",
            "timeframe": "1h", "strategy_name": "trend_ema_v1",
        }
        invalid_models = [
            {"last_candle_count": -1},
            {"last_candle_count": 1.5},
            {"last_candle_count": True},
            {"last_retrained_at": 123},
            {"last_retrained_at": "not-a-timestamp"},
            {"auto_promote": "yes"},
            {"model_type": ""},
            {"model_type": 7},
        ]
        for invalid in invalid_models:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "registry.json"
                model = {**valid_model, **invalid}
                path.write_text(json.dumps({
                    "enabled": True,
                    "registered_models": {"btc": model},
                }), encoding="utf-8")
                with patch.object(online_retrainer, "REGISTRY_PATH", path):
                    registry = online_retrainer._load_registry()
                self.assertIs(registry["enabled"], False)
                self.assertEqual(registry["registered_models"], {})

    def test_valid_optional_runtime_fields_remain_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps({
                "enabled": True,
                "registered_models": {"btc": {
                    "symbol": "BTCUSDT", "market": "spot",
                    "timeframe": "1h", "strategy_name": "trend_ema_v1",
                    "last_candle_count": 100,
                    "last_retrained_at": "2026-11-03T00:00:00+00:00",
                    "auto_promote": False,
                    "model_type": "hist_gradient_boosting",
                }},
            }), encoding="utf-8")
            with patch.object(online_retrainer, "REGISTRY_PATH", path):
                registry = online_retrainer._load_registry()
            self.assertIs(registry["enabled"], True)
            self.assertEqual(registry["registered_models"]["btc"]["last_candle_count"], 100)

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
