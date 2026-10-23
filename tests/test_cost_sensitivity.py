import unittest

from backtest.cost_sensitivity import evaluate_scenarios


class TestCostSensitivity(unittest.TestCase):
    def test_evaluates_cartesian_scenarios_without_mutating_base_params(self):
        base = {"BACKTEST_FEE_PCT": 0.001, "BACKTEST_SLIPPAGE_PCT": 0.0005}
        seen = []

        def strategy(_dataframe, params):
            seen.append((params["BACKTEST_FEE_PCT"], params["BACKTEST_SLIPPAGE_PCT"]))
            # A tiny deterministic fixture to prove scenario-specific params reach the strategy.
            cost = params["BACKTEST_FEE_PCT"] + params["BACKTEST_SLIPPAGE_PCT"]
            return [{"r_multiple": 1.0 - cost * 100, "net_pnl": 1.0 - cost}]

        rows = evaluate_scenarios(strategy, object(), base, [0.0, 0.01], [0.0, 0.02])

        self.assertEqual(len(rows), 4)
        self.assertEqual(seen, [(0.0, 0.0), (0.0, 0.02), (0.01, 0.0), (0.01, 0.02)])
        self.assertEqual(base, {"BACKTEST_FEE_PCT": 0.001, "BACKTEST_SLIPPAGE_PCT": 0.0005})
        self.assertGreater(rows[0]["expectancy_r"], rows[-1]["expectancy_r"])

    def test_rejects_invalid_rates(self):
        for rates in ([-0.1], [1.0], [float("nan")], [float("inf")], []):
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                evaluate_scenarios(lambda _df, _params: [], object(), {}, rates, [0.0])
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                evaluate_scenarios(lambda _df, _params: [], object(), {}, [0.0], rates)


if __name__ == "__main__":
    unittest.main()
