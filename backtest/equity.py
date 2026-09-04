"""
Converts a list of trades (each with an r_multiple) into an actual dollar
equity curve, using fixed-fractional position sizing: every trade risks
RISK_PER_TRADE_PCT of CURRENT equity (compounding), regardless of that
trade's own stop distance.

This is mathematically consistent with the R-multiples already computed:
r_multiple = net_return_pct / risk_pct, so
    dollar_pnl = equity * (risk_per_trade_pct/100) * r_multiple
exactly reproduces what actually sizing the position from that trade's real
entry/stop distance and current equity would have given -- no separate
per-unit position sizing math needed.
"""


def simulate_equity_curve(trades: list, initial_capital: float, risk_per_trade_pct: float) -> dict:
    """
    trades: list of dicts in chronological order, each with at least an
        'r_multiple' key (None entries are skipped -- can happen if a
        trade's risk_pct was somehow zero, extremely rare edge case).
    Returns initial_capital, final_equity, total_return_pct,
    max_drawdown_dollars, max_drawdown_pct, and the full per-trade curve.
    """
    equity = initial_capital
    peak = initial_capital
    max_drawdown_dollars = 0.0
    max_drawdown_pct = 0.0
    curve = [{
        "trade_num": 0, "equity": round(equity, 2),
        "entry_time": None, "exit_time": None, "pnl_dollars": 0.0,
    }]

    for idx, t in enumerate(trades, start=1):
        r = t.get("r_multiple")
        if r is None:
            continue
        risk_dollars = equity * (risk_per_trade_pct / 100)
        pnl_dollars = risk_dollars * r
        equity += pnl_dollars
        peak = max(peak, equity)
        dd_dollars = peak - equity
        dd_pct = (dd_dollars / peak * 100) if peak > 0 else 0
        max_drawdown_dollars = max(max_drawdown_dollars, dd_dollars)
        max_drawdown_pct = max(max_drawdown_pct, dd_pct)
        curve.append({
            "trade_num": idx, "equity": round(equity, 2),
            "entry_time": t.get("entry_time"), "exit_time": t.get("exit_time"),
            "pnl_dollars": round(pnl_dollars, 2),
        })

    final_equity = equity
    total_return_pct = (final_equity - initial_capital) / initial_capital * 100 if initial_capital else 0

    return {
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_dollars": round(max_drawdown_dollars, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "curve": curve,
    }
