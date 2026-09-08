import json
import tempfile
import unittest

from ml.result import ExperimentResult


class TestMLResult(unittest.TestCase):
    def test_result_is_serializable_and_research_only_by_default(self):
        result = ExperimentResult(
            experiment_id="exp-001", symbol="BTCUSDT", timeframe="1h",
            base_strategy="trend_ema_v1", model_type="logistic_regression",
            baseline_metrics={"net_pnl_r": 10},
            ml_validation_metrics={"net_pnl_r": 12},
            ml_test_metrics={"net_pnl_r": 9}, threshold=.6,
        )
        payload = json.loads(result.to_json())
        self.assertEqual(payload["status"], "research")
        self.assertEqual(payload["experiment_id"], "exp-001")

    def test_result_can_be_saved(self):
        result = ExperimentResult(
            experiment_id="exp-save", symbol="BTCUSDT", timeframe="1h",
            base_strategy="trend_ema_v1", model_type="logistic_regression",
            baseline_metrics={}, ml_validation_metrics={}, ml_test_metrics={}, threshold=.6,
        )
        with tempfile.TemporaryDirectory() as d:
            path = result.save(d)
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["symbol"], "BTCUSDT")

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            ExperimentResult(
                experiment_id="bad", symbol="BTCUSDT", timeframe="1h",
                base_strategy="trend_ema_v1", model_type="logistic_regression",
                baseline_metrics={}, ml_validation_metrics={}, ml_test_metrics={}, threshold=.6,
                status="paper",
            )


if __name__ == "__main__":
    unittest.main()
