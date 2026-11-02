"""Online retraining status must not claim paper promotion that was not performed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerPromotionStatus(unittest.TestCase):
    def test_retraining_audit_distinguishes_requested_from_applied_promotion(self):
        experiment_result = {
            "winner": {"model_type": "hist_gradient_boosting"},
            "experiment": {
                "threshold": 0.2,
                "baseline_test_metrics": {"total_outcome_r": 1.0},
                "ml_test_metrics": {"total_outcome_r": 1.5},
            },
        }

        for auto_promote in (True, False):
            with self.subTest(auto_promote=auto_promote):
                registry = {
                    "min_new_candles": 48,
                    "registered_models": {
                        "BTCUSDT_spot_1h_trend_ema_v1": {
                            "symbol": "BTCUSDT",
                            "market": "spot",
                            "timeframe": "1h",
                            "strategy_name": "trend_ema_v1",
                            "model_type": "hist_gradient_boosting",
                            "auto_promote": auto_promote,
                            "last_retrained_at": None,
                            "last_candle_count": 0,
                        }
                    },
                }
                with patch.object(online_retrainer, "_load_registry", return_value=registry), \\
                     patch.object(online_retrainer, "_save_registry"), \\
                     patch.object(online_retrainer, "_append_audit_log") as append_audit, \\
                     patch.object(online_retrainer, "fetch_ohlcv_with_features_df", return_value=[None] * 200), \\
                     patch.object(online_retrainer, "run_real_experiment", return_value=experiment_result):
                    results = online_retrainer.execute_retraining_cycle(force=True)

                self.assertEqual(len(results), 1)
                audit = append_audit.call_args.args[0]
                self.assertEqual(audit["status"], "retrained")
                self.assertEqual(audit["auto_promote_requested"], auto_promote)
                self.assertFalse(audit["auto_promote_applied"])
                self.assertEqual(
                    audit["promotion_status"],
                    "not_implemented" if auto_promote else "disabled",
                )

    def test_public_retraining_status_includes_safe_promotion_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            audit_path.write_text(json.dumps([{
                "key": "BTCUSDT_spot_1h_trend_ema_v1",
                "timestamp": "2026-11-02T00:00:00Z",
                "status": "retrained",
                "auto_promote_requested": True,
                "auto_promote_applied": False,
                "promotion_status": "not_implemented",
                "error": "secret-token /srv/private/config.env",
            }]), encoding="utf-8")
            with patch.object(online_retrainer, "_load_registry", return_value={"registered_models": {}}), \\
                 patch.object(online_retrainer, "AUDIT_LOG_PATH", audit_path):
                status = online_retrainer.get_retraining_status()

        event = status["recent_audit"][0]
        self.assertEqual(event["status"], "retrained")
        self.assertTrue(event["auto_promote_requested"])
        self.assertFalse(event["auto_promote_applied"])
        self.assertEqual(event["promotion_status"], "not_implemented")
        self.assertNotIn("error", event)
        self.assertNotIn("secret-token", str(status))
        self.assertNotIn("/srv/private", str(status))


if __name__ == "__main__":
    unittest.main()
