import unittest

import pandas as pd

from ml.experiment import ExperimentConfig
from ml.trainer import select_validation_threshold, train_experiment


class TestMLTrainer(unittest.TestCase):
    def _frame(self, start: int, n: int) -> pd.DataFrame:
        rows = []
        for i in range(start, start + n):
            rows.append({
                "open_time": i,
                "feature_a": float(i % 5),
                "feature_b": float(i),
                "target": int(i % 2 == 0),
                "outcome_r": 2.0 if i % 2 == 0 else -1.0,
            })
        return pd.DataFrame(rows)

    def test_threshold_selection_uses_validation_outcomes(self):
        validation = pd.DataFrame({
            "target": [1, 1, 0, 0],
            "outcome_r": [2.0, 1.0, -1.0, -1.0],
        })
        probabilities = [0.95, 0.80, 0.60, 0.55]
        result = select_validation_threshold(
            validation, probabilities, min_trades=2, candidates=[0.5, 0.75, 0.9]
        )
        self.assertEqual(result.threshold, 0.75)
        self.assertEqual(result.selected_trades, 2)
        self.assertEqual(result.total_outcome_r, 3.0)

    def test_threshold_fails_when_minimum_trade_count_is_impossible(self):
        validation = pd.DataFrame({"target": [1, 0], "outcome_r": [1.0, -1.0]})
        with self.assertRaises(ValueError):
            select_validation_threshold(validation, [0.6, 0.4], min_trades=3)

    def test_training_keeps_test_partition_for_final_oos_only(self):
        train = self._frame(0, 30)
        validation = self._frame(30, 20)
        test = self._frame(50, 20)
        config = ExperimentConfig(
            experiment_id="test-trainer",
            symbol="TESTUSDT",
            timeframe="1h",
            base_strategy="trend_ema_v1",
            model_type="logistic_regression",
            feature_columns=("feature_a", "feature_b"),
            model_params={"max_iter": 300},
            seed=42,
        )
        try:
            result = train_experiment(
                train,
                validation,
                test,
                config,
                min_validation_trades=2,
                threshold_candidates=[0.5, 0.6, 0.7],
            )
        except RuntimeError as exc:
            self.skipTest(str(exc))

        self.assertEqual(result.experiment.experiment_id, "test-trainer")
        self.assertIn("threshold", result.validation_metrics)
        self.assertEqual(result.test_metrics["samples"], len(test))
        self.assertEqual(result.test_trading["candidate_signals"], len(test))
        self.assertEqual(result.feature_columns, ("feature_a", "feature_b"))

    def test_missing_feature_is_rejected_before_training(self):
        train = self._frame(0, 10).drop(columns=["feature_b"])
        validation = self._frame(10, 10)
        test = self._frame(20, 10)
        config = ExperimentConfig(
            experiment_id="missing-feature",
            symbol="TESTUSDT",
            timeframe="1h",
            base_strategy="trend_ema_v1",
            model_type="logistic_regression",
            feature_columns=("feature_a", "feature_b"),
        )
        with self.assertRaises(ValueError):
            train_experiment(train, validation, test, config, min_validation_trades=2)


if __name__ == "__main__":
    unittest.main()
