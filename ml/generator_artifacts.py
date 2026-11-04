"""Typed, isolated artifacts for independently generated ML trading signals."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import hashlib
import stat
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from ml.artifacts import read_model_manifest_file, resolve_model_artifact_paths, resolve_model_manifest_path
from ml.models import supported_models

GENERATOR_TASK_TYPE = "signal_generator_v1"
GENERATOR_LABEL_PROTOCOL = "execution_aligned_gap_aware_stop_target_timeout_v4"
GENERATOR_EVALUATION_PROTOCOL = "purged_generator_max_execution_end_gap_aware_v4"
GENERATOR_FEATURE_SCHEMA = "shared_features_v1"
GENERATOR_CLASS_MAPPING = {"SHORT": 0, "HOLD": 1, "LONG": 2}
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_FORBIDDEN_FEATURES = {
    "open_time", "entry_time", "exit_time", "label_end_time",
    "entry_price", "label_exit_price", "target", "target_name",
    "gross_return", "net_return", "forward_return",
    "long_net_return", "short_net_return", "label_reason", "is_closed",
}


def generator_paper_eligibility_report(
    test_metrics: Mapping[str, Any],
    *,
    min_test_trades: int = 30,
    min_profit_factor: float = 1.0,
    max_drawdown_pct: float = 25.0,
    min_total_net_return: float = 0.0,
) -> dict[str, Any]:
    """Apply a conservative, generator-specific untouched-test paper gate."""
    reasons = []
    if not isinstance(test_metrics, Mapping):
        return {"eligible": False, "reasons": ["test metrics must be an object"]}
    if test_metrics.get("evaluation_mode") != "canonical_simulator_oos":
        reasons.append("test metrics must come from canonical out-of-sample simulator execution")
    if test_metrics.get("execution_engine") != "backtest.simulate":
        reasons.append("test metrics must identify the canonical backtest simulator")
    if test_metrics.get("non_overlapping_execution") is not True:
        reasons.append("test metrics must confirm non-overlapping executable trades")

    trades = test_metrics.get("trades")
    if isinstance(trades, bool) or not isinstance(trades, (int, float)) or not math.isfinite(float(trades)) or int(trades) != trades or trades < min_test_trades:
        reasons.append(f"needs at least {min_test_trades} untouched test trades")

    profit_factor = test_metrics.get("profit_factor")
    if isinstance(profit_factor, bool) or not isinstance(profit_factor, (int, float)) or not math.isfinite(float(profit_factor)) or profit_factor < min_profit_factor:
        reasons.append(f"profit factor must be finite and >= {min_profit_factor:g}")

    drawdown = test_metrics.get("max_drawdown_pct")
    if isinstance(drawdown, bool) or not isinstance(drawdown, (int, float)) or not math.isfinite(float(drawdown)) or drawdown < 0 or drawdown > max_drawdown_pct:
        reasons.append(f"max drawdown must be between 0 and {max_drawdown_pct:g}%")

    total_return = test_metrics.get("total_net_return")
    if isinstance(total_return, bool) or not isinstance(total_return, (int, float)) or not math.isfinite(float(total_return)) or total_return < min_total_net_return:
        reasons.append(f"total net return must be >= {min_total_net_return:g}")

    return {"eligible": not reasons, "reasons": reasons}


@dataclass(frozen=True)
class GeneratorArtifact:
    model_id: str
    experiment_id: str
    model_type: str
    feature_columns: tuple[str, ...]
    class_mapping: dict[str, int]
    decision_policy: dict[str, float]
    label_config: dict[str, Any]
    data_boundaries: dict[str, Any]
    train_metrics: dict[str, Any]
    validation_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    task_type: str = GENERATOR_TASK_TYPE
    label_protocol_version: str = GENERATOR_LABEL_PROTOCOL
    evaluation_protocol_version: str = GENERATOR_EVALUATION_PROTOCOL
    feature_schema_version: str = GENERATOR_FEATURE_SCHEMA
    eligible_for_paper: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    weights_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not _MODEL_ID_RE.fullmatch(self.model_id) or ".." in self.model_id:
            raise ValueError("model_id must be a safe identifier of at most 100 characters")
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if self.model_type not in supported_models():
            raise ValueError("unsupported generator model_type")
        if self.task_type != GENERATOR_TASK_TYPE:
            raise ValueError("artifact task_type must identify an independent signal generator")
        if self.label_protocol_version != GENERATOR_LABEL_PROTOCOL:
            raise ValueError("unsupported generator label protocol")
        if self.evaluation_protocol_version != GENERATOR_EVALUATION_PROTOCOL:
            raise ValueError("unsupported generator evaluation protocol")
        if self.feature_schema_version != GENERATOR_FEATURE_SCHEMA:
            raise ValueError("unsupported generator feature schema")
        if not isinstance(self.feature_columns, tuple) or not self.feature_columns or len(set(self.feature_columns)) != len(self.feature_columns):
            raise ValueError("feature_columns must be a non-empty unique tuple")
        if any(not isinstance(column, str) or not column.strip() for column in self.feature_columns):
            raise ValueError("feature_columns must contain non-empty strings")
        forbidden = sorted(set(self.feature_columns) & _FORBIDDEN_FEATURES)
        if forbidden:
            raise ValueError("feature schema contains target/future metadata: " + ", ".join(forbidden))
        if (
            not isinstance(self.class_mapping, dict)
            or set(self.class_mapping) != set(GENERATOR_CLASS_MAPPING)
            or any(type(value) is not int for value in self.class_mapping.values())
            or self.class_mapping != GENERATOR_CLASS_MAPPING
        ):
            raise ValueError("generator class mapping must be SHORT=0, HOLD=1, LONG=2")
        if not isinstance(self.decision_policy, dict):
            raise ValueError("decision_policy must be an object")
        for key in ("min_confidence", "min_directional_margin"):
            value = self.decision_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 < value < 1:
                raise ValueError(f"decision_policy.{key} must be finite and between zero and one")
        for name in ("label_config", "data_boundaries", "train_metrics", "validation_metrics", "test_metrics"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be an object")
        if not isinstance(self.eligible_for_paper, bool):
            raise ValueError("eligible_for_paper must be boolean")
        if self.eligible_for_paper:
            report = generator_paper_eligibility_report(self.test_metrics)
            if not report["eligible"]:
                raise ValueError("paper-eligible generator artifact failed its test gate: " + "; ".join(report["reasons"]))
        if self.weights_sha256 is not None and (
            not isinstance(self.weights_sha256, str) or not _SHA256_RE.fullmatch(self.weights_sha256)
        ):
            raise ValueError("weights_sha256 must be a lowercase SHA-256 digest")

    def manifest(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> "GeneratorArtifact":
        if not isinstance(manifest, Mapping):
            raise ValueError("generator manifest must be an object")
        data = dict(manifest)
        if data.get("task_type") != GENERATOR_TASK_TYPE:
            raise ValueError("artifact is not an independent ML signal generator")
        data["feature_columns"] = tuple(data.get("feature_columns") or ())
        return cls(**data)

    def save_manifest(self, directory: str | Path) -> Path:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()
        destination = root / f"{self.model_id}.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=root,
                prefix=".generator-manifest-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(self.manifest(), handle, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
            descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return destination
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("generator manifest could not be written safely") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def save_generator_artifact(
    artifact: GeneratorArtifact,
    model: Any,
    directory: str | Path = "ml_models",
) -> tuple[Path, Path]:
    """Persist the typed generator artifact using the hardened shared model store."""
    from ml.model_store import ModelStore

    return ModelStore(directory).save(artifact, model)


def load_generator_artifact(
    model_id: str,
    directory: str | Path = "ml_models",
    *,
    require_eligible: bool = False,
):
    """Validate generator type and securely deserialize its verified weights."""
    from ml.model_store import ModelStore

    manifest_path = resolve_model_manifest_path(model_id, directory)
    manifest = read_model_manifest_file(manifest_path)
    artifact = GeneratorArtifact.from_manifest(manifest)
    if artifact.model_id != model_id:
        raise ValueError("generator manifest ID does not match requested model ID")
    if not isinstance(artifact.weights_sha256, str) or not _SHA256_RE.fullmatch(artifact.weights_sha256):
        raise ValueError("generator manifest must contain a valid weights digest")
    if require_eligible:
        report = generator_paper_eligibility_report(artifact.test_metrics)
        if not artifact.eligible_for_paper or not report["eligible"]:
            raise ValueError("generator model is not eligible for paper trading: " + "; ".join(report["reasons"]))

    model = ModelStore(directory).load(
        artifact, artifact.feature_columns, manifest=manifest
    )
    import numpy as np
    classes = set(np.asarray(getattr(model, "classes_", [])).tolist())
    if classes != set(GENERATOR_CLASS_MAPPING.values()):
        raise ValueError("loaded generator model does not support SHORT, HOLD, and LONG")
    if not callable(getattr(model, "predict_proba", None)):
        raise ValueError("loaded generator model does not expose predict_proba")
    if tuple(getattr(model, "_signal_bot_generator_feature_columns", ())) != artifact.feature_columns:
        raise ValueError("loaded generator model feature schema does not match its manifest")
    preprocessor = getattr(model, "_signal_bot_generator_preprocessor", None)
    if (
        not isinstance(preprocessor, tuple)
        or len(preprocessor) != 2
        or not all(callable(getattr(item, "transform", None)) for item in preprocessor)
    ):
        raise ValueError("loaded generator model is missing its fitted preprocessor")
    return artifact, model


def list_generator_artifacts(directory: str | Path = "ml_models") -> list[dict[str, Any]]:
    """List typed generator manifests without deserializing model weights."""
    root = Path(directory).resolve()
    if not root.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for candidate in root.glob("*.json"):
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.parent != root or not resolved.is_file():
                continue
            manifest = read_model_manifest_file(resolved)
            artifact = GeneratorArtifact.from_manifest(manifest)
            if artifact.model_id != candidate.stem:
                continue
            weights_verified = False
            try:
                _, weights_path = resolve_model_artifact_paths(artifact.model_id, root)
                descriptor = os.open(
                    weights_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                )
                with os.fdopen(descriptor, "rb") as handle:
                    if stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                        weights_verified = digest.hexdigest() == artifact.weights_sha256
            except (OSError, ValueError, FileNotFoundError):
                weights_verified = False
            report = generator_paper_eligibility_report(artifact.test_metrics)
            reasons = list(report["reasons"])
            if not artifact.eligible_for_paper:
                reasons.append("manifest is not flagged eligible_for_paper")
            if not weights_verified:
                reasons.append("model weights are missing or fail SHA-256 verification")
            results.append({
                "model_id": artifact.model_id,
                "experiment_id": artifact.experiment_id,
                "model_type": artifact.model_type,
                "feature_columns": list(artifact.feature_columns),
                "created_at": artifact.created_at,
                "eligible_for_paper": bool(artifact.eligible_for_paper and not reasons),
                "eligibility": {"eligible": not reasons, "reasons": reasons},
                "test_metrics": artifact.test_metrics,
            })
        except Exception:
            # Invalid, filter-type, or unsafe manifests are intentionally omitted.
            continue
    return sorted(results, key=lambda item: item["created_at"], reverse=True)
