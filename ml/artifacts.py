"""Safe, versioned model-artifact metadata for ML strategy promotion.

The artifact store is intentionally filesystem based for now. It stores only
metadata here; callers can persist the serialized model beside the manifest.
Promotion requires an explicit passing test result and never happens merely
because a model has the highest profit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelArtifact:
    model_id: str
    experiment_id: str
    model_type: str
    base_strategy: str
    feature_columns: tuple[str, ...]
    strategy_params: dict[str, Any]
    model_params: dict[str, Any]
    threshold: float
    train_metrics: dict[str, Any]
    validation_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    eligible_for_paper: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if not self.model_id.strip() or not self.experiment_id.strip():
            raise ValueError("model_id and experiment_id are required")
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be between 0 and 1")

    def manifest(self) -> dict[str, Any]:
        return asdict(self)

    def save_manifest(self, directory: str | Path) -> Path:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.model_id}.json"
        path.write_text(json.dumps(self.manifest(), sort_keys=True, indent=2), encoding="utf-8")
        return path


def paper_eligibility(
    *,
    test_metrics: dict[str, Any],
    min_test_trades: int = 30,
    min_profit_factor: float = 1.0,
    max_drawdown_pct: float = 25.0,
    min_net_pnl: float = 0.0,
) -> bool:
    """Apply conservative promotion gates to an untouched OOS result."""
    trades = int(test_metrics.get("trade_count", 0))
    profit_factor = float(test_metrics.get("profit_factor", 0.0))
    drawdown = float(test_metrics.get("max_drawdown_pct", float("inf")))
    net_pnl = float(test_metrics.get("net_pnl", float("-inf")))
    return (
        trades >= min_test_trades
        and profit_factor >= min_profit_factor
        and drawdown <= max_drawdown_pct
        and net_pnl >= min_net_pnl
    )
