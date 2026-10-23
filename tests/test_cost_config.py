"""Regression tests for environment-configurable accounting assumptions."""
import os
import subprocess
import sys
import unittest


class TestAccountingCostConfiguration(unittest.TestCase):
    def test_cost_assumptions_can_be_overridden_without_changing_defaults(self):
        script = (
            "import config; "
            "print(config.BACKTEST_FEE_PCT); "
            "print(config.BACKTEST_SLIPPAGE_PCT)"
        )
        base_env = os.environ.copy()
        base_env.pop("BACKTEST_FEE_PCT", None)
        base_env.pop("BACKTEST_SLIPPAGE_PCT", None)
        defaults = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=base_env,
        ).stdout.splitlines()
        self.assertEqual([float(value) for value in defaults], [0.001, 0.0005])

        override_env = base_env.copy()
        override_env["BACKTEST_FEE_PCT"] = "0.0023"
        override_env["BACKTEST_SLIPPAGE_PCT"] = "0.0012"
        overrides = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=override_env,
        ).stdout.splitlines()
        self.assertEqual([float(value) for value in overrides], [0.0023, 0.0012])


if __name__ == "__main__":
    unittest.main()
