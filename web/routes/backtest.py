import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

from db.db import (
    fetch_ohlcv_with_features_df, insert_backtest_run, insert_backtest_trades, get_pool
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
    }
}

SHARED_EXECUTION_PARAMS = [
    {"key": "BACKTEST_FEE_PCT", "label": "Fee per Side (%)", "type": "float", "default": 0.001, "factor": 100, "min": 0.0, "max": 0.5, "step": 0.01},
    {"key": "BACKTEST_SLIPPAGE_PCT", "label": "Slippage per Side (%)", "type": "float", "default": 0.0005, "factor": 100, "min": 0.0, "max": 0.2, "step": 0.01},
    {"key": "BACKTEST_MAX_HOLD_BARS", "label": "Max Holding Period (Bars)", "type": "int", "default": 200, "min": 20, "max": 1000, "step": 20},
    {"key": "USE_TRAILING_STOP", "label": "Enable Trailing Stop", "type": "bool", "default": False},
    {"key": "TRAIL_ACTIVATION_R", "label": "Trail Activation (R)", "type": "float", "default": 1.0, "min": 0.5, "max": 3.0, "step": 0.25},
    {"key": "TRAIL_DISTANCE_ATR_MULT", "label": "Trail Distance (x ATR)", "type": "float", "default": 1.5, "min": 0.5, "max": 4.0, "step": 0.25},
]


class BacktestRunRequest(BaseModel):
    symbol: str = Field(..., example="BTCUSDT")
    market: str = Field("spot", example="spot")
    timeframe: str = Field("1d", example="1d")
    strategy: str = Field("confluence_ensemble_v1", example="confluence_ensemble_v1")
    data_segment: str = Field("full", example="full")  # train, holdout, full
    initial_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    save_to_db: bool = Field(True)
    params: Dict[str, Any] = Field(default_factory=dict)


@router.get("/strategies")
def list_strategies():
    results = []
    for name in STRATEGIES.keys():
        is_inverse = name.startswith("inverse_")
        base_name = name[8:] if is_inverse else name
        meta = STRATEGY_METADATA.get(base_name, {})
        display_name = ("Inverse: " if is_inverse else "") + meta.get("displayName", name)
        results.append({
            "id": name,
            "baseName": base_name,
            "isInverse": is_inverse,
            "displayName": display_name,
            "category": meta.get("category", "Other"),
            "validated": meta.get("validated", False),
            "description": ("Opposite direction counterpart of " + base_name) if is_inverse else meta.get("description", ""),
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
    if strategy_name in PAIRS_STRATEGIES and req.symbol != PAIRS_BASE_SYMBOL:
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, req.market, req.timeframe, closed_only=True)
        if len(pair_full) > 0:
            if data_segment in ("train", "holdout"):
                p_train, p_holdout = split_by_cutoff(pair_full, cutoff)
                run_params["pair_df"] = p_train if data_segment == "train" else p_holdout
            else:
                run_params["pair_df"] = pair_full
            run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

    # Run the simulation
    trades = strategy_fn(segment_df, run_params)
    metrics = compute_metrics(trades)
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
            logger.error(f"Failed to save backtest to DB: {db_err}")

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
            "netReturnPct": round(float(t.get("net_return_pct", 0)) * 100, 3),
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
# NEW: Run ALL strategies and return top results ranked by profitability
# ---------------------------------------------------------------------------
MIN_TRADES_FOR_RANK = 10  # minimum total_trades for a strategy to be ranked


@router.post("/run-all")
def run_all_strategies(
    symbol: str = Query("BTCUSDT", example="BTCUSDT"),
    market: str = Query("spot", example="spot"),
    timeframe: str = Query("1d", example="1d"),
    data_segment: str = Query("full", example="full"),  # train, holdout, full
    initial_capital: float = Query(default=5000.0, ge=100.0),
    risk_per_trade_pct: float = Query(default=1.0, ge=0.1, le=10.0),
):
    """Run ALL available strategies and return them ranked by profitability.

    Ranking order (primary to secondary):
    1. expectancy_r — average R-multiple per trade (most important per PRD)
    2. profit_factor — gross winning R / gross losing R
    3. total_trades — number of trades (must be >= MIN_TRADES_FOR_RANK)

    Strategies that don't meet the minimum trade filter are excluded from
    the ranked ``top3`` list but appear in ``allResults`` with a note.

    Returns:
        top3: top 3 strategies ranked by composite score
        allResults: all strategies that ran (ranked if they meet the trade filter,
                    otherwise listed with a warning note)
    """
    from backtest.params import BASE_PARAMS
    from backtest.metrics import compute_metrics
    from backtest.equity import simulate_equity_curve

    results = []

    for strategy_name in STRATEGIES.keys():
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

            # Determine if this strategy needs funding data
            include_funding = strategy_name in FUNDING_STRATEGIES

            # Fetch OHLCV + features
            df = fetch_ohlcv_with_features_df(
                symbol, market, timeframe, closed_only=True,
                include_funding=include_funding,
            )
            if len(df) < 50:
                results.append({
                    "strategy": strategy_name,
                    "displayName": STRATEGY_METADATA.get(strategy_name, {}).get(
                        "displayName", strategy_name
                    ),
                    "category": STRATEGY_METADATA.get(strategy_name, {}).get("category", "Other"),
                    "error": "insufficient_candles",
                    "totalCandles": 0,
                })
                continue

            # Run the strategy
            strategy_fn = STRATEGIES[strategy_name]
            trades = strategy_fn(df, run_params)
            metrics = compute_metrics(trades)
            equity_res = simulate_equity_curve(trades, initial_capital, risk_per_trade_pct)

            results.append({
                "strategy": strategy_name,
                "displayName": STRATEGY_METADATA.get(strategy_name, {}).get(
                    "displayName", strategy_name
                ),
                "category": STRATEGY_METADATA.get(strategy_name, {}).get("category", "Other"),
                "metrics": metrics,
                "equity": equity_res,
                "totalCandles": len(df),
                "error": None,
            })

        except Exception as e:
            logger.error(f"Failed to run {strategy_name}: {e}", exc_info=True)
            results.append({
                "strategy": strategy_name,
                "displayName": STRATEGY_METADATA.get(strategy_name, {}).get(
                    "displayName", strategy_name
                ),
                "category": STRATEGY_METADATA.get(strategy_name, {}).get("category", "Other"),
                "error": str(e),
                "totalCandles": 0,
            })

    # ------------------------------------------------------------------
    # Rank results: filter by minimum trades, then composite score
    # ------------------------------------------------------------------
    ranked = []
    all_with_info = []

    for r in results:
        if r.get("error"):
            all_with_info.append({
                "strategy": r["strategy"],
                "displayName": r["displayName"],
                "category": r["category"],
                "rankScore": None,
                "rankNote": f"Failed: {r['error']}",
                "metrics": {"total_trades": 0, "expectancy_r": None, "profit_factor": None, "win_rate_pct": None},
                "equity": None,
                "totalCandles": r.get("totalCandles", 0),
            })
            continue

        total_trades = r["metrics"]["total_trades"]
        if total_trades < MIN_TRADES_FOR_RANK:
            all_with_info.append({
                "strategy": r["strategy"],
                "displayName": r["displayName"],
                "category": r["category"],
                "rankScore": None,
                "rankNote": f"Only {total_trades} trades (minimum {MIN_TRADES_FOR_RANK} required for ranking)",
                "metrics": r["metrics"],
                "equity": r["equity"],
                "totalCandles": r.get("totalCandles", 0),
            })
            continue

        # Composite rank score: expectancy_R (50%) + profit_factor (30%) + win_rate (15%) + trade count (5%)
        ev = r["metrics"].get("expectancy_r") or 0
        pf = r["metrics"].get("profit_factor") or 1
        wr = r["metrics"].get("win_rate_pct") or 0
        tc = total_trades

        ev_norm = min(abs(ev) / 1.0, 1.0)    # assume ~1R typical range
        pf_norm = min(pf / 3.0, 1.0)         # assume ~3.0 typical PF
        wr_norm = min(wr / 100.0, 1.0)
        tc_norm = min(tc / 100, 1.0) if tc > 0 else 0

        rank_score = (ev_norm * 0.5) + (pf_norm * 0.3) + (wr_norm * 0.15) + (tc_norm * 0.05)

        ranked.append({
            "strategy": r["strategy"],
            "displayName": r["displayName"],
            "category": r["category"],
            "rankScore": round(rank_score, 4),
            "rankNote": None,
            "metrics": r["metrics"],
            "equity": r["equity"],
            "totalCandles": r.get("totalCandles", 0),
        })

    # Sort: rankScore desc, then total_trades desc
    ranked.sort(key=lambda x: (x["rankScore"], x.get("metrics", {}).get("total_trades", 0) or 0), reverse=True)

    top3 = ranked[:3] if ranked else []

    return {
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "dataSegment": data_segment,
        "initialCapital": initial_capital,
        "riskPerTradePct": risk_per_trade_pct,
        "minTradesForRank": MIN_TRADES_FOR_RANK,
        "count": len(ranked),
        "top3": top3,
        "allResults": ranked,  # all strategies, ranked or with notes
    }


@router.get("/history")
def get_backtest_history(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    strategy: Optional[str] = None,
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
                "netReturnPct": round(float(t["net_return_pct"]) * 100, 3),
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
