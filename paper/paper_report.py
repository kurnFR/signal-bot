"""Paper Trading performance report text (Telegram HTML).

Extracted from ``paper/retailbot2.py`` so the hourly report no longer depends on
the retired RetailBot2 worker. ``TelegramAlert`` delegates here, so existing
callers and tests keep working unchanged.
"""
import logging
from html import escape


def paper_performance_lines() -> list[str]:
    """Read the same realized paper ledger totals shown on the dashboard."""
    lines = ["📝 <b>Paper Trading Engine · Realized P&amp;L</b>"]
    try:
        try:
            from paper.engine import get_paper_metrics
        except ModuleNotFoundError:
            from engine import get_paper_metrics

        metrics = get_paper_metrics()
    except Exception as exc:
        logging.getLogger(__name__).warning("Paper performance report unavailable (%s: %s)", type(exc).__name__, exc)
        return [*lines, "Paper trade totals unavailable"]

    if not metrics["totalTrades"]:
        return [*lines, "No closed trades included in reporting yet."]

    def pnl_label(value: float) -> str:
        if abs(value) < 0.005:
            return "$0.00"
        sign = "+" if value > 0 else "−"
        return f"{sign}${abs(value):,.2f}"

    lines.extend([
        f"Net P&amp;L: <b>{pnl_label(metrics['netPnl'])}</b>",
        f"{metrics['totalTrades']} closed · Wins {metrics['wins']} · Losses {metrics['losses']}"
        f" · {metrics['winRatePct']:g}% win rate",
    ])
    by_pair: dict[str, dict[str, list]] = {}
    for row in metrics["byStrategy"]:
        if not row["totalTrades"]:
            continue
        strategies = by_pair.setdefault(row["symbol"], {})
        stats = strategies.setdefault(row["strategy"], [0, 0, 0, 0.0])
        stats[0] += row["totalTrades"]
        stats[1] += row["wins"]
        stats[2] += row["losses"]
        stats[3] += row["netPnl"]

    omitted = 0
    remaining = 1750
    for symbol, strategies in sorted(by_pair.items()):
        pair_trades = sum(row[0] for row in strategies.values())
        pair_wins = sum(row[1] for row in strategies.values())
        pair_pnl = sum(row[3] for row in strategies.values())
        heading = (
            f"<b>{escape(symbol)}</b> · {pnl_label(pair_pnl)}\n"
            f"{pair_trades} closed · {pair_wins / pair_trades:.0%} win rate"
        )
        pair_lines = []
        for name, (trades, wins, losses, pnl) in sorted(strategies.items()):
            pair_lines.append(
                f"• <b>{escape(name)}</b> · {pnl_label(pnl)}\n"
                f"  {trades} closed · Wins {wins} · Losses {losses} · {wins / trades:.0%} win rate"
            )
        # Keep the pair heading and its first strategy together.
        heading_size = len(heading) + 2
        if remaining < heading_size + len(pair_lines[0]) + 1:
            omitted += len(pair_lines)
            continue
        lines.extend(["", heading])
        remaining -= heading_size
        for line in pair_lines:
            if remaining < len(line) + 1:
                omitted += 1
                continue
            lines.append(line)
            remaining -= len(line) + 1
    if omitted:
        lines.append(f"Total includes {omitted} more pair/strategy rows. View all in Paper Trading.")
    return lines

def format_paper_report() -> str:
    return "\n".join([
        "📊 <b>PAPER TRADING PERFORMANCE</b>",
        "─" * 30,
        *paper_performance_lines(),
    ])
