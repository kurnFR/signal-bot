"""
Reports on backtest results, per symbol/timeframe, broken out by strategy
so the mean-reversion / breakout / trend-following results can be
compared side by side rather than one silently overwriting the view of
another.

Usage:
    python3 -m backtest.report                       # full detail, all strategies
    python3 -m backtest.report --compare              # compact side-by-side comparison table (includes $ PnL)
    python3 -m backtest.report --compare --no-pnl     # comparison table without the PnL columns (faster, fewer queries)
    python3 -m backtest.report --symbol BTCUSDT --timeframe 1h
    python3 -m backtest.report --symbol ETHUSDT --market futures --timeframe 1d --strategy pairs_ratio_v1 --top-trades 3
    python3 -m backtest.report --symbol ETHUSDT --market futures --timeframe 1d --strategy pairs_ratio_v1 --top-trades 3 --worst
"""
import argparse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, TIMEFRAMES, MARKETS, INITIAL_CAPITAL_USD, RISK_PER_TRADE_PCT
from db.db import get_pool
from backtest.strategies import STRATEGIES
from backtest.equity import simulate_equity_curve


def query(sql, params=None):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def latest_run(symbol, market, timeframe, strategy_name, data_segment="train"):
    rows = query(
        "SELECT * FROM backtest_runs WHERE symbol=%s AND market=%s AND timeframe=%s "
        "AND strategy_name=%s AND data_segment=%s ORDER BY created_at DESC LIMIT 1",
        (symbol, market, timeframe, strategy_name, data_segment),
    )
    return rows[0] if rows else None


def exit_reason_breakdown(run_id):
    return query(
        "SELECT exit_reason, COUNT(*) as n, AVG(net_return_pct) as avg_return, "
        "AVG(r_multiple) as avg_r FROM backtest_trades WHERE run_id=%s GROUP BY exit_reason",
        (run_id,),
    )


def direction_breakdown(run_id):
    return query(
        "SELECT direction, COUNT(*) as n, "
        "SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END) as wins, "
        "AVG(net_return_pct) as avg_return, AVG(r_multiple) as avg_r, "
        "MAX(r_multiple) as best_r, MIN(r_multiple) as worst_r "
        "FROM backtest_trades WHERE run_id=%s GROUP BY direction",
        (run_id,),
    )


def get_trades_for_run(run_id):
    return query(
        "SELECT entry_time, exit_time, r_multiple FROM backtest_trades "
        "WHERE run_id=%s ORDER BY entry_time ASC",
        (run_id,),
    )


def top_trades(run_id, direction, n, best=True):
    order = "DESC" if best else "ASC"
    return query(
        f"SELECT * FROM backtest_trades WHERE run_id=%s AND direction=%s "
        f"ORDER BY r_multiple {order} LIMIT %s",
        (run_id, direction, n),
    )


def _print_trade_detail(t):
    r = float(t["r_multiple"]) if t["r_multiple"] is not None else 0.0
    print(f"    Entry: {t['entry_time_wib']} WIB  @ {t['entry_price']}")
    print(f"      SL={t['stop_loss']}   TP={t['take_profit']}")
    print(f"    Exit:  {t['exit_time_wib']} WIB  @ {t['exit_price']}   reason={t['exit_reason']}")
    print(f"      return={float(t['net_return_pct']):+.3f}%   R={r:+.3f}   held {t['holding_bars']} bars")
    print()


def print_top_trades(symbol, market, timeframe, strategy_name, n=3, best=True):
    run = latest_run(symbol, market, timeframe, strategy_name)
    if not run:
        print(f"No run found for {symbol} {market} {timeframe} [{strategy_name}] -- run backtest.run_backtest first")
        return
    if run["total_trades"] == 0:
        print(f"{symbol} {market} {timeframe} [{strategy_name}]: 0 trades in this run, nothing to show")
        return

    label = "BEST" if best else "WORST"
    print(f"\n{'='*70}")
    print(f"TOP {n} {label} TRADES: {symbol} {market} {timeframe} [{strategy_name}] (run_id={run['id']})")
    print(f"{'='*70}")

    for direction in ["LONG", "SHORT"]:
        trades = top_trades(run["id"], direction, n, best=best)
        print(f"\n  Top {n} {label.lower()} {direction} trades:")
        if not trades:
            print("    (none -- this direction had no trades in this run)")
            continue
        for t in trades:
            _print_trade_detail(t)


def print_report(symbol, market, timeframe, strategy_name):
    run = latest_run(symbol, market, timeframe, strategy_name)
    if not run:
        return  # this strategy simply hasn't been run for this combo yet

    print(f"\n{'='*70}")
    print(f"{symbol} {market} {timeframe} [{strategy_name}]  (run_id={run['id']}, {run['created_at']})")
    print(f"{'='*70}")
    print(f"  Total trades:    {run['total_trades']}")
    print(f"  Win rate:        {run['win_rate_pct']}%")
    print(f"  Expectancy:      {run['expectancy_r']} R per trade")
    print(f"  Profit factor:   {run['profit_factor']}")
    print(f"  Max drawdown:    {run['max_drawdown_r']} R")

    if run["total_trades"] == 0:
        print("  (No trades were generated -- either the strategy conditions never "
              "triggered in this data, or the data window is too short.)")
        return

    breakdown = exit_reason_breakdown(run["id"])
    print(f"\n  Exit reason breakdown:")
    for b in breakdown:
        pct_of_total = b["n"] / run["total_trades"] * 100
        print(f"    {b['exit_reason']:10s}: {b['n']:5d} trades ({pct_of_total:5.1f}%)  "
              f"avg_return={float(b['avg_return']):+.3f}%  avg_R={float(b['avg_r'] or 0):+.3f}")

    dir_breakdown = direction_breakdown(run["id"])
    print(f"\n  By direction:")
    for d in dir_breakdown:
        win_rate = (d["wins"] / d["n"] * 100) if d["n"] else 0
        pct_of_total = d["n"] / run["total_trades"] * 100
        print(f"    {d['direction']:6s}: {d['n']:5d} trades ({pct_of_total:5.1f}%)  win_rate={win_rate:5.1f}%  "
              f"avg_return={float(d['avg_return']):+.3f}%  avg_R={float(d['avg_r'] or 0):+.3f}  "
              f"best_R={float(d['best_r']):+.3f}  worst_R={float(d['worst_r']):+.3f}")

        # $ PnL for this direction alone, at the same default capital/risk
        dir_trades = query(
            "SELECT entry_time, exit_time, r_multiple FROM backtest_trades "
            "WHERE run_id=%s AND direction=%s ORDER BY entry_time ASC",
            (run["id"], d["direction"]),
        )
        dir_trade_dicts = [{"entry_time": t["entry_time"], "exit_time": t["exit_time"],
                             "r_multiple": float(t["r_multiple"]) if t["r_multiple"] is not None else None}
                            for t in dir_trades]
        dir_pnl = simulate_equity_curve(dir_trade_dicts, INITIAL_CAPITAL_USD, RISK_PER_TRADE_PCT)
        print(f"             PnL if traded alone: ${dir_pnl['final_equity']:,.2f} ({dir_pnl['total_return_pct']:+.2f}%)")

    trades = get_trades_for_run(run["id"])
    trade_dicts = [{"entry_time": t["entry_time"], "exit_time": t["exit_time"],
                     "r_multiple": float(t["r_multiple"]) if t["r_multiple"] is not None else None}
                    for t in trades]
    pnl = simulate_equity_curve(trade_dicts, INITIAL_CAPITAL_USD, RISK_PER_TRADE_PCT)
    print(f"\n  PnL @ ${INITIAL_CAPITAL_USD:,.0f} start, {RISK_PER_TRADE_PCT}% risk/trade:")
    print(f"    Final equity:  ${pnl['final_equity']:,.2f}  ({pnl['total_return_pct']:+.2f}%)")
    print(f"    Max drawdown:  ${pnl['max_drawdown_dollars']:,.2f} ({pnl['max_drawdown_pct']:.2f}%)")


def print_comparison(symbol, market, timeframe, show_pnl=True):
    rows = []
    for strategy_name in STRATEGIES.keys():
        run = latest_run(symbol, market, timeframe, strategy_name)
        if run:
            rows.append((strategy_name, run))
    if not rows:
        return

    print(f"\n{symbol} {market} {timeframe}")
    header = f"  {'strategy':28s} {'trades':>7s} {'win%':>7s} {'exp(R)':>8s} {'PF':>7s} {'maxDD(R)':>9s}"
    if show_pnl:
        header += f" {'finalEquity':>13s} {'return%':>9s}"
    print(header)

    for strategy_name, run in rows:
        line = (f"  {strategy_name:28s} {run['total_trades']:>7d} "
                f"{('%.1f' % run['win_rate_pct']) if run['win_rate_pct'] is not None else '-':>7s} "
                f"{('%.3f' % run['expectancy_r']) if run['expectancy_r'] is not None else '-':>8s} "
                f"{('%.3f' % run['profit_factor']) if run['profit_factor'] not in (None,) else '-':>7s} "
                f"{('%.2f' % run['max_drawdown_r']) if run['max_drawdown_r'] is not None else '-':>9s}")
        if show_pnl:
            if run["total_trades"] > 0:
                trades = get_trades_for_run(run["id"])
                trade_dicts = [{"entry_time": t["entry_time"], "exit_time": t["exit_time"],
                                 "r_multiple": float(t["r_multiple"]) if t["r_multiple"] is not None else None}
                                for t in trades]
                pnl = simulate_equity_curve(trade_dicts, INITIAL_CAPITAL_USD, RISK_PER_TRADE_PCT)
                line += f" {('$%.0f' % pnl['final_equity']):>13s} {pnl['total_return_pct']:>+8.2f}%"
            else:
                line += f" {'-':>13s} {'-':>9s}"
        print(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--timeframe")
    parser.add_argument("--market", choices=["spot", "futures"])
    parser.add_argument("--strategy", choices=list(STRATEGIES.keys()))
    parser.add_argument("--compare", action="store_true", help="Compact side-by-side comparison across strategies")
    parser.add_argument("--no-pnl", action="store_true", help="Skip $ PnL columns in --compare (faster)")
    parser.add_argument("--top-trades", type=int, default=0, metavar="N",
                         help="Show top N best LONG and SHORT trades with full detail "
                              "(requires --symbol --timeframe --strategy; add --worst for worst trades instead)")
    parser.add_argument("--worst", action="store_true", help="With --top-trades, show worst instead of best")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else SYMBOLS
    timeframes = [args.timeframe] if args.timeframe else TIMEFRAMES
    markets = [args.market] if args.market else MARKETS
    strategies = [args.strategy] if args.strategy else list(STRATEGIES.keys())

    if args.top_trades > 0:
        if not (args.symbol and args.timeframe and args.strategy):
            print("--top-trades requires --symbol, --timeframe, and --strategy to all be specified.")
            return
        print_top_trades(args.symbol, args.market or "spot", args.timeframe, args.strategy,
                          n=args.top_trades, best=not args.worst)
        return

    if args.compare:
        for market in markets:
            for symbol in symbols:
                for timeframe in timeframes:
                    print_comparison(symbol, market, timeframe, show_pnl=not args.no_pnl)
        return

    for market in markets:
        for symbol in symbols:
            for timeframe in timeframes:
                for strategy_name in strategies:
                    print_report(symbol, market, timeframe, strategy_name)


if __name__ == "__main__":
    main()
