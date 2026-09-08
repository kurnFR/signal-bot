import unittest

import pandas as pd

from backtest.simulate import simulate


class TestSimulationWindow(unittest.TestCase):
    def _frame(self):
        rows = []
        for i in range(8):
            price = 100.0 + i
            rows.append({
                "open_time": i + 1,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "atr": 1.0,
            })
        return pd.DataFrame(rows)

    def _params(self, **extra):
        params = {
            "BACKTEST_FEE_PCT": 0.0,
            "BACKTEST_SLIPPAGE_PCT": 0.0,
            "BACKTEST_MAX_HOLD_BARS": 1,
            "USE_TRAILING_STOP": False,
        }
        params.update(extra)
        return params

    @staticmethod
    def _long(row, prev_row, params):
        return True

    @staticmethod
    def _short(row, prev_row, params):
        return False

    @staticmethod
    def _stop_target(direction, entry_price, atr, row, params):
        return entry_price - atr, entry_price + atr

    def test_start_boundary_prevents_pre_test_entries(self):
        trades = simulate(
            self._frame(),
            self._params(_simulation_start_open_time=5),
            self._long,
            self._short,
            self._stop_target,
        )

        self.assertTrue(trades)
        self.assertTrue(all(t["signal_open_time"] >= 5 for t in trades))
        self.assertEqual(trades[0]["signal_open_time"], 5)

    def test_without_boundary_behavior_is_unchanged(self):
        trades = simulate(
            self._frame(),
            self._params(),
            self._long,
            self._short,
            self._stop_target,
        )

        self.assertTrue(trades)
        self.assertEqual(trades[0]["signal_open_time"], 2)


if __name__ == "__main__":
    unittest.main()
