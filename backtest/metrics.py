"""
Performance metrics computed from a completed backtest's trade list.
Kept separate from strategy.py so the same metrics code can summarize
results from live paper-trading (Step 6) too, not just historical backtests.
"""


def compute_metrics(trades: list) -> dict:
    """
    trades: list of dicts as produced by backtest/strategies/*.py's run() functions

    Returns a dict with total_trades, win_rate_pct, expectancy_r,
    profit_factor, max_drawdown_r. All None if there are no trades.
    """
    if not trades:
        return {
            "total_trades": 0, "win_rate_pct": None, "expectancy_r": None,
            "profit_factor": None, "max_drawdown_r": None,
        }

    r_multiples = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r <= 0]

    total_trades = len(trades)
    win_rate_pct = (len(wins) / len(r_multiples) * 100) if r_multiples else None
    expectancy_r = (sum(r_multiples) / len(r_multiples)) if r_multiples else None

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else None)

    # Max drawdown on the cumulative R-multiple equity curve
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_multiples:
        cum += r
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate_pct, 3) if win_rate_pct is not None else None,
        "expectancy_r": round(expectancy_r, 4) if expectancy_r is not None else None,
        "profit_factor": round(profit_factor, 4) if profit_factor not in (None, float("inf")) else profit_factor,
        "max_drawdown_r": round(max_dd, 4),
    }
