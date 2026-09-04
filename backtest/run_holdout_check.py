"""
The ONLY script that touches the holdout data segment (the most recent
HOLDOUT_FRACTION of history, excluded from every run_backtest.py and
walk_forward.py run so far).

This is meant to be run ONCE, deliberately, after you've fully committed
to a specific strategy/symbol/timeframe/parameter combination based on
train-only results -- not as another exploratory tool. Checking holdout
results, not liking them, then going back to try more variations defeats
the entire purpose: it turns the "holdout" into just more training data,
and the multiple-comparisons problem creeps back in.

Requires --confirm to actually run, specifically to prevent casually
running this the same way you'd run backtest.report.

Usage:
    python3 -m backtest.run_holdout_check --symbol BNBUSDT --timeframe 4h --strategy trend_alignment_v1 --confirm
"""
import argparse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HOLDOUT_FRACTION, PAIRS_BASE_SYMBOL
from db.db import fetch_ohlcv_with_features_df, insert_backtest_run, insert_backtest_trades
from backtest.strategies import STRATEGIES, FUNDING_STRATEGIES
from backtest.params import BASE_PARAMS, parse_overrides
from backtest.strategies import TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.metrics import compute_metrics
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--market", default="spot", choices=["spot", "futures"])
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument("--confirm", action="store_true",
                         help="Required. This is a one-time-use check, not an exploratory tool.")
    parser.add_argument("--override", action="append", metavar="KEY=VALUE",
                         help="Override a parameter, e.g. --override USE_TRAILING_STOP=true "
                              "--override TRAIL_DISTANCE_ATR_MULT=1.5 (repeatable). Use this to "
                              "holdout-check a specific combination found by backtest.optimize.")
    args = parser.parse_args()

    if not args.confirm:
        print("This checks the HOLDOUT data segment -- meant to be run ONCE, after you've fully")
        print("committed to a strategy based on train-only results. Re-checking repeatedly while")
        print("trying different variations defeats the purpose of having a holdout at all.")
        print("\nIf you're sure, re-run with --confirm.")
        return

    strategy_fn = STRATEGIES[args.strategy]
    df = fetch_ohlcv_with_features_df(args.symbol, args.market, args.timeframe, closed_only=True,
                                       include_funding=args.strategy in FUNDING_STRATEGIES)
    cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION)
    _train_df, holdout_df = split_by_cutoff(df, cutoff)

    if len(holdout_df) < 30:
        print(f"Only {len(holdout_df)} candles in the holdout segment -- too few to be meaningful.")
        return

    overrides = parse_overrides(args.override)
    effective_params = dict(BASE_PARAMS, **overrides)
    if overrides:
        print(f"Using parameter overrides: {overrides}\n")
    call_params = dict(effective_params, symbol=args.symbol, market=args.market, timeframe=args.timeframe)
    if args.strategy in TREND_ALIGNMENT_STRATEGIES and args.timeframe != HTF_TIMEFRAME:
        htf_full = fetch_ohlcv_with_features_df(args.symbol, args.market, HTF_TIMEFRAME, closed_only=True)
        _htf_train, htf_holdout = split_by_cutoff(htf_full, cutoff)
        call_params["htf_df"] = htf_holdout

    if args.strategy in PAIRS_STRATEGIES and args.symbol != PAIRS_BASE_SYMBOL:
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, args.market, args.timeframe, closed_only=True)
        _pair_train, pair_holdout = split_by_cutoff(pair_full, cutoff)
        call_params["pair_df"] = pair_holdout
        call_params["pair_symbol"] = PAIRS_BASE_SYMBOL

    trades = strategy_fn(holdout_df, call_params)
    metrics = compute_metrics(trades)

    run_id = insert_backtest_run(
        args.symbol, args.market, args.timeframe, args.strategy, effective_params,
        int(holdout_df.iloc[0]["open_time"]), int(holdout_df.iloc[-1]["open_time"]),
        metrics["total_trades"], metrics["win_rate_pct"], metrics["expectancy_r"],
        metrics["profit_factor"], metrics["max_drawdown_r"],
        data_segment="holdout",
    )
    insert_backtest_trades(run_id, trades)

    print(f"\n{'='*70}")
    print(f"HOLDOUT CHECK: {args.symbol} {args.market} {args.timeframe} [{args.strategy}]")
    print(f"{'='*70}")
    print(f"  Candles in holdout: {len(holdout_df)}")
    print(f"  Total trades:       {metrics['total_trades']}")
    print(f"  Win rate:           {metrics['win_rate_pct']}%")
    print(f"  Expectancy:         {metrics['expectancy_r']} R per trade")
    print(f"  Profit factor:      {metrics['profit_factor']}")
    print(f"  Max drawdown:       {metrics['max_drawdown_r']} R")
    print(f"\n  (run_id={run_id}, stored with data_segment='holdout')")


if __name__ == "__main__":
    main()
