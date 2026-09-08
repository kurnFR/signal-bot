"""Configuration contract for selectable ML strategies."""
from __future__ import annotations

from typing import Any

ML_STRATEGY_CONFIG = {
    "ml_signal_filter": {
        "name": "ML Signal Filter",
        "description": "Filters an existing strategy using an ML success probability.",
        "base_strategy_required": True,
        "model_types": ["logistic_regression", "random_forest", "hist_gradient_boosting"],
        "parameters": {
            "probability_threshold": {"type": "float", "min": 0.50, "max": 0.95, "default": 0.60},
        },
    }
}


def validate_ml_config(strategy_name: str, config: dict[str, Any]) -> None:
    """Fail closed on unknown strategy/config values before a run."""
    if strategy_name not in ML_STRATEGY_CONFIG:
        raise ValueError(f"unsupported ML strategy '{strategy_name}'")
    definition = ML_STRATEGY_CONFIG[strategy_name]
    model_type = str(config.get("model_type", "")).strip().lower()
    if model_type not in definition["model_types"]:
        raise ValueError(f"unsupported ML model_type '{model_type}'")
    threshold = float(config.get("probability_threshold", definition["parameters"]["probability_threshold"]["default"]))
    bounds = definition["parameters"]["probability_threshold"]
    if not bounds["min"] <= threshold <= bounds["max"]:
        raise ValueError("probability_threshold is outside the allowed range")
    if not str(config.get("base_strategy", "")).strip():
        raise ValueError("base_strategy is required for ml_signal_filter")
