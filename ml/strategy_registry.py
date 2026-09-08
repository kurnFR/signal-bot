"""Safe bridge between the ML registry and the existing strategy menu."""
from __future__ import annotations

from typing import Any

from backtest.strategies import STRATEGIES
from ml.registry import ModelRegistry


def available_ml_strategies(registry: ModelRegistry, *, paper_only: bool = False) -> dict[str, dict[str, Any]]:
    """Return selectable ML entries without mutating the existing registry."""
    result: dict[str, dict[str, Any]] = {}
    for entry in registry.list():
        if entry.status == "retired":
            continue
        if paper_only and entry.status != "paper":
            continue
        result[f"ml:{entry.artifact.model_id}"] = {
            "model_id": entry.artifact.model_id,
            "status": entry.status,
            "model_type": entry.artifact.model_type,
            "base_strategy": entry.artifact.base_strategy,
            "threshold": entry.artifact.threshold,
            "features": list(entry.artifact.feature_columns),
        }
    return result


def validate_base_strategy(name: str) -> None:
    """Ensure an ML artifact cannot reference a missing base strategy."""
    if name not in STRATEGIES:
        raise ValueError(f"unknown base strategy: {name}")
