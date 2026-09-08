import unittest

from ml.experiment import ExperimentConfig


class TestMLExperimentContract(unittest.TestCase):
    def test_config_is_deterministic_and_serializable(self):
        config = ExperimentConfig(
            experiment_id="exp-test-001",
            symbol="BTCUSDT",
            timeframe="1h",
            base_strategy="trend_ema_v1",
            model_type="logistic_regression",
            feature_columns=("rsi", "atr"),
            strategy_params={"ema_fast": 12},
            model_params={"C": 1.0},
        )
        payload = config.to_dict()
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["timeframe"], "1h")
        self.assertEqual(payload["feature_columns"], ("rsi", "atr"))
        self.assertIn("experiment_id", payload)
        self.assertEqual(config.to_json(), config.to_json())

    def test_fraction_contract_rejects_non_unit_sum(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(
                experiment_id="bad",
                symbol="BTCUSDT",
                timeframe="1h",
                base_strategy="trend_ema_v1",
                model_type="logistic_regression",
                feature_columns=("rsi",),
                train_fraction=.7,
                validation_fraction=.2,
                test_fraction=.2,
            )

    def test_threshold_is_not_allowed_at_extremes(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(
                experiment_id="bad-threshold",
                symbol="BTCUSDT",
                timeframe="1h",
                base_strategy="trend_ema_v1",
                model_type="logistic_regression",
                feature_columns=("rsi",),
                probability_threshold=1.0,
            )


if __name__ == "__main__":
    unittest.main()
