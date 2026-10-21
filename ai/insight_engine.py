"""
AI Quantitative Reasoning & Strategy Insight Engine.
Generates institutional-grade quantitative diagnostics, regime analysis,
overfitting risk checks, and actionable deployment recommendations
based on backtest results.
"""
import math
from typing import Dict, Any, List, Optional
import pandas as pd


MAX_HALF_KELLY_PCT = 3.0


def kelly_fraction_from_edge(win_rate_pct: float, profit_factor: float) -> float:
    """Kelly fraction (0..1) from realized win rate and profit factor.

    PF = (w * avgWin) / ((1 - w) * avgLoss), so the payoff ratio b = avgWin/avgLoss
    is PF * (1 - w) / w and Kelly = w - (1 - w) / b = w * (1 - 1/PF).
    Returns 0.0 for any non-positive, undefined or non-finite edge: never a floor.
    """
    try:
        w = float(win_rate_pct) / 100.0
        pf = float(profit_factor)
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(w) and math.isfinite(pf)) or not 0.0 < w < 1.0 or pf <= 1.0:
        return 0.0
    return max(0.0, w * (1.0 - 1.0 / pf))


def _ema_last(df: pd.DataFrame, span: int) -> Optional[float]:
    """Latest EMA(span) of close: use a stored ema_<span> column if present, else compute it."""
    column = f"ema_{span}"
    if column in df.columns:
        value = pd.to_numeric(df[column], errors="coerce").iloc[-1]
        if pd.notna(value) and float(value) > 0:
            return float(value)
    if "close" not in df.columns:
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(close) < span:
        return None
    value = close.ewm(span=span, adjust=False).mean().iloc[-1]
    return float(value) if pd.notna(value) and value > 0 else None


def generate_ai_insight(
    symbol: str,
    market: str,
    timeframe: str,
    top_strategies: List[Dict[str, Any]],
    all_results: List[Dict[str, Any]],
    df: Optional[pd.DataFrame] = None
) -> Dict[str, Any]:
    """
    Synthesizes multi-strategy backtest distributions and generates an AI
    verdict with risk profiling, regime alignment, and Telegram deployment guidance.
    """
    if not top_strategies:
        return {
            "verdict": "NO_VIABLE_STRATEGY",
            "verdictLabel": "No Viable Strategies Found",
            "badgeColor": "red",
            "headline": f"None of the evaluated strategies met the minimum profitability threshold on {symbol} {timeframe}.",
            "summary": "Every evaluated model exhibited negative expectancy or had fewer than the required minimum trades.",
            "recommendation": "Avoid deploying automated signals on this pair/timeframe. Consider testing on higher timeframes (4h, 1d) where fee friction is lower.",
            "telegramReady": False,
            "regime": {"state": "Indeterminate", "volatility": "Normal", "fitNote": "Insufficient edge"},
            "riskProfile": {"recommendedRiskPct": 0.0, "maxConsecutiveLossesRisk": "High", "safeCapital": 0},
            "prescriptions": [
                "Switch to a higher timeframe like 4h or 1d to reduce spread and commission drag.",
                "Backfill longer historical data to establish statistically robust sample sizes."
            ]
        }

    best = top_strategies[0]
    metrics = best.get("metrics", {})
    equity = best.get("equity", {})

    strategy_name = best.get("strategy", "Unknown")
    display_name = best.get("displayName", strategy_name)
    ev = float(metrics.get("expectancy_r") or 0.0)
    pf = float(metrics.get("profit_factor") or 0.0)
    wr = float(metrics.get("win_rate_pct") or 0.0)
    trades = int(metrics.get("total_trades") or 0)
    max_dd = abs(float(metrics.get("max_drawdown_r") or 0.0))
    rank_score = float(best.get("rankScore") or 0.0)

    # 1. Statistical Reliability & Sample Size
    if trades >= 45:
        sample_confidence = "High (Statistically Robust)"
        sample_note = f"Sample size of {trades} trades provides high statistical confidence (low risk of random walk noise)."
        sample_level = "high"
    elif trades >= 20:
        sample_confidence = "Moderate (Acceptable Confidence)"
        sample_note = f"Sample size of {trades} trades is acceptable, but results should be monitored closely in live paper forward testing."
        sample_level = "moderate"
    else:
        sample_confidence = "Low (Small Sample Warning)"
        sample_note = f"⚠️ Only {trades} trades observed. Small sample sizes are susceptible to curve-fitting and statistical anomalies."
        sample_level = "low"

    # 2. Risk Metrics & Loss Streaks
    # Probability of 4 consecutive losses = (1 - WR)^4
    loss_rate = max(0.0, 1.0 - (wr / 100.0))
    prob_4_losses = round((loss_rate ** 4) * 100, 1)
    prob_6_losses = round((loss_rate ** 6) * 100, 1)

    # Kelly from realized edge (win rate + profit factor). No floor: no edge, no risk.
    kelly_fraction = kelly_fraction_from_edge(wr, pf)
    has_edge = ev > 0 and pf > 1.0 and kelly_fraction > 0
    half_kelly = min(round((kelly_fraction / 2.0) * 100, 1), MAX_HALF_KELLY_PCT) if has_edge else 0.0

    if not has_edge:
        recommended_risk = 0.0
    elif max_dd > 4.5 or ev < 0.15:
        recommended_risk = 0.5
    elif max_dd > 3.0 or sample_level == "low":
        recommended_risk = 0.75
    else:
        recommended_risk = 1.0
    if has_edge and half_kelly > 0:
        # Never recommend more than half-Kelly of the measured edge.
        recommended_risk = min(recommended_risk, half_kelly)

    # 3. Market Regime Diagnostics
    regime_state = "Neutral / Sideways Range"
    regime_volatility = "Normal"
    regime_fit_note = "Standard momentum and support/resistance rules apply."

    if df is not None and len(df) >= 50:
        last_bar = df.iloc[-1]
        close = float(pd.to_numeric(last_bar.get("close"), errors="coerce") or 0)
        atr_raw = pd.to_numeric(last_bar.get("atr"), errors="coerce")
        atr = float(atr_raw) if pd.notna(atr_raw) else 0.0
        ema20 = _ema_last(df, 20)
        ema50 = _ema_last(df, 50)

        if close > 0 and ema20 and ema50:
            if close > ema20 > ema50:
                regime_state = "Bullish Trend (Close > EMA20 > EMA50)"
            elif close < ema20 < ema50:
                regime_state = "Bearish Downtrend (Close < EMA20 < EMA50)"
            else:
                regime_state = "Ranging / Mixed (EMAs not aligned)"
        else:
            regime_state = "Indeterminate (insufficient price history)"

        if atr > 0 and close > 0:
            atr_pct = (atr / close) * 100
            if atr_pct > 3.5:
                regime_volatility = f"Elevated Volatility (ATR {atr_pct:.1f}%)"
            elif atr_pct < 1.2:
                regime_volatility = f"Low Volatility Compression (ATR {atr_pct:.1f}%)"
            else:
                regime_volatility = f"Normal Volatility (ATR {atr_pct:.1f}%)"

        if "inverse" in strategy_name.lower() or "reversal" in strategy_name.lower():
            regime_fit_note = f"Strategy '{display_name}' capitalizes on false breakouts and overextended market extremes."
        elif "breakout" in strategy_name.lower() or "trend" in strategy_name.lower():
            regime_fit_note = f"Strategy '{display_name}' thrives in directional momentum; best aligned during expansion cycles."
        else:
            regime_fit_note = f"Multi-indicator confluence filters noise and optimizes risk:reward distribution."

    # 4. Verdict Determination
    if not has_edge:
        verdict = "NO_EDGE"
        verdict_label = "🔴 No Positive Edge: Do Not Deploy"
        badge_color = "red"
        headline = f"Strategy <b>{display_name}</b> has no positive edge on this sample (expectancy {ev:+.2f}R, Profit Factor {pf:.2f})."
        telegram_ready = False
    elif ev >= 0.25 and pf >= 1.35 and trades >= 15:
        verdict = "STRONG_DEPLOY"
        verdict_label = "🟢 Highly Recommended for Live Paper Trading"
        badge_color = "emerald"
        headline = f"🥇 <b>{display_name}</b> demonstrates exceptional alpha with +{ev:.2f}R expectancy and Profit Factor {pf:.2f}."
        telegram_ready = True
    elif ev > 0.05 and pf >= 1.10 and trades >= 10:
        verdict = "MODERATE_DEPLOY"
        verdict_label = "🟡 Viable Candidate (Forward Paper Testing Recommended)"
        badge_color = "amber"
        headline = f"🥈 <b>{display_name}</b> delivers positive edge (+{ev:.2f}R/trade) with moderate payoff efficiency."
        telegram_ready = True
    else:
        verdict = "MARGINAL_EDGE"
        verdict_label = "⚠️ Marginal Edge / Higher Risk"
        badge_color = "orange"
        headline = f"Strategy <b>{display_name}</b> shows borderline profitability. High sensitivity to fees."
        telegram_ready = False

    # Actionable Prescriptions
    prescriptions = []
    if not has_edge:
        prescriptions.append("Position Sizing: <b>0%</b>. Do not risk capital without a measured positive edge; re-test on more data or a different timeframe.")
    else:
        prescriptions.append(f"Position Sizing: Cap risk at <b>{recommended_risk}%</b> per trade to cushion against potential drawdowns.")
    if "trend" in strategy_name or "breakout" in strategy_name:
        prescriptions.append("Exit Strategy: Enable <b>Trailing Stop</b> (activation at 1.0R, distance 1.5x ATR) to capture extended runs.")
    else:
        prescriptions.append("Exit Strategy: Use <b>Strict 1:2 Take Profit</b> to lock in gains at structural resistance levels.")

    prescriptions.append(f"Streak Warning: Expect a {prob_4_losses}% chance of experiencing 4 consecutive losses during regular market chop.")
    if telegram_ready:
        prescriptions.append("Telegram Forwarding: Stream paper entry and exit alerts to your configured Telegram chat.")

    return {
        "verdict": verdict,
        "verdictLabel": verdict_label,
        "badgeColor": badge_color,
        "headline": headline,
        "topStrategy": {
            "name": strategy_name,
            "displayName": display_name,
            "category": best.get("category", "Quantitative"),
            "expectancyR": ev,
            "profitFactor": pf,
            "winRatePct": wr,
            "totalTrades": trades,
            "maxDrawdownR": max_dd,
            "rankScore": rank_score,
            "sampleConfidence": sample_confidence,
            "sampleLevel": sample_level,
            "sampleNote": sample_note,
        },
        "regime": {
            "state": regime_state,
            "volatility": regime_volatility,
            "fitNote": regime_fit_note,
        },
        "riskProfile": {
            "recommendedRiskPct": recommended_risk,
            "halfKellyPct": half_kelly,
            "prob4Losses": prob_4_losses,
            "prob6Losses": prob_6_losses,
            "maxDrawdownR": max_dd,
            "hasEdge": has_edge,
        },
        "prescriptions": prescriptions,
        "telegramReady": telegram_ready,
        "runnerCount": len(all_results),
        "profitableCount": len([r for r in all_results if (r.get("metrics", {}).get("expectancy_r") or 0) > 0]),
    }
