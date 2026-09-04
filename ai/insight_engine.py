"""
AI Quantitative Reasoning & Strategy Insight Engine.
Generates institutional-grade quantitative diagnostics, regime analysis,
overfitting risk checks, and actionable deployment recommendations
based on backtest results.
"""
import math
from typing import Dict, Any, List, Optional
import pandas as pd


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

    # Kelly Criterion fraction estimate: K = W - (1-W)/R
    # Assume target R:R ratio ~ 2.0
    rr_est = 2.0
    kelly_fraction = (wr / 100.0) - ((1.0 - (wr / 100.0)) / rr_est)
    half_kelly = max(0.2, min(round((kelly_fraction / 2.0) * 100, 1), 3.0))

    if max_dd > 4.5 or ev < 0.15:
        recommended_risk = 0.5
    elif max_dd > 3.0 or sample_level == "low":
        recommended_risk = 0.75
    else:
        recommended_risk = 1.0

    # 3. Market Regime Diagnostics
    regime_state = "Neutral / Sideways Range"
    regime_volatility = "Normal"
    regime_fit_note = "Standard momentum and support/resistance rules apply."

    if df is not None and len(df) >= 50:
        last_bar = df.iloc[-1]
        close = float(last_bar.get("close", 0))
        ema20 = float(last_bar.get("ema_20", 0))
        ema50 = float(last_bar.get("ema_50", 0))
        atr = float(last_bar.get("atr", 0))
        adx = float(last_bar.get("adx", 0)) if "adx" in last_bar else None

        if ema20 > 0 and ema50 > 0:
            if close > ema20 > ema50:
                regime_state = "Strong Bullish Trend (EMA20 > EMA50)"
            elif close < ema20 < ema50:
                regime_state = "Strong Bearish Downtrend (EMA20 < EMA50)"
            else:
                regime_state = "Ranging / Consolidation Channel"

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
    if ev >= 0.25 and pf >= 1.35 and trades >= 15:
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
    prescriptions.append(f"Position Sizing: Cap risk at <b>{recommended_risk}%</b> per trade to cushion against potential drawdowns.")
    if "trend" in strategy_name or "breakout" in strategy_name:
        prescriptions.append("Exit Strategy: Enable <b>Trailing Stop</b> (activation at 1.0R, distance 1.5x ATR) to capture extended runs.")
    else:
        prescriptions.append("Exit Strategy: Use <b>Strict 1:2 Take Profit</b> to lock in gains at structural resistance levels.")

    prescriptions.append(f"Streak Warning: Expect a {prob_4_losses}% chance of experiencing 4 consecutive losses during regular market chop.")
    prescriptions.append(f"Telegram Forwarding: Automatically stream live entry and exit alerts to <b>@SignBTBot</b>.")

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
        },
        "prescriptions": prescriptions,
        "telegramReady": telegram_ready,
        "runnerCount": len(all_results),
        "profitableCount": len([r for r in all_results if (r.get("metrics", {}).get("expectancy_r") or 0) > 0]),
    }
