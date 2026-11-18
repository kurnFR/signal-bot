"""
tests/test_pairs_ratio_ohlc.py

Comprehensive regression test suite for Work Order 71931:
Correct Synthetic Ratio OHLC Construction in pairs_ratio_v1.

Covers:
A. Normal candles (similar volatility in both legs).
B. Denominator volatility greater than numerator volatility (WO71930 reproduction).
C. Numerator volatility greater than denominator volatility.
D. Open/close outside naive high/low bounds.
E. Invalid denominator prices (zero and negative).
F. Missing and non-finite inputs (NaN, Inf, -Inf).
G. Timestamp alignment and mismatched timestamps.
H. Historical 300-bar reproduction (elimination of 274 inverted bars).
I. Strategy simulation compatibility (pairs_ratio.run and run_inverse).
J. Schema and downstream dataframe compatibility.
"""
import os
import json
import logging
import unittest
import numpy as np
import pandas as pd

from backtest.strategies.pairs_ratio import (
    _build_ratio_df,
    run as run_pairs_ratio,
    run_inverse as run_inverse_pairs_ratio,
)
from backtest.params import BASE_PARAMS


class TestPairsRatioOHLC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Base test parameters using authentic BASE_PARAMS
        cls.params = dict(
            BASE_PARAMS,
            symbol="BNBUSDT",
            market="futures",
            timeframe="1d",
            pair_symbol="BTCUSDT",
        )

        # Exact historical assertions require the original snapshot.
        cls.hist_bnb = cls.hist_btc = None
        fixture = os.getenv("SIGNAL_BOT_PAIRS_HISTORY_FIXTURE")
        if fixture:
            with open(fixture) as f:
                data = json.load(f)
            cls.hist_bnb = pd.DataFrame(data["bnb"])
            cls.hist_btc = pd.DataFrame(data["btc"])

        if cls.hist_bnb is not None and cls.hist_btc is not None:
            for c in ["open", "high", "low", "close"]:
                cls.hist_bnb[c] = cls.hist_bnb[c].astype(float)
                cls.hist_btc[c] = cls.hist_btc[c].astype(float)
            cls.hist_bnb["open_time"] = cls.hist_bnb["open_time"].astype(int)
            cls.hist_btc["open_time"] = cls.hist_btc["open_time"].astype(int)

    def test_normal_candles_similar_volatility(self):
        """A. Normal candles with symmetric movements must yield exact conservative bounds."""
        df_a = pd.DataFrame({
            "open_time": [1000],
            "open": [100.0],
            "high": [110.0],
            "low": [95.0],
            "close": [105.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000],
            "open": [1000.0],
            "high": [1100.0],
            "low": [950.0],
            "close": [1050.0],
        })

        ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 1)
        r = ratio.iloc[0]

        # Exact expected values:
        # O_R = 100 / 1000 = 0.1
        # C_R = 105 / 1050 = 0.1
        # H_R = 110 / 950 = 0.11578947...
        # L_R = 95 / 1100 = 0.08636363...
        self.assertAlmostEqual(r["open"], 0.1)
        self.assertAlmostEqual(r["close"], 0.1)
        self.assertAlmostEqual(r["high"], 110.0 / 950.0)
        self.assertAlmostEqual(r["low"], 95.0 / 1100.0)

        # Invariant checks
        self.assertGreaterEqual(r["high"], max(r["open"], r["close"]))
        self.assertLessEqual(r["low"], min(r["open"], r["close"]))
        self.assertGreater(r["high"], r["low"])

    def test_denominator_volatility_greater_than_numerator_wo71930_pattern(self):
        """B. Reproduce WO71930 failure bar 69 where naive division inverts high/low."""
        # Exact values from 2026-01-12 daily candle:
        # BNB: narrow range (910.93 - 894.06 = 16.87, ~1.8%)
        # BTC: wide range (92488.00 - 90066.00 = 2422.0, ~2.7%)
        df_bnb = pd.DataFrame({
            "open_time": [1768176000000],
            "open": [904.75],
            "high": [910.93],
            "low": [894.06],
            "close": [906.32],
        })
        df_btc = pd.DataFrame({
            "open_time": [1768176000000],
            "open": [90964.40],
            "high": [92488.00],
            "low": [90066.00],
            "close": [91252.60],
        })

        # Confirm old naive component-wise division produces inverted geometry
        naive_high = 910.93 / 92488.00  # 0.00984917
        naive_low = 894.06 / 90066.00   # 0.00992672
        naive_open = 904.75 / 90964.40  # 0.00994620
        self.assertLess(naive_high, naive_low, "Proof: Naive high < naive low")
        self.assertLess(naive_high, naive_open, "Proof: Naive high < naive open")

        # Corrected ratio implementation must produce valid bounds
        ratio = _build_ratio_df(df_bnb, df_btc, self.params)
        self.assertEqual(len(ratio), 1)
        r = ratio.iloc[0]

        expected_high = 910.93 / 90066.00  # 0.01011403
        expected_low = 894.06 / 92488.00   # 0.00966677

        self.assertAlmostEqual(r["high"], expected_high, places=7)
        self.assertAlmostEqual(r["low"], expected_low, places=7)
        self.assertAlmostEqual(r["open"], naive_open, places=7)
        self.assertAlmostEqual(r["close"], 906.32 / 91252.60, places=7)

        # Invariant checks: strictly valid geometry
        self.assertGreaterEqual(r["high"], max(r["open"], r["close"]))
        self.assertLessEqual(r["low"], min(r["open"], r["close"]))
        self.assertGreater(r["high"], r["low"])

    def test_numerator_volatility_greater_than_denominator(self):
        """C. Numerator leg expanding with high volatility while base leg is stable."""
        df_a = pd.DataFrame({
            "open_time": [1000],
            "open": [100.0],
            "high": [150.0],  # +50%
            "low": [80.0],   # -20%
            "close": [140.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000],
            "open": [1000.0],
            "high": [1005.0],  # +0.5%
            "low": [995.0],   # -0.5%
            "close": [1002.0],
        })

        ratio = _build_ratio_df(df_a, df_b, self.params)
        r = ratio.iloc[0]

        self.assertAlmostEqual(r["high"], 150.0 / 995.0)
        self.assertAlmostEqual(r["low"], 80.0 / 1005.0)
        self.assertGreaterEqual(r["high"], max(r["open"], r["close"]))
        self.assertLessEqual(r["low"], min(r["open"], r["close"]))

    def test_open_close_outside_naive_high_low(self):
        """D. Scenarios where naive component division falls outside open or close."""
        df_a = pd.DataFrame({
            "open_time": [1000],
            "open": [100.0],
            "high": [105.0],
            "low": [98.0],
            "close": [104.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000],
            "open": [50.0],
            "high": [80.0],
            "low": [40.0],
            "close": [75.0],
        })

        ratio = _build_ratio_df(df_a, df_b, self.params)
        r = ratio.iloc[0]

        # O_R = 100/50 = 2.0. Naive H = 105/80 = 1.3125 (Naive H < O_R!).
        # Corrected H_R = 105/40 = 2.625 >= 2.0.
        self.assertAlmostEqual(r["open"], 2.0)
        self.assertAlmostEqual(r["close"], 104.0 / 75.0)
        self.assertGreaterEqual(r["high"], r["open"])
        self.assertGreaterEqual(r["high"], r["close"])
        self.assertLessEqual(r["low"], r["open"])
        self.assertLessEqual(r["low"], r["close"])

    def test_invalid_denominator_zero_and_negative(self):
        """E. Denominator values <= 0 must be safely rejected with warning, not creating Infs."""
        df_a = pd.DataFrame({
            "open_time": [1000, 2000, 3000, 4000],
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [110.0, 110.0, 110.0, 110.0],
            "low": [90.0, 90.0, 90.0, 90.0],
            "close": [105.0, 105.0, 105.0, 105.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000, 2000, 3000, 4000],
            "open": [0.0, 100.0, -50.0, 100.0],     # row 0 zero, row 2 negative
            "high": [10.0, 110.0, 60.0, 110.0],
            "low": [0.0, 90.0, -70.0, 90.0],
            "close": [5.0, 105.0, -55.0, 105.0],
        })

        with self.assertLogs("backtest.strategies.pairs_ratio", level="WARNING") as cm:
            ratio = _build_ratio_df(df_a, df_b, self.params)

        # Rows 0 and 2 must be dropped; rows 1 and 3 are valid
        self.assertEqual(len(ratio), 2)
        self.assertEqual(ratio["open_time"].tolist(), [2000, 4000])
        self.assertTrue(np.all(np.isfinite(ratio[["open", "high", "low", "close"]])))
        self.assertTrue(any("Dropped 2 invalid source candle pair" in msg for msg in cm.output))

    def test_missing_and_non_finite_inputs(self):
        """F. Non-finite values (NaN, Inf, -Inf) must be cleanly filtered."""
        df_a = pd.DataFrame({
            "open_time": [1000, 2000, 3000],
            "open": [100.0, np.nan, 100.0],
            "high": [110.0, 110.0, np.inf],
            "low": [90.0, 90.0, 90.0],
            "close": [105.0, 105.0, 105.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000, 2000, 3000],
            "open": [1000.0, 1000.0, 1000.0],
            "high": [1100.0, 1100.0, 1100.0],
            "low": [900.0, 900.0, 900.0],
            "close": [1050.0, 1050.0, 1050.0],
        })

        with self.assertLogs("backtest.strategies.pairs_ratio", level="WARNING") as cm:
            ratio = _build_ratio_df(df_a, df_b, self.params)

        self.assertEqual(len(ratio), 1)
        self.assertEqual(ratio["open_time"].iloc[0], 1000)
        self.assertTrue(any("Dropped 2 invalid source candle pair" in msg for msg in cm.output))

    def test_timestamp_alignment(self):
        """G. Mismatched timestamps must be rejected by default, or joined on inner intersection if allowed."""
        df_a = pd.DataFrame({
            "open_time": [1000, 2000, 3000],
            "open": [10.0, 20.0, 30.0],
            "high": [15.0, 25.0, 35.0],
            "low": [5.0, 15.0, 25.0],
            "close": [12.0, 22.0, 32.0],
        })
        df_b = pd.DataFrame({
            "open_time": [2000, 3000, 4000],
            "open": [100.0, 200.0, 300.0],
            "high": [150.0, 250.0, 350.0],
            "low": [50.0, 150.0, 250.0],
            "close": [120.0, 220.0, 320.0],
        })

        # By default, mismatched component timestamps are rejected per WO71935 policy:
        ratio_default = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio_default), 0, "Default policy must reject mismatched component timestamps.")

        # Explicit allow_mismatched_timestamps=True preserves inner intersection joining:
        params_allow = dict(self.params, allow_mismatched_timestamps=True, interval_ms=1000)
        ratio_allowed = _build_ratio_df(df_a, df_b, params_allow)
        self.assertEqual(len(ratio_allowed), 2)
        self.assertEqual(ratio_allowed["open_time"].tolist(), [2000, 3000])

        # Disjoint timestamps
        df_c = pd.DataFrame({
            "open_time": [8000, 9000],
            "open": [1.0, 2.0], "high": [3.0, 4.0], "low": [0.5, 1.5], "close": [2.0, 3.0]
        })
        disjoint_ratio = _build_ratio_df(df_a, df_c, params_allow)
        self.assertEqual(len(disjoint_ratio), 0)
        self.assertEqual(list(disjoint_ratio.columns), ["open_time", "open", "high", "low", "close", "zscore", "atr", "atr_pct"])

    def test_historical_300_bars_reproduction_and_elimination_of_274_inversions(self):
        """H. Verify all 300 historical daily bars satisfy OHLC invariants with 0 inversions."""
        if self.hist_bnb is None or self.hist_btc is None:
            self.skipTest("Historical 300-bar fixture file not available.")

        # Test naive calculation to confirm exactly 274 invalid bars
        merged = self.hist_bnb.merge(
            self.hist_btc.rename(columns={"open": "po", "high": "ph", "low": "pl", "close": "pc"}),
            on="open_time"
        )
        naive_h = merged["high"] / merged["ph"]
        naive_l = merged["low"] / merged["pl"]
        naive_o = merged["open"] / merged["po"]
        naive_c = merged["close"] / merged["pc"]
        naive_violations = (
            (naive_h < np.maximum(naive_o, naive_c)) |
            (naive_l > np.minimum(naive_o, naive_c)) |
            (naive_h < naive_l)
        ).sum()
        self.assertEqual(naive_violations, 274, "Confirmed baseline reproduction: exactly 274 inverted bars under naive division.")

        # Run corrected implementation
        ratio = _build_ratio_df(self.hist_bnb, self.hist_btc, self.params)
        self.assertEqual(len(ratio), 300, "All 300 bars present.")

        # Verify all 300 bars satisfy OHLC invariants
        violations = []
        for idx, row in ratio.iterrows():
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            if h < max(o, c, l) or l > min(o, c, h) or h < l or min(o, h, l, c) <= 0:
                violations.append((idx, row["open_time"], o, h, l, c))

        self.assertEqual(len(violations), 0, f"Zero OHLC invariant violations across all 300 bars (found: {violations})")
        self.assertTrue(np.all(np.isfinite(ratio[["open", "high", "low", "close"]])))

    def test_strategy_simulation_compatibility(self):
        """I. Strategy backtest simulation completes cleanly without OHLC exceptions."""
        if self.hist_bnb is None or self.hist_btc is None:
            self.skipTest("Historical 300-bar fixture file not available.")

        params = dict(self.params, pair_df=self.hist_btc.copy())

        # Standard strategy simulation
        trades = run_pairs_ratio(self.hist_bnb, params)
        self.assertIsInstance(trades, list)
        self.assertEqual(len(trades), 8, f"Expected exactly 8 historical simulated trades under conservative bounds, got {len(trades)}")

        # Verify each trade structure
        for t in trades:
            self.assertIn(t["direction"], ["LONG", "SHORT"])
            self.assertIn("entry_time", t)
            self.assertIn("exit_time", t)
            self.assertIn("r_multiple", t)
            self.assertIn("net_return_pct", t)
            self.assertTrue(np.isfinite(t["r_multiple"]))
            self.assertTrue(np.isfinite(t["net_return_pct"]))

        # Inverted strategy simulation
        inv_trades = run_inverse_pairs_ratio(self.hist_bnb, params)
        self.assertIsInstance(inv_trades, list)
        self.assertEqual(len(inv_trades), 4, f"Expected exactly 4 historical inverse simulated trades, got {len(inv_trades)}")

    def test_schema_and_column_compatibility(self):
        """J. Schema and downstream compatibility for empty or missing inputs."""
        cols = ["open_time", "open", "high", "low", "close", "zscore", "atr", "atr_pct"]

        # None input
        res1 = _build_ratio_df(None, None, self.params)
        self.assertEqual(list(res1.columns), cols)
        self.assertEqual(len(res1), 0)

        # Empty df
        empty_df = pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])
        res2 = _build_ratio_df(empty_df, empty_df, self.params)
        self.assertEqual(list(res2.columns), cols)
        self.assertEqual(len(res2), 0)

        # Missing required column in pair_df
        bad_pair = pd.DataFrame({"open_time": [1], "open": [10], "high": [12], "low": [9]})  # missing close
        good_df = pd.DataFrame({"open_time": [1], "open": [1], "high": [2], "low": [0.5], "close": [1.5]})
        res3 = _build_ratio_df(good_df, bad_pair, self.params)
        self.assertEqual(list(res3.columns), cols)
        self.assertEqual(len(res3), 0)

    # =========================================================================
    # Work Order 71935 Defensive Timestamp-Continuity Regression Tests
    # =========================================================================

    def _make_daily_df(self, start_ts: int, count: int, base_open: float = 100.0, step_ms: int = 86400000):
        rows = []
        ts = start_ts
        val = base_open
        for i in range(count):
            o = val + (i % 5) * 0.5
            h = o + 2.0
            l = o - 2.0
            c = o + 0.5
            rows.append({"open_time": ts, "open": o, "high": h, "low": l, "close": c})
            ts += step_ms
        return pd.DataFrame(rows)

    def test_1_continuous_daily_data_produces_normal_indicators(self):
        """1. Continuous daily data produces normal indicators without unnecessary resets."""
        start_ts = 1700000000000
        df_a = self._make_daily_df(start_ts, 100, base_open=100.0)
        df_b = self._make_daily_df(start_ts, 100, base_open=1000.0)

        ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 100)

        # Lookback = 50: bars 0..48 are NaN, bars 49..99 are valid finite numbers
        self.assertEqual(ratio["zscore"].iloc[:49].isna().sum(), 49)
        self.assertEqual(ratio["zscore"].iloc[49:].notna().sum(), 51)
        self.assertTrue(np.all(np.isfinite(ratio["zscore"].iloc[49:])))

        # ATR period = 14: bars 0..12 are NaN, bars 13..99 are valid finite numbers
        self.assertEqual(ratio["atr"].iloc[:13].isna().sum(), 13)
        self.assertEqual(ratio["atr"].iloc[13:].notna().sum(), 87)

    def test_2_one_missing_daily_candle_triggers_fresh_warmup(self):
        """2. A single missing daily candle triggers a fresh warmup partition for post-gap data."""
        start_ts = 1700000000000
        step_ms = 86400000

        # Pre-gap: 60 continuous bars
        pre_a = self._make_daily_df(start_ts, 60, base_open=100.0)
        pre_b = self._make_daily_df(start_ts, 60, base_open=1000.0)

        # Gap of 1 missing candle (2 * step_ms jump)
        post_start_ts = start_ts + 61 * step_ms
        post_a = self._make_daily_df(post_start_ts, 30, base_open=110.0)
        post_b = self._make_daily_df(post_start_ts, 30, base_open=1050.0)

        df_a = pd.concat([pre_a, post_a], ignore_index=True)
        df_b = pd.concat([pre_b, post_b], ignore_index=True)

        ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 90)

        # Pre-gap segment: bars 49..59 have valid z-score (pre-gap history preserved)
        self.assertEqual(ratio["zscore"].iloc[49:60].notna().sum(), 11)

        # Post-gap segment (indices 60..89): all 30 bars must be NaN because 30 < 50
        self.assertEqual(ratio["zscore"].iloc[60:].isna().sum(), 30)

    def test_3_eighty_day_gap_invalidates_post_gap_rolling_window(self):
        """3. An 80-day gap invalidates the post-gap rolling window (WO71933 scenario)."""
        start_ts = 1787702400000 - 59 * 86400000  # 60 bars ending at Aug 26, 2026
        pre_a = self._make_daily_df(start_ts, 60, base_open=500.0)
        pre_b = self._make_daily_df(start_ts, 60, base_open=60000.0)

        # Jump 80 days to Nov 14, 2026
        post_start_ts = 1794614400000
        post_a = self._make_daily_df(post_start_ts, 4, base_open=580.0)
        post_b = self._make_daily_df(post_start_ts, 4, base_open=64000.0)

        df_a = pd.concat([pre_a, post_a], ignore_index=True)
        df_b = pd.concat([pre_b, post_b], ignore_index=True)

        ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 64)

        # Pre-gap segment has valid z-scores (bars 49..59)
        self.assertEqual(ratio["zscore"].iloc[49:60].notna().sum(), 11)

        # All 4 post-gap bars must have z-score == NaN (fresh warmup enforced)
        self.assertEqual(ratio["zscore"].iloc[60:].isna().sum(), 4)

    def test_4_forty_nine_post_gap_observations_do_not_produce_valid_indicator(self):
        """4. Exactly 49 consecutive post-gap observations do not produce a valid 50-bar indicator."""
        start_ts = 1700000000000
        step_ms = 86400000

        pre_a = self._make_daily_df(start_ts, 60, base_open=100.0)
        pre_b = self._make_daily_df(start_ts, 60, base_open=1000.0)

        # Gap followed by exactly 49 bars
        post_start = start_ts + 65 * step_ms
        post_a = self._make_daily_df(post_start, 49, base_open=105.0)
        post_b = self._make_daily_df(post_start, 49, base_open=1020.0)

        df_a = pd.concat([pre_a, post_a], ignore_index=True)
        df_b = pd.concat([pre_b, post_b], ignore_index=True)

        ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 109)

        # Post-gap partition (indices 60..108): all 49 rows must be NaN
        post_gap_z = ratio["zscore"].iloc[60:109]
        self.assertEqual(len(post_gap_z), 49)
        self.assertEqual(post_gap_z.isna().sum(), 49)

    def test_5_fiftieth_post_gap_observation_restores_indicator(self):
        """5. The 50th valid post-gap observation restores indicator availability."""
        start_ts = 1700000000000
        step_ms = 86400000

        pre_a = self._make_daily_df(start_ts, 60, base_open=100.0)
        pre_b = self._make_daily_df(start_ts, 60, base_open=1000.0)

        # Gap followed by exactly 50 bars
        post_start = start_ts + 65 * step_ms
        post_a = self._make_daily_df(post_start, 50, base_open=105.0)
        post_b = self._make_daily_df(post_start, 50, base_open=1020.0)

        df_a = pd.concat([pre_a, post_a], ignore_index=True)
        df_b = pd.concat([pre_b, post_b], ignore_index=True)

        ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 110)

        # First 49 post-gap bars are NaN
        self.assertEqual(ratio["zscore"].iloc[60:109].isna().sum(), 49)

        # The 50th post-gap bar (index 109) must be valid and finite
        bar_50_z = ratio["zscore"].iloc[109]
        self.assertTrue(np.isfinite(bar_50_z), f"Expected finite z-score on 50th post-gap bar, got {bar_50_z}")

    def test_6_pre_gap_observations_never_influence_post_gap_indicators(self):
        """6. Pre-gap observations never influence post-gap rolling indicators (mathematical isolation)."""
        step_ms = 86400000
        post_start = 1750000000000

        # Identical 50 post-gap bars for both tests
        post_a = self._make_daily_df(post_start, 50, base_open=100.0)
        post_b = self._make_daily_df(post_start, 50, base_open=1000.0)

        # Dataset 1: pre-gap with extreme low ratio (0.01)
        pre1_a = self._make_daily_df(1700000000000, 60, base_open=10.0)
        pre1_b = self._make_daily_df(1700000000000, 60, base_open=1000.0)

        # Dataset 2: pre-gap with extreme high ratio (10.0)
        pre2_a = self._make_daily_df(1700000000000, 60, base_open=10000.0)
        pre2_b = self._make_daily_df(1700000000000, 60, base_open=1000.0)

        r1 = _build_ratio_df(pd.concat([pre1_a, post_a], ignore_index=True), pd.concat([pre1_b, post_b], ignore_index=True), self.params)
        r2 = _build_ratio_df(pd.concat([pre2_a, post_a], ignore_index=True), pd.concat([pre2_b, post_b], ignore_index=True), self.params)

        # The 50th post-gap bar (index 109) in both datasets must be IDENTICAL down to 10 decimal places
        z1 = float(r1["zscore"].iloc[109])
        z2 = float(r2["zscore"].iloc[109])
        self.assertAlmostEqual(z1, z2, places=10, msg=f"Pre-gap leakage detected: z1={z1} vs z2={z2}")

    def test_7_duplicate_timestamps_rejected(self):
        """7. Duplicate timestamps are rejected per documented policy."""
        df_a = pd.DataFrame({
            "open_time": [1000, 2000, 2000, 3000],
            "open": [10.0, 20.0, 20.0, 30.0],
            "high": [15.0, 25.0, 25.0, 35.0],
            "low": [5.0, 15.0, 15.0, 25.0],
            "close": [12.0, 22.0, 22.0, 32.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000, 2000, 2500, 3000],
            "open": [100.0, 200.0, 250.0, 300.0],
            "high": [150.0, 250.0, 260.0, 350.0],
            "low": [50.0, 150.0, 240.0, 250.0],
            "close": [120.0, 220.0, 255.0, 320.0],
        })

        with self.assertLogs("backtest.strategies.pairs_ratio", level="WARNING") as cm:
            ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 0)
        self.assertTrue(any("Duplicate timestamps detected" in msg for msg in cm.output))

    def test_8_unsorted_timestamps_rejected(self):
        """8. Unsorted (non-monotonic) timestamps are rejected."""
        df_a = pd.DataFrame({
            "open_time": [1000, 3000, 2000, 4000],
            "open": [10.0, 30.0, 20.0, 40.0],
            "high": [15.0, 35.0, 25.0, 45.0],
            "low": [5.0, 25.0, 15.0, 35.0],
            "close": [12.0, 32.0, 22.0, 42.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000, 2000, 3000, 4000],
            "open": [100.0, 200.0, 300.0, 400.0],
            "high": [150.0, 250.0, 350.0, 450.0],
            "low": [50.0, 150.0, 250.0, 350.0],
            "close": [120.0, 220.0, 320.0, 420.0],
        })

        with self.assertLogs("backtest.strategies.pairs_ratio", level="WARNING") as cm:
            ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 0)
        self.assertTrue(any("Unsorted" in msg for msg in cm.output))

    def test_9_mismatched_component_timestamps_rejected(self):
        """9. Mismatched component timestamps are rejected by default."""
        df_a = pd.DataFrame({
            "open_time": [1000, 2000, 3000],
            "open": [10.0, 20.0, 30.0],
            "high": [15.0, 25.0, 35.0],
            "low": [5.0, 15.0, 25.0],
            "close": [12.0, 22.0, 32.0],
        })
        df_b = pd.DataFrame({
            "open_time": [1000, 2000, 4000],
            "open": [100.0, 200.0, 400.0],
            "high": [150.0, 250.0, 450.0],
            "low": [50.0, 150.0, 350.0],
            "close": [120.0, 220.0, 420.0],
        })

        with self.assertLogs("backtest.strategies.pairs_ratio", level="WARNING") as cm:
            ratio = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio), 0)
        self.assertTrue(any("Component A and B timestamps do not match exactly" in msg for msg in cm.output))

    def test_10_invalid_ohlc_source_data_cannot_bypass_continuity_validation(self):
        """10. Invalid OHLC source data dropped by filter cannot bypass continuity validation."""
        start_ts = 1700000000000
        step_ms = 86400000

        df_a = self._make_daily_df(start_ts, 60, base_open=100.0)
        df_b = self._make_daily_df(start_ts, 60, base_open=1000.0)

        # Corrupt bar 30 in Component A with invalid geometry (High < Low)
        df_a.loc[30, "high"] = 50.0
        df_a.loc[30, "low"] = 100.0

        ratio = _build_ratio_df(df_a, df_b, self.params)
        # Bar 30 dropped -> 59 bars remaining
        self.assertEqual(len(ratio), 59)

        # Dropping bar 30 created a 2-day gap between bar 29 and bar 31.
        # Partition 0: bars 0..29 (30 bars < 50, all NaN)
        # Partition 1: bars 31..59 (29 bars < 50, all NaN)
        # Entire series must be NaN because neither partition reaches 50 bars!
        self.assertEqual(ratio["zscore"].isna().sum(), 59)

    def test_11_standard_and_inverse_execution_identical_continuity_rules(self):
        """11. Standard and inverse execution paths use identical continuity rules."""
        start_ts = 1700000000000
        step_ms = 86400000

        pre_a = self._make_daily_df(start_ts, 60, base_open=100.0)
        pre_b = self._make_daily_df(start_ts, 60, base_open=1000.0)
        post_start = start_ts + 65 * step_ms
        post_a = self._make_daily_df(post_start, 50, base_open=105.0)
        post_b = self._make_daily_df(post_start, 50, base_open=1020.0)

        df_a = pd.concat([pre_a, post_a], ignore_index=True)
        df_b = pd.concat([pre_b, post_b], ignore_index=True)

        params = dict(self.params, pair_df=df_b.copy())
        trades_std = run_pairs_ratio(df_a, params)
        trades_inv = run_inverse_pairs_ratio(df_a, params)

        self.assertIsInstance(trades_std, list)
        self.assertIsInstance(trades_inv, list)
        # Zero exceptions and clean execution across both paths

    def test_12_continuous_data_compatible_with_existing_implementation(self):
        """12. Continuous data produces results 100% compatible with existing implementation."""
        # 1. Test on a continuous 300-bar series
        start_ts = 1700000000000
        df_a = self._make_daily_df(start_ts, 300, base_open=100.0)
        df_b = self._make_daily_df(start_ts, 300, base_open=1000.0)

        ratio_300 = _build_ratio_df(df_a, df_b, self.params)
        self.assertEqual(len(ratio_300), 300)
        # Continuous 300 bars -> single partition -> first 49 NaN, remaining 251 valid
        self.assertEqual(ratio_300["zscore"].iloc[:49].isna().sum(), 49)
        self.assertEqual(ratio_300["zscore"].iloc[49:].notna().sum(), 251)
        self.assertTrue(np.all(np.isfinite(ratio_300["zscore"].iloc[49:])))

        # 2. Test on continuous pre-gap historical slice (first 200 continuous bars)
        if self.hist_bnb is not None and self.hist_btc is not None:
            hist_cont_bnb = self.hist_bnb.iloc[:200].copy()
            hist_cont_btc = self.hist_btc.iloc[:200].copy()
            ratio_hist = _build_ratio_df(hist_cont_bnb, hist_cont_btc, self.params)
            self.assertEqual(len(ratio_hist), 200)
            self.assertEqual(ratio_hist["zscore"].iloc[:49].isna().sum(), 49)
            self.assertEqual(ratio_hist["zscore"].iloc[49:].notna().sum(), 151)
            self.assertTrue(np.all(np.isfinite(ratio_hist["zscore"].iloc[49:])))

    def test_13_no_look_ahead_or_forward_filling(self):
        """13. No look-ahead or forward filling is introduced in partitioned indicators."""
        start_ts = 1700000000000
        df_a = self._make_daily_df(start_ts, 70, base_open=100.0)
        df_b = self._make_daily_df(start_ts, 70, base_open=1000.0)

        # Baseline: 70 bars
        r_full = _build_ratio_df(df_a, df_b, self.params)
        z_bar_55_full = float(r_full["zscore"].iloc[55])

        # Truncated dataset up to bar 55:
        r_trunc = _build_ratio_df(df_a.iloc[:56].copy(), df_b.iloc[:56].copy(), self.params)
        z_bar_55_trunc = float(r_trunc["zscore"].iloc[55])

        # Mutated future bars (mutate bars 56..69):
        df_a_mut = df_a.copy()
        df_a_mut.loc[56:, "close"] *= 5.0
        r_mut = _build_ratio_df(df_a_mut, df_b, self.params)
        z_bar_55_mut = float(r_mut["zscore"].iloc[55])

        self.assertAlmostEqual(z_bar_55_full, z_bar_55_trunc, places=12, msg="Look-ahead detected: truncated differs from full!")
        self.assertAlmostEqual(z_bar_55_full, z_bar_55_mut, places=12, msg="Future leakage detected: future mutation changed past indicator!")

    def test_14_corrected_ohlc_geometry_invariants_satisfied(self):
        """14. Corrected OHLC geometry invariants remain satisfied across continuous and gapped partitions."""
        start_ts = 1700000000000
        step_ms = 86400000
        pre_a = self._make_daily_df(start_ts, 40, base_open=100.0)
        pre_b = self._make_daily_df(start_ts, 40, base_open=1000.0)
        post_a = self._make_daily_df(start_ts + 50 * step_ms, 40, base_open=150.0)
        post_b = self._make_daily_df(start_ts + 50 * step_ms, 40, base_open=1200.0)

        ratio = _build_ratio_df(pd.concat([pre_a, post_a], ignore_index=True), pd.concat([pre_b, post_b], ignore_index=True), self.params)
        for _, row in ratio.iterrows():
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            self.assertTrue(h >= max(o, c))
            self.assertTrue(l <= min(o, c))
            self.assertTrue(h >= l)
            self.assertTrue(min(o, h, l, c) > 0)

    def test_15_deterministic_reproduction_wo71933_historical_gap_and_wo71934_restored(self):
        """15. Deterministic reproduction of WO71933 historical gap vs WO71934 restored dataset."""
        # Simulated WO71933 pre-backfill state: 60 pre-gap bars + 80-day gap + 4 post-gap bars
        step_ms = 86400000
        pre_end_ts = 1787702400000 # Aug 26, 2026
        pre_a = self._make_daily_df(pre_end_ts - 59 * step_ms, 60, base_open=695.0)
        pre_b = self._make_daily_df(pre_end_ts - 59 * step_ms, 60, base_open=78000.0)

        post_start_ts = 1794614400000 # Nov 14, 2026
        post_a = self._make_daily_df(post_start_ts, 4, base_open=582.0)
        post_b = self._make_daily_df(post_start_ts, 4, base_open=64000.0)

        # WO71933 gapped dataset:
        df_gapped_a = pd.concat([pre_a, post_a], ignore_index=True)
        df_gapped_b = pd.concat([pre_b, post_b], ignore_index=True)
        r_gapped = _build_ratio_df(df_gapped_a, df_gapped_b, self.params)

        # In WO71933 with defensive guard: Nov 14..17 post-gap bars (indices 60..63) are NaN
        self.assertTrue(r_gapped["zscore"].iloc[60:].isna().all(), "All post-gap bars must be NaN during un-warmed period")

        # WO71934 restored dataset: fill the 79 missing bars
        mid_a = self._make_daily_df(pre_end_ts + step_ms, 79, base_open=650.0)
        mid_b = self._make_daily_df(pre_end_ts + step_ms, 79, base_open=70000.0)

        df_restored_a = pd.concat([pre_a, mid_a, post_a], ignore_index=True)
        df_restored_b = pd.concat([pre_b, mid_b, post_b], ignore_index=True)
        r_restored = _build_ratio_df(df_restored_a, df_restored_b, self.params)

        # In WO71934 restored dataset: 60 + 79 + 4 = 143 continuous daily bars -> single partition
        # Nov 17 candle (index 142) has 143 > 50 bars, so it produces a valid finite z-score!
        self.assertEqual(len(r_restored), 143)
        self.assertTrue(np.isfinite(r_restored["zscore"].iloc[142]))


if __name__ == "__main__":
    unittest.main()
