"""
Step 4: Walk-forward validation.

Splits the TRAIN portion of historical data (holdout excluded -- see
backtest/holdout.py) into N sequential, non-overlapping time folds and runs
the SAME strategy with the SAME fixed parameters against each fold
independently. Checks temporal STABILITY: a strategy that's only
profitable because of one unusual sub-period will show wildly inconsistent
per-fold results even if its full-history aggregate looked good.

This is deliberately NOT walk-forward OPTIMIZATION (no re-tuning of
parameters per fold) -- it answers a narrower question first: does this
fixed strategy/parameter combination hold up across different time periods
at all?

Usage:
    python3 -m backtest.walk_forward --symbol BTCUSDT --timeframe 1d --strategy breakout_continuation_v1
    python3 -m backtest.walk_forward --symbol BTCUSDT --timeframe 4h --strategy trend_alignment_v1 --folds 6
"""
import argparse
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HOLDOUT_FRACTION, PAIRS_BASE_SYMBOL
from db.db import fetch_ohlcv_with_features_df
from backtest.strategies import STRATEGIES, FUNDING_STRATEGIES
from backtest.params import BASE_PARAMS, parse_overrides
from backtest.strategies import TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.metrics import compute_metrics
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff



def ms_to_wib_str(ms):
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms, hours=7)
    return dt.strftime("%Y-%m-%d")


def split_into_folds(df, n_folds):
    """Sequential, non-overlapping, chronological."""
    fold_size = len(df) // n_folds
    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else len(df)
        folds.append(df.iloc[start:end].reset_index(drop=True))
    return folds


def run_walk_forward(symbol, market, timeframe, strategy_name, n_folds=4, overrides=None):
    if strategy_name not in STRATEGIES:
        print(f"Unknown strategy '{strategy_name}'. Available: {list(STRATEGIES.keys())}")
        return

    effective_params = dict(BASE_PARAMS, **(overrides or {}))
    if overrides:
        print(f"Using parameter overrides: {overrides}\n")

    strategy_fn = STRATEGIES[strategy_name]
    full_df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True,
                                            include_funding=strategy_name in FUNDING_STRATEGIES)
    if len(full_df) < 100:
        print(f"{symbol} {market} {timeframe}: only {len(full_df)} candles total, too few to proceed")
        return

    cutoff = compute_holdout_cutoff(full_df, HOLDOUT_FRACTION)
    train_df, _holdout_df = split_by_cutoff(full_df, cutoff)  # holdout never touched here

    if len(train_df) < n_folds * 50:
        print(f"{symbol} {market} {timeframe}: only {len(train_df)} TRAIN candles (holdout excluded) -- "
              f"too few to split into {n_folds} meaningful folds. Reduce --folds or gather more history.")
        return

    htf_train_full = None
    if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
        htf_full = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True)
        htf_train_full, _htf_holdout = split_by_cutoff(htf_full, cutoff)

    pair_train_full = None
    if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True)
        pair_train_full, _pair_holdout = split_by_cutoff(pair_full, cutoff)

    folds = split_into_folds(train_df, n_folds)

    print(f"\n{'='*70}")
    print(f"WALK-FORWARD: {symbol} {market} {timeframe} [{strategy_name}], {n_folds} folds "
          f"(TRAIN portion only, holdout excluded)")
    print(f"{'='*70}")

    fold_results = []
    for idx, fold_df in enumerate(folds, start=1):
        call_params = dict(effective_params, symbol=symbol, market=market, timeframe=timeframe)
        if htf_train_full is not None:
            # Give this fold only the HTF data that had already closed before
            # this fold's time range ends -- i.e. HTF data up to the fold's
            # own end, never beyond it (no lookahead into later folds either).
            fold_end_time = int(fold_df.iloc[-1]["open_time"])
            call_params["htf_df"] = htf_train_full[htf_train_full["open_time"] <= fold_end_time].reset_index(drop=True)
        if pair_train_full is not None:
            fold_end_time = int(fold_df.iloc[-1]["open_time"])
            call_params["pair_df"] = pair_train_full[pair_train_full["open_time"] <= fold_end_time].reset_index(drop=True)
            call_params["pair_symbol"] = PAIRS_BASE_SYMBOL

        trades = strategy_fn(fold_df, call_params)
        m = compute_metrics(trades)
        period = f"{ms_to_wib_str(int(fold_df.iloc[0]['open_time']))} to {ms_to_wib_str(int(fold_df.iloc[-1]['open_time']))}"
        fold_results.append(m)
        print(f"\n  Fold {idx} ({period}, {len(fold_df)} candles):")
        print(f"    trades={m['total_trades']:<5} win_rate={m['win_rate_pct']}%  "
              f"expectancy={m['expectancy_r']}R  profit_factor={m['profit_factor']}  "
              f"max_dd={m['max_drawdown_r']}R")

    valid_folds = [f for f in fold_results if f["total_trades"] > 0]
    profitable_folds = [f for f in valid_folds if f["expectancy_r"] is not None and f["expectancy_r"] > 0]

    print(f"\n  {'-'*66}")
    print(f"  STABILITY SUMMARY: profitable in {len(profitable_folds)}/{len(valid_folds)} "
          f"folds with trades ({len(fold_results) - len(valid_folds)} fold(s) had zero trades)")

    if len(valid_folds) == 0:
        print("  No fold produced any trades -- can't assess stability, need more history or a "
              "less restrictive entry condition.")
    elif len(profitable_folds) == len(valid_folds):
        print("  Consistently profitable across every fold that had trades -- this is a good sign,")
        print("  though still worth treating cautiously with small per-fold trade counts.")
    elif len(profitable_folds) == 0:
        print("  Not profitable in ANY fold -- the full-history aggregate result was likely driven")
        print("  by a small number of large winning trades, not a consistent edge.")
    else:
        print("  Mixed -- profitable in some periods, not others. This may mean the edge is real but")
        print("  regime-dependent, or that the aggregate result is fragile.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--market", default="spot", choices=["spot", "futures"])
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--override", action="append", metavar="KEY=VALUE",
                         help="Override a parameter, e.g. --override USE_TRAILING_STOP=true "
                              "--override PAIRS_ZSCORE_ENTRY_THRESHOLD=1.5 (repeatable). Use this "
                              "to walk-forward validate a specific combination found by backtest.optimize "
                              "before spending a holdout check on it.")
    args = parser.parse_args()

    overrides = parse_overrides(args.override)
    run_walk_forward(args.symbol, args.market, args.timeframe, args.strategy, args.folds, overrides=overrides)


if __name__ == "__main__":
    main()
