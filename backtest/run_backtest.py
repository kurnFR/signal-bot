"""
Runs every registered strategy (backtest/strategies/) against the TRAIN
portion of historical data for every configured symbol x timeframe, and
stores results in backtest_runs / backtest_trades tagged data_segment='train'.

The most recent HOLDOUT_FRACTION of history is deliberately excluded here.
It's reserved and only checked once, via backtest/run_holdout_check.py,
after a strategy has been fully decided on -- see backtest/holdout.py for
why this matters (guards against the multiple-comparisons trap).

Usage:
    python3 -m backtest.run_backtest
    python3 -m backtest.run_backtest --symbol BTCUSDT --timeframe 1h
    python3 -m backtest.run_backtest --strategy breakout_continuation_v1
"""
import argparse
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, TIMEFRAMES, MARKETS, HOLDOUT_FRACTION, PAIRS_BASE_SYMBOL
from db.db import fetch_ohlcv_with_features_df, insert_backtest_run, insert_backtest_trades
from backtest.strategies import STRATEGIES, FUNDING_STRATEGIES
from backtest.params import BASE_PARAMS
from backtest.strategies import TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.metrics import compute_metrics
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_backtest")

# JSON-serializable params only -- this is what's stored in backtest_runs
# for reproducibility. Runtime-only extras (symbol/market/timeframe/htf_df)
# are added to a COPY of this before calling a strategy, never stored.


def run_one(symbol, market, timeframe, strategy_name, strategy_fn, data_segment="train"):
    df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True,
                                       include_funding=strategy_name in FUNDING_STRATEGIES)
    if len(df) < 100:
        logger.warning(f"{symbol} {market} {timeframe}: only {len(df)} closed candles, skipping")
        return

    cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION)
    train_df, holdout_df = split_by_cutoff(df, cutoff)
    segment_df = train_df if data_segment == "train" else holdout_df

    if len(segment_df) < 50:
        logger.warning(f"{symbol} {market} {timeframe} [{strategy_name}] {data_segment}: "
                        f"only {len(segment_df)} candles in this segment, skipping")
        return

    call_params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)

    # trend_alignment_v1 needs a higher-timeframe series, split at the SAME
    # absolute cutoff as the working timeframe -- never let the HTF series
    # peek into calendar time the working series' segment doesn't cover.
    if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
        htf_full = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True)
        htf_train, htf_holdout = split_by_cutoff(htf_full, cutoff)
        call_params["htf_df"] = htf_train if data_segment == "train" else htf_holdout

    # pairs_ratio_v1 needs a second symbol's series (the base/quote leg),
    # split at the SAME absolute cutoff as the working symbol -- never let
    # the pair leg peek into calendar time the working segment doesn't cover.
    if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
        pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True)
        pair_train, pair_holdout = split_by_cutoff(pair_full, cutoff)
        call_params["pair_df"] = pair_train if data_segment == "train" else pair_holdout
        call_params["pair_symbol"] = PAIRS_BASE_SYMBOL

    trades = strategy_fn(segment_df, call_params)
    metrics = compute_metrics(trades)

    run_id = insert_backtest_run(
        symbol, market, timeframe, strategy_name, BASE_PARAMS,
        int(segment_df.iloc[0]["open_time"]), int(segment_df.iloc[-1]["open_time"]),
        metrics["total_trades"], metrics["win_rate_pct"], metrics["expectancy_r"],
        metrics["profit_factor"], metrics["max_drawdown_r"],
        data_segment=data_segment,
    )
    insert_backtest_trades(run_id, trades)

    logger.info(
        f"{symbol} {market} {timeframe} [{strategy_name}] ({data_segment}): run_id={run_id} "
        f"trades={metrics['total_trades']} win_rate={metrics['win_rate_pct']}% "
        f"expectancy={metrics['expectancy_r']}R profit_factor={metrics['profit_factor']} "
        f"max_dd={metrics['max_drawdown_r']}R"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--timeframe")
    parser.add_argument("--market", choices=["spot", "futures"])
    parser.add_argument("--strategy", choices=list(STRATEGIES.keys()),
                         help="Run only this strategy (default: all registered strategies)")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    timeframes = [args.timeframe] if args.timeframe else TIMEFRAMES
    markets = [args.market] if args.market else MARKETS
    strategies = {args.strategy: STRATEGIES[args.strategy]} if args.strategy else STRATEGIES

    for market in markets:
        for symbol in symbols:
            for timeframe in timeframes:
                for strategy_name, strategy_fn in strategies.items():
                    try:
                        run_one(symbol, market, timeframe, strategy_name, strategy_fn, data_segment="train")
                    except Exception as e:
                        logger.error(f"FAILED {symbol} {market} {timeframe} [{strategy_name}]: {e}")


if __name__ == "__main__":
    main()
