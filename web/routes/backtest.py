import json
import logging
import hashlib
import math
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

from db.db import (
    fetch_ohlcv_with_features_df, insert_backtest_run, insert_backtest_trades, get_pool,
    get_strategy_auto_promotion_policy, set_strategy_auto_promotion_policy,
    get_strategy_promotion_audit, get_strategy_promotion_decision,
    record_strategy_promotion_decision, update_strategy_promotion_decision,
)
from backtest.strategies import (
    STRATEGIES, FUNDING_STRATEGIES, TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
)
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.params import BASE_PARAMS
from backtest.metrics import compute_metrics
from backtest.equity import simulate_equity_curve
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff
from config import HOLDOUT_FRACTION, PAIRS_BASE_SYMBOL

router = APIRouter(prefix="/api/backtest", tags=["backtest"])
logger = logging.getLogger("web.backtest")

STRATEGY_METADATA = {
    "confluence_ensemble_v1": {
        "displayName": "Confluence Ensemble (Multi-Component)",
        "category": "Multi-Signal Ensemble",
        "validated": True,
        "description": "Combines 6 independent components (RSI, Trend, MACD, Volume, S/R, Funding) into a composite score. Validated survivor on ETH spot 1d (+0.494R holdout).",
        "params": [
            {"key": "MIN_CONFLUENCE_SCORE", "label": "Min Score Threshold", "type": "float", "default": 3.0, "min": 1.0, "max": 6.0, "step": 0.5},
            {"key": "CONFLUENCE_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
            {"key": "CONFLUENCE_WEIGHT_RSI", "label": "Weight: RSI", "type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.5},
            {"key": "CONFLUENCE_WEIGHT_TREND", "label": "Weight: Trend (EMA)", "type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.5},
            {"key": "CONFLUENCE_WEIGHT_MACD", "label": "Weight: MACD", "type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.5},
            {"key": "CONFLUENCE_WEIGHT_VOLUME", "label": "Weight: Volume", "type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.5},
            {"key": "CONFLUENCE_WEIGHT_SR", "label": "Weight: Support/Resistance", "type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.5},
            {"key": "CONFLUENCE_WEIGHT_FUNDING", "label": "Weight: Funding Rate", "type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "step": 0.5},
        ]
    },
    "pairs_ratio_v1": {
        "displayName": "Pairs Ratio / Stat-Arb (Relative Value)",
        "category": "Relative Value",
        "validated": True,
        "description": "Trades the ratio of an asset against BTC reverting to its rolling mean via Z-Score. Top performer on BNB futures 1d (+0.711R holdout).",
        "params": [
            {"key": "PAIRS_BASE_SYMBOL", "label": "Base Leg Symbol", "type": "string", "default": "BTCUSDT"},
            {"key": "PAIRS_ZSCORE_LOOKBACK_BARS", "label": "Z-Score Lookback (Bars)", "type": "int", "default": 50, "min": 10, "max": 200, "step": 5},
            {"key": "PAIRS_ZSCORE_ENTRY_THRESHOLD", "label": "Entry Z-Score Threshold", "type": "float", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.25},
            {"key": "PAIRS_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "trend_ema_v1": {
        "displayName": "Trend EMA Crossover",
        "category": "Trend Following",
        "validated": False,
        "description": "Classic trend-following system entering on fast/slow EMA crossovers confirmed by MACD momentum.",
        "params": [
            {"key": "TREND_EMA_FAST", "label": "Fast EMA Period", "type": "int", "default": 20, "min": 5, "max": 50, "step": 1},
            {"key": "TREND_EMA_SLOW", "label": "Slow EMA Period", "type": "int", "default": 50, "min": 20, "max": 200, "step": 5},
            {"key": "TREND_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 3.0, "min": 1.5, "max": 6.0, "step": 0.5},
        ]
    },
    "trend_alignment_v1": {
        "displayName": "Trend Alignment (Multi-Timeframe)",
        "category": "Trend Following",
        "validated": False,
        "description": "Trades EMA crossover only when aligned with higher-timeframe (Daily) trend bias.",
        "params": [
            {"key": "TREND_EMA_FAST", "label": "Fast EMA Period", "type": "int", "default": 20, "min": 5, "max": 50, "step": 1},
            {"key": "TREND_EMA_SLOW", "label": "Slow EMA Period", "type": "int", "default": 50, "min": 20, "max": 200, "step": 5},
            {"key": "TREND_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 3.0, "min": 1.5, "max": 6.0, "step": 0.5},
        ]
    },
    "breakout_continuation_v1": {
        "displayName": "Breakout Continuation",
        "category": "Momentum Breakout",
        "validated": False,
        "description": "Enters on decisive breaks of rolling multi-candle high/low with minimum margin threshold.",
        "params": [
            {"key": "BREAKOUT_LOOKBACK_BARS", "label": "Breakout Lookback Bars", "type": "int", "default": 100, "min": 20, "max": 300, "step": 10},
            {"key": "BREAKOUT_MIN_MARGIN_PCT", "label": "Min Margin Beyond Level (%)", "type": "float", "default": 0.15, "min": 0.05, "max": 1.0, "step": 0.05},
            {"key": "BREAKOUT_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 3.0, "min": 1.5, "max": 5.0, "step": 0.5},
        ]
    },
    "confluence_reversal_v1": {
        "displayName": "Confluence Reversal (Mean Reversion)",
        "category": "Mean Reversion",
        "validated": False,
        "description": "Fades extremes when RSI is overbought/oversold, price touches support/resistance, and volume spikes.",
        "params": [
            {"key": "RSI_OVERSOLD", "label": "RSI Oversold Level", "type": "int", "default": 30, "min": 15, "max": 40, "step": 1},
            {"key": "RSI_OVERBOUGHT", "label": "RSI Overbought Level", "type": "int", "default": 70, "min": 60, "max": 85, "step": 1},
            {"key": "SR_PROXIMITY_PCT", "label": "S/R Proximity (%)", "type": "float", "default": 0.5, "min": 0.1, "max": 2.0, "step": 0.1},
            {"key": "VOLUME_MULTIPLIER", "label": "Volume Multiplier", "type": "float", "default": 1.2, "min": 1.0, "max": 3.0, "step": 0.1},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.5},
        ]
    },
    "funding_extreme_reversal_v1": {
        "displayName": "Funding Rate Extreme Reversal",
        "category": "Sentiment Reversal",
        "validated": False,
        "description": "Fades crowded positions when 8h funding rate hits historical extremes (short squeezes / long unwinds).",
        "params": [
            {"key": "FUNDING_EXTREME_POSITIVE_PCT", "label": "Positive Extreme (% per 8h)", "type": "float", "default": 0.05, "min": 0.01, "max": 0.3, "step": 0.01},
            {"key": "FUNDING_EXTREME_NEGATIVE_PCT", "label": "Negative Extreme (% per 8h)", "type": "float", "default": -0.05, "min": -0.3, "max": -0.01, "step": 0.01},
            {"key": "FUNDING_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "smc_liquidity_sweep_v1": {
        "displayName": "SMC Liquidity Sweep",
        "category": "Smart Money Concepts",
        "validated": False,
        "description": "Detects institutional stop hunts where wicks sweep beyond S/R and close back inside.",
        "params": [
            {"key": "SMC_WICK_MIN_ATR_MULT", "label": "Wick Min ATR Multiple", "type": "float", "default": 0.3, "min": 0.1, "max": 1.5, "step": 0.05},
            {"key": "SMC_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "supply_demand_v1": {
        "displayName": "Supply & Demand Zone Retest",
        "category": "Price Action",
        "validated": False,
        "description": "Identifies base candle + displacement zones, entering when price returns to retest the unmitigated zone.",
        "params": [
            {"key": "SD_BASE_MAX_ATR_MULT", "label": "Base Max ATR Mult", "type": "float", "default": 0.3, "min": 0.1, "max": 1.0, "step": 0.05},
            {"key": "SD_DISPLACEMENT_ATR_MULT", "label": "Displacement ATR Mult", "type": "float", "default": 1.0, "min": 0.5, "max": 3.0, "step": 0.1},
            {"key": "SD_MAX_ZONE_AGE_BARS", "label": "Max Zone Age (Bars)", "type": "int", "default": 100, "min": 20, "max": 300, "step": 10},
            {"key": "SD_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "fibonacci_retracement_v1": {
        "displayName": "Fibonacci Retracement Pullback",
        "category": "Retracement",
        "validated": False,
        "description": "Trades in the direction of the EMA trend when price pulls back into the 61.8%–78.6% golden pocket.",
        "params": [
            {"key": "FIB_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "double_pattern_v1": {
        "displayName": "Double Top / Double Bottom",
        "category": "Chart Patterns",
        "validated": False,
        "description": "Classic W/M reversal pattern detecting two confirmed swing pivots and entering on neckline break.",
        "params": [
            {"key": "PIVOT_LOOKBACK_BARS", "label": "Pivot Confirmation Bars", "type": "int", "default": 5, "min": 2, "max": 15, "step": 1},
            {"key": "DOUBLE_PATTERN_TOLERANCE_PCT", "label": "Pivot Price Tolerance (%)", "type": "float", "default": 1.0, "min": 0.2, "max": 3.0, "step": 0.1},
            {"key": "DOUBLE_PATTERN_MAX_BARS_APART", "label": "Max Bars Between Pivots", "type": "int", "default": 60, "min": 10, "max": 150, "step": 5},
            {"key": "DOUBLE_PATTERN_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.5, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "volatility_breakout_v1": {
        "displayName": "Volatility Expansion Breakout",
        "category": "Momentum Breakout",
        "validated": False,
        "description": "Enters on price breaking a rolling high/low when current ATR expands over baseline ATR.",
        "params": [
            {"key": "VOL_BREAKOUT_LOOKBACK_BARS", "label": "Lookback Bars", "type": "int", "default": 100, "min": 20, "max": 200, "step": 10},
            {"key": "VOL_BREAKOUT_ATR_BASELINE_BARS", "label": "ATR Baseline Window", "type": "int", "default": 50, "min": 14, "max": 100, "step": 5},
            {"key": "VOL_BREAKOUT_ATR_EXPANSION_MULT", "label": "ATR Expansion Mult", "type": "float", "default": 1.1, "min": 1.0, "max": 2.0, "step": 0.05},
            {"key": "VOL_BREAKOUT_MIN_MARGIN_PCT", "label": "Min Breakout Margin (%)", "type": "float", "default": 0.15, "min": 0.05, "max": 1.0, "step": 0.05},
            {"key": "VOL_BREAKOUT_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 3.0, "min": 1.5, "max": 5.0, "step": 0.5},
        ]
    },
    "BB_REV": {
        "displayName": "Bollinger Band Reversion (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Fades price reaching outer Bollinger Bands expecting mean reversion. Classic retail behavior vulnerable to strong trends.",
        "params": [
            {"key": "BB_PERIOD", "label": "BB Period", "type": "int", "default": 20, "min": 10, "max": 50, "step": 1},
            {"key": "BB_STD", "label": "BB Std Dev", "type": "float", "default": 2.0, "min": 1.0, "max": 3.5, "step": 0.1},
            {"key": "BB_PROXIMITY_PCT", "label": "Proximity Threshold (%)", "type": "float", "default": 0.3, "factor": 100, "min": 0.05, "max": 1.0, "step": 0.05},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "BB_REV_INV": {
        "displayName": "Inverse Bollinger Band Reversion",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts BB_REV. Takes opposite side of retail crowd when price reaches Bollinger Bands to ride momentum breakouts.",
        "params": [
            {"key": "BB_PERIOD", "label": "BB Period", "type": "int", "default": 20, "min": 10, "max": 50, "step": 1},
            {"key": "BB_STD", "label": "BB Std Dev", "type": "float", "default": 2.0, "min": 1.0, "max": 3.5, "step": 0.1},
            {"key": "BB_PROXIMITY_PCT", "label": "Proximity Threshold (%)", "type": "float", "default": 0.3, "factor": 100, "min": 0.05, "max": 1.0, "step": 0.05},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "DBL_PAT": {
        "displayName": "Double Top / Double Bottom (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Enters classic retail double top (short) or double bottom (long) formations.",
        "params": [
            {"key": "PATTERN_LOOKBACK", "label": "Lookback Bars", "type": "int", "default": 60, "min": 20, "max": 120, "step": 5},
            {"key": "PATTERN_TOLERANCE", "label": "Pivot Tolerance (%)", "type": "float", "default": 0.2, "factor": 100, "min": 0.05, "max": 1.0, "step": 0.05},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "DBL_PAT_INV": {
        "displayName": "Inverse Double Pattern",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts DBL_PAT. Anticipates retail stop hunts and false breakout traps at double top and bottom levels.",
        "params": [
            {"key": "PATTERN_LOOKBACK", "label": "Lookback Bars", "type": "int", "default": 60, "min": 20, "max": 120, "step": 5},
            {"key": "PATTERN_TOLERANCE", "label": "Pivot Tolerance (%)", "type": "float", "default": 0.2, "factor": 100, "min": 0.05, "max": 1.0, "step": 0.05},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "RSI_CTR": {
        "displayName": "RSI Counter-Trend (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Fades overbought/oversold RSI extremes without overall trend filter.",
        "params": [
            {"key": "RSI_PERIOD", "label": "RSI Period", "type": "int", "default": 14, "min": 7, "max": 30, "step": 1},
            {"key": "RSI_OVERSOLD", "label": "RSI Oversold Level", "type": "int", "default": 35, "min": 20, "max": 45, "step": 1},
            {"key": "RSI_OVERBOUGHT", "label": "RSI Overbought Level", "type": "int", "default": 65, "min": 55, "max": 80, "step": 1},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "RSI_CTR_INV": {
        "displayName": "Inverse RSI Counter-Trend",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts RSI_CTR. Follows strong trend momentum when RSI pushes into extreme territory.",
        "params": [
            {"key": "RSI_PERIOD", "label": "RSI Period", "type": "int", "default": 14, "min": 7, "max": 30, "step": 1},
            {"key": "RSI_OVERSOLD", "label": "RSI Oversold Level", "type": "int", "default": 35, "min": 20, "max": 45, "step": 1},
            {"key": "RSI_OVERBOUGHT", "label": "RSI Overbought Level", "type": "int", "default": 65, "min": 55, "max": 80, "step": 1},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "MA_WHIP": {
        "displayName": "MA Crossover Whipsaw (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Enters on fast/slow EMA crossovers. Prone to severe whipsaws during ranging markets.",
        "params": [
            {"key": "MA_FAST", "label": "Fast EMA Period", "type": "int", "default": 9, "min": 3, "max": 20, "step": 1},
            {"key": "MA_SLOW", "label": "Slow EMA Period", "type": "int", "default": 21, "min": 15, "max": 50, "step": 1},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "MA_WHIP_INV": {
        "displayName": "Inverse MA Crossover Whipsaw",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts MA_WHIP. Capitalizes on retail whipsaws by fading late MA crosses.",
        "params": [
            {"key": "MA_FAST", "label": "Fast EMA Period", "type": "int", "default": 9, "min": 3, "max": 20, "step": 1},
            {"key": "MA_SLOW", "label": "Slow EMA Period", "type": "int", "default": 21, "min": 15, "max": 50, "step": 1},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "SR_TRAP": {
        "displayName": "S/R Bounce Trap (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Buys support and sells resistance blindly without higher-timeframe trend context.",
        "params": [
            {"key": "SR_LOOKBACK", "label": "S/R Lookback Bars", "type": "int", "default": 50, "min": 20, "max": 100, "step": 5},
            {"key": "SR_TOUCH_THRESHOLD", "label": "Touch Threshold (%)", "type": "float", "default": 0.15, "factor": 100, "min": 0.05, "max": 0.5, "step": 0.05},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "SR_TRAP_INV": {
        "displayName": "Inverse S/R Bounce Trap",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts SR_TRAP. Trades breakdown continuations when support fails and breakout continuations when resistance breaks.",
        "params": [
            {"key": "SR_LOOKBACK", "label": "S/R Lookback Bars", "type": "int", "default": 50, "min": 20, "max": 100, "step": 5},
            {"key": "SR_TOUCH_THRESHOLD", "label": "Touch Threshold (%)", "type": "float", "default": 0.15, "factor": 100, "min": 0.05, "max": 0.5, "step": 0.05},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "MACD_DIV": {
        "displayName": "MACD Divergence (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Fades trends based on MACD histogram divergences.",
        "params": [
            {"key": "MACD_FAST", "label": "MACD Fast Period", "type": "int", "default": 12, "min": 5, "max": 20, "step": 1},
            {"key": "MACD_SLOW", "label": "MACD Slow Period", "type": "int", "default": 26, "min": 20, "max": 40, "step": 1},
            {"key": "DIVERGENCE_LOOKBACK", "label": "Divergence Lookback", "type": "int", "default": 30, "min": 15, "max": 60, "step": 5},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "MACD_DIV_INV": {
        "displayName": "Inverse MACD Divergence",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts MACD_DIV. Exploits false divergence signals in strong trending markets.",
        "params": [
            {"key": "MACD_FAST", "label": "MACD Fast Period", "type": "int", "default": 12, "min": 5, "max": 20, "step": 1},
            {"key": "MACD_SLOW", "label": "MACD Slow Period", "type": "int", "default": 26, "min": 20, "max": 40, "step": 1},
            {"key": "DIVERGENCE_LOOKBACK", "label": "Divergence Lookback", "type": "int", "default": 30, "min": 15, "max": 60, "step": 5},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "SPIKE_FADE": {
        "displayName": "Spike Fade (Retail)",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Fades large volatility expansion candles anticipating immediate mean reversion.",
        "params": [
            {"key": "SPIKE_ATR_MULTIPLIER", "label": "Spike ATR Multiple", "type": "float", "default": 2.5, "min": 1.5, "max": 4.0, "step": 0.25},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "SPIKE_FADE_INV": {
        "displayName": "Inverse Spike Fade",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts SPIKE_FADE. Rides high-momentum breakout candles rather than fading them.",
        "params": [
            {"key": "SPIKE_ATR_MULTIPLIER", "label": "Spike ATR Multiple", "type": "float", "default": 2.5, "min": 1.5, "max": 4.0, "step": 0.25},
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "COMPOSITE": {
        "displayName": "Composite Retail Trap Bot",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Combines signals from all retail strategies; trades when majority consensus triggers.",
        "params": [
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
    "COMPOSITE_INV": {
        "displayName": "Inverse Composite Retail Trap Bot",
        "category": "RetailBot2 (Research)",
        "validated": False,
        "description": "Inverts composite retail consensus. Takes opposite side of combined retail signals.",
        "params": [
            {"key": "RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5},
        ]
    },
}

SHARED_EXECUTION_PARAMS = [
    {"key": "BACKTEST_FEE_PCT", "label": "Fee per Side (%)", "type": "float", "default": 0.001, "factor": 100, "min": 0.0, "max": 0.5, "step": 0.01},
    {"key": "BACKTEST_SLIPPAGE_PCT", "label": "Slippage per Side (%)", "type": "float", "default": 0.0005, "factor": 100, "min": 0.0, "max": 0.2, "step": 0.01},
    {"key": "BACKTEST_MAX_HOLD_BARS", "label": "Max Holding Period (Bars)", "type": "int", "default": 200, "min": 20, "max": 1000, "step": 20},
    {"key": "USE_TRAILING_STOP", "label": "Enable Trailing Stop", "type": "bool", "default": False},
    {"key": "TRAIL_ACTIVATION_R", "label": "Trail Activation (R)", "type": "float", "default": 1.0, "min": 0.5, "max": 3.0, "step": 0.25},
    {"key": "TRAIL_DISTANCE_ATR_MULT", "label": "Trail Distance (x ATR)", "type": "float", "default": 1.5, "min": 0.5, "max": 4.0, "step": 0.25},
]


class StrategyAutoPromotionPolicyRequest(BaseModel):
    enabled: bool = False
    min_test_trades: int = Field(30, ge=30, le=10000)
    min_profit_factor: float = Field(1.2, ge=1.0, le=10.0)
    min_expectancy_r: float = Field(0.05, ge=0.0, le=10.0)
    max_drawdown_r: float = Field(8.0, gt=0.0, le=100.0)
    min_rank_score: float = Field(0.1, ge=0.01, le=1.0)
    require_holdout: bool = True
    send_telegram: bool = True


@router.get("/promotion-policy")
def get_promotion_policy():
    try:
        return {"policy": get_strategy_auto_promotion_policy()}
    except Exception as exc:
        logger.error("Failed to load auto-promotion policy (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unable to load promotion policy")


@router.put("/promotion-policy")
def update_promotion_policy(req: StrategyAutoPromotionPolicyRequest):
    values = req.dict()
    for key in ("min_profit_factor", "min_expectancy_r", "max_drawdown_r", "min_rank_score"):
        if not math.isfinite(values[key]):
            raise HTTPException(status_code=400, detail=f"{key} must be finite")
    try:
        set_strategy_auto_promotion_policy(values)
    except Exception as exc:
        logger.error("Failed to save auto-promotion policy (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unable to save promotion policy")

    try:
        persisted_policy = get_strategy_auto_promotion_policy()
    except Exception as exc:
        # The write has already returned successfully. Do not report this as a
        # failed save: the UI might retry and the user could mistake the
        # committed policy for a rejected update. Keep the submitted values
        # explicit, but do not imply that authoritative readback was confirmed.
        logger.error("Promotion policy saved but readback failed (%s)", type(exc).__name__)
        return {
            "status": "saved_readback_unavailable",
            "policy": values,
            "warning": (
                "The policy save completed, but reading it back failed. "
                "The returned policy reflects the submitted values and has not been verified by readback."
            ),
        }

    return {"status": "ok", "policy": persisted_policy}


@router.get("/promotion-audit")
def list_promotion_audit(limit: int = Query(25, ge=1, le=100)):
    try:
        return {"events": get_strategy_promotion_audit(limit)}
    except Exception as exc:
        logger.error("Failed to load promotion audit (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unable to load promotion audit")


class BacktestRunRequest(BaseModel):
    symbol: str = Field(..., max_length=20, json_schema_extra={"example": "BTCUSDT"})
    market: str = Field("spot", max_length=7, json_schema_extra={"example": "spot"})
    timeframe: str = Field("1d", max_length=10, json_schema_extra={"example": "1d"})
    strategy: str = Field("confluence_ensemble_v1", max_length=50, json_schema_extra={"example": "confluence_ensemble_v1"})
    data_segment: str = Field("full", max_length=10, json_schema_extra={"example": "full"})  # train, holdout, full
    initial_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    save_to_db: bool = Field(True)
    params: Dict[str, Any] = Field(default_factory=dict)


def _auto_promote_top_candidate(
    candidate, *, symbol, market, timeframe, data_segment,
    allocated_capital, risk_per_trade_pct, validation_metrics=None,
    validation_segment=None,
):
    """Apply the saved policy to the single top-ranked candidate. Fail closed
    on missing metrics, non-holdout data, unmapped strategies, or DB errors."""
    try:
        policy = get_strategy_auto_promotion_policy()
    except Exception as exc:
        logger.exception("Could not load auto-promotion policy")
        return {"status": "error", "reason": f"Policy unavailable: {exc}"}

    # Persisted flags must be actual booleans. In particular, strings such
    # as "false" are truthy in Python and must never enable promotion or alerts.
    for key in ("enabled", "require_holdout", "send_telegram"):
        if not isinstance(policy.get(key), bool):
            return {
                "status": "error",
                "reason": f"Invalid auto-promotion policy: {key} must be a boolean. Correct the saved policy before retrying.",
            }

    if not policy.get("enabled"):
        return {"status": "disabled", "reason": "Automatic promotion is disabled in settings"}

    # The UI/API validates policy writes, but the persisted row may be stale,
    # manually edited, or corrupted. Revalidate before building the decision
    # identity or claiming an audit row so invalid thresholds cannot crash the
    # backtest request or weaken promotion gates.
    try:
        min_trades_raw = policy["min_test_trades"]
        min_trades = int(min_trades_raw)
        if isinstance(min_trades_raw, bool) or float(min_trades_raw) != min_trades:
            raise ValueError("min_test_trades must be an integer")
        if not 30 <= min_trades <= 10000:
            raise ValueError("min_test_trades is outside the supported range")
        threshold_ranges = {
            "min_profit_factor": (1.0, 10.0, False),
            "min_expectancy_r": (0.0, 10.0, False),
            "max_drawdown_r": (0.0, 100.0, True),
            "min_rank_score": (0.01, 1.0, False),
        }
        for key, (minimum, maximum, exclusive_minimum) in threshold_ranges.items():
            value = float(policy[key])
            if not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
            if (value <= minimum if exclusive_minimum else value < minimum) or value > maximum:
                raise ValueError(f"{key} is outside the supported range")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        logger.error("Invalid persisted auto-promotion policy: %s", exc)
        return {
            "status": "error",
            "reason": f"Invalid auto-promotion policy: {exc}. Correct the saved policy before retrying.",
        }

    if not candidate:
        return {"status": "no_candidate", "reason": "No profitable ranked candidate was found"}

    strategy_name = str(candidate.get("strategy") or "")
    selection_metrics = candidate.get("metrics") or {}
    # When holdout is required, apply profitability gates to the separately
    # evaluated holdout metrics, never to training metrics used for ranking.
    metrics = (
        validation_metrics
        if policy.get("require_holdout") and isinstance(validation_metrics, dict)
        else selection_metrics
    )
    try:
        total_trades_raw = metrics.get("total_trades")
        total_trades = int(total_trades_raw)
        if isinstance(total_trades_raw, bool) or float(total_trades_raw) != total_trades:
            raise ValueError("total_trades is not an integer")
        expectancy = float(metrics["expectancy_r"])
        profit_factor = float(metrics["profit_factor"])
        drawdown_r = abs(float(metrics["max_drawdown_r"]))
        rank_score = float(candidate["rankScore"])
        numeric_metrics = (expectancy, profit_factor, drawdown_r, rank_score)
        if not all(math.isfinite(value) for value in numeric_metrics):
            raise ValueError("metrics contain non-finite values")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {"status": "rejected", "strategy": strategy_name,
                "reason": f"Required metrics are missing or invalid: {exc}"}

    criteria = {
        key: policy[key] for key in (
            "enabled", "min_test_trades", "min_profit_factor", "min_expectancy_r",
            "max_drawdown_r", "min_rank_score", "require_holdout", "send_telegram",
        )
    }
    metrics_snapshot = {
        "total_trades": total_trades,
        "expectancy_r": expectancy,
        "profit_factor": profit_factor,
        "max_drawdown_r": drawdown_r,
        "rank_score": rank_score,
        "selection_segment": data_segment,
        "validation_segment": validation_segment,
    }
    audit_segment = validation_segment if validation_metrics is not None and validation_segment else data_segment
    identity = {
        "symbol": symbol, "market": market, "timeframe": timeframe,
        "data_segment": audit_segment, "selection_segment": data_segment,
        "strategy_name": strategy_name,
        "metrics": metrics_snapshot, "criteria": criteria,
    }
    decision_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()

    failures = []
    if strategy_name not in STRATEGIES:
        failures.append("Strategy is not present in the executable paper-trading registry")
    # Ranking must use train data. If holdout is required, profitability
    # thresholds below are applied to a separate evaluation of that fixed winner.
    if data_segment == "holdout":
        failures.append(
            "Candidate was selected on the same holdout segment; it cannot also serve as independent validation"
        )
    elif data_segment != "train":
        failures.append(
            "Promotion requires train-based candidate selection; full data is not an independent evaluation segment"
        )
    if criteria["require_holdout"]:
        if validation_segment != "holdout" or not isinstance(validation_metrics, dict):
            failures.append(
                "Policy requires a separate holdout evaluation of the train-selected candidate"
            )
    if total_trades < int(criteria["min_test_trades"]):
        failures.append(f"Trades {total_trades} < minimum {criteria['min_test_trades']}")
    if profit_factor < float(criteria["min_profit_factor"]):
        failures.append(f"Profit factor {profit_factor:.4f} < minimum {criteria['min_profit_factor']}")
    if expectancy < float(criteria["min_expectancy_r"]):
        failures.append(f"Expectancy {expectancy:.4f}R < minimum {criteria['min_expectancy_r']}R")
    if drawdown_r > float(criteria["max_drawdown_r"]):
        failures.append(f"Drawdown {drawdown_r:.4f}R > maximum {criteria['max_drawdown_r']}R")
    if rank_score < float(criteria["min_rank_score"]):
        failures.append(f"Rank score {rank_score:.4f} < minimum {criteria['min_rank_score']}")

    status = "rejected" if failures else "processing"
    reason = "; ".join(failures) if failures else "Candidate passed the configured promotion gates"
    decision = {
        "decision_hash": decision_hash,
        "symbol": symbol, "market": market, "timeframe": timeframe,
        "data_segment": audit_segment, "strategy_name": strategy_name,
        "status": status, "reason": reason,
        "metrics_json": json.dumps(metrics_snapshot, sort_keys=True, allow_nan=False),
        "criteria_json": json.dumps(criteria, sort_keys=True, allow_nan=False),
    }
    try:
        claimed = record_strategy_promotion_decision(decision)
        if not claimed:
            prior = get_strategy_promotion_decision(decision_hash) or {}
            prior_status = prior.get("status")
            # A persisted "processing" claim may be left behind if the worker
            # exits between claiming the audit row and finalizing activation.
            # Do not silently label it complete or retry automatically: the
            # paper-config/Telegram side effects may have partially succeeded.
            if prior_status == "processing":
                return {
                    "status": "in_progress",
                    "strategy": strategy_name,
                    "previous_status": prior_status,
                    "reason": (
                        "A prior promotion claim is still marked processing. "
                        "Reconcile the paper config and Telegram outcome before retrying."
                    ),
                }
            return {
                "status": "already_processed",
                "strategy": strategy_name,
                "previous_status": prior_status,
                "reason": prior.get("reason", "This identical metrics/policy decision was already recorded"),
            }
    except Exception as exc:
        logger.exception("Could not claim promotion decision")
        return {"status": "error", "strategy": strategy_name,
                "reason": f"Could not persist promotion audit: {exc}"}

    if failures:
        return {"status": "rejected", "strategy": strategy_name, "reason": reason}

    try:
        from paper.engine import set_paper_config
        config_result = set_paper_config(
            symbol=symbol,
            market=market,
            timeframe=timeframe,
            strategy_name=strategy_name,
            is_active=True,
            allocated_capital=allocated_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            telegram_enabled=bool(criteria["send_telegram"]),
            report_enabled=True,
        )
        config_id = config_result.get("id") if isinstance(config_result, dict) else None
        if (
            not isinstance(config_result, dict)
            or config_result.get("status") != "success"
            or isinstance(config_id, bool)
            or not isinstance(config_id, int)
            or config_id <= 0
        ):
            final_reason = (
                "Paper config activation did not return a confirmed persisted config ID; "
                "reconcile paper_configs before retrying or sending a deployment alert"
            )
            try:
                update_strategy_promotion_decision(
                    decision_hash, status="reconciliation_required", reason=final_reason,
                    paper_config_id=config_id if isinstance(config_id, int) and not isinstance(config_id, bool) else None,
                    telegram_result="not_attempted",
                )
            except Exception as audit_exc:
                # The config helper did not confirm a persisted ID. It may still
                # have performed a partial write, so do not claim activation
                # failed conclusively or send an alert; require reconciliation.
                logger.exception("Paper config ID unconfirmed and promotion audit write failed")
                return {
                    "status": "reconciliation_required", "strategy": strategy_name,
                    "reason": (
                        f"{final_reason}; promotion audit update also failed: {audit_exc}. "
                        "Reconcile paper_configs and the promotion audit before retrying."
                    ),
                    "telegram_result": "not_attempted",
                    "criteria": criteria,
                }
            return {
                "status": "reconciliation_required", "strategy": strategy_name,
                "reason": final_reason, "telegram_result": "not_attempted",
                "criteria": criteria,
            }

        telegram_result = "disabled"
        final_status = "promoted"
        final_reason = "Passed policy gates and activated in paper trading"
        if criteria["send_telegram"]:
            try:
                from notifications.telegram_notifier import send_deployment_alert
                alert = send_deployment_alert(
                    symbol=symbol, market=market, timeframe=timeframe,
                    strategy_name=strategy_name, allocated_capital=allocated_capital,
                    risk_per_trade_pct=risk_per_trade_pct,
                    rank=1, rank_score=rank_score,
                )
                telegram_ok = isinstance(alert, dict) and alert.get("success") is True
                telegram_result = "sent" if telegram_ok else "failed"
                if not telegram_ok:
                    final_status = "promoted_alert_failed"
                    final_reason = "Paper config activated, but deployment alert delivery reported failure"
            except Exception as alert_exc:
                logger.exception("Auto-promotion Telegram alert failed")
                telegram_result = "failed"
                final_status = "promoted_alert_failed"
                final_reason = f"Paper config activated; Telegram alert failed: {alert_exc}"

        try:
            audit_updated = update_strategy_promotion_decision(
                decision_hash, status=final_status, reason=final_reason,
                paper_config_id=config_result.get("id"), telegram_result=telegram_result,
            )
            if audit_updated is False:
                raise RuntimeError("promotion audit row was not updated")
        except Exception as audit_exc:
            # Activation and any alert delivery have already happened. Do not
            # misreport that side effect as failed or trigger an unsafe retry.
            logger.exception("Paper config activated but promotion audit finalization failed")
            return {
                "status": "promoted_audit_pending", "strategy": strategy_name,
                "reason": (
                    "Paper config is active and Telegram outcome is "
                    f"{telegram_result}; final promotion audit write failed: {audit_exc}. "
                    "Reconcile the promotion audit and paper_configs before retrying."
                ),
                "telegram_result": telegram_result,
                "criteria": criteria,
            }
        return {
            "status": final_status, "strategy": strategy_name,
            "reason": final_reason, "telegram_result": telegram_result,
            "criteria": criteria,
        }
    except Exception as exc:
        # The activation helper may raise after a write has started (for
        # example, if the connection drops before commit acknowledgement).
        # A generic failure status could invite an unsafe retry, so treat the
        # side-effect outcome as uncertain and require explicit reconciliation.
        logger.exception("Auto-promotion activation outcome is uncertain")
        final_reason = (
            "Paper config activation raised an exception and its persisted state "
            f"could not be confirmed: {exc}. Reconcile paper_configs and the "
            "promotion audit before retrying; Telegram outcome is not confirmed."
        )
        try:
            update_strategy_promotion_decision(
                decision_hash, status="reconciliation_required",
                reason=final_reason, telegram_result="unknown",
            )
        except Exception:
            logger.exception("Could not update uncertain promotion audit")
        return {
            "status": "reconciliation_required", "strategy": strategy_name,
            "reason": final_reason, "telegram_result": "unknown",
        }


@router.get("/strategies")
def list_strategies():
    results = []
    seen = set()
    for name in STRATEGIES.keys():
        if name in seen:
            continue
        seen.add(name)
        is_inverse = (
            name.startswith("inverse_")
            or name.endswith("_INV")
            or name.endswith("_INV_inverse")
        )
        if name.endswith("_INV_inverse"):
            base_name = name[:-12]
        elif name.endswith("_INV"):
            base_name = name[:-4]
        elif name.startswith("inverse_"):
            base_name = name[8:]
        else:
            base_name = name

        meta = STRATEGY_METADATA.get(name) or STRATEGY_METADATA.get(base_name, {})
        display_name = meta.get("displayName") or (("Inverse: " if is_inverse else "") + meta.get("displayName", name))
        category = meta.get("category", "Other")
        desc = meta.get("description") or (("Opposite direction counterpart of " + base_name) if is_inverse else "")
        results.append({
            "id": name,
            "baseName": base_name,
            "isInverse": is_inverse,
            "displayName": display_name,
            "category": category,
            "validated": meta.get("validated", False),
            "description": desc,
            "params": meta.get("params", []),
            "sharedParams": SHARED_EXECUTION_PARAMS,
        })
    return {"strategies": results}


@router.post("/run")
def execute_backtest(req: BacktestRunRequest):
    strategy_name = req.strategy
    if strategy_name not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy_name}")

    strategy_fn = STRATEGIES[strategy_name]
    include_funding = strategy_name in FUNDING_STRATEGIES

    df = fetch_ohlcv_with_features_df(req.symbol, req.market, req.timeframe, closed_only=True, include_funding=include_funding)
    if len(df) < 50:
        raise HTTPException(status_code=400, detail=f"Insufficient candles for {req.symbol} {req.market} {req.timeframe} (found {len(df)}, need >= 50). Please run backfill and feature computation first.")

    # Apply data segment split
    data_segment = req.data_segment.lower()
    if data_segment in ("train", "holdout"):
        cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION)
        train_df, holdout_df = split_by_cutoff(df, cutoff)
        segment_df = train_df if data_segment == "train" else holdout_df
    else:
        segment_df = df
        data_segment = "full"

    if len(segment_df) < 30:
        raise HTTPException(status_code=400, detail=f"Segment {data_segment} has only {len(segment_df)} candles; cannot simulate.")

    # Merge user parameters on top of BASE_PARAMS
    run_params = dict(BASE_PARAMS)
    run_params.update(req.params)
    run_params["symbol"] = req.symbol
    run_params["market"] = req.market
    run_params["timeframe"] = req.timeframe

    # Higher timeframe handling for trend_alignment
    if strategy_name in TREND_ALIGNMENT_STRATEGIES and req.timeframe != HTF_TIMEFRAME:
        htf_full = fetch_ohlcv_with_features_df(req.symbol, req.market, HTF_TIMEFRAME, closed_only=True)
        if len(htf_full) > 0:
            if data_segment in ("train", "holdout"):
                htf_train, htf_holdout = split_by_cutoff(htf_full, cutoff)
                run_params["htf_df"] = htf_train if data_segment == "train" else htf_holdout
            else:
                run_params["htf_df"] = htf_full

    # Pairs handling for pairs_ratio
    if strategy_name in PAIRS_STRATEGIES:
        if req.symbol == PAIRS_BASE_SYMBOL:
            raise HTTPException(
                status_code=400,
                detail="Invalid pair comparison: target symbol cannot be the same as comparator (BTCUSDT). Choose an altcoin pair.",
            )
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, req.market, req.timeframe, closed_only=True)
        if pair_full is None or len(pair_full) == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Comparator data unavailable: no candle data found for {PAIRS_BASE_SYMBOL} {req.market} {req.timeframe}.",
            )
        if data_segment in ("train", "holdout"):
            p_train, p_holdout = split_by_cutoff(pair_full, cutoff)
            run_params["pair_df"] = p_train if data_segment == "train" else p_holdout
        else:
            run_params["pair_df"] = pair_full
        run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

    # Run the simulation
    trades = strategy_fn(segment_df, run_params)
    metrics = compute_metrics(trades)
    # All-win runs have an unbounded profit factor. SQL DECIMAL and JSON
    # represent an unavailable finite ratio as null.
    if metrics["profit_factor"] is not None and not math.isfinite(metrics["profit_factor"]):
        metrics["profit_factor"] = None
    equity_res = simulate_equity_curve(trades, req.initial_capital, req.risk_per_trade_pct)

    # Save to database if requested
    run_id = None
    if req.save_to_db:
        try:
            # Storable params only (exclude DataFrames)
            clean_params = {k: v for k, v in run_params.items() if not k.endswith("_df")}
            run_id = insert_backtest_run(
                req.symbol, req.market, req.timeframe, strategy_name, clean_params,
                int(segment_df.iloc[0]["open_time"]), int(segment_df.iloc[-1]["open_time"]),
                metrics["total_trades"], metrics["win_rate_pct"], metrics["expectancy_r"],
                metrics["profit_factor"], metrics["max_drawdown_r"],
                data_segment=data_segment,
            )
            insert_backtest_trades(run_id, trades)
        except Exception as db_err:
            logger.error("Failed to save backtest to DB (%s)", type(db_err).__name__)

    # Format trades with human-readable timestamps
    formatted_trades = []
    for t in trades:
        entry_ms = t.get("entry_time")
        exit_ms = t.get("exit_time")
        entry_dt = datetime.fromtimestamp((entry_ms // 1000) + 25200, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if entry_ms else ""
        exit_dt = datetime.fromtimestamp((exit_ms // 1000) + 25200, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if exit_ms else ""
        formatted_trades.append({
            "direction": t.get("direction"),
            "signalTime": t.get("signal_open_time"),
            "entryTime": entry_ms,
            "entryTimeWib": entry_dt,
            "entryPrice": round(float(t.get("entry_price", 0)), 4),
            "exitTime": exit_ms,
            "exitTimeWib": exit_dt,
            "exitPrice": round(float(t.get("exit_price", 0)), 4),
            "exitReason": t.get("exit_reason"),
            "stopLoss": round(float(t.get("stop_loss", 0)), 4),
            "takeProfit": round(float(t.get("take_profit", 0)), 4),
            "netReturnPct": round(float(t.get("net_return_pct", 0)), 3),
            "rMultiple": round(float(t.get("r_multiple", 0)), 4) if t.get("r_multiple") is not None else None,
            "holdingBars": t.get("holding_bars", 0),
        })

    # Prepare candle slice (last 500 candles for chart plotting)
    plot_slice = segment_df.iloc[-500:] if len(segment_df) > 500 else segment_df
    candle_records = []
    for _, row in plot_slice.iterrows():
        candle_records.append({
            "time": int(row["open_time"] // 1000),  # seconds for TradingView chart
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })

    return {
        "runId": run_id,
        "symbol": req.symbol,
        "market": req.market,
        "timeframe": req.timeframe,
        "strategy": strategy_name,
        "dataSegment": data_segment,
        "totalCandles": len(segment_df),
        "metrics": metrics,
        "equity": equity_res,
        "trades": formatted_trades,
        "candles": candle_records,
    }


# ---------------------------------------------------------------------------
# NEW: Run ALL strategies and return top results ranked by profitability + AI Insight
# ---------------------------------------------------------------------------
MIN_TRADES_FOR_RANK = 10  # minimum total_trades for a strategy to be ranked


class WalkForwardBacktestRequest(BaseModel):
    symbol: str = Field("BTCUSDT", max_length=20)
    market: str = Field("spot", max_length=7)
    timeframe: str = Field("1h", max_length=10)
    strategy: str = Field("trend_ema_v1", max_length=50)
    rolling: bool = True
    folds: int = 4
    train_window_bars: int = 500
    test_window_bars: int = 150
    step_bars: int = 100
    params: dict = {}


@router.post("/walk-forward")
def execute_walk_forward_backtest(req: WalkForwardBacktestRequest):
    """Run rolling or fixed-fold walk-forward analysis with Walk-Forward Efficiency (WFE)."""
    if req.strategy in PAIRS_STRATEGIES and req.symbol == PAIRS_BASE_SYMBOL:
        raise HTTPException(
            status_code=400,
            detail="Invalid pair comparison: target symbol cannot be the same as comparator (BTCUSDT). Choose an altcoin pair.",
        )
    from backtest.walk_forward import run_rolling_walk_forward, run_walk_forward

    if req.rolling:
        try:
            return run_rolling_walk_forward(
                symbol=req.symbol,
                market=req.market,
                timeframe=req.timeframe,
                strategy_name=req.strategy,
                train_window_bars=req.train_window_bars,
                test_window_bars=req.test_window_bars,
                step_bars=req.step_bars,
                overrides=req.params,
            )
        except Exception as exc:
            logger.exception("Rolling walk-forward failed")
            raise HTTPException(status_code=400, detail="Rolling walk-forward analysis failed; check server logs for diagnostics")
    else:
        res = run_walk_forward(
            symbol=req.symbol,
            market=req.market,
            timeframe=req.timeframe,
            strategy_name=req.strategy,
            n_folds=req.folds,
            overrides=req.params,
        )
        if not res:
            raise HTTPException(status_code=400, detail="Walk-forward analysis produced no folds or failed")
        return res


class RunAllStrategiesRequest(BaseModel):
    symbol: str = Field("BTCUSDT", max_length=20)
    market: str = Field("spot", max_length=7)
    timeframe: str = Field("1d", max_length=10)
    data_segment: str = Field("full", max_length=10)
    initial_capital: float = 5000.0
    risk_per_trade_pct: float = 1.0


@router.post("/run-all")
def run_all_strategies(
    req: Optional[RunAllStrategiesRequest] = None,
    symbol: Optional[str] = Query(None, max_length=20),
    market: Optional[str] = Query(None, max_length=7),
    timeframe: Optional[str] = Query(None, max_length=10),
    data_segment: Optional[str] = Query(None, max_length=10),
    initial_capital: Optional[float] = None,
    risk_per_trade_pct: Optional[float] = None,
):
    """Run ALL available strategies, rank them strictly by profitability,
    and generate institutional AI Insights and Telegram deployment parameters.
    """
    from backtest.params import BASE_PARAMS
    from backtest.metrics import compute_metrics
    from backtest.equity import simulate_equity_curve
    from backtest.holdout import compute_holdout_cutoff, split_by_cutoff
    from ai.insight_engine import generate_ai_insight

    # Resolve parameters safely from body, query params, or defaults
    symbol = (req.symbol if req else None) or symbol or "BTCUSDT"
    market = (req.market if req else None) or market or "spot"
    timeframe = (req.timeframe if req else None) or timeframe or "1d"
    data_segment = (req.data_segment if req else None) or data_segment or "full"
    initial_capital = float(req.initial_capital if req else (initial_capital if initial_capital is not None else 5000.0))
    risk_per_trade_pct = float(req.risk_per_trade_pct if req else (risk_per_trade_pct if risk_per_trade_pct is not None else 1.0))

    # 1. Fetch base OHLCV + features once (without funding) and with funding
    base_df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=False)
    if len(base_df) < 50:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient candles for {symbol} {market} {timeframe} (found {len(base_df)}, need >= 50). Please run backfill first."
        )

    funding_df = None  # lazy loaded only if needed

    # Segment slicing
    data_segment = data_segment.lower()
    if data_segment in ("train", "holdout"):
        cutoff = compute_holdout_cutoff(base_df, HOLDOUT_FRACTION)
        train_df, holdout_df = split_by_cutoff(base_df, cutoff)
        segment_df = train_df if data_segment == "train" else holdout_df
    else:
        segment_df = base_df
        data_segment = "full"
        cutoff = None

    if len(segment_df) < 30:
        raise HTTPException(status_code=400, detail=f"Segment {data_segment} has only {len(segment_df)} candles; cannot simulate.")

    # Higher timeframe for trend_alignment
    htf_full = None
    htf_segment = None
    if timeframe != HTF_TIMEFRAME:
        htf_full = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True)
        if len(htf_full) > 0:
            if data_segment in ("train", "holdout") and cutoff is not None:
                htf_tr, htf_ho = split_by_cutoff(htf_full, cutoff)
                htf_segment = htf_tr if data_segment == "train" else htf_ho
            else:
                htf_segment = htf_full

    # Pair dataframe for pairs_ratio
    pair_full = None
    pair_segment = None
    if symbol != PAIRS_BASE_SYMBOL:
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True)
        if len(pair_full) > 0:
            if data_segment in ("train", "holdout") and cutoff is not None:
                p_tr, p_ho = split_by_cutoff(pair_full, cutoff)
                pair_segment = p_tr if data_segment == "train" else p_ho
            else:
                pair_segment = pair_full

    results = []

    for strategy_name, strategy_fn in STRATEGIES.items():
        try:
            run_params = dict(BASE_PARAMS)
            run_params.update({
                "symbol": symbol,
                "market": market,
                "timeframe": timeframe,
                "data_segment": data_segment,
                "initial_capital": initial_capital,
                "risk_per_trade_pct": risk_per_trade_pct,
            })

            # Pass HTF / Pair if required
            if strategy_name in TREND_ALIGNMENT_STRATEGIES and htf_segment is not None:
                run_params["htf_df"] = htf_segment

            if strategy_name in PAIRS_STRATEGIES and pair_segment is not None:
                run_params["pair_df"] = pair_segment
                run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

            # Determine input dataframe (standard vs funding)
            if strategy_name in FUNDING_STRATEGIES:
                if funding_df is None:
                    funding_full = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=True)
                    if data_segment in ("train", "holdout") and cutoff is not None:
                        f_tr, f_ho = split_by_cutoff(funding_full, cutoff)
                        funding_df = f_tr if data_segment == "train" else f_ho
                    else:
                        funding_df = funding_full
                strat_input_df = funding_df
            else:
                strat_input_df = segment_df

            trades = strategy_fn(strat_input_df, run_params)
            metrics = compute_metrics(trades)
            equity_res = simulate_equity_curve(trades, initial_capital, risk_per_trade_pct)

            results.append({
                "strategy": strategy_name,
                "displayName": STRATEGY_METADATA.get(strategy_name, {}).get("displayName", strategy_name),
                "category": STRATEGY_METADATA.get(strategy_name, {}).get("category", "Other"),
                "metrics": metrics,
                "equity": equity_res,
                "totalCandles": len(strat_input_df),
                "error": None,
            })
        except Exception as e:
            logger.warning("Strategy %s simulation failed (%s)", strategy_name, type(e).__name__)
            results.append({
                "strategy": strategy_name,
                "displayName": STRATEGY_METADATA.get(strategy_name, {}).get("displayName", strategy_name),
                "category": STRATEGY_METADATA.get(strategy_name, {}).get("category", "Other"),
                "error": "Strategy simulation failed",
                "totalCandles": len(segment_df),
                "metrics": {"total_trades": 0, "expectancy_r": None, "profit_factor": None, "win_rate_pct": None, "max_drawdown_r": None},
                "equity": None,
            })

    # ------------------------------------------------------------------
    # Ranking Logic: Strict Profitability & Statistical Edge
    # ------------------------------------------------------------------
    all_with_info = []
    eligible_for_top = []

    for r in results:
        if r.get("error"):
            all_with_info.append({
                "strategy": r["strategy"],
                "displayName": r["displayName"],
                "category": r["category"],
                "rankScore": 0.0,
                "isProfitable": False,
                "rankNote": "Strategy simulation failed",
                "metrics": r["metrics"],
                "equity": None,
                "totalCandles": r.get("totalCandles", 0),
            })
            continue

        metrics = r["metrics"]
        total_trades = metrics.get("total_trades", 0)
        ev = metrics.get("expectancy_r")
        pf = metrics.get("profit_factor")
        wr = metrics.get("win_rate_pct") or 0.0
        max_dd = abs(metrics.get("max_drawdown_r") or 0.0)

        # Handle nulls
        ev_val = float(ev) if ev is not None else -999.0
        pf_val = float(pf) if pf is not None else 0.0

        # Profitability check: Must have positive expectancy and profit factor >= 1.0
        is_profitable = ev_val > 0.0 and pf_val >= 1.0

        if total_trades < MIN_TRADES_FOR_RANK:
            note = f"⚠️ Only {total_trades} trades (minimum {MIN_TRADES_FOR_RANK} required for ranking)"
            rank_score = 0.0
        elif not is_profitable:
            note = f"❌ Unprofitable (Exp: {ev_val:+.2f}R, PF: {pf_val:.2f})"
            rank_score = 0.0
        else:
            note = None
            # Composite rank score for profitable strategies
            ev_norm = min(max(ev_val, 0.0) / 1.0, 1.0)
            pf_norm = min(max(pf_val - 1.0, 0.0) / 2.0, 1.0)
            wr_norm = min(wr / 100.0, 1.0)
            tc_norm = min(total_trades / 50.0, 1.0)
            dd_penalty = max(0.0, (max_dd - 3.0) * 0.04)

            rank_score = round(max(0.01, (ev_norm * 0.45) + (pf_norm * 0.30) + (wr_norm * 0.15) + (tc_norm * 0.10) - dd_penalty), 4)

        entry = {
            "strategy": r["strategy"],
            "displayName": r["displayName"],
            "category": r["category"],
            "rankScore": rank_score,
            "isProfitable": is_profitable,
            "rankNote": note,
            "metrics": metrics,
            "equity": r["equity"],
            "totalCandles": r.get("totalCandles", 0),
        }

        all_with_info.append(entry)
        if is_profitable and total_trades >= MIN_TRADES_FOR_RANK:
            eligible_for_top.append(entry)

    # Sort eligible top strategies by rankScore desc, then expectancy desc
    eligible_for_top.sort(
        key=lambda x: (x["rankScore"], float(x["metrics"].get("expectancy_r") or 0.0), x["metrics"].get("total_trades", 0)),
        reverse=True
    )

    # Sort allResults: profitable first by rankScore, then unprofitable
    all_with_info.sort(
        key=lambda x: (1 if x["isProfitable"] else 0, x["rankScore"], float(x["metrics"].get("expectancy_r") or -999)),
        reverse=True
    )

    top3 = eligible_for_top[:3]

    # Promotion is opt-in. When candidates are ranked on train, evaluate the
    # selected winner on the separate chronological holdout before applying
    # profitability gates. Never promote a winner selected on holdout/full.
    promotion_validation_metrics = None
    promotion_validation_segment = None
    if top3 and data_segment == "train" and cutoff is not None:
        winner_name = top3[0]["strategy"]
        try:
            validation_df = holdout_df
            validation_params = dict(BASE_PARAMS)
            validation_params.update({
                "symbol": symbol,
                "market": market,
                "timeframe": timeframe,
                "data_segment": "holdout",
                "initial_capital": initial_capital,
                "risk_per_trade_pct": risk_per_trade_pct,
            })
            if winner_name in TREND_ALIGNMENT_STRATEGIES and htf_full is not None and len(htf_full) > 0:
                _, validation_params["htf_df"] = split_by_cutoff(htf_full, cutoff)
            if winner_name in PAIRS_STRATEGIES and pair_full is not None and len(pair_full) > 0:
                validation_params["pair_df"] = split_by_cutoff(pair_full, cutoff)[1]
                validation_params["pair_symbol"] = PAIRS_BASE_SYMBOL
            if winner_name in FUNDING_STRATEGIES:
                funding_full = fetch_ohlcv_with_features_df(
                    symbol, market, timeframe, closed_only=True, include_funding=True
                )
                _, validation_df = split_by_cutoff(funding_full, cutoff)
            validation_trades = STRATEGIES[winner_name](validation_df, validation_params)
            promotion_validation_metrics = compute_metrics(validation_trades)
            promotion_validation_segment = "holdout"
        except Exception as validation_error:
            logger.warning(
                "Independent holdout evaluation failed for promotion candidate %s: %s",
                winner_name, validation_error,
            )

    # The helper fails closed if the independent validation result is unavailable.
    auto_promotion = _auto_promote_top_candidate(
        top3[0] if top3 else None,
        symbol=symbol, market=market, timeframe=timeframe,
        data_segment=data_segment, allocated_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        validation_metrics=promotion_validation_metrics,
        validation_segment=promotion_validation_segment,
    )

    # Generate Institutional AI Insight & Recommendations
    ai_insight = generate_ai_insight(
        symbol=symbol,
        market=market,
        timeframe=timeframe,
        top_strategies=top3,
        all_results=all_with_info,
        df=segment_df
    )

    return {
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "dataSegment": data_segment,
        "initialCapital": initial_capital,
        "riskPerTradePct": risk_per_trade_pct,
        "minTradesForRank": MIN_TRADES_FOR_RANK,
        "totalEvaluated": len(results),
        "profitableCount": len(eligible_for_top),
        "top3": top3,
        "autoPromotion": auto_promotion,
        "allResults": all_with_info,
        "aiInsight": ai_insight,
    }


@router.post("/ai-insight")
def get_standalone_ai_insight(
    symbol: str = Query("BTCUSDT", max_length=20),
    market: str = Query("spot", max_length=7),
    timeframe: str = Query("1d", max_length=10),
    strategy: str = Query("confluence_ensemble_v1", max_length=50),
):
    """Provides deep AI quantitative diagnosis for a specific strategy."""
    from ai.insight_engine import generate_ai_insight

    df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True)
    if len(df) < 50:
        raise HTTPException(status_code=400, detail="Insufficient candles for AI insight")

    # Run quick simulation
    strategy_fn = STRATEGIES.get(strategy)
    if not strategy_fn:
        raise HTTPException(status_code=400, detail="Unknown strategy")

    from backtest.params import BASE_PARAMS
    from backtest.metrics import compute_metrics

    params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)
    trades = strategy_fn(df, params)
    metrics = compute_metrics(trades)

    top_dummy = [{
        "strategy": strategy,
        "displayName": STRATEGY_METADATA.get(strategy, {}).get("displayName", strategy),
        "category": STRATEGY_METADATA.get(strategy, {}).get("category", "Quantitative"),
        "metrics": metrics,
        "rankScore": 0.5 if (metrics.get("expectancy_r") or 0) > 0 else 0.0,
    }]

    insight = generate_ai_insight(symbol, market, timeframe, top_dummy, top_dummy, df=df)
    return {"symbol": symbol, "strategy": strategy, "metrics": metrics, "insight": insight}



@router.get("/history")
def get_backtest_history(
    symbol: Optional[str] = Query(None, max_length=20),
    timeframe: Optional[str] = Query(None, max_length=10),
    strategy: Optional[str] = Query(None, max_length=50),
    limit: int = Query(30, ge=1, le=100),
):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        conditions = []
        params = []
        if symbol:
            conditions.append("symbol = %s")
            params.append(symbol)
        if timeframe:
            conditions.append("timeframe = %s")
            params.append(timeframe)
        if strategy:
            conditions.append("strategy_name = %s")
            params.append(strategy)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""
            SELECT 
                id, symbol, market, timeframe, strategy_name, data_segment,
                total_trades, win_rate_pct, expectancy_r, profit_factor, max_drawdown_r,
                created_at
            FROM backtest_runs
            {where_clause}
            ORDER BY id DESC
            LIMIT %s
        """
        params.append(limit)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        cur.close()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "symbol": r["symbol"],
                "market": r["market"],
                "timeframe": r["timeframe"],
                "strategy": r["strategy_name"],
                "dataSegment": r["data_segment"],
                "totalTrades": r["total_trades"],
                "winRatePct": float(r["win_rate_pct"]) if r["win_rate_pct"] is not None else None,
                "expectancyR": float(r["expectancy_r"]) if r["expectancy_r"] is not None else None,
                "profitFactor": float(r["profit_factor"]) if r["profit_factor"] is not None else None,
                "maxDrawdownR": float(r["max_drawdown_r"]) if r["max_drawdown_r"] is not None else None,
                "createdAt": str(r["created_at"]),
            })
        return {"runs": results}
    finally:
        conn.close()


@router.get("/runs/{run_id}")
def get_run_details(run_id: int):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM backtest_runs WHERE id = %s", (run_id,))
        run_row = cur.fetchone()
        if not run_row:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        cur.execute("""
            SELECT 
                direction, entry_time, entry_time_wib, entry_price,
                exit_time, exit_time_wib, exit_price, exit_reason,
                stop_loss, take_profit, net_return_pct, r_multiple, holding_bars
            FROM backtest_trades
            WHERE run_id = %s
            ORDER BY id ASC
        """, (run_id,))
        trade_rows = cur.fetchall()
        cur.close()

        trades = []
        for t in trade_rows:
            trades.append({
                "direction": t["direction"],
                "entryTime": t["entry_time"],
                "entryTimeWib": str(t["entry_time_wib"]),
                "entryPrice": float(t["entry_price"]),
                "exitTime": t["exit_time"],
                "exitTimeWib": str(t["exit_time_wib"]),
                "exitPrice": float(t["exit_price"]),
                "exitReason": t["exit_reason"],
                "stopLoss": float(t["stop_loss"]),
                "takeProfit": float(t["take_profit"]),
                "netReturnPct": round(float(t["net_return_pct"]), 3),
                "rMultiple": float(t["r_multiple"]) if t["r_multiple"] is not None else None,
                "holdingBars": t["holding_bars"],
            })

        return {
            "run": {
                "id": run_row["id"],
                "symbol": run_row["symbol"],
                "market": run_row["market"],
                "timeframe": run_row["timeframe"],
                "strategy": run_row["strategy_name"],
                "dataSegment": run_row["data_segment"],
                "params": json.loads(run_row["params_json"]) if isinstance(run_row["params_json"], str) else run_row["params_json"],
                "totalTrades": run_row["total_trades"],
                "winRatePct": float(run_row["win_rate_pct"]) if run_row["win_rate_pct"] is not None else None,
                "expectancyR": float(run_row["expectancy_r"]) if run_row["expectancy_r"] is not None else None,
                "profitFactor": float(run_row["profit_factor"]) if run_row["profit_factor"] is not None else None,
                "maxDrawdownR": float(run_row["max_drawdown_r"]) if run_row["max_drawdown_r"] is not None else None,
                "createdAt": str(run_row["created_at"]),
            },
            "trades": trades,
        }
    finally:
        conn.close()
