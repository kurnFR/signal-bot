"""Immutable, JSON-serializable ML experiment contract."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json


TARGET_TYPES = ("binary_positive_r", "binary_r_above_half", "binary_r_above_one")


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    symbol: str
    timeframe: str
    base_strategy: str
    model_type: str
    feature_columns: tuple[str, ...]
    strategy_params: dict[str, Any] = field(default_factory=dict)
    model_params: dict[str, Any] = field(default_factory=dict)
    probability_threshold: float = 0.5
    target_type: str = "binary_positive_r"
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    test_fraction: float = 0.2
    seed: int = 42
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        total = self.train_fraction + self.validation_fraction + self.test_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError("train/validation/test fractions must sum to 1")
        if not all(0 < value < 1 for value in (self.train_fraction, self.validation_fraction, self.test_fraction)):
            raise ValueError("all dataset fractions must be between 0 and 1")
        if not 0 < self.probability_threshold < 1:
            raise ValueError("probability_threshold must be between 0 and 1")
        if self.target_type not in TARGET_TYPES:
            raise ValueError(f"target_type must be one of {TARGET_TYPES}")
        if not self.feature_columns:
            raise ValueError("feature_columns must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
