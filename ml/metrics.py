"""Evaluation helpers for ML classification outputs.

Classification metrics are diagnostic only. Promotion decisions must use the
real trading simulator and canonical accounting.
"""
from __future__ import annotations

from typing import Any


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile without adding a statistics dependency."""
    if not values or not 0.0 <= q <= 1.0:
        raise ValueError("values must be non-empty and q must be between 0 and 1")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _roc_auc(y: list[int], probabilities: list[float]) -> float | None:
    """Compute ROC-AUC from ranks; return None when only one class exists."""
    positives = sum(y)
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return None

    # Mann-Whitney U via average ranks, with ties handled correctly.
    ranked = sorted(enumerate(probabilities), key=lambda item: item[1])
    rank_sum_positive = 0.0
    position = 0
    while position < len(ranked):
        end = position + 1
        value = ranked[position][1]
        while end < len(ranked) and ranked[end][1] == value:
            end += 1
        average_rank = (position + 1 + end) / 2.0
        for index, _ in ranked[position:end]:
            if y[index] == 1:
                rank_sum_positive += average_rank
        position = end
    u = rank_sum_positive - positives * (positives + 1) / 2.0
    return u / (positives * negatives)


def probability_diagnostics(y_true, probabilities, *, thresholds=(0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)) -> dict[str, Any]:
    """Describe model probability separation without using future outcomes.

    This is a diagnostic view of validation/model behavior. Threshold rows report
    how many signals would be accepted; trading economics must still come from
    the execution-aware simulator.
    """
    y = [int(v) for v in y_true]
    p = [float(v) for v in probabilities]
    if len(y) != len(p) or not y:
        raise ValueError("y_true and probabilities must have the same non-zero length")
    if any(value < 0.0 or value > 1.0 for value in p):
        raise ValueError("probabilities must be between 0 and 1")
    normalized_thresholds = tuple(float(t) for t in thresholds)
    if any(not 0.0 < t < 1.0 for t in normalized_thresholds):
        raise ValueError("thresholds must be strictly between 0 and 1")

    return {
        "samples": len(p),
        "positive_rate": round(sum(y) / len(y), 8),
        "probability_min": round(min(p), 8),
        "probability_max": round(max(p), 8),
        "probability_mean": round(sum(p) / len(p), 8),
        "probability_median": round(_quantile(p, 0.50), 8),
        "probability_quantiles": {
            "p10": round(_quantile(p, 0.10), 8),
            "p25": round(_quantile(p, 0.25), 8),
            "p50": round(_quantile(p, 0.50), 8),
            "p75": round(_quantile(p, 0.75), 8),
            "p90": round(_quantile(p, 0.90), 8),
        },
        "roc_auc": round(_roc_auc(y, p), 8) if _roc_auc(y, p) is not None else None,
        "thresholds": [
            {
                "threshold": round(t, 4),
                "selected_count": sum(value >= t for value in p),
                "selection_rate_pct": round(sum(value >= t for value in p) / len(p) * 100.0, 4),
            }
            for t in normalized_thresholds
        ],
    }


def classification_metrics(y_true, probabilities, threshold: float = 0.5) -> dict[str, Any]:
    """Return deterministic, dependency-light diagnostics for a binary model."""
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be between 0 and 1")

    y = [int(v) for v in y_true]
    p = [float(v) for v in probabilities]
    if len(y) != len(p) or not y:
        raise ValueError("y_true and probabilities must have the same non-zero length")

    pred = [1 if value >= threshold else 0 for value in p]
    tp = sum(a == 1 and b == 1 for a, b in zip(y, pred))
    tn = sum(a == 0 and b == 0 for a, b in zip(y, pred))
    fp = sum(a == 0 and b == 1 for a, b in zip(y, pred))
    fn = sum(a == 1 and b == 0 for a, b in zip(y, pred))
    total = len(y)

    return {
        "samples": total,
        "positive_rate": sum(y) / total,
        "predicted_positive_rate": sum(pred) / total,
        "accuracy": (tp + tn) / total,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "threshold": threshold,
    }
