import unittest

import pandas as pd

from ml.experiment import ExperimentConfig
from ml.runner import run_experiment


class TestMLRunner(unittest.TestCase):
    def _frame(self):
        rows = []
        for i in range(30):
            row = {"open_time": i + 1, "open": 100 + i, "high": 101 + i,
                   "low": 99 + i, "close": 100.5 + i, "volume": 1000 + i}
            for col in ("rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct",
                        "volume_sma", "volume_ratio", "support", "resistance",
                        "swing_high", "swing_low", "fib_0", "fib_236", "fib_382",
                        "fib_5", "fib_618", "fib_786", "fib_1"):
                row[col] = float(i + 1)
            rows.append(row)
        return pd.DataFrame(rows)

    def test_runner_requires_both_absolute_boundaries(self):
        config = ExperimentConfig(
            experiment_id="t1", symbol="BTCUSDT", timeframe="1h",
            base_strategy="trend_ema", model_type="logistic_regression",
            feature_columns=("rsi",),
        )
        with self.assertRaises(ValueError):
            run_experiment(self._frame(), [], config, train_end_time=10)

    def test_runner_rejects_empty_trade_dataset(self):
        config = ExperimentConfig(
            experiment_id="t2", symbol="BTCUSDT", timeframe="1h",
            base_strategy="trend_ema", model_type="logistic_regression",
            feature_columns=("rsi",),
        )
        with self.assertRaises(ValueError):
            run_experiment(self._frame(), [], config)


if __name__ == "__main__":
    unittest.main()
