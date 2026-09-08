import unittest

import pandas as pd

from ml.dataset import SHARED_FEATURE_COLUMNS, build_signal_dataset


class TestMLDataset(unittest.TestCase):
    def _frame(self):
        rows = []
        for i in range(3):
            row = {
                "open_time": (i + 1) * 1000,
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 1000 + i,
            }
            row.update({c: float(i + 1) for c in SHARED_FEATURE_COLUMNS})
            rows.append(row)
        return pd.DataFrame(rows)

    def test_signal_features_are_taken_at_signal_timestamp(self):
        df = self._frame()
        trades = [{
            "signal_open_time": 2000,
            "direction": "LONG",
            "r_multiple": 1.25,
            "net_pnl": 62.5,
        }]
        result = build_signal_dataset(df, trades, {"RSI_OVERSOLD": 30, "USE_TRAILING_STOP": False})
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["open_time"], 2000)
        self.assertEqual(result.iloc[0]["rsi"], 2.0)
        self.assertEqual(result.iloc[0]["signal_direction"], 1)
        self.assertEqual(result.iloc[0]["target"], 1)
        self.assertEqual(result.iloc[0]["param__RSI_OVERSOLD"], 30.0)
        self.assertEqual(result.iloc[0]["param__USE_TRAILING_STOP"], 0)

    def test_negative_outcome_is_zero(self):
        df = self._frame()
        trades = [{
            "signal_open_time": 1000,
            "direction": "SHORT",
            "r_multiple": -0.8,
            "net_pnl": -40.0,
        }]
        result = build_signal_dataset(df, trades)
        self.assertEqual(result.iloc[0]["target"], 0)
        self.assertEqual(result.iloc[0]["signal_direction"], -1)

    def test_missing_feature_row_fails_closed(self):
        df = self._frame()
        trades = [{"signal_open_time": 9999, "direction": "LONG", "r_multiple": 1.0}]
        with self.assertRaises(ValueError):
            build_signal_dataset(df, trades)

    def test_missing_required_column_is_rejected(self):
        df = self._frame().drop(columns=["rsi"])
        with self.assertRaises(ValueError):
            build_signal_dataset(df, [])


if __name__ == "__main__":
    unittest.main()
