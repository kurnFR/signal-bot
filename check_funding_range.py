"""
Diagnostic: shows the actual range of funding_rate values within the
holdout time window for a symbol -- run this to check whether "0 trades"
on a holdout check genuinely means no extreme funding events occurred, or
whether something is broken (e.g. no funding data landed in that window
at all).

Usage:
    python3 check_funding_range.py --symbol ETHUSDT --timeframe 4h
"""
import argparse
import sys

sys.path.insert(0, ".")
from config import HOLDOUT_FRACTION
from db.db import fetch_ohlcv_with_features_df
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--market", default="spot")
    args = parser.parse_args()

    df = fetch_ohlcv_with_features_df(args.symbol, args.market, args.timeframe,
                                       closed_only=True, include_funding=True)
    cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION)
    train_df, holdout_df = split_by_cutoff(df, cutoff)

    for label, seg in [("TRAIN", train_df), ("HOLDOUT", holdout_df)]:
        fr = seg["funding_rate"].dropna()
        if len(fr) == 0:
            print(f"{label}: no funding_rate values present at all (0 non-null rows) -- likely a join/data bug")
            continue
        fr_pct = fr * 100
        n_pos_extreme = (fr_pct >= 0.05).sum()
        n_neg_extreme = (fr_pct <= -0.05).sum()
        print(f"{label}: {len(seg)} candles, {len(fr)} with funding data")
        print(f"  funding_rate range: {fr_pct.min():.4f}% to {fr_pct.max():.4f}%  (mean {fr_pct.mean():.4f}%)")
        print(f"  candles at/above +0.05%: {n_pos_extreme}   candles at/below -0.05%: {n_neg_extreme}")


if __name__ == "__main__":
    main()
