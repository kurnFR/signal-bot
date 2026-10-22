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
import math
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


def _num(metrics: dict[str, Any], *keys: str) -> float | None:
    """First usable numeric value among ``keys``; ``None`` when absent or invalid."""
    for key in keys:
        if key not in metrics:
            continue
        value = metrics[key]
        if value is None:
            continue
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        # Promotion metrics are untrusted JSON metadata; NaN and +/-infinity
        # must never satisfy a gate through comparisons that treat them oddly.
        if not math.isfinite(number):
            return None
        return number
    return None


def paper_eligibility_report(
    *,
    test_metrics: dict[str, Any],
    min_test_trades: int = 30,
    min_profit_factor: float = 1.0,
    max_drawdown_pct: float = 25.0,
    min_net_pnl: float = 0.0,
) -> dict[str, Any]:
    """Evaluate the promotion gate and explain every failure.

    Fails closed: a metric that is missing, ``None`` or NaN fails its check.
    Accepts both the gate's own key names and the names produced by
    ``backtest.metrics.compute_metrics`` (``total_trades``).
    """
    metrics_are_object = isinstance(test_metrics, dict)
    metrics = test_metrics if metrics_are_object else {}
    reasons: list[str] = []
    if not metrics_are_object:
        reasons.append("test metrics must be a JSON object")

    trades = _num(metrics, "trade_count", "total_trades")
    if trades is None or not trades.is_integer() or trades < min_test_trades:
        reasons.append(f"needs at least {min_test_trades} untouched test trades as an integer count (got {'n/a' if trades is None else f'{trades:g}'})")

    profit_factor = _num(metrics, "profit_factor")
    if profit_factor is None or profit_factor < min_profit_factor:
        reasons.append(f"profit factor must be >= {min_profit_factor:g} (got {'n/a' if profit_factor is None else round(profit_factor, 3)})")

    drawdown = _num(metrics, "max_drawdown_pct")
    if drawdown is None or drawdown < 0 or drawdown > max_drawdown_pct:
        reasons.append(f"max drawdown must be between 0 and {max_drawdown_pct:g}% (got {'n/a' if drawdown is None else round(drawdown, 2)})")

    net_pnl = _num(metrics, "net_pnl")
    if net_pnl is None or net_pnl < min_net_pnl:
        reasons.append(f"net PnL must be >= {min_net_pnl:g} (got {'n/a' if net_pnl is None else round(net_pnl, 4)})")

    return {"eligible": not reasons, "reasons": reasons}


def paper_eligibility(
    *,
    test_metrics: dict[str, Any],
    min_test_trades: int = 30,
    min_profit_factor: float = 1.0,
    max_drawdown_pct: float = 25.0,
    min_net_pnl: float = 0.0,
) -> bool:
    """Apply conservative promotion gates to an untouched OOS result."""
    return paper_eligibility_report(
        test_metrics=test_metrics, min_test_trades=min_test_trades,
        min_profit_factor=min_profit_factor, max_drawdown_pct=max_drawdown_pct,
        min_net_pnl=min_net_pnl,
    )["eligible"]
