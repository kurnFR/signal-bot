import unittest

import numpy as np
import pandas as pd

from ml.dataset import build_signal_dataset
from ml.evaluation import evaluate_filtered_trades
from ml.experiment import ExperimentConfig
from ml.split import split_by_fractions


class TestMLEndToEndContracts(unittest.TestCase):
    def test_chronological_split_has_no_overlap(self):
        n = 100
        frame = pd.DataFrame({"open_time": np.arange(n), "x": np.arange(n, dtype=float), "target": [i % 2 for i in range(n)]})
        split = split_by_fractions(frame, train_fraction=.6, validation_fraction=.2)
        self.assertLess(split.train["open_time"].max(), split.validation["open_time"].min())
        self.assertLess(split.validation["open_time"].max(), split.test["open_time"].min())

    def test_dataset_keeps_trade_identity(self):
        candles = pd.DataFrame({
            "open_time": [1, 2, 3],
            "open": [100., 101., 102.],
            "high": [101., 102., 103.],
            "low": [99., 100., 101.],
            "close": [100.5, 101.5, 102.5],
            "volume": [1000., 1100., 1200.],
            "rsi": [40., 50., 60.],
        })
        trades = [
            {"signal_open_time": 1, "direction": "LONG", "r_multiple": 1.0},
            {"signal_open_time": 2, "direction": "SHORT", "r_multiple": -1.0},
        ]
        ds = build_signal_dataset(candles, trades, params={}, feature_columns=["rsi"])
        self.assertEqual(ds["trade_index"].tolist(), [0, 1])
        self.assertEqual(ds["target"].tolist(), [1, 0])

    def test_test_results_are_based_on_locked_probabilities(self):
        trades = [
            {"trade_index": 0, "net_pnl": 2.0, "r_multiple": 2.0},
            {"trade_index": 1, "net_pnl": -1.0, "r_multiple": -1.0},
            {"trade_index": 2, "net_pnl": 1.0, "r_multiple": 1.0},
        ]
        split = pd.DataFrame({"trade_index": [2]})
        result = evaluate_filtered_trades(trades, split, np.array([.8]), .6)
        self.assertEqual(result["ml_filtered"]["total_trades"], 1)
        self.assertEqual(result["ml_filtered"]["net_pnl"], 1.0)

    def test_experiment_config_rejects_empty_features(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(
                experiment_id="test",
                symbol="BTCUSDT",
                timeframe="1h",
                base_strategy="trend_ema_v1",
                model_type="logistic_regression",
                feature_columns=(),
            )


if __name__ == "__main__":
    unittest.main()
