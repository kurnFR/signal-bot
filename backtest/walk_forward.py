"""
Step 4: Walk-forward validation and Walk-Forward Analysis (WFA).

Splits the TRAIN portion of historical data (holdout excluded -- see
backtest/holdout.py) into sequential folds or rolling windows to test
temporal stability, edge persistence, and Walk-Forward Efficiency (WFE).

Usage:
    python3 -m backtest.walk_forward --symbol BTCUSDT --timeframe 1d --strategy breakout_continuation_v1
    python3 -m backtest.walk_forward --symbol BTCUSDT --timeframe 4h --strategy trend_alignment_v1 --folds 6
    python3 -m backtest.walk_forward --symbol BTCUSDT --timeframe 1h --strategy trend_ema_v1 --rolling
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
        return None

    effective_params = dict(BASE_PARAMS, **(overrides or {}))
    if overrides:
        print(f"Using parameter overrides: {overrides}\n")

    strategy_fn = STRATEGIES[strategy_name]
    full_df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True,
                                            include_funding=strategy_name in FUNDING_STRATEGIES)
    if len(full_df) < 100:
        print(f"{symbol} {market} {timeframe}: only {len(full_df)} candles total, too few to proceed")
        return None

    cutoff = compute_holdout_cutoff(full_df, HOLDOUT_FRACTION)
    train_df, _holdout_df = split_by_cutoff(full_df, cutoff)  # holdout never touched here

    if len(train_df) < n_folds * 50:
        print(f"{symbol} {market} {timeframe}: only {len(train_df)} TRAIN candles (holdout excluded) -- "
              f"too few to split into {n_folds} meaningful folds. Reduce --folds or gather more history.")
        return None

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

    return {
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "strategy": strategy_name,
        "n_folds": n_folds,
        "valid_folds": len(valid_folds),
        "profitable_folds": len(profitable_folds),
        "fold_results": fold_results,
    }


def run_rolling_walk_forward(
    symbol: str,
    market: str,
    timeframe: str,
    strategy_name: str,
    train_window_bars: int = 500,
    test_window_bars: int = 150,
    step_bars: int = 100,
    overrides: dict = None,
) -> dict:
    """
    Continuous Rolling Walk-Forward Analysis (WFA).
    Rolls a train window of size train_window_bars and tests out-of-sample on test_window_bars,
    advancing by step_bars each iteration.
    Computes:
      - In-Sample (IS) vs Out-of-Sample (OOS) performance
      - Walk-Forward Efficiency (WFE) ratio
      - Edge consistency and degradation metrics
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy_name}'. Available: {list(STRATEGIES.keys())}")

    effective_params = dict(BASE_PARAMS, **(overrides or {}))
    strategy_fn = STRATEGIES[strategy_name]
    full_df = fetch_ohlcv_with_features_df(
        symbol, market, timeframe, closed_only=True,
        include_funding=strategy_name in FUNDING_STRATEGIES
    )
    if len(full_df) < (train_window_bars + test_window_bars):
        raise ValueError(
            f"Insufficient candles ({len(full_df)}) for rolling WFA with train={train_window_bars} + test={test_window_bars}"
        )

    cutoff = compute_holdout_cutoff(full_df, HOLDOUT_FRACTION)
    train_df, _ = split_by_cutoff(full_df, cutoff)
    n_rows = len(train_df)

    windows = []
    start = 0
    while start + train_window_bars + test_window_bars <= n_rows:
        is_df = train_df.iloc[start:start + train_window_bars].reset_index(drop=True)
        oos_df = train_df.iloc[start + train_window_bars:start + train_window_bars + test_window_bars].reset_index(drop=True)
        windows.append((is_df, oos_df))
        start += step_bars

    if not windows:
        raise ValueError("Not enough train rows to construct at least one rolling window.")

    is_trades_all = []
    oos_trades_all = []
    window_reports = []

    for idx, (is_df, oos_df) in enumerate(windows, start=1):
        call_params = dict(effective_params, symbol=symbol, market=market, timeframe=timeframe)
        is_trades = strategy_fn(is_df, call_params)
        oos_trades = strategy_fn(oos_df, call_params)

        is_m = compute_metrics(is_trades)
        oos_m = compute_metrics(oos_trades)

        is_trades_all.extend(is_trades)
        oos_trades_all.extend(oos_trades)

        window_reports.append({
            "window": idx,
            "is_start": ms_to_wib_str(int(is_df.iloc[0]["open_time"])),
            "is_end": ms_to_wib_str(int(is_df.iloc[-1]["open_time"])),
            "oos_start": ms_to_wib_str(int(oos_df.iloc[0]["open_time"])),
            "oos_end": ms_to_wib_str(int(oos_df.iloc[-1]["open_time"])),
            "is_metrics": is_m,
            "oos_metrics": oos_m,
        })

    total_is_metrics = compute_metrics(is_trades_all)
    total_oos_metrics = compute_metrics(oos_trades_all)

    # Walk-Forward Efficiency (WFE) = OOS Expectancy / IS Expectancy
    is_exp = total_is_metrics.get("expectancy_r")
    oos_exp = total_oos_metrics.get("expectancy_r")
    wfe_pct = None
    if is_exp and is_exp > 0 and oos_exp is not None:
        wfe_pct = round((oos_exp / is_exp) * 100.0, 2)

    robustness = "INCONCLUSIVE"
    if wfe_pct is not None:
        if wfe_pct >= 60.0:
            robustness = "ROBUST_EDGE"
        elif wfe_pct >= 40.0:
            robustness = "MODERATE_EDGE"
        elif wfe_pct > 0.0:
            robustness = "FRAGILE_EDGE"
        else:
            robustness = "OVERFIT"

    return {
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "strategy": strategy_name,
        "train_window_bars": train_window_bars,
        "test_window_bars": test_window_bars,
        "step_bars": step_bars,
        "total_windows": len(windows),
        "in_sample_metrics": total_is_metrics,
        "out_of_sample_metrics": total_oos_metrics,
        "walk_forward_efficiency_pct": wfe_pct,
        "robustness": robustness,
        "windows": window_reports,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--market", default="spot", choices=["spot", "futures"])
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--rolling", action="store_true", help="Execute rolling walk-forward analysis instead of fixed folds")
    parser.add_argument("--train-window", type=int, default=500, help="Train window size for rolling WFA")
    parser.add_argument("--test-window", type=int, default=150, help="Test window size for rolling WFA")
    parser.add_argument("--step-bars", type=int, default=100, help="Step bars for rolling WFA")
    parser.add_argument("--override", action="append", metavar="KEY=VALUE",
                         help="Override a parameter, e.g. --override USE_TRAILING_STOP=true "
                              "--override PAIRS_ZSCORE_ENTRY_THRESHOLD=1.5 (repeatable). Use this "
                              "to walk-forward validate a specific combination found by backtest.optimize "
                              "before spending a holdout check on it.")
    args = parser.parse_args()

    overrides = parse_overrides(args.override)
    if args.rolling:
        res = run_rolling_walk_forward(
            args.symbol, args.market, args.timeframe, args.strategy,
            train_window_bars=args.train_window, test_window_bars=args.test_window,
            step_bars=args.step_bars, overrides=overrides
        )
        print(f"\nROLLING WFA RESULT: WFE={res['walk_forward_efficiency_pct']}% Status={res['robustness']}")
    else:
        run_walk_forward(args.symbol, args.market, args.timeframe, args.strategy, args.folds, overrides=overrides)


if __name__ == "__main__":
    main()
