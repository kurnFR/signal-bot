"""
Converts a stored backtest run's trades into a $ PnL / equity curve using
fixed-fractional position sizing (risk RISK_PER_TRADE_PCT of CURRENT
equity per trade, compounding). Backtest results only tracked R-multiples
(risk-adjusted, account-size-independent) -- this translates that into
what it would have actually meant for a specific starting capital.

Usage:
    python3 -m backtest.pnl_report --symbol BNBUSDT --timeframe 1d --strategy trend_ema_v1
    python3 -m backtest.pnl_report --symbol BNBUSDT --timeframe 1d --strategy trend_ema_v1 --capital 10000 --risk-pct 2
"""
import argparse
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import INITIAL_CAPITAL_USD, RISK_PER_TRADE_PCT
from db.db import get_pool
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


def ms_to_wib_str(ms):
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms, hours=7)
    return dt.strftime("%Y-%m-%d")


def run_pnl_report(symbol, market, timeframe, strategy_name, capital, risk_pct, data_segment="train"):
    run_rows = query(
        "SELECT * FROM backtest_runs WHERE symbol=%s AND market=%s AND timeframe=%s "
        "AND strategy_name=%s AND data_segment=%s ORDER BY created_at DESC LIMIT 1",
        (symbol, market, timeframe, strategy_name, data_segment),
    )
    if not run_rows:
        print(f"No {data_segment} run found for {symbol} {market} {timeframe} [{strategy_name}]. "
              f"Run backtest.run_backtest first.")
        return
    run = run_rows[0]

    trades = query(
        "SELECT * FROM backtest_trades WHERE run_id=%s ORDER BY entry_time ASC",
        (run["id"],),
    )
    if not trades:
        print(f"{symbol} {market} {timeframe} [{strategy_name}]: 0 trades in this run, nothing to simulate")
        return

    trade_dicts = [
        {
            "entry_time": t["entry_time"], "exit_time": t["exit_time"],
            "r_multiple": float(t["r_multiple"]) if t["r_multiple"] is not None else None,
            "direction": t["direction"], "exit_reason": t["exit_reason"],
        }
        for t in trades
    ]

    result = simulate_equity_curve(trade_dicts, capital, risk_pct)

    print(f"\n{'='*70}")
    print(f"PnL SIMULATION: {symbol} {market} {timeframe} [{strategy_name}] ({data_segment})")
    print(f"{'='*70}")
    print(f"  Initial capital:      ${result['initial_capital']:,.2f}")
    print(f"  Risk per trade:       {risk_pct}% of current equity (compounding)")
    print(f"  Period:               {ms_to_wib_str(run['data_start_time'])} to {ms_to_wib_str(run['data_end_time'])}")
    print(f"  Trades simulated:     {len(trade_dicts)}")
    print(f"  Final equity:         ${result['final_equity']:,.2f}")
    print(f"  Total return:         {result['total_return_pct']:+.2f}%")
    print(f"  Max drawdown:         ${result['max_drawdown_dollars']:,.2f} ({result['max_drawdown_pct']:.2f}%)")

    non_zero = result["curve"][1:]
    if non_zero:
        biggest_win = max(non_zero, key=lambda c: c["pnl_dollars"])
        biggest_loss = min(non_zero, key=lambda c: c["pnl_dollars"])
        print(f"  Biggest single win:   ${biggest_win['pnl_dollars']:,.2f} (trade #{biggest_win['trade_num']})")
        print(f"  Biggest single loss:  ${biggest_loss['pnl_dollars']:,.2f} (trade #{biggest_loss['trade_num']})")

    if result["final_equity"] <= 0:
        print("\n  WARNING: equity went to zero or below at this risk-per-trade setting --")
        print("  this position size would have blown up the account. Try a lower --risk-pct.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--market", default="spot", choices=["spot", "futures"])
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL_USD)
    parser.add_argument("--risk-pct", type=float, default=RISK_PER_TRADE_PCT)
    parser.add_argument("--segment", default="train", choices=["train", "holdout"])
    args = parser.parse_args()

    run_pnl_report(args.symbol, args.market, args.timeframe, args.strategy,
                    args.capital, args.risk_pct, args.segment)


if __name__ == "__main__":
    main()
