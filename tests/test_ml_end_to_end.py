import unittest

import numpy as np
import pandas as pd

from ml.dataset import build_signal_dataset
from ml.evaluation import evaluate_filtered_trades
from ml.experiment import ExperimentConfig
from ml.split import chronological_split


class TestMLEndToEndContracts(unittest.TestCase):
    def test_chronological_split_has_no_overlap(self):
        n = 100
        frame = pd.DataFrame({"open_time": np.arange(n), "x": np.arange(n, dtype=float), "target": [i % 2 for i in range(n)]})
        split = chronological_split(frame, train_fraction=.6, validation_fraction=.2)
        self.assertLess(split.train["open_time"].max(), split.validation["open_time"].min())
        self.assertLess(split.validation["open_time"].max(), split.test["open_time"].min())

    def test_dataset_keeps_trade_identity(self):
        candles = pd.DataFrame({"open_time": [1, 2, 3], "rsi": [40., 50., 60.]})
        trades = [{"entry_time": 1, "exit_time": 2, "r_multiple": 1.0}, {"entry_time": 2, "exit_time": 3, "r_multiple": -1.0}]
        ds = build_signal_dataset(candles, trades, params={}, feature_columns=["rsi"])
        self.assertEqual(ds["trade_index"].tolist(), [0, 1])
        self.assertEqual(ds["target"].tolist(), [1, 0])

    def test_test_results_are_based_on_locked_probabilities(self):
        trades = [{"trade_index": 0, "r_multiple": 2.0}, {"trade_index": 1, "r_multiple": -1.0}, {"trade_index": 2, "r_multiple": 1.0}]
        split = pd.DataFrame({"trade_index": [2]})
        result = evaluate_filtered_trades(trades, split, np.array([.8]), .6)
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["net_pnl_r"], 1.0)

    def test_experiment_config_rejects_empty_features(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(base_strategy="trend_ema_v1", feature_columns=[])


if __name__ == "__main__":
    unittest.main()
