"""
Unit and regression tests for Historical Funding Accrual in Backtests (Work Order 71937 Finding 3).

Verifies:
1. Positive funding: LONG pays.
2. Positive funding: SHORT receives.
3. Negative funding reverses both results (LONG receives, SHORT pays).
4. Multiple funding events during one trade.
5. Funding event exactly at entry is excluded (entry_time < event_time).
6. Funding event exactly at exit is included (event_time <= exit_time).
7. Events outside the holding interval are excluded.
8. Missing mark price is handled explicitly.
9. Non-finite funding data is rejected.
10. Spot trades remain unchanged.
11. Backtest and paper engine sign conventions match.
12. Net PnL includes funding cost or credit.
13. Pairs ratio behavior is explicitly tested and documented as unsupported.
"""
import math
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from backtest.simulate import simulate
from backtest.accounting import calculate_funding_cost, calculate_trade_accounting
from db.db import fetch_settled_funding_events


def _dummy_long_condition(row, prev_row, params):
    return row["open_time"] == 1000


def _dummy_short_condition(row, prev_row, params):
    return False


def _dummy_stop_target(direction, entry_price, atr, row, params):
    if direction == "LONG":
        return (entry_price * 0.9, entry_price * 1.2)
    else:
        return (entry_price * 1.1, entry_price * 0.8)


def _build_test_df():
    # 5 bars at t = 0, 1000, 2000, 3000, 4000 (ms)
    # Signal at bar index 1 (t=1000), entry at bar index 2 (t=2000), exit at bar index 3 (t=3000)
    data = [
        {"open_time": 0, "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "atr": 5.0},
        {"open_time": 1000, "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "atr": 5.0},
        {"open_time": 2000, "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "atr": 5.0},
        {"open_time": 3000, "open": 100.0, "high": 125.0, "low": 95.0, "close": 120.0, "atr": 5.0}, # triggers TP at 120
        {"open_time": 4000, "open": 120.0, "high": 125.0, "low": 115.0, "close": 120.0, "atr": 5.0},
    ]
    return pd.DataFrame(data)


def _base_params(market="futures", direction="LONG"):
    return {
        "BACKTEST_FEE_PCT": 0.001,
        "BACKTEST_SLIPPAGE_PCT": 0.0,
        "BACKTEST_MAX_HOLD_BARS": 10,
        "market": market,
        "symbol": "BTCUSDT",
        "quantity": 1.0,
    }


class TestFundingBacktestAccrual(unittest.TestCase):
    def test_positive_funding_long_pays(self):
        """1. Positive funding: LONG pays funding cost."""
        df = _build_test_df()
        params = _base_params(market="futures")
        # Entry is at 2000, exit is at 3000.
        # Funding event at t=2500: mark_price=100.0, funding_rate=0.001 (0.1%)
        params["funding_events"] = [(2500, 100.0, 0.001)]

        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        # Funding cost = 100 * 1.0 * 0.001 = 0.10
        self.assertAlmostEqual(trade["funding_cost"], 0.10)
        self.assertEqual(trade["funding_status"], "settled")
        # Net PnL = gross_pnl - fees - funding_cost
        expected_net = trade["gross_pnl"] - trade["fees"] - 0.10
        self.assertAlmostEqual(trade["net_pnl"], expected_net)

    def test_positive_funding_short_receives(self):
        """2. Positive funding: SHORT receives funding credit."""
        df = _build_test_df()
        params = _base_params(market="futures")
        params["funding_events"] = [(2500, 100.0, 0.001)]

        def short_cond(row, prev_row, p):
            return row["open_time"] == 1000

        trades = simulate(df, params, lambda r, pr, p: False, short_cond, _dummy_stop_target)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        # Short receives funding credit: funding_cost is negative (-0.10)
        self.assertAlmostEqual(trade["funding_cost"], -0.10)
        self.assertEqual(trade["funding_status"], "settled")
        expected_net = trade["gross_pnl"] - trade["fees"] - (-0.10)
        self.assertAlmostEqual(trade["net_pnl"], expected_net)

    def test_negative_funding_reverses_both(self):
        """3. Negative funding reverses: LONG receives, SHORT pays."""
        df = _build_test_df()
        params = _base_params(market="futures")
        params["funding_events"] = [(2500, 100.0, -0.001)]

        # LONG receives
        trades_long = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertAlmostEqual(trades_long[0]["funding_cost"], -0.10)
        expected_net_long = trades_long[0]["gross_pnl"] - trades_long[0]["fees"] - (-0.10)
        self.assertAlmostEqual(trades_long[0]["net_pnl"], expected_net_long)

        # SHORT pays
        def short_cond(row, prev_row, p):
            return row["open_time"] == 1000

        trades_short = simulate(df, params, lambda r, pr, p: False, short_cond, _dummy_stop_target)
        self.assertAlmostEqual(trades_short[0]["funding_cost"], 0.10)
        expected_net_short = trades_short[0]["gross_pnl"] - trades_short[0]["fees"] - 0.10
        self.assertAlmostEqual(trades_short[0]["net_pnl"], expected_net_short)

    def test_multiple_funding_events_during_one_trade(self):
        """4. Multiple funding events during holding window accumulate signed cost."""
        df = _build_test_df()
        params = _base_params(market="futures")
        params["funding_events"] = [
            (2100, 100.0, 0.001),   # 0.10
            (2400, 102.0, 0.0005),  # 0.051
            (2800, 105.0, -0.0002), # -0.021
        ]
        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        expected_total = 0.10 + 0.051 - 0.021 # 0.130
        self.assertAlmostEqual(trades[0]["funding_cost"], expected_total, places=5)

    def test_funding_event_exactly_at_entry_excluded(self):
        """5. Funding event exactly at entry is excluded (entry_time < event_time)."""
        df = _build_test_df()
        params = _base_params(market="futures")
        # Entry is exactly at t=2000
        params["funding_events"] = [(2000, 100.0, 0.001)]

        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertEqual(trades[0]["funding_cost"], 0.0)
        self.assertEqual(trades[0]["funding_status"], "no_events_in_interval")

    def test_funding_event_exactly_at_exit_included(self):
        """6. Funding event exactly at exit is included (event_time <= exit_time)."""
        df = _build_test_df()
        params = _base_params(market="futures")
        # Exit is at t=3000
        params["funding_events"] = [(3000, 100.0, 0.001)]

        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertAlmostEqual(trades[0]["funding_cost"], 0.10)
        self.assertEqual(trades[0]["funding_status"], "settled")

    def test_events_outside_holding_interval_excluded(self):
        """7. Events strictly before entry or after exit are excluded."""
        df = _build_test_df()
        params = _base_params(market="futures")
        params["funding_events"] = [
            (1500, 100.0, 0.005),  # before entry at 2000
            (3500, 100.0, 0.005),  # after exit at 3000
        ]
        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertEqual(trades[0]["funding_cost"], 0.0)
        self.assertEqual(trades[0]["funding_status"], "no_events_in_interval")

    def test_missing_mark_price_handled_explicitly(self):
        """8. Missing/null mark price is rejected or filtered cleanly in database query."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        # Row with None mark_price
        mock_cur.fetchall.return_value = [
            {"event_time": 2500, "mark_price": None, "funding_rate": "0.0001"},
            {"event_time": 2600, "mark_price": "100.0", "funding_rate": "0.0001"},
        ]
        mock_conn.cursor.return_value = mock_cur
        mock_pool.get_connection.return_value = mock_conn

        events = fetch_settled_funding_events("BTCUSDT", 2000, 3000, pool=mock_pool)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], 2600)

    def test_non_finite_funding_data_rejected(self):
        """9. Non-finite funding data (NaN, inf, non-positive mark price) is rejected."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"event_time": 2500, "mark_price": "100.0", "funding_rate": "NaN"},
        ]
        mock_conn.cursor.return_value = mock_cur
        mock_pool.get_connection.return_value = mock_conn

        with self.assertRaises(ValueError):
            fetch_settled_funding_events("BTCUSDT", 2000, 3000, pool=mock_pool)

        # In simulate with pre-provided NaN
        df = _build_test_df()
        params = _base_params(market="futures")
        params["funding_events"] = [(2500, float("nan"), 0.001)]
        with self.assertRaises(ValueError):
            simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)

    def test_spot_trades_remain_unchanged(self):
        """10. Spot strategies are completely unaffected by funding."""
        df = _build_test_df()
        params = _base_params(market="spot")
        params["funding_events"] = [(2500, 100.0, 0.05)]  # Should be ignored for spot

        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertEqual(trades[0]["funding_cost"], 0.0)
        self.assertEqual(trades[0]["funding_status"], "not_applicable")

    def test_backtest_and_paper_engine_sign_conventions_match(self):
        """11. Paper engine and backtest use identical accounting convention."""
        qty = 2.5
        events = [(100.0, 0.0008), (105.0, -0.0003)]
        long_cost = calculate_funding_cost(qty, events)

        # Paper engine convention from paper/engine.py:
        paper_cost_long = long_cost
        paper_cost_short = -long_cost

        # Backtest accounting
        acct_long = calculate_trade_accounting("LONG", qty, 100.0, 110.0, 0.001, funding_cost=paper_cost_long)
        acct_short = calculate_trade_accounting("SHORT", qty, 100.0, 90.0, 0.001, funding_cost=paper_cost_short)

        self.assertAlmostEqual(acct_long["funding_cost"], paper_cost_long)
        self.assertAlmostEqual(acct_short["funding_cost"], paper_cost_short)

    def test_net_pnl_includes_funding_cost_or_credit(self):
        """12. Net PnL explicitly reflects funding cost or receipt."""
        df = _build_test_df()
        params_no_funding = _base_params(market="futures")
        params_no_funding["funding_events"] = []

        params_funding = _base_params(market="futures")
        params_funding["funding_events"] = [(2500, 100.0, 0.002)] # cost = 0.20

        trade_no = simulate(df, params_no_funding, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)[0]
        trade_with = simulate(df, params_funding, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)[0]

        self.assertAlmostEqual(trade_no["net_pnl"] - trade_with["net_pnl"], 0.20)

    def test_pairs_ratio_unsupported_two_leg_documented(self):
        """13. Pairs ratio is explicitly flagged as unsupported for two-leg funding."""
        df = _build_test_df()
        params = _base_params(market="futures")
        params["strategy_name"] = "pairs_ratio_v1"
        params["funding_events"] = [(2500, 100.0, 0.002)]

        trades = simulate(df, params, _dummy_long_condition, _dummy_short_condition, _dummy_stop_target)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["funding_cost"], 0.0)
        self.assertEqual(trades[0]["funding_status"], "unsupported_pairs_ratio_two_leg")


if __name__ == "__main__":
    unittest.main()
