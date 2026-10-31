"""
Performance metrics computed from a completed backtest's trade list.
Kept separate from strategy.py so the same metrics code can summarize
results from live paper-trading (Step 6) too, not just historical backtests.
"""


def compute_metrics(trades: list) -> dict:
    """
    trades: list of dicts as produced by backtest/strategies/*.py's run() functions

    Returns a dict with total_trades, scored_trades, win_rate_pct, expectancy_r,
    profit_factor, max_drawdown_r, sharpe_ratio, sortino_ratio, calmar_ratio,
    sqn, and distribution stats. All None if there are no trades.

    ``total_trades`` counts every trade. ``scored_trades`` counts only trades with a
    recorded R multiple: that is the population win rate, expectancy, profit factor
    and drawdown are computed on (they differ only when a trade had zero risk).
    """
    empty_result = {
        "total_trades": 0, "scored_trades": 0, "win_rate_pct": None, "expectancy_r": None,
        "profit_factor": None, "max_drawdown_r": None, "total_pnl_r": None,
        "sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None,
        "sqn": None, "avg_win_r": None, "avg_loss_r": None, "payoff_ratio": None,
        "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        "max_drawdown_duration_trades": 0,
    }
    if not trades:
        return empty_result

    r_multiples = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    if not r_multiples:
        res = dict(empty_result)
        res["total_trades"] = len(trades)
        return res

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
    dd_duration = 0
    max_dd_duration = 0
    consecutive_wins = 0
    max_consecutive_wins = 0
    consecutive_losses = 0
    max_consecutive_losses = 0

    for r in r_multiples:
        cum += r
        if cum >= peak:
            peak = cum
            dd_duration = 0
        else:
            dd = peak - cum
            max_dd = max(max_dd, dd)
            dd_duration += 1
            max_dd_duration = max(max_dd_duration, dd_duration)

        if r > 0:
            consecutive_wins += 1
            consecutive_losses = 0
            max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
        else:
            consecutive_losses += 1
            consecutive_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

    # Advanced statistical and risk-adjusted metrics
    sharpe_ratio = None
    sortino_ratio = None
    calmar_ratio = None
    sqn = None
    avg_win_r = round(sum(wins) / len(wins), 4) if wins else None
    avg_loss_r = round(sum(losses) / len(losses), 4) if losses else None
    payoff_ratio = round(abs(avg_win_r / avg_loss_r), 4) if (avg_win_r is not None and avg_loss_r is not None and avg_loss_r != 0) else None

    if len(r_multiples) > 1 and expectancy_r is not None:
        mean_r = sum(r_multiples) / len(r_multiples)
        variance = sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1)
        std_r = variance ** 0.5
        if std_r > 1e-9:
            sharpe_ratio = round(mean_r / std_r, 4)
            sqn = round((len(r_multiples) ** 0.5) * (mean_r / std_r), 4)

        downside_variance = sum(min(0.0, r) ** 2 for r in r_multiples) / len(r_multiples)
        downside_std = downside_variance ** 0.5
        if downside_std > 1e-9:
            sortino_ratio = round(mean_r / downside_std, 4)

        if max_dd > 1e-9:
            calmar_ratio = round(sum(r_multiples) / max_dd, 4)

    return {
        "total_trades": total_trades,
        "scored_trades": len(r_multiples),
        "win_rate_pct": round(win_rate_pct, 3) if win_rate_pct is not None else None,
        "expectancy_r": round(expectancy_r, 4) if expectancy_r is not None else None,
        "profit_factor": round(profit_factor, 4) if profit_factor not in (None, float("inf")) else profit_factor,
        "max_drawdown_r": round(max_dd, 4),
        "total_pnl_r": round(sum(r_multiples), 4) if r_multiples else None,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
        "sqn": sqn,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "payoff_ratio": payoff_ratio,
        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": max_consecutive_losses,
        "max_drawdown_duration_trades": max_dd_duration,
    }
