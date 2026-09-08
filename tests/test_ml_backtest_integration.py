import unittest

import pandas as pd

from backtest.simulate import simulate


class _Filter:
    threshold = 0.6

    def __init__(self, probability):
        self.probability = probability
        self.calls = []

    def predict_probability(self, frame):
        self.calls.append(frame)
        return [self.probability]


def _params(ml_filter=None):
    params = {
        "BACKTEST_FEE_PCT": 0.0,
        "BACKTEST_SLIPPAGE_PCT": 0.0,
        "BACKTEST_MAX_HOLD_BARS": 2,
        "USE_TRAILING_STOP": False,
    }
    if ml_filter is not None:
        params["_ml_signal_filter"] = ml_filter
    return params


def _data():
    return pd.DataFrame([
        {"open_time": 0, "open": 100.0, "high": 100.0, "low": 99.0, "close": 100.0, "atr": 1.0},
        {"open_time": 1, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0},
        {"open_time": 2, "open": 100.0, "high": 103.0, "low": 100.0, "close": 102.0, "atr": 1.0},
        {"open_time": 3, "open": 102.0, "high": 102.0, "low": 102.0, "close": 102.0, "atr": 1.0},
    ])


def _long(row, prev, params):
    return row["open_time"] == 1


def _short(row, prev, params):
    return False


def _stop_target(direction, entry, atr, row, params):
    return entry - atr, entry + 2 * atr


class TestMLBacktestIntegration(unittest.TestCase):
    def test_filter_blocks_base_signal_before_execution(self):
        filt = _Filter(0.59)
        trades = simulate(_data(), _params(filt), _long, _short, _stop_target)
        self.assertEqual(trades, [])
        self.assertEqual(len(filt.calls), 1)
        self.assertEqual(int(filt.calls[0].iloc[0]["open_time"]), 1)

    def test_filter_allows_base_signal_and_records_probability(self):
        filt = _Filter(0.75)
        trades = simulate(_data(), _params(filt), _long, _short, _stop_target)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["ml_probability"], 0.75)
        self.assertEqual(trades[0]["signal_open_time"], 1)
        self.assertEqual(trades[0]["entry_time"], 2)

    def test_without_filter_behavior_is_unchanged(self):
        trades = simulate(_data(), _params(), _long, _short, _stop_target)
        self.assertEqual(len(trades), 1)
        self.assertNotIn("ml_probability", trades[0])

    def test_invalid_filter_interface_is_rejected(self):
        with self.assertRaises(TypeError):
            simulate(_data(), _params(object()), _long, _short, _stop_target)


if __name__ == "__main__":
    unittest.main()
