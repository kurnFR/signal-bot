"""Evaluation helpers for ML classification outputs.

Classification metrics are diagnostic only. Promotion decisions must use the
real trading simulator and canonical accounting.
"""
from __future__ import annotations

from typing import Any


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
