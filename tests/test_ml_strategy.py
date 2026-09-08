import unittest

import pandas as pd

from ml.strategy import MLSignalFilter
from ml.strategy_config import validate_ml_config


class TestMLStrategy(unittest.TestCase):
    def test_config_validation(self):
        validate_ml_config("ml_signal_filter", {
            "base_strategy": "trend_ema",
            "model_type": "logistic_regression",
            "probability_threshold": 0.65,
        })
        with self.assertRaises(ValueError):
            validate_ml_config("ml_signal_filter", {
                "base_strategy": "trend_ema",
                "model_type": "unknown",
            })

    def test_filter_trades(self):
        filt = MLSignalFilter(lambda df, p: [], "logistic_regression", threshold=0.6)
        trades = [{"direction": "LONG"}, {"direction": "SHORT"}]
        result = filt.filter_trades(trades, [0.61, 0.59])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ml_probability"], 0.61)

    def test_fit_rejects_single_class(self):
        filt = MLSignalFilter(lambda df, p: [], "logistic_regression")
        with self.assertRaises(ValueError):
            filt.fit(pd.DataFrame({"x": [1, 2]}), [1, 1])


if __name__ == "__main__":
    unittest.main()
