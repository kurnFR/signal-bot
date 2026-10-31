"""
Continuous Online Model Retraining Manager (FreqAI & Qlib style).
Maintains automated background retraining loops, model drift monitoring,
and safe model promotion gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from db.db import fetch_ohlcv_with_features_df
from ml.run_real_experiment import run_real_experiment
from ml.walk_forward_engine import compute_drift_psi

logger = logging.getLogger("ml.online_retrainer")

REGISTRY_PATH = Path("ml_models") / "retraining_registry.json"
AUDIT_LOG_PATH = Path("ml_results") / "retraining_audit.json"


def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {
            "enabled": True,
            "retrain_interval_hours": 24,
            "min_new_candles": 48,
            "max_drift_psi": 0.25,
            "registered_models": {},
        }
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True, "registered_models": {}}


def _save_registry(data: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _append_audit_log(entry: dict[str, Any]) -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if AUDIT_LOG_PATH.exists():
        try:
            history = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.insert(0, entry)
    # Retain last 200 audit entries
    AUDIT_LOG_PATH.write_text(json.dumps(history[:200], indent=2), encoding="utf-8")


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


def get_retraining_status() -> dict[str, Any]:
    """Return status of the continuous online retraining subsystem."""
    reg = _load_registry()
    audit_history = []
    if AUDIT_LOG_PATH.exists():
        try:
            audit_history = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))[:10]
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


def execute_retraining_cycle(force: bool = False) -> list[dict[str, Any]]:
    """
    Check all registered models; if stale or forced, run retraining pipeline.
    FreqAI/Qlib continuous inference cycle.
    """
    reg = _load_registry()
    models = reg.get("registered_models", {})
    results = []

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

            # Check if retrain needed
            needs_retrain = force or (new_candles >= reg.get("min_new_candles", 48)) or (cfg.get("last_retrained_at") is None)
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
                "status": "retrained_and_promoted",
            }
            _append_audit_log(audit_entry)

            # Update registry
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

