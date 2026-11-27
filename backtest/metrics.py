"""
Performance metrics computed from a completed backtest's trade list.
Kept separate from strategy.py so the same metrics code can summarize
results from live paper-trading (Step 6) too, not just historical backtests.
"""
from typing import Any, Dict


def _exit_reason_breakdown(trades: list) -> list:
    """Per-exit-reason counts. Canonical reasons are always present (even
    with zero trades); any other reason present in the data is appended."""
    breakdown_acc: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        reason = str(t.get("exit_reason") or "UNKNOWN")
        row = breakdown_acc.setdefault(reason, {"exit_reason": reason, "trades": 0, "wins": 0,
                                                "losses": 0, "net_pnl": 0.0, "sum_r": 0.0})
        row["trades"] += 1
        r = t.get("r_multiple")
        if r is not None:
            if r > 0:
                row["wins"] += 1
            elif r < 0:
                row["losses"] += 1
            row["sum_r"] = round(row["sum_r"] + float(r), 4)
        row["net_pnl"] = round(row["net_pnl"] + float(t.get("net_pnl") or 0.0), 8)
    canonical_order = ["TP", "SL", "TRAIL", "TIMEOUT", "MANUAL", "END_OF_DATA"]
    breakdown = [
        breakdown_acc.get(reason, {"exit_reason": reason, "trades": 0, "wins": 0,
                                   "losses": 0, "net_pnl": 0.0, "sum_r": 0.0})
        for reason in canonical_order
    ]
    for reason in sorted(breakdown_acc):
        if reason not in canonical_order:
            breakdown.append(breakdown_acc[reason])
    return breakdown


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
        "sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None, "recovery_factor": None,
        "sqn": None, "avg_win_r": None, "avg_loss_r": None, "payoff_ratio": None,
        "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        "max_drawdown_duration_trades": 0,
        # Pure TP/SL aggregates: only trades closed by TP or SL. TRAIL /
        # TIMEOUT / MANUAL / END_OF_DATA exits are excluded here and shown
        # separately in exit_reason_breakdown, so a trailing-stop or timeout
        # partial win can no longer inflate the pure win rate.
        "pure_trades": 0, "pure_wins": 0, "pure_losses": 0, "pure_win_rate_pct": None,
        "pure_net_pnl": 0.0, "pure_sum_r": 0.0, "pure_expectancy_r": None,
        "pure_avg_win_r": None, "pure_avg_loss_r": None,
        "exit_reason_breakdown": [],
    }
    if not trades:
        empty_result["exit_reason_breakdown"] = _exit_reason_breakdown(trades)
        return empty_result

    r_multiples = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    if not r_multiples:
        res = dict(empty_result)
        res["total_trades"] = len(trades)
        pure = [t for t in trades if t.get("exit_reason") in ("TP", "SL")]
        res["pure_trades"] = len(pure)
        res["pure_net_pnl"] = round(sum(float(t.get("net_pnl") or 0.0) for t in pure), 8)
        res["exit_reason_breakdown"] = _exit_reason_breakdown(trades)
        return res

    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r < 0]

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
        elif r < 0:
            consecutive_losses += 1
            consecutive_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

        else:
            consecutive_wins = consecutive_losses = 0

    # Advanced statistical and risk-adjusted metrics
    sharpe_ratio = None
    sortino_ratio = None
    calmar_ratio = None
    sqn = None
    avg_win_r = sum(wins) / len(wins) if wins else None
    avg_loss_r = sum(losses) / len(losses) if losses else None
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

    recovery_factor = round(sum(r_multiples) / max_dd, 4) if max_dd > 0 else None

    # --- Pure TP/SL aggregates (exit_reason in TP/SL only) ---
    pure_trades = [t for t in trades if t.get("exit_reason") in ("TP", "SL")]
    pure_r = [t["r_multiple"] for t in pure_trades if t.get("r_multiple") is not None]
    pure_wins = [r for r in pure_r if r > 0]
    pure_losses = [r for r in pure_r if r < 0]
    pure_net_pnl = round(sum(float(t.get("net_pnl") or 0.0) for t in pure_trades), 8)
    pure_sum_r = round(sum(pure_r), 4) if pure_r else 0.0
    exit_reason_breakdown = _exit_reason_breakdown(trades)
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
        "recovery_factor": recovery_factor,
        "calmar_ratio": calmar_ratio,  # Legacy non-annualized recovery metric
        "sqn": sqn,
        "avg_win_r": round(avg_win_r, 4) if wins else None,
        "avg_loss_r": round(avg_loss_r, 4) if losses else None,
        "payoff_ratio": payoff_ratio,
        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": max_consecutive_losses,
        "max_drawdown_duration_trades": max_dd_duration,
        "pure_trades": len(pure_trades),
        "pure_wins": len(pure_wins),
        "pure_losses": len(pure_losses),
        "pure_win_rate_pct": round(len(pure_wins) / len(pure_r) * 100, 3) if pure_r else None,
        "pure_net_pnl": pure_net_pnl,
        "pure_sum_r": pure_sum_r,
        "pure_expectancy_r": round(sum(pure_r) / len(pure_r), 4) if pure_r else None,
        "pure_avg_win_r": round(sum(pure_wins) / len(pure_wins), 4) if pure_wins else None,
        "pure_avg_loss_r": round(sum(pure_losses) / len(pure_losses), 4) if pure_losses else None,
        "exit_reason_breakdown": exit_reason_breakdown,
    }
