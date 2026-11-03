"""Concurrent retraining checkpoints must merge into the latest registry safely."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerRegistryConcurrency(unittest.TestCase):
    def _registry(self, models):
        return {
            "enabled": True,
            "retrain_interval_hours": 24,
            "min_new_candles": 48,
            "max_drift_psi": 0.25,
            "registered_models": models,
        }

    def _model(self, *, registered_at="2026-01-01T00:00:00+00:00", auto_promote=False):
        return {
            "key": "BTCUSDT_spot_1h_trend_ema_v1",
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": "trend_ema_v1",
            "model_type": "hist_gradient_boosting",
            "registered_at": registered_at,
            "auto_promote": auto_promote,
            "last_retrained_at": None,
            "last_candle_count": 0,
        }

    def _update(self, model):
        return {
            "identity": {
                field: model.get(field)
                for field in ("symbol", "market", "timeframe", "strategy_name", "registered_at")
            },
            "runtime": {
                "last_retrained_at": "2026-11-03T00:00:00+00:00",
                "last_candle_count": 500,
                "last_result": {"winner": "hist_gradient_boosting"},
            },
        }

    def test_checkpoint_preserves_model_registered_during_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            original = self._model()
            newly_registered = self._model()
            newly_registered["key"] = "ETHUSDT_spot_1h_trend_ema_v1"
            newly_registered["symbol"] = "ETHUSDT"
            path.write_text(json.dumps(self._registry({
                original["key"]: original,
                newly_registered["key"]: newly_registered,
            })), encoding="utf-8")

            with patch.object(online_retrainer, "REGISTRY_PATH", path), patch.object(
                online_retrainer, "REGISTRY_LOCK_PATH", Path(tmp) / "registry.lock"
            ):
                online_retrainer._persist_retraining_updates({
                    original["key"]: self._update(original)
                })

            saved = json.loads(path.read_text(encoding="utf-8"))["registered_models"]
            self.assertIn(newly_registered["key"], saved)
            self.assertEqual(saved[original["key"]]["last_candle_count"], 500)

    def test_checkpoint_does_not_resurrect_model_removed_during_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(self._registry({})), encoding="utf-8")
            original = self._model()

            with patch.object(online_retrainer, "REGISTRY_PATH", path), patch.object(
                online_retrainer, "REGISTRY_LOCK_PATH", Path(tmp) / "registry.lock"
            ):
                online_retrainer._persist_retraining_updates({
                    original["key"]: self._update(original)
                })

            saved = json.loads(path.read_text(encoding="utf-8"))["registered_models"]
            self.assertEqual(saved, {})

    def test_checkpoint_does_not_overwrite_re_registered_model_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            original = self._model()
            replacement = self._model(registered_at="2026-11-03T00:00:00+00:00", auto_promote=True)
            path.write_text(json.dumps(self._registry({original["key"]: replacement})), encoding="utf-8")

            with patch.object(online_retrainer, "REGISTRY_PATH", path), patch.object(
                online_retrainer, "REGISTRY_LOCK_PATH", Path(tmp) / "registry.lock"
            ):
                online_retrainer._persist_retraining_updates({
                    original["key"]: self._update(original)
                })

            saved = json.loads(path.read_text(encoding="utf-8"))["registered_models"][original["key"]]
            self.assertIs(saved["auto_promote"], True)
            self.assertEqual(saved["registered_at"], replacement["registered_at"])
            self.assertIsNone(saved["last_retrained_at"])

    def test_checkpoint_never_reenables_invalid_or_disabled_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            disabled = self._registry({})
            disabled["enabled"] = False
            path.write_text(json.dumps(disabled), encoding="utf-8")
            original = self._model()

            with patch.object(online_retrainer, "REGISTRY_PATH", path), patch.object(
                online_retrainer, "REGISTRY_LOCK_PATH", Path(tmp) / "registry.lock"
            ):
                online_retrainer._persist_retraining_updates({
                    original["key"]: self._update(original)
                })

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(saved["enabled"], False)
            self.assertEqual(saved["registered_models"], {})

    def test_registration_uses_locked_latest_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            existing = self._model()
            path.write_text(json.dumps(self._registry({existing["key"]: existing})), encoding="utf-8")

            with patch.object(online_retrainer, "REGISTRY_PATH", path), patch.object(
                online_retrainer, "REGISTRY_LOCK_PATH", Path(tmp) / "registry.lock"
            ):
                created = online_retrainer.register_model_for_retraining(
                    "ETHUSDT", market="spot", timeframe="4h", strategy_name="breakout"
                )

            saved = json.loads(path.read_text(encoding="utf-8"))["registered_models"]
            self.assertIn(existing["key"], saved)
            self.assertIn(created["key"], saved)


if __name__ == "__main__":
    unittest.main()
