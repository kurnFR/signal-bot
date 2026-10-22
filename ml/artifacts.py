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
import re
from typing import Any


_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


def resolve_model_artifact_paths(
    model_id: str, directory: str | Path = "ml_models"
) -> tuple[Path, Path]:
    """Return manifest/weight paths only when both resolve inside the artifact root.

    Model IDs can come from persisted paper configuration, not just the
    deployment endpoint. Validate them again at load time and resolve symlinks
    so a later filesystem change cannot redirect joblib loading outside the
    configured artifact directory.
    """
    if (
        not isinstance(model_id, str)
        or not _MODEL_ID_RE.fullmatch(model_id)
        or ".." in model_id
    ):
        raise ValueError("Invalid model_id")

    root = Path(directory).resolve()
    manifest_path = (root / f"{model_id}.json").resolve()
    model_path = (root / f"{model_id}.joblib").resolve()
    if manifest_path.parent != root or model_path.parent != root:
        raise ValueError("Model artifacts must resolve inside the artifact directory")
    if not manifest_path.is_file() or not model_path.is_file():
        raise FileNotFoundError("Model manifest and weights must be regular files inside the artifact directory")
    return manifest_path, model_path


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
        if not isinstance(self.model_id, str) or not _MODEL_ID_RE.fullmatch(self.model_id) or ".." in self.model_id:
            raise ValueError("model_id must be a safe filesystem identifier")
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
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



def load_eligible_paper_artifact(
    manifest: Any, expected_model_id: str
) -> ModelArtifact:
    """Validate mutable manifest metadata again at paper-runtime load time.

    Deployment-time checks are not sufficient because the JSON manifest can be
    edited or replaced while a paper config remains active. Fail closed before
    deserializing model weights unless both the explicit eligibility flag and
    the current untouched-test metrics satisfy the promotion gate.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Model manifest must be a JSON object")
    if manifest.get("model_id") != expected_model_id:
        raise ValueError("Model manifest ID does not match configured model ID")
    if manifest.get("eligible_for_paper") is not True:
        raise ValueError("Model manifest is not marked eligible for paper trading")
    report = paper_eligibility_report(test_metrics=manifest.get("test_metrics"))
    if not report["eligible"]:
        raise ValueError("Model no longer passes paper eligibility: " + "; ".join(report["reasons"]))
    return ModelArtifact(**manifest)


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
