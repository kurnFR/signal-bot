import unittest

from backtest.accounting import calculate_position_size, calculate_trade_accounting


class TestAccounting(unittest.TestCase):
    def test_position_size_risk(self):
        size = calculate_position_size(5000, 1.0, 100.0, 95.0)
        self.assertAlmostEqual(size.risk_amount, 50.0)
        self.assertAlmostEqual(size.quantity, 10.0)
        self.assertAlmostEqual(size.notional, 1000.0)

    def test_long_net_pnl_includes_costs(self):
        result = calculate_trade_accounting(
            "LONG", 10.0, 100.0, 110.0, fee_pct=0.001,
            entry_slippage_pct=0.0005, stop_price=95.0
        )
        self.assertGreater(result["gross_pnl"], 0)
        self.assertGreater(result["fees"], 0)
        self.assertLess(result["net_pnl"], result["gross_pnl"])
        self.assertGreater(result["r_multiple"], 0)

    def test_short_net_pnl(self):
        result = calculate_trade_accounting(
            "SHORT", 10.0, 100.0, 90.0, fee_pct=0.001,
            entry_slippage_pct=0.0005, stop_price=105.0
        )
        self.assertGreater(result["net_pnl"], 0)
        self.assertGreater(result["r_multiple"], 0)

    def test_funding_can_reduce_or_increase_pnl(self):
        paid = calculate_trade_accounting("LONG", 1.0, 100.0, 100.0, 0.0, funding_cost=2.0)
        received = calculate_trade_accounting("LONG", 1.0, 100.0, 100.0, 0.0, funding_cost=-2.0)
        self.assertEqual(paid["net_pnl"], -2.0)
        self.assertEqual(received["net_pnl"], 2.0)


if __name__ == "__main__":
    unittest.main()
