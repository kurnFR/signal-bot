"""Serializable research result for an ML experiment."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    symbol: str
    timeframe: str
    base_strategy: str
    model_type: str
    baseline_metrics: dict[str, Any]
    ml_validation_metrics: dict[str, Any]
    ml_test_metrics: dict[str, Any]
    threshold: float
    status: str = "research"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.status not in {"research", "paper_candidate", "rejected"}:
            raise ValueError("invalid experiment result status")
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def save(self, root: str) -> str:
        from pathlib import Path
        path = Path(root) / f"{self.experiment_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return str(path)
