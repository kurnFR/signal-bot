"""Compare backtest metrics across explicit fee/slippage scenarios.

This command reruns the selected strategy on the TRAIN segment only and
prints CSV to stdout. It never persists backtest runs/trades or changes
strategy defaults. It models fixed per-side rates, not spread or market impact.
"""
import argparse
import csv
import math
import sys
from itertools import product


def _validate_rates(fee_rates, slippage_rates):
    for label, rates in (("fee", fee_rates), ("slippage", slippage_rates)):
        if not rates:
            raise ValueError(f"at least one {label} rate is required")
        for rate in rates:
            if not math.isfinite(rate) or rate < 0 or rate >= 1:
                raise ValueError(f"{label} rates must be finite values in [0, 1)")


def evaluate_scenarios(strategy_fn, dataframe, base_params, fee_rates, slippage_rates):
    """Run the same strategy/data under each fee x slippage combination."""
    _validate_rates(fee_rates, slippage_rates)
    rows = []
    for fee, slippage in product(fee_rates, slippage_rates):
        params = dict(base_params)
        params["BACKTEST_FEE_PCT"] = fee
        params["BACKTEST_SLIPPAGE_PCT"] = slippage
        trades = strategy_fn(dataframe, params)
        # Local import keeps this helper lightweight for unit tests and reuse.
        from backtest.metrics import compute_metrics
        metrics = compute_metrics(trades)
        rows.append({
            "fee_pct_per_side": fee,
            "slippage_pct_per_side": slippage,
            **metrics,
        })
    return rows


def _parse_rates(raw, label):
    try:
        rates = [float(value.strip()) for value in raw.split(",") if value.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be comma-separated decimal rates") from exc
    if not rates:
        raise argparse.ArgumentTypeError(f"{label} must contain at least one rate")
    try:
        _validate_rates(rates if label == "fees" else [0.0],
                        rates if label == "slippage" else [0.0])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return rates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market", choices=["spot", "futures"], required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--fees", default="0.0005,0.001,0.002",
                        help="comma-separated fee rates per side; decimals, not percentages")
    parser.add_argument("--slippages", default="0,0.0005,0.001",
                        help="comma-separated slippage rates per side; decimals, not percentages")
    args = parser.parse_args(argv)

    fees = _parse_rates(args.fees, "fees")
    slippages = _parse_rates(args.slippages, "slippage")

    # Delay project imports until after argument validation; no DB writes occur.
    from config import HOLDOUT_FRACTION, PAIRS_BASE_SYMBOL
    from db.db import fetch_ohlcv_with_features_df
    from backtest.holdout import compute_holdout_cutoff, split_by_cutoff
    from backtest.params import BASE_PARAMS
    from backtest.strategies import STRATEGIES, FUNDING_STRATEGIES, TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
    from backtest.strategies.trend_alignment import HTF_TIMEFRAME

    if args.strategy not in STRATEGIES:
        parser.error(f"unknown strategy {args.strategy!r}; choose from: {', '.join(STRATEGIES)}")

    df = fetch_ohlcv_with_features_df(
        args.symbol, args.market, args.timeframe, closed_only=True,
        include_funding=args.strategy in FUNDING_STRATEGIES,
    )
    if len(df) < 100:
        parser.error(f"need at least 100 closed candles; found {len(df)}")

    cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION)
    train_df, _holdout_df = split_by_cutoff(df, cutoff)
    if len(train_df) < 50:
        parser.error(f"need at least 50 train candles; found {len(train_df)}")

    params = dict(BASE_PARAMS, symbol=args.symbol, market=args.market, timeframe=args.timeframe)
    if args.strategy in TREND_ALIGNMENT_STRATEGIES and args.timeframe != HTF_TIMEFRAME:
        htf = fetch_ohlcv_with_features_df(args.symbol, args.market, HTF_TIMEFRAME, closed_only=True)
        htf_train, _ = split_by_cutoff(htf, cutoff)
        params["htf_df"] = htf_train
    if args.strategy in PAIRS_STRATEGIES and args.symbol != PAIRS_BASE_SYMBOL:
        pair = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, args.market, args.timeframe, closed_only=True)
        pair_train, _ = split_by_cutoff(pair, cutoff)
        params["pair_df"] = pair_train
        params["pair_symbol"] = PAIRS_BASE_SYMBOL

    rows = evaluate_scenarios(STRATEGIES[args.strategy], train_df, params, fees, slippages)
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)


if __name__ == "__main__":
    main()
