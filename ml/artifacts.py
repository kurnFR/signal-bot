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
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any


CURRENT_EVALUATION_PROTOCOL_VERSION = "purged_exit_time_v1"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def verify_model_weights_hash(manifest: dict[str, Any], model_path: str | Path) -> str:
    """Verify model bytes against the SHA-256 recorded in the manifest."""
    expected = manifest.get("weights_sha256") if isinstance(manifest, dict) else None
    if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
        raise ValueError("Model manifest must contain a valid weights_sha256 digest")
    digest = hashlib.sha256()
    try:
        with Path(model_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("Model weights could not be read for integrity verification") from exc
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError("Model weights SHA-256 does not match manifest")
    return actual


def _validate_model_id(model_id: str) -> None:
    if (
        not isinstance(model_id, str)
        or not _MODEL_ID_RE.fullmatch(model_id)
        or ".." in model_id
    ):
        raise ValueError("Invalid model_id")


def resolve_model_manifest_path(
    model_id: str, directory: str | Path = "ml_models"
) -> Path:
    """Resolve a manifest only when it is a regular file inside the artifact root."""
    _validate_model_id(model_id)
    root = Path(directory).resolve()
    manifest_path = (root / f"{model_id}.json").resolve()
    if manifest_path.parent != root:
        raise ValueError("Model manifest must resolve inside the artifact directory")
    if not manifest_path.is_file():
        raise FileNotFoundError("Model manifest must be a regular file inside the artifact directory")
    return manifest_path


def resolve_model_artifact_paths(
    model_id: str, directory: str | Path = "ml_models"
) -> tuple[Path, Path]:
    """Return manifest/weight paths only when both resolve inside the artifact root.

    Model IDs can come from persisted paper configuration, not just the
    deployment endpoint. Validate them again at load time and resolve symlinks
    so a later filesystem change cannot redirect joblib loading outside the
    configured artifact directory.
    """
    manifest_path = resolve_model_manifest_path(model_id, directory)
    root = Path(directory).resolve()
    model_path = (root / f"{model_id}.joblib").resolve()
    if model_path.parent != root:
        raise ValueError("Model weights must resolve inside the artifact directory")
    if not model_path.is_file():
        raise FileNotFoundError("Model weights must be a regular file inside the artifact directory")
    return manifest_path, model_path


def read_model_manifest_file(path: str | Path) -> dict[str, Any]:
    """Read a resolved manifest through a no-follow descriptor.

    Callers must resolve and contain the path first. O_NOFOLLOW and fstat then
    prevent a symlink or non-regular file from being substituted before open.

    On platforms without os.O_NOFOLLOW (notably Windows), the flag is simply
    unavailable, so the open falls back to 0 instead of raising: the caller
    already resolved the path and contained it inside the artifact root, and
    the post-open fstat check below still rejects anything that is not a
    regular file. The only defence lost on those platforms is the
    open-time no-follow itself, i.e. a symlink swapped in between the
    caller's resolve() and this open(); that TOCTOU window does not exist
    on POSIX, where O_NOFOLLOW is honoured.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(Path(path), flags)
    except OSError as exc:
        raise ValueError("Model manifest could not be opened safely") from exc

    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Model manifest must be a regular file")
        try:
            manifest = json.load(handle)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Model manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Model manifest must be a JSON object")
    return manifest


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
    weights_sha256: str | None = None
    evaluation_protocol_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not _MODEL_ID_RE.fullmatch(self.model_id) or ".." in self.model_id:
            raise ValueError("model_id must be a safe filesystem identifier")
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be between 0 and 1")
        if self.weights_sha256 is not None and (not isinstance(self.weights_sha256, str) or not _SHA256_RE.fullmatch(self.weights_sha256)):
            raise ValueError("weights_sha256 must be a lowercase SHA-256 hex digest")
        if self.evaluation_protocol_version is not None and (
            not isinstance(self.evaluation_protocol_version, str)
            or not re.fullmatch(r"[a-z0-9_]{1,64}", self.evaluation_protocol_version)
        ):
            raise ValueError("evaluation_protocol_version must be a safe protocol identifier")
        if self.eligible_for_paper is True and (
            self.evaluation_protocol_version != CURRENT_EVALUATION_PROTOCOL_VERSION
        ):
            raise ValueError(
                "paper-eligible artifacts require the current purged exit-time evaluation protocol"
            )

    def manifest(self) -> dict[str, Any]:
        return asdict(self)

    def save_manifest(self, directory: str | Path) -> Path:
        """Persist a manifest atomically without following a destination symlink."""
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()
        path = root / f"{self.model_id}.json"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=root,
                prefix=".model-manifest-", suffix=".tmp", delete=False,
            ) as handle:
                temp_path = handle.name
                json.dump(self.manifest(), handle, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Replacing a symlink replaces the link itself; it does not write
            # through it to the external target.
            os.replace(temp_path, path)
            temp_path = None
            return path
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("Model manifest could not be written safely") from exc
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


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
    if manifest.get("evaluation_protocol_version") != CURRENT_EVALUATION_PROTOCOL_VERSION:
        raise ValueError(
            "Model was not evaluated with the current purged exit-time protocol"
        )
    if not isinstance(manifest.get("weights_sha256"), str) or not _SHA256_RE.fullmatch(manifest["weights_sha256"]):
        raise ValueError("Paper model manifest must contain a valid weights_sha256 digest")
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
