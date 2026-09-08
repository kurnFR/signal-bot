"""Small, deterministic model factory for the ML research pipeline.

Models are intentionally tabular and CPU-friendly. This module keeps model
construction separate from dataset generation and trading evaluation.
"""
from __future__ import annotations

from typing import Any


def create_model(model_type: str, params: dict[str, Any] | None = None):
    """Create a supported sklearn classifier from explicit parameters."""
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is required for ML training; install the project ML dependencies"
        ) from exc

    p = dict(params or {})
    normalized = model_type.strip().lower()

    if normalized in {"logistic_regression", "logistic"}:
        allowed = {
            "C", "class_weight", "max_iter", "solver", "tol", "random_state"
        }
        return LogisticRegression(**{k: v for k, v in p.items() if k in allowed})

    if normalized in {"random_forest", "rf"}:
        allowed = {
            "n_estimators", "max_depth", "min_samples_split", "min_samples_leaf",
            "max_features", "class_weight", "random_state", "n_jobs"
        }
        return RandomForestClassifier(**{k: v for k, v in p.items() if k in allowed})

    if normalized in {"hist_gradient_boosting", "histgb", "gradient_boosting"}:
        allowed = {
            "learning_rate", "max_iter", "max_leaf_nodes", "max_depth",
            "min_samples_leaf", "l2_regularization", "random_state"
        }
        return HistGradientBoostingClassifier(**{k: v for k, v in p.items() if k in allowed})

    raise ValueError(
        f"unsupported ML model '{model_type}'; supported models: "
        "logistic_regression, random_forest, hist_gradient_boosting"
    )


def supported_models() -> tuple[str, ...]:
    return ("logistic_regression", "random_forest", "hist_gradient_boosting")
