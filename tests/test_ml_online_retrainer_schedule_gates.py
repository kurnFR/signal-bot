"""Retraining schedule gates must honor registry settings and explicit force."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerScheduleGates(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
        self.registry = {"retrain_interval_hours": 24, "min_new_candles": 48}

    def test_first_retraining_is_due(self):
        cfg = {"last_retrained_at": None, "last_candle_count": 0}
        self.assertTrue(online_retrainer._is_retraining_due(cfg, self.registry, 10, self.now))

    def test_both_interval_and_candle_thresholds_are_required(self):
        cfg = {
            "last_retrained_at": (self.now - timedelta(hours=23)).isoformat(),
            "last_candle_count": 100,
        }
        self.assertFalse(online_retrainer._is_retraining_due(cfg, self.registry, 200, self.now))
        cfg["last_retrained_at"] = (self.now - timedelta(hours=24)).isoformat()
        self.assertFalse(online_retrainer._is_retraining_due(cfg, self.registry, 147, self.now))
        self.assertTrue(online_retrainer._is_retraining_due(cfg, self.registry, 148, self.now))

    def test_drift_gate_triggers_early_only_when_min_candles_met(self):
        cfg = {
            "last_retrained_at": (self.now - timedelta(hours=1)).isoformat(),
            "last_candle_count": 100,
        }
        reg = {**self.registry, "max_drift_psi": 0.25}
        # Below min_new_candles (147 < 148): high drift still waits for min_new_candles
        self.assertFalse(online_retrainer._is_retraining_due(cfg, reg, 147, self.now, drift_psi=0.60))
        # At min_new_candles (148) with high drift: retrains before 24h interval expires
        self.assertTrue(online_retrainer._is_retraining_due(cfg, reg, 148, self.now, drift_psi=0.25))
        # Below drift threshold and before interval: skips
        self.assertFalse(online_retrainer._is_retraining_due(cfg, reg, 200, self.now, drift_psi=0.10))

    def test_recent_feature_drift_computation(self):
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(7)
        base = pd.DataFrame({
            "rsi": rng.normal(50, 5, 160),
            "atr_pct": rng.normal(0.01, 0.002, 160),
            "volume_ratio": rng.normal(1.0, 0.2, 160),
            "macd_hist": rng.normal(0.0, 0.5, 160),
        })
        shifted = pd.DataFrame({
            "rsi": rng.normal(80, 5, 50),
            "atr_pct": rng.normal(0.05, 0.01, 50),
            "volume_ratio": rng.normal(4.0, 0.5, 50),
            "macd_hist": rng.normal(3.0, 0.5, 50),
        })
        df = pd.concat([base, shifted], ignore_index=True)
        psi = online_retrainer._compute_recent_feature_drift(df, min_new_candles=48)
        self.assertGreater(psi, 0.25)
        self.assertEqual(online_retrainer._compute_recent_feature_drift([None] * 200), 0.0)

    def test_explicit_force_bypasses_per_model_gates(self):
        cfg = {
            "last_retrained_at": self.now.isoformat(),
            "last_candle_count": 100,
        }
        self.assertTrue(online_retrainer._is_retraining_due(cfg, self.registry, 100, self.now, force=True))

    def test_invalid_timestamp_fails_closed_unless_forced(self):
        cfg = {"last_retrained_at": "not-a-timestamp", "last_candle_count": 0}
        self.assertFalse(online_retrainer._is_retraining_due(cfg, self.registry, 100, self.now))
        self.assertTrue(online_retrainer._is_retraining_due(cfg, self.registry, 0, self.now, force=True))

    def test_disabled_registry_blocks_even_forced_cycle(self):
        registry = {"enabled": False, "registered_models": {"model": {
            "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
            "strategy_name": "trend_ema_v1",
        }}}
        with patch.object(online_retrainer, "_load_registry", return_value=registry) as load, patch.object(online_retrainer, "fetch_ohlcv_with_features_df") as fetch, patch.object(online_retrainer, "_save_registry") as save:
            self.assertEqual(online_retrainer.execute_retraining_cycle(force=True), [])
        fetch.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
