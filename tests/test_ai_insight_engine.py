import unittest

import numpy as np
import pandas as pd

from ai.insight_engine import generate_ai_insight, kelly_fraction_from_edge


def strat(ev, pf, wr, trades=40, dd=2.0):
    return {"strategy": "trend_ema_v1", "displayName": "Trend EMA", "rankScore": 0.5,
            "metrics": {"expectancy_r": ev, "profit_factor": pf, "win_rate_pct": wr,
                        "total_trades": trades, "max_drawdown_r": dd}}


def frame(direction, n=120):
    base = np.linspace(100, 100 + direction * 60, n)
    return pd.DataFrame({"close": base, "atr": np.full(n, 2.0)})


class TestKelly(unittest.TestCase):
    def test_no_edge_gives_zero_not_a_floor(self):
        self.assertEqual(kelly_fraction_from_edge(40, 0.8), 0.0)
        self.assertEqual(kelly_fraction_from_edge(50, 1.0), 0.0)
        self.assertEqual(kelly_fraction_from_edge(0, 2.0), 0.0)
        self.assertEqual(kelly_fraction_from_edge(100, 2.0), 0.0)
        self.assertEqual(kelly_fraction_from_edge(float("nan"), 2.0), 0.0)
        self.assertEqual(kelly_fraction_from_edge(50, float("inf")), 0.0)
        self.assertEqual(kelly_fraction_from_edge(None, 2.0), 0.0)

    def test_matches_closed_form(self):
        # w=0.5, pf=2 -> b=2 -> K = 0.5 - 0.5/2 = 0.25
        self.assertAlmostEqual(kelly_fraction_from_edge(50, 2.0), 0.25)


class TestInsightRisk(unittest.TestCase):
    def test_negative_edge_recommends_zero_risk_and_no_deploy(self):
        out = generate_ai_insight("BTCUSDT", "spot", "1d", [strat(-0.2, 0.7, 35)], [strat(-0.2, 0.7, 35)])
        self.assertEqual(out["verdict"], "NO_EDGE")
        self.assertFalse(out["telegramReady"])
        self.assertEqual(out["riskProfile"]["recommendedRiskPct"], 0.0)
        self.assertEqual(out["riskProfile"]["halfKellyPct"], 0.0)
        self.assertFalse(out["riskProfile"]["hasEdge"])

    def test_zero_expectancy_is_not_deployable(self):
        out = generate_ai_insight("BTCUSDT", "spot", "1d", [strat(0.0, 1.0, 50)], [strat(0.0, 1.0, 50)])
        self.assertEqual(out["riskProfile"]["recommendedRiskPct"], 0.0)

    def test_positive_edge_risk_is_capped_by_half_kelly(self):
        s = strat(0.3, 1.5, 45)
        out = generate_ai_insight("BTCUSDT", "spot", "1d", [s], [s])
        rp = out["riskProfile"]
        self.assertGreater(rp["recommendedRiskPct"], 0)
        self.assertLessEqual(rp["recommendedRiskPct"], rp["halfKellyPct"])
        self.assertLessEqual(rp["halfKellyPct"], 3.0)

    def test_weak_pf_cannot_get_default_one_percent(self):
        s = strat(0.4, 1.02, 50)  # tiny Kelly -> tiny half-Kelly
        out = generate_ai_insight("BTCUSDT", "spot", "1d", [s], [s])
        self.assertLess(out["riskProfile"]["recommendedRiskPct"], 1.0)


class TestRegime(unittest.TestCase):
    def _regime(self, df):
        s = strat(0.3, 1.5, 45)
        return generate_ai_insight("BTCUSDT", "spot", "1d", [s], [s], df=df)["regime"]["state"]

    def test_uptrend_detected_from_close_without_ema_columns(self):
        self.assertIn("Bullish", self._regime(frame(+1)))

    def test_downtrend_detected(self):
        self.assertIn("Bearish", self._regime(frame(-1)))

    def test_misaligned_emas_are_ranging(self):
        # Uptrend whose final bar drops below EMA20 (EMA20 still > EMA50): not a clean trend.
        df = frame(+1)
        df.loc[df.index[-1], "close"] = df["close"].iloc[0]
        self.assertIn("Ranging", self._regime(df))

    def test_missing_close_is_indeterminate_not_a_fake_label(self):
        df = pd.DataFrame({"atr": np.ones(80)})
        self.assertIn("Indeterminate", self._regime(df))

    def test_short_history_falls_back_safely(self):
        s = strat(0.3, 1.5, 45)
        out = generate_ai_insight("BTCUSDT", "spot", "1d", [s], [s], df=frame(+1, n=30))
        self.assertIn("regime", out)


if __name__ == "__main__":
    unittest.main()
