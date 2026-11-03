"""Crash recovery for completed online retraining experiments."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerRecoveryJournal(unittest.TestCase):
    def _model(self):
        return {
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": "trend_ema_v1",
            "registered_at": "2026-11-01T00:00:00+00:00",
            "last_retrained_at": None,
            "last_candle_count": 0,
        }

    def _registry(self, *, enabled=True):
        model = self._model()
        return {
            "enabled": enabled,
            "retrain_interval_hours": 24,
            "min_new_candles": 48,
            "max_drift_psi": 0.25,
            "registered_models": {"BTCUSDT_spot_1h_trend_ema_v1": model},
        }

    def _update(self):
        model = self._model()
        return {
            "identity": {
                field: model.get(field)
                for field in ("symbol", "market", "timeframe", "strategy_name", "registered_at")
            },
            "runtime": {
                "last_retrained_at": "2026-11-03T01:00:00+00:00",
                "last_candle_count": 720,
                "last_result": {"winner": "hist_gradient_boosting", "threshold": 0.2},
            },
        }

    def _audit(self):
        return {
            "key": "BTCUSDT_spot_1h_trend_ema_v1",
            "timestamp": "2026-11-03T01:00:00+00:00",
            "status": "retrained",
            "winner_model": "hist_gradient_boosting",
        }

    def _paths(self, tmp):
        root = Path(tmp)
        return {
            "REGISTRY_PATH": root / "registry.json",
            "REGISTRY_LOCK_PATH": root / "registry.lock",
            "AUDIT_LOG_PATH": root / "audit.json",
            "AUDIT_LOCK_PATH": root / "audit.lock",
            "RETRAINING_JOURNAL_PATH": root / "journal.json",
            "JOURNAL_LOCK_PATH": root / "journal.lock",
        }

    def test_completed_journal_recovers_registry_and_audit_then_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            paths["REGISTRY_PATH"].write_text(json.dumps(self._registry()), encoding="utf-8")
            run_id = "run-123"
            audit = self._audit()
            audit["run_id"] = run_id
            paths["RETRAINING_JOURNAL_PATH"].write_text(json.dumps({
                run_id: {
                    "key": "BTCUSDT_spot_1h_trend_ema_v1",
                    "update": self._update(),
                    "audit": self._audit(),
                }
            }), encoding="utf-8")

            with patch.multiple(online_retrainer, **paths):
                online_retrainer._recover_completed_retraining()

            saved_model = json.loads(paths["REGISTRY_PATH"].read_text(encoding="utf-8"))["registered_models"]["BTCUSDT_spot_1h_trend_ema_v1"]
            self.assertEqual(saved_model["last_candle_count"], 720)
            history = json.loads(paths["AUDIT_LOG_PATH"].read_text(encoding="utf-8"))
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["run_id"], run_id)
            self.assertFalse(paths["RETRAINING_JOURNAL_PATH"].exists())

    def test_recovery_keeps_completed_entry_when_registry_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            paths["REGISTRY_PATH"].write_text(json.dumps(self._registry(enabled=False)), encoding="utf-8")
            paths["RETRAINING_JOURNAL_PATH"].write_text(json.dumps({
                "run-456": {
                    "key": "BTCUSDT_spot_1h_trend_ema_v1",
                    "update": self._update(),
                    "audit": self._audit(),
                }
            }), encoding="utf-8")

            with patch.multiple(online_retrainer, **paths):
                online_retrainer._recover_completed_retraining()

            self.assertTrue(paths["RETRAINING_JOURNAL_PATH"].exists())
            self.assertFalse(paths["AUDIT_LOG_PATH"].exists())

    def test_audit_append_is_idempotent_for_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            entry = self._audit()
            entry["run_id"] = "run-789"
            with patch.multiple(online_retrainer, **paths):
                online_retrainer._append_audit_log(entry)
                online_retrainer._append_audit_log(entry)

            history = json.loads(paths["AUDIT_LOG_PATH"].read_text(encoding="utf-8"))
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["run_id"], "run-789")

    def test_corrupt_journal_fails_closed_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            paths["RETRAINING_JOURNAL_PATH"].write_text("{broken", encoding="utf-8")
            with patch.multiple(online_retrainer, **paths):
                with self.assertRaises(RuntimeError):
                    online_retrainer._load_retraining_journal()
            self.assertEqual(paths["RETRAINING_JOURNAL_PATH"].read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
