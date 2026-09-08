"""Regression tests for the financial contract shared by paper/backtest.

These tests intentionally exercise the canonical accounting layer rather than
reimplementing formulas in the test suite. Paper-engine integration must use
these same primitives before P0.5 can be marked complete.
"""
import unittest

from backtest.accounting import calculate_position_size, calculate_trade_accounting


class TestPaperBacktestParity(unittest.TestCase):
    def test_identical_position_inputs_produce_identical_risk(self):
        backtest_size = calculate_position_size(5000.0, 1.0, 100.0, 95.0)
        paper_size = calculate_position_size(5000.0, 1.0, 100.0, 95.0)
        self.assertEqual(backtest_size, paper_size)

    def test_identical_exit_inputs_produce_identical_net_pnl_and_r(self):
        kwargs = dict(
            direction="LONG",
            quantity=10.0,
            entry_price=100.0,
            exit_price=110.0,
            fee_pct=0.001,
            entry_slippage_pct=0.0005,
            exit_slippage_pct=0.0005,
            stop_price=95.0,
            funding_cost=2.0,
        )
        backtest = calculate_trade_accounting(**kwargs)
        paper = calculate_trade_accounting(**kwargs)
        self.assertAlmostEqual(backtest["net_pnl"], paper["net_pnl"])
        self.assertAlmostEqual(backtest["r_multiple"], paper["r_multiple"])

    def test_short_trade_uses_same_accounting_contract(self):
        result = calculate_trade_accounting(
            direction="SHORT",
            quantity=10.0,
            entry_price=100.0,
            exit_price=90.0,
            fee_pct=0.001,
            stop_price=105.0,
            funding_cost=-1.5,
        )
        self.assertGreater(result["net_pnl"], 0.0)
        self.assertGreater(result["r_multiple"], 0.0)


if __name__ == "__main__":
    unittest.main()
