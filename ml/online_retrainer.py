"""
Continuous Online Model Retraining Manager (FreqAI & Qlib style).
Maintains automated background retraining loops, model drift monitoring,
and safe model promotion gates.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import logging
import math
import os
import stat
from pathlib import Path
import tempfile
import uuid
from typing import Any, Iterator, Mapping

from db.db import fetch_ohlcv_with_features_df
from ml.run_real_experiment import run_real_experiment
from ml.walk_forward_engine import compute_drift_psi

logger = logging.getLogger("ml.online_retrainer")

MODEL_ARTIFACTS_DIR = Path("ml_models")
REGISTRY_PATH = MODEL_ARTIFACTS_DIR / "retraining_registry.json"
AUDIT_LOG_PATH = Path("ml_results") / "retraining_audit.json"
CYCLE_LOCK_PATH = Path("ml_models") / "retraining_cycle.lock"
REGISTRY_LOCK_PATH = Path("ml_models") / "retraining_registry.lock"
AUDIT_LOCK_PATH = Path("ml_results") / "retraining_audit.lock"
RETRAINING_JOURNAL_PATH = Path("ml_results") / "retraining_journal.json"
JOURNAL_LOCK_PATH = Path("ml_results") / "retraining_journal.lock"


@contextmanager
def _registry_lock() -> Iterator[None]:
    """Serialize short registry read/modify/write sections across processes."""
    REGISTRY_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY_LOCK_PATH.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _retraining_cycle_lock() -> Iterator[bool]:
    """Acquire a non-blocking process-wide lock; the OS releases it after a crash."""
    CYCLE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CYCLE_LOCK_PATH.open("a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Online retraining cycle already running; skipping duplicate trigger")
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, data: Any) -> None:
    """Replace a JSON state file atomically so crashes cannot leave partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, path)
        temp_path = None

        # Persist the directory entry as well as the file contents on POSIX.
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _default_registry(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "retrain_interval_hours": 24,
        "min_new_candles": 48,
        "max_drift_psi": 0.25,
        "registered_models": {},
    }


def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return _default_registry()

    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.error("Online retraining registry is unreadable; disabling retraining until repaired")
        return _default_registry(enabled=False)

    if not isinstance(registry, dict):
        logger.error("Online retraining registry must be a JSON object; disabling retraining")
        return _default_registry(enabled=False)

    enabled = registry.get("enabled", True)
    models = registry.get("registered_models", {})
    if not isinstance(enabled, bool) or not isinstance(models, dict):
        logger.error("Online retraining registry has invalid control fields; disabling retraining")
        return _default_registry(enabled=False)

    interval = registry.get("retrain_interval_hours", 24)
    min_candles = registry.get("min_new_candles", 48)
    max_drift = registry.get("max_drift_psi", 0.25)
    valid_interval = (
        isinstance(interval, (int, float))
        and not isinstance(interval, bool)
        and math.isfinite(interval)
        and interval >= 0
    )
    valid_min_candles = (
        isinstance(min_candles, int)
        and not isinstance(min_candles, bool)
        and min_candles >= 0
    )
    valid_max_drift = (
        isinstance(max_drift, (int, float))
        and not isinstance(max_drift, bool)
        and math.isfinite(max_drift)
        and max_drift >= 0
    )
    if not (valid_interval and valid_min_candles and valid_max_drift):
        logger.error("Online retraining registry contains invalid policy values; disabling retraining")
        return _default_registry(enabled=False)

    required_model_fields = ("symbol", "market", "timeframe", "strategy_name")
    for key, model in models.items():
        if (
            not isinstance(key, str)
            or not isinstance(model, dict)
            or any(not isinstance(model.get(field), str) or not model[field].strip()
                   for field in required_model_fields)
        ):
            logger.error("Online retraining registry contains an invalid model entry; disabling retraining")
            return _default_registry(enabled=False)

        # Validate optional runtime fields before scheduling arithmetic or
        # experiment dispatch can consume them. Missing fields remain valid
        # for older registry entries.
        last_candle_count = model.get("last_candle_count", 0)
        if (
            not isinstance(last_candle_count, int)
            or isinstance(last_candle_count, bool)
            or last_candle_count < 0
        ):
            logger.error("Online retraining registry contains an invalid candle checkpoint; disabling retraining")
            return _default_registry(enabled=False)

        last_retrained_at = model.get("last_retrained_at")
        if last_retrained_at is not None:
            if not isinstance(last_retrained_at, str):
                logger.error("Online retraining registry contains an invalid retraining timestamp; disabling retraining")
                return _default_registry(enabled=False)
            try:
                datetime.fromisoformat(last_retrained_at.replace("Z", "+00:00"))
            except ValueError:
                logger.error("Online retraining registry contains an invalid retraining timestamp; disabling retraining")
                return _default_registry(enabled=False)

        if "auto_promote" in model and not isinstance(model["auto_promote"], bool):
            logger.error("Online retraining registry contains an invalid promotion flag; disabling retraining")
            return _default_registry(enabled=False)

        model_type = model.get("model_type")
        if model_type is not None and (
            not isinstance(model_type, str) or not model_type.strip()
        ):
            logger.error("Online retraining registry contains an invalid model type; disabling retraining")
            return _default_registry(enabled=False)

    # Keep historical registries compatible while ensuring their control
    # values have safe types before they reach scheduling arithmetic.
    registry["enabled"] = enabled
    registry.setdefault("retrain_interval_hours", 24)
    registry.setdefault("min_new_candles", 48)
    registry.setdefault("max_drift_psi", 0.25)
    registry["registered_models"] = models
    return registry


def _save_registry(data: dict[str, Any]) -> None:
    _atomic_write_json(REGISTRY_PATH, data)


@contextmanager
def _audit_log_lock() -> Iterator[None]:
    """Serialize audit-log read/modify/write operations across processes."""
    AUDIT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK_PATH.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _append_audit_log(entry: dict[str, Any]) -> bool:
    """Append without losing concurrent entries or destroying unreadable history."""
    with _audit_log_lock():
        history: list[dict[str, Any]] = []
        if AUDIT_LOG_PATH.exists():
            try:
                loaded = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                logger.error("Retraining audit log is unreadable; preserving it without appending")
                return False
            if not isinstance(loaded, list) or any(not isinstance(item, dict) for item in loaded):
                logger.error("Retraining audit log has an invalid structure; preserving it without appending")
                return False
            history = loaded

        run_id = entry.get("run_id")
        if run_id is not None and any(item.get("run_id") == run_id for item in history):
            return True
        history.insert(0, entry)
        _atomic_write_json(AUDIT_LOG_PATH, history[:200])
        return True


def register_model_for_retraining(
    symbol: str,
    market: str = "spot",
    timeframe: str = "1h",
    strategy_name: str = "trend_ema_v1",
    model_type: str = "hist_gradient_boosting",
    auto_promote: bool = True,
) -> dict[str, Any]:
    """Register a strategy model pipeline to be tracked and periodically retrained."""
    key = f"{symbol}_{market}_{timeframe}_{strategy_name}"
    now_iso = datetime.now(timezone.utc).isoformat()

    with _registry_lock():
        reg = _load_registry()
        model = {
            "key": key,
            "symbol": symbol,
            "market": market,
            "timeframe": timeframe,
            "strategy_name": strategy_name,
            "model_type": model_type,
            "auto_promote": auto_promote,
            "registered_at": now_iso,
            "last_retrained_at": None,
            "last_candle_count": 0,
            "drift_psi": 0.0,
            "status": "active",
        }
        reg["registered_models"][key] = model
        _save_registry(reg)
        return model


def _persist_retraining_updates(updates: Mapping[str, Mapping[str, Any]]) -> bool:
    """Merge cycle results into the latest registry; return False if state is disabled/invalid."""
    if not updates:
        return True

    identity_fields = ("symbol", "market", "timeframe", "strategy_name", "registered_at")
    runtime_fields = ("last_retrained_at", "last_candle_count", "last_result")
    with _registry_lock():
        latest = _load_registry()
        # A malformed/disabled registry is a hard stop; never replace it with
        # the cycle's older, previously valid snapshot.
        if latest.get("enabled", True) is not True:
            logger.error("Registry changed to disabled/invalid during retraining; retaining recovery journal")
            return False

        latest_models = latest.get("registered_models", {})
        changed = False
        for key, update in updates.items():
            current = latest_models.get(key)
            if not isinstance(current, dict):
                continue
            expected_identity = update.get("identity", {})
            if any(current.get(field) != expected_identity.get(field) for field in identity_fields):
                continue
            runtime = update.get("runtime", {})
            for field in runtime_fields:
                if field in runtime:
                    current[field] = runtime[field]
                    changed = True

        if changed:
            _save_registry(latest)
        return True


@contextmanager
def _journal_lock() -> Iterator[None]:
    """Serialize recovery-journal updates across processes."""
    JOURNAL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_LOCK_PATH.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_retraining_journal() -> dict[str, dict[str, Any]]:
    with _journal_lock():
        if not RETRAINING_JOURNAL_PATH.exists():
            return {}
        try:
            data = json.loads(RETRAINING_JOURNAL_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Retraining recovery journal is unreadable; refusing new cycles") from exc
        if not isinstance(data, dict) or any(
            not isinstance(run_id, str) or not isinstance(entry, dict)
            or not isinstance(entry.get("update"), dict)
            or not isinstance(entry.get("audit"), dict)
            for run_id, entry in data.items()
        ):
            raise RuntimeError("Retraining recovery journal has an invalid structure; refusing new cycles")
        return data


def _save_retraining_journal_entry(run_id: str, entry: dict[str, Any]) -> None:
    with _journal_lock():
        current: dict[str, Any] = {}
        if RETRAINING_JOURNAL_PATH.exists():
            try:
                loaded = json.loads(RETRAINING_JOURNAL_PATH.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Retraining recovery journal is unreadable; refusing to overwrite it") from exc
            if not isinstance(loaded, dict):
                raise RuntimeError("Retraining recovery journal has an invalid structure; refusing to overwrite it")
            current = loaded
        current[run_id] = entry
        _atomic_write_json(RETRAINING_JOURNAL_PATH, current)


def _clear_retraining_journal_entry(run_id: str) -> None:
    with _journal_lock():
        if not RETRAINING_JOURNAL_PATH.exists():
            return
        try:
            current = json.loads(RETRAINING_JOURNAL_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Retraining recovery journal is unreadable; refusing to clear it") from exc
        if not isinstance(current, dict):
            raise RuntimeError("Retraining recovery journal has an invalid structure; refusing to clear it")
        current.pop(run_id, None)
        if current:
            _atomic_write_json(RETRAINING_JOURNAL_PATH, current)
        else:
            RETRAINING_JOURNAL_PATH.unlink(missing_ok=True)


def _recover_completed_retraining() -> None:
    """Finish registry/audit commits for experiments durably journaled as completed."""
    journal = _load_retraining_journal()
    for run_id, entry in journal.items():
        audit = dict(entry["audit"])
        audit["run_id"] = run_id
        if not _persist_retraining_updates({entry["key"]: entry["update"]}):
            logger.error("Retaining completed retraining journal entry %s while registry is disabled", run_id)
            continue
        if not _append_audit_log(audit):
            logger.error("Retaining completed retraining journal entry %s because audit history is unavailable", run_id)
            continue
        _clear_retraining_journal_entry(run_id)


def _load_experiment_result(result: Any) -> Mapping[str, Any]:
    """Load the persisted experiment payload returned by run_real_experiment.

    The experiment runner's public contract is a result-file Path. Accept a
    mapping as well for older integrations and focused unit-test doubles.
    """
    if isinstance(result, Mapping):
        payload = result
    elif isinstance(result, (str, os.PathLike)):
        result_path = Path(result)
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to load experiment result {result_path}") from exc
    else:
        raise TypeError("Experiment runner must return a result mapping or JSON file path")

    if not isinstance(payload, Mapping):
        raise ValueError("Experiment result must be a JSON object")
    winner = payload.get("winner")
    experiment = payload.get("experiment")
    if not isinstance(winner, Mapping) or not isinstance(winner.get("model_type"), str):
        raise ValueError("Experiment result is missing winner.model_type")
    if not isinstance(experiment, Mapping):
        raise ValueError("Experiment result is missing experiment details")
    if not isinstance(experiment.get("threshold"), (int, float)):
        raise ValueError("Experiment result is missing a numeric threshold")

    # Current execution-tournament results group baseline metrics by segment,
    # while older integrations used a flat baseline_test_metrics field.
    # Normalize both contracts so the retrainer consumes the actual test slice.
    baseline_test_metrics = experiment.get("baseline_test_metrics")
    if not isinstance(baseline_test_metrics, Mapping):
        baseline_metrics = experiment.get("baseline_metrics")
        if isinstance(baseline_metrics, Mapping):
            baseline_test_metrics = baseline_metrics.get("test")
    if not isinstance(baseline_test_metrics, Mapping):
        raise ValueError("Experiment result is missing baseline test metrics")
    if not isinstance(experiment.get("ml_test_metrics"), Mapping):
        raise ValueError("Experiment result is missing ml_test_metrics")

    normalized_experiment = dict(experiment)
    normalized_experiment["baseline_test_metrics"] = baseline_test_metrics
    normalized_payload = dict(payload)
    normalized_payload["experiment"] = normalized_experiment
    return normalized_payload

def _verify_persisted_model_artifact(model_id: str, root: str | Path | None = None) -> tuple[Path, Path]:
    """Require a complete, digest-consistent model artifact before checkpointing retraining."""
    from ml.artifacts import CURRENT_EVALUATION_PROTOCOL_VERSION, resolve_model_artifact_paths

    artifact_root = MODEL_ARTIFACTS_DIR if root is None else Path(root)
    try:
        manifest_path, weights_path = resolve_model_artifact_paths(model_id, artifact_root)
        manifest_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        manifest_descriptor = os.open(manifest_path, manifest_flags)
        with os.fdopen(manifest_descriptor, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except Exception as exc:
        raise RuntimeError(f"Retrained model artifact is missing or invalid: {model_id}") from exc

    if not isinstance(manifest, dict) or manifest.get("model_id") != model_id:
        raise RuntimeError(f"Retrained model artifact manifest ID mismatch: {model_id}")
    if manifest.get("evaluation_protocol_version") != CURRENT_EVALUATION_PROTOCOL_VERSION:
        raise RuntimeError(f"Retrained model artifact uses an obsolete evaluation protocol: {model_id}")
    expected_digest = manifest.get("weights_sha256")
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(char not in "0123456789abcdef" for char in expected_digest)
    ):
        raise RuntimeError(f"Retrained model artifact has an invalid SHA-256 digest: {model_id}")

    try:
        weights_descriptor = os.open(weights_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise RuntimeError(f"Retrained model weights could not be opened safely: {model_id}") from exc
    digest = hashlib.sha256()
    with os.fdopen(weights_descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise RuntimeError(f"Retrained model weights are not a regular file: {model_id}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise RuntimeError(f"Retrained model artifact SHA-256 verification failed: {model_id}")
    return manifest_path, weights_path


def _public_audit_entry(entry: Any) -> dict[str, Any]:
    """Expose only documented audit fields; never return persisted exception text."""
    if not isinstance(entry, dict):
        return {}

    safe_fields = (
        "key",
        "timestamp",
        "new_candles_processed",
        "winner_model",
        "threshold",
        "baseline_test_r",
        "ml_test_r",
        "status",
        "auto_promote_requested",
        "auto_promote_applied",
        "promotion_status",
    )
    public_entry = {field: entry[field] for field in safe_fields if field in entry}
    if public_entry.get("status") == "failed":
        public_entry["message"] = "Retraining failed; check protected server logs."
    return public_entry


def get_retraining_status() -> dict[str, Any]:
    """Return status of the continuous online retraining subsystem."""
    reg = _load_registry()
    audit_history = []
    if AUDIT_LOG_PATH.exists():
        try:
            audit_history = [
                _public_audit_entry(entry)
                for entry in json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))[:10]
            ]
        except Exception:
            pass

    return {
        "enabled": reg.get("enabled", True),
        "retrain_interval_hours": reg.get("retrain_interval_hours", 24),
        "min_new_candles": reg.get("min_new_candles", 48),
        "max_drift_psi": reg.get("max_drift_psi", 0.25),
        "registered_models_count": len(reg.get("registered_models", {})),
        "models": list(reg.get("registered_models", {}).values()),
        "recent_audit": audit_history,
    }


def _is_retraining_due(
    cfg: Mapping[str, Any],
    registry: Mapping[str, Any],
    current_candles: int,
    now: datetime,
    force: bool = False,
) -> bool:
    """Apply the configured candle and minimum-interval gates to a model."""
    if force:
        return True

    last_retrained_at = cfg.get("last_retrained_at")
    if last_retrained_at is None:
        return True
    if not isinstance(last_retrained_at, str):
        return False

    try:
        last_run = datetime.fromisoformat(last_retrained_at.replace("Z", "+00:00"))
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        elapsed = now - last_run.astimezone(timezone.utc)
    except (TypeError, ValueError):
        logger.warning("Skipping retraining with invalid last_retrained_at value")
        return False

    interval_hours = max(0.0, float(registry.get("retrain_interval_hours", 24)))
    min_new_candles = max(0, int(registry.get("min_new_candles", 48)))
    new_candles = current_candles - int(cfg.get("last_candle_count", 0))
    return elapsed >= timedelta(hours=interval_hours) and new_candles >= min_new_candles


def execute_retraining_cycle(force: bool = False) -> list[dict[str, Any]]:
    """Run at most one retraining cycle across all workers/processes."""
    with _retraining_cycle_lock() as acquired:
        if not acquired:
            return []
        return _execute_retraining_cycle_unlocked(force=force)


def _execute_retraining_cycle_unlocked(force: bool = False) -> list[dict[str, Any]]:
    """
    Check all registered models; if stale or forced, run retraining pipeline.
    FreqAI/Qlib continuous inference cycle. Caller must hold the cycle lock.
    """
    _recover_completed_retraining()
    reg = _load_registry()
    models = reg.get("registered_models", {})
    results = []

    # A disabled registry is a hard stop; force only bypasses per-model due gates.
    if reg.get("enabled", True) is not True:
        logger.info("Online retraining is disabled; skipping cycle")
        return results

    for key, cfg in models.items():
        journaled = False
        symbol = cfg["symbol"]
        market = cfg["market"]
        timeframe = cfg["timeframe"]
        strategy_name = cfg["strategy_name"]
        model_type = cfg.get("model_type", "hist_gradient_boosting")

        try:
            df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True)
            current_candles = len(df)
            last_candles = cfg.get("last_candle_count", 0)
            new_candles = current_candles - last_candles

            needs_retrain = _is_retraining_due(
                cfg, reg, current_candles, datetime.now(timezone.utc), force=force
            )
            if not needs_retrain:
                continue

            logger.info("Executing continuous retraining for %s (new candles: %d)", key, new_candles)
            exp_result_path = run_real_experiment(
                symbol=symbol,
                market=market,
                timeframe=timeframe,
                strategy_name=strategy_name,
                model_type=model_type,
            )
            exp_res = _load_experiment_result(exp_result_path)
            experiment_id = exp_res["experiment"].get("experiment_id")
            if not isinstance(experiment_id, str) or not experiment_id:
                raise RuntimeError("Retraining result is missing its experiment_id")
            _verify_persisted_model_artifact(experiment_id)

            now_iso = datetime.now(timezone.utc).isoformat()
            run_id = uuid.uuid4().hex
            audit_entry = {
                "run_id": run_id,
                "key": key,
                "timestamp": now_iso,
                "new_candles_processed": new_candles,
                "winner_model": exp_res["winner"]["model_type"],
                "threshold": exp_res["experiment"]["threshold"],
                "baseline_test_r": exp_res["experiment"]["baseline_test_metrics"].get("total_outcome_r"),
                "ml_test_r": exp_res["experiment"]["ml_test_metrics"].get("total_outcome_r"),
                "status": "retrained",
                "auto_promote_requested": cfg.get("auto_promote") is True,
                "auto_promote_applied": False,
                "promotion_status": "not_implemented" if cfg.get("auto_promote") is True else "disabled",
            }
            update = {
                "identity": {
                    field: cfg.get(field)
                    for field in ("symbol", "market", "timeframe", "strategy_name", "registered_at")
                },
                "runtime": {
                    "last_retrained_at": now_iso,
                    "last_candle_count": current_candles,
                    "last_result": {
                        "winner": exp_res["winner"]["model_type"],
                        "ml_test_r": exp_res["experiment"]["ml_test_metrics"].get("total_outcome_r"),
                        "threshold": exp_res["experiment"]["threshold"],
                    },
                },
            }
            # Persist the completed result before touching the registry or audit.
            # A crash after this point is recoverable without rerunning the experiment.
            _save_retraining_journal_entry(run_id, {
                "key": key,
                "update": update,
                "audit": audit_entry,
            })
            journaled = True
            if not _persist_retraining_updates({key: update}):
                continue
            if not _append_audit_log(audit_entry):
                logger.error("Completed retraining for %s is journaled pending audit recovery", key)
                continue
            _clear_retraining_journal_entry(run_id)
            results.append(audit_entry)

        except Exception as exc:
            logger.warning("Error retraining model %s: %s", key, exc)
            if journaled:
                logger.error("Completed retraining for %s remains in recovery journal", key)
                continue
            _append_audit_log({
                "key": key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "status": "failed",
            })

    return results
