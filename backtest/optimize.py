"""
Grid-search parameter optimizer. Evaluates every combination of a
strategy's parameter grid against TRAIN data ONLY (holdout stays
completely untouched -- same discipline as run_backtest.py/walk_forward.py).
Ranks results by expectancy, with a minimum trade-count filter so a lucky
handful of trades can't "win" over a larger, more meaningful sample.

This does NOT replace the holdout check. After finding a promising
parameter set here, validate it (once) with run_holdout_check.py before
trusting it -- optimizing on train and then declaring victory without ever
checking holdout is exactly the overfitting trap this whole pipeline is
built to avoid.

Usage:
    python3 -m backtest.optimize --symbol BNBUSDT --timeframe 1d --strategy trend_ema_v1
    python3 -m backtest.optimize --symbol BNBUSDT --timeframe 1d --strategy smc_liquidity_sweep_v1 --min-trades 8 --top-n 5
"""
import argparse
import itertools
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HOLDOUT_FRACTION, PAIRS_BASE_SYMBOL
from db.db import fetch_ohlcv_with_features_df
from backtest.strategies import STRATEGIES, FUNDING_STRATEGIES
from backtest.strategies import TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.params import BASE_PARAMS
from backtest.metrics import compute_metrics
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff

# Sensible default grids per strategy, keyed by strategy name -- each
# strategy's meaningful parameters differ. Trailing-stop variations are
# included for the strategies that showed at least some train-data promise,
# since a fixed 2:1/3:1 target can both cap winners early and get clipped
# by a brief spike that later reverses.
DEFAULT_GRIDS = {
    "confluence_reversal_v1": {
        "RSI_OVERSOLD": [25, 30, 35],
        "RSI_OVERBOUGHT": [65, 70, 75],
        "SR_PROXIMITY_PCT": [0.25, 0.5, 1.0],
        "VOLUME_MULTIPLIER": [1.0, 1.2, 1.5],
        "RISK_REWARD_RATIO": [1.5, 2, 2.5],
    },
    "pairs_ratio_v1": {
        "PAIRS_ZSCORE_LOOKBACK_BARS": [30, 50, 100],
        "PAIRS_ZSCORE_ENTRY_THRESHOLD": [1.5, 2.0, 2.5],
        "PAIRS_RISK_REWARD_RATIO": [2, 2.5, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "confluence_ensemble_v1": {
        "MIN_CONFLUENCE_SCORE": [2.0, 3.0, 4.0],
        "CONFLUENCE_RISK_REWARD_RATIO": [2, 2.5, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "funding_extreme_reversal_v1": {
        "FUNDING_EXTREME_POSITIVE_PCT": [0.03, 0.05, 0.08],
        "FUNDING_EXTREME_NEGATIVE_PCT": [-0.03, -0.05, -0.08],
        "FUNDING_RISK_REWARD_RATIO": [2, 2.5, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "trend_ema_v1": {
        "TREND_EMA_FAST": [10, 20, 30],
        "TREND_EMA_SLOW": [40, 50, 100],
        "TREND_RISK_REWARD_RATIO": [2, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "smc_liquidity_sweep_v1": {
        "SMC_WICK_MIN_ATR_MULT": [0.2, 0.3, 0.5],
        "SMC_RISK_REWARD_RATIO": [2, 2.5, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "supply_demand_v1": {
        "SD_BASE_MAX_ATR_MULT": [0.2, 0.3, 0.5],
        "SD_DISPLACEMENT_ATR_MULT": [0.8, 1.0, 1.5],
        "SD_RISK_REWARD_RATIO": [2, 2.5, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "breakout_continuation_v1": {
        "BREAKOUT_LOOKBACK_BARS": [50, 100, 150],
        "BREAKOUT_MIN_MARGIN_PCT": [0.1, 0.15, 0.25],
        "BREAKOUT_RISK_REWARD_RATIO": [2, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "double_pattern_v1": {
        "PIVOT_LOOKBACK_BARS": [3, 5, 8],
        "DOUBLE_PATTERN_TOLERANCE_PCT": [0.5, 1.0, 1.5],
        "DOUBLE_PATTERN_RISK_REWARD_RATIO": [2, 2.5, 3],
        "USE_TRAILING_STOP": [False, True],
        "TRAIL_ACTIVATION_R": [1.0],
        "TRAIL_DISTANCE_ATR_MULT": [1.0, 1.5, 2.0],
    },
    "fibonacci_retracement_v1": {"FIB_RISK_REWARD_RATIO": [2, 2.5, 3]},
    "trend_alignment_v1": {
        "TREND_EMA_FAST": [10, 20, 30], "TREND_EMA_SLOW": [50, 100, 150],
        "TREND_RISK_REWARD_RATIO": [2, 3],
    },
    "volatility_breakout_v1": {
        "VOL_BREAKOUT_LOOKBACK_BARS": [50, 100, 150],
        "VOL_BREAKOUT_ATR_BASELINE_BARS": [30, 50, 75],
        "VOL_BREAKOUT_ATR_EXPANSION_MULT": [1.0, 1.1, 1.3],
        "VOL_BREAKOUT_MIN_MARGIN_PCT": [0.1, 0.15, 0.25],
        "VOL_BREAKOUT_EMA_PERIOD": [50, 100, 200],
        "VOL_BREAKOUT_RISK_REWARD_RATIO": [2, 3],
    },
}


def run_optimize(symbol, market, timeframe, strategy_name, min_trades=10, top_n=10):
    if strategy_name not in STRATEGIES:
        print(f"Unknown strategy '{strategy_name}'. Available: {list(STRATEGIES.keys())}")
        return
    base_strategy_name = strategy_name[8:] if strategy_name.startswith("inverse_") else strategy_name
    grid = DEFAULT_GRIDS.get(strategy_name) or DEFAULT_GRIDS.get(base_strategy_name)
    if not grid:
        print(f"No default parameter grid defined for '{strategy_name}' -- "
              f"add one to DEFAULT_GRIDS in backtest/optimize.py")
        return

    strategy_fn = STRATEGIES[strategy_name]
    df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True,
                                       include_funding=strategy_name in FUNDING_STRATEGIES)
    if len(df) < 100:
        print(f"{symbol} {market} {timeframe}: only {len(df)} candles, too few to optimize on")
        return

    cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION)
    train_df, _holdout_df = split_by_cutoff(df, cutoff)  # holdout never touched here

    htf_train = None
    if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
        htf_full = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True)
        htf_train, _ = split_by_cutoff(htf_full, cutoff)

    pair_train = None
    if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True)
        pair_train, _ = split_by_cutoff(pair_full, cutoff)

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print(f"Testing {len(combos)} parameter combinations for {strategy_name} on "
          f"{symbol} {market} {timeframe} (TRAIN data only, holdout untouched)...")

    results = []
    seen_effective_combos = set()
    skipped_redundant = 0
    for combo in combos:
        overrides = dict(zip(keys, combo))

        # When USE_TRAILING_STOP is False, TRAIL_ACTIVATION_R / TRAIL_DISTANCE_ATR_MULT
        # have zero effect on the outcome -- skip re-testing combos that are
        # identical once normalized, so results aren't cluttered with
        # duplicates and we don't waste compute re-running the same thing.
        normalized = dict(overrides)
        if not normalized.get("USE_TRAILING_STOP", False):
            normalized.pop("TRAIL_ACTIVATION_R", None)
            normalized.pop("TRAIL_DISTANCE_ATR_MULT", None)
        norm_key = tuple(sorted(normalized.items()))
        if norm_key in seen_effective_combos:
            skipped_redundant += 1
            continue
        seen_effective_combos.add(norm_key)

        call_params = dict(BASE_PARAMS)
        call_params.update(overrides)
        call_params["symbol"] = symbol
        call_params["market"] = market
        call_params["timeframe"] = timeframe
        if htf_train is not None:
            call_params["htf_df"] = htf_train
        if pair_train is not None:
            call_params["pair_df"] = pair_train
            call_params["pair_symbol"] = PAIRS_BASE_SYMBOL

        try:
            trades = strategy_fn(train_df, call_params)
        except Exception:
            continue
        m = compute_metrics(trades)
        if m["total_trades"] >= min_trades and m["expectancy_r"] is not None:
            results.append((overrides, m))

    if skipped_redundant:
        print(f"(skipped {skipped_redundant} redundant combinations where trailing-stop "
              f"sub-parameters had no effect)")

    results.sort(key=lambda r: r[1]["expectancy_r"], reverse=True)

    print(f"\nTop {min(top_n, len(results))} of {len(results)} combinations meeting "
          f"the {min_trades}-trade minimum (ranked by expectancy):\n")
    for overrides, m in results[:top_n]:
        print(f"  {overrides}")
        print(f"    trades={m['total_trades']}  win_rate={m['win_rate_pct']}%  "
              f"expectancy={m['expectancy_r']}R  PF={m['profit_factor']}  max_dd={m['max_drawdown_r']}R\n")

    if not results:
        print(f"  No combination produced >= {min_trades} trades with valid metrics. "
              f"Try lowering --min-trades or widening the grid.")
    else:
        example_overrides = " ".join(f"--override {k}={v}" for k, v in results[0][0].items())
        print("Reminder: these are TRAIN results. Before trusting the top combination, holdout-check")
        print("it ONCE with the exact same parameter values, e.g.:")
        print(f"\n  python3 -m backtest.run_holdout_check --symbol {symbol} --timeframe {timeframe} "
              f"--strategy {strategy_name} {example_overrides} --confirm\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--market", default="spot", choices=["spot", "futures"])
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    run_optimize(args.symbol, args.market, args.timeframe, args.strategy, args.min_trades, args.top_n)


if __name__ == "__main__":
    main()
