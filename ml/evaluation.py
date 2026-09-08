"""Trading evaluation for an ML-filtered strategy.

The evaluator compares the original strategy signal set with the same completed
trades after ML filtering. It never recalculates execution or P&L; those values
come from the canonical backtest trade records.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import pandas as pd

from backtest.metrics import compute_metrics


def _trading_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = compute_metrics(list(trades))
    net_pnl = float(sum(float(t.get("net_pnl", 0.0)) for t in trades))
    return {**metrics, "net_pnl": round(net_pnl, 8)}


def evaluate_filtered_trades(
    trades: Sequence[Mapping[str, Any]],
    signal_dataset: pd.DataFrame,
    probabilities,
    threshold: float,
) -> dict[str, Any]:
    """Compare baseline trades with the ML-selected subset.

    ``signal_dataset`` must retain canonical ``trade_index`` values. The
    probabilities must be aligned to that dataset. Threshold selection must
    happen upstream on VALIDATION only; this function is an evaluator and does
    not optimize any parameter.
    """
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if "trade_index" not in signal_dataset.columns:
        raise ValueError("signal_dataset must contain trade_index")

    probabilities = pd.Series(probabilities, index=signal_dataset.index, dtype=float)
    if len(probabilities) != len(signal_dataset):
        raise ValueError("probabilities must have the same length as signal_dataset")

    selected_ids = {
        int(row["trade_index"])
        for _, row in signal_dataset.loc[probabilities >= threshold].iterrows()
    }
    filtered = [trade for trade_index, trade in enumerate(trades) if trade_index in selected_ids]

    baseline = _trading_summary(trades)
    ml_summary = _trading_summary(filtered)
    return {
        "threshold": threshold,
        "baseline": baseline,
        "ml_filtered": ml_summary,
        "delta": {
            "net_pnl": round(ml_summary["net_pnl"] - baseline["net_pnl"], 8),
            "expectancy_r": (
                round(ml_summary["expectancy_r"] - baseline["expectancy_r"], 8)
                if ml_summary["expectancy_r"] is not None and baseline["expectancy_r"] is not None
                else None
            ),
            "profit_factor": (
                round(ml_summary["profit_factor"] - baseline["profit_factor"], 8)
                if isinstance(ml_summary["profit_factor"], (int, float))
                and isinstance(baseline["profit_factor"], (int, float))
                else None
            ),
            "max_drawdown_r": (
                round(ml_summary["max_drawdown_r"] - baseline["max_drawdown_r"], 8)
                if ml_summary["max_drawdown_r"] is not None and baseline["max_drawdown_r"] is not None
                else None
            ),
            "trades": ml_summary["total_trades"] - baseline["total_trades"],
        },
    }
