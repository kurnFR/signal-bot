"""
Continuous Online Model Retraining Manager (FreqAI & Qlib style).
Maintains automated background retraining loops, model drift monitoring,
and safe model promotion gates.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping

from db.db import fetch_ohlcv_with_features_df
from ml.run_real_experiment import run_real_experiment
from ml.walk_forward_engine import compute_drift_psi

logger = logging.getLogger("ml.online_retrainer")

REGISTRY_PATH = Path("ml_models") / "retraining_registry.json"
AUDIT_LOG_PATH = Path("ml_results") / "retraining_audit.json"
CYCLE_LOCK_PATH = Path("ml_models") / "retraining_cycle.lock"


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
    except (OSError, json.JSONDecodeError):
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


def _append_audit_log(entry: dict[str, Any]) -> None:
    history = []
    if AUDIT_LOG_PATH.exists():
        try:
            history = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.insert(0, entry)
    _atomic_write_json(AUDIT_LOG_PATH, history[:200])


def register_model_for_retraining(
    symbol: str,
    market: str = "spot",
    timeframe: str = "1h",
    strategy_name: str = "trend_ema_v1",
    model_type: str = "hist_gradient_boosting",
    auto_promote: bool = True,
) -> dict[str, Any]:
    """Register a strategy model pipeline to be tracked and periodically retrained."""
    reg = _load_registry()
    key = f"{symbol}_{market}_{timeframe}_{strategy_name}"
    now_iso = datetime.now(timezone.utc).isoformat()

    reg["registered_models"][key] = {
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
    _save_registry(reg)
    return reg["registered_models"][key]


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
    reg = _load_registry()
    models = reg.get("registered_models", {})
    results = []

    # A disabled registry is a hard stop; force only bypasses per-model due gates.
    if reg.get("enabled", True) is not True:
        logger.info("Online retraining is disabled; skipping cycle")
        return results

    for key, cfg in models.items():
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
            exp_res = run_real_experiment(
                symbol=symbol,
                market=market,
                timeframe=timeframe,
                strategy_name=strategy_name,
                model_type=model_type,
            )

            now_iso = datetime.now(timezone.utc).isoformat()
            audit_entry = {
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
            _append_audit_log(audit_entry)

            cfg["last_retrained_at"] = now_iso
            cfg["last_candle_count"] = current_candles
            cfg["last_result"] = {
                "winner": exp_res["winner"]["model_type"],
                "ml_test_r": exp_res["experiment"]["ml_test_metrics"].get("total_outcome_r"),
                "threshold": exp_res["experiment"]["threshold"],
            }
            results.append(audit_entry)

        except Exception as exc:
            logger.warning("Error retraining model %s: %s", key, exc)
            _append_audit_log({
                "key": key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "status": "failed",
            })

    _save_registry(reg)
    return results
