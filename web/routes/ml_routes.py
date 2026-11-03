"""
API Router for Machine Learning Strategy Tournaments & Forecasting.
Connects the ML research pipeline with the Web Dashboard and Paper Trading Engine.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, field_validator, model_validator

from ml.artifacts import (
    CURRENT_EVALUATION_PROTOCOL_VERSION,
    paper_eligibility_report,
    read_model_manifest_file,
)
from ml.run_real_experiment import run_real_experiment
from ml.dataset import TARGET_TYPES
from ml.generator_artifacts import list_generator_artifacts
from backtest.strategies import STRATEGIES
from paper.engine import set_paper_config, sync_and_evaluate_paper_trading
from notifications.telegram_notifier import send_telegram_message

router = APIRouter(prefix="/api/ml", tags=["machine-learning"])
logger = logging.getLogger("web.ml_routes")

ML_RESULTS_DIR = Path("ml_results")
ML_MODELS_DIR = Path("ml_models")


VALID_ML_TIMEFRAMES = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}
SUPPORTED_ML_MODELS = {"logistic_regression", "random_forest", "hist_gradient_boosting"}
SUPPORTED_EXPERIMENT_MODELS = SUPPORTED_ML_MODELS | {"all", "tournament"}
SUPPORTED_RL_AGENTS = {"dqn", "policy_gradient"}
SUPPORTED_RL_REWARDS = {"pnl", "sharpe", "sortino"}


class MLBaseRequest(BaseModel):
    symbol: str = Field("BTCUSDT", min_length=1, max_length=24)
    market: str = Field("spot", min_length=1, max_length=10)
    timeframe: str = Field("1h", min_length=1, max_length=5)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,20}", normalized):
            raise ValueError("symbol must contain 2-20 ASCII letters or digits")
        return normalized

    @field_validator("market")
    @classmethod
    def validate_market(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"spot", "futures"}:
            raise ValueError("market must be spot or futures")
        return normalized

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in VALID_ML_TIMEFRAMES:
            raise ValueError("timeframe is unsupported")
        return normalized


class MLStrategyRequest(MLBaseRequest):
    strategy_name: str = Field("trend_ema_v1", min_length=1, max_length=50)

    @field_validator("strategy_name")
    @classmethod
    def validate_strategy_name(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in STRATEGIES:
            raise ValueError("strategy_name must identify a registered strategy")
        return normalized


class RunExperimentRequest(MLStrategyRequest):
    model_type: str = Field("all", min_length=1, max_length=30)
    target_type: str = Field("binary_positive_r", min_length=1, max_length=40)
    min_validation_trades: int = Field(5, ge=1, le=100)

    @field_validator("model_type")
    @classmethod
    def validate_model_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_EXPERIMENT_MODELS:
            raise ValueError("model_type is unsupported")
        return normalized

    @field_validator("target_type")
    @classmethod
    def validate_target_type(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in TARGET_TYPES:
            raise ValueError("target_type is unsupported")
        return normalized


class RunGeneratorExperimentRequest(MLBaseRequest):
    """Parameters for the independent signal-generator research pipeline."""
    model_type: str = Field("hist_gradient_boosting", min_length=1, max_length=30)
    horizon_bars: int = Field(12, ge=2, le=200)
    barrier_atr_multiple: float = Field(1.5, gt=0, le=20)
    fee_rate: float = Field(0.001, ge=0, lt=0.1)
    slippage_rate: float = Field(0.0005, ge=0, lt=0.1)
    min_net_return: float = Field(0.0, ge=0, le=1)
    min_validation_trades: int = Field(10, ge=1, le=500)
    train_fraction: float = Field(0.60, gt=0.1, lt=0.9)
    validation_fraction: float = Field(0.20, gt=0.05, lt=0.5)
    stop_atr_multiple: float = Field(1.5, gt=0, le=20)
    risk_reward_ratio: float = Field(2.0, gt=0, le=20)
    max_hold_bars: int = Field(72, ge=1, le=1000)
    seed: int = Field(42, ge=0, le=2147483647)

    @field_validator("model_type")
    @classmethod
    def validate_model_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_ML_MODELS:
            raise ValueError("model_type is unsupported")
        return normalized

    @field_validator(
        "barrier_atr_multiple", "fee_rate", "slippage_rate", "min_net_return",
        "train_fraction", "validation_fraction", "stop_atr_multiple", "risk_reward_ratio",
    )
    @classmethod
    def validate_finite_generator_parameters(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("generator numeric parameters must be finite")
        return value

    @model_validator(mode="after")
    def validate_split_fractions(self):
        if self.train_fraction + self.validation_fraction >= 1.0:
            raise ValueError("train_fraction + validation_fraction must be less than one")
        return self




_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_EXPERIMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


def _normalize_experiment_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Expose stable dashboard aliases for current and legacy experiment files."""
    if not isinstance(data, dict):
        raise ValueError("Experiment result must be a JSON object")
    normalized = dict(data)
    experiment = normalized.get("experiment")
    if not isinstance(experiment, dict):
        return normalized

    experiment = dict(experiment)
    baseline_test = experiment.get("baseline_test_metrics")
    if not isinstance(baseline_test, dict):
        baseline = experiment.get("baseline_metrics")
        baseline_test = baseline.get("test") if isinstance(baseline, dict) else {}
    experiment["baseline_test_metrics"] = (
        baseline_test if isinstance(baseline_test, dict) else {}
    )

    diagnostics = experiment.get("probability_diagnostics")
    if not isinstance(diagnostics, dict):
        validation = experiment.get("ml_validation_metrics")
        diagnostics = (
            validation.get("probability_diagnostics")
            if isinstance(validation, dict) else {}
        )
    experiment["probability_diagnostics"] = (
        diagnostics if isinstance(diagnostics, dict) else {}
    )

    if not experiment.get("market") and isinstance(normalized.get("market"), str):
        experiment["market"] = normalized["market"]
    if not normalized.get("experiment_id") and isinstance(experiment.get("experiment_id"), str):
        normalized["experiment_id"] = experiment["experiment_id"]
    normalized["experiment"] = experiment
    return normalized


class DeployMLToPaperRequest(BaseModel):
    # Restricted charset: the id is used to build a filesystem path.
    model_id: str = Field(..., max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    allocated_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    send_telegram: bool = Field(True)


@router.get("/experiments")
def list_experiments() -> Dict[str, Any]:
    """List completed ML tournaments without following external JSON symlinks."""
    results_root = ML_RESULTS_DIR.resolve()
    if not results_root.is_dir():
        return {"experiments": []}

    safe_result_files = []
    for candidate in results_root.glob("*.json"):
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.parent != results_root or not resolved.is_file():
                logger.warning("Skipping experiment file outside results directory: %s", candidate.name)
                continue
            safe_result_files.append(resolved)
        except (OSError, RuntimeError):
            logger.warning("Skipping unresolved experiment file: %s", candidate.name)

    results: List[Dict[str, Any]] = []
    for file in sorted(safe_result_files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = _normalize_experiment_payload(json.loads(file.read_text(encoding="utf-8")))
            exp = data.get("experiment", {})
            winner = data.get("winner", {})
            ml_test = exp.get("ml_test_metrics", {})
            base_test = exp.get("baseline_test_metrics", {})
            diagnostics = exp.get("probability_diagnostics", {})

            summary = {
                "experiment_id": data.get("experiment_id", file.stem),
                "symbol": exp.get("symbol", "N/A"),
                "market": exp.get("market", "N/A"),
                "timeframe": exp.get("timeframe", "N/A"),
                "strategy": exp.get("base_strategy", "N/A"),
                "winner_model": winner.get("model_type", "N/A"),
                "threshold": exp.get("threshold", 0.5),
                "roc_auc": diagnostics.get("roc_auc"),
                "brier_score": diagnostics.get("brier_score"),
                "baseline_test_r": base_test.get("total_outcome_r", 0.0),
                "baseline_test_win_rate": base_test.get("win_rate_pct", 0.0),
                "ml_test_r": ml_test.get("total_outcome_r", 0.0),
                "ml_test_win_rate": ml_test.get("win_rate_pct", 0.0),
                "ml_test_trades": ml_test.get("trade_count", 0),
                "dataset_rows": data.get("dataset_rows", 0),
                "file_mtime": file.stat().st_mtime,
            }
            results.append(summary)
        except Exception as exc:
            logger.warning("Error reading experiment file %s (%s)", file.name, type(exc).__name__)

    return {"experiments": results}


@router.get("/experiments/{experiment_id}")
def get_experiment_details(experiment_id: str) -> Dict[str, Any]:
    """Retrieve the full JSON result for an ML tournament."""
    if not _EXPERIMENT_ID_RE.fullmatch(experiment_id) or ".." in experiment_id:
        raise HTTPException(status_code=422, detail="Invalid experiment_id")
    results_root = ML_RESULTS_DIR.resolve()
    file_path = (results_root / f"{experiment_id}.json").resolve()
    if file_path.parent != results_root:
        raise HTTPException(status_code=422, detail="Invalid experiment_id path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")

    try:
        data = _normalize_experiment_payload(json.loads(file_path.read_text(encoding="utf-8")))
        return data
    except Exception as exc:
        logger.warning("Failed to parse experiment data %s (%s)", file_path.name, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to parse experiment data")


@router.get("/models")
def list_saved_models() -> Dict[str, Any]:
    """List trained model artifacts eligible for paper trading."""
    models_root = ML_MODELS_DIR.resolve()
    if not models_root.is_dir():
        return {"models": []}

    safe_manifest_files = []
    for candidate in models_root.glob("*.json"):
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.parent != models_root or not resolved.is_file():
                logger.warning("Skipping model manifest outside model directory: %s", candidate.name)
                continue
            safe_manifest_files.append(resolved)
        except (OSError, RuntimeError):
            logger.warning("Skipping unresolved model manifest: %s", candidate.name)

    models: List[Dict[str, Any]] = []
    for safe_manifest in sorted(safe_manifest_files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = read_model_manifest_file(safe_manifest)
            if data.get("task_type") == "signal_generator_v1":
                continue
            joblib_file = (models_root / f"{safe_manifest.stem}.joblib").resolve()
            data["has_weights"] = joblib_file.parent == models_root and joblib_file.is_file()
            report = paper_eligibility_report(test_metrics=data.get("test_metrics") or {})
            reasons = list(report["reasons"])
            if not data.get("eligible_for_paper"):
                reasons.append("manifest is not flagged eligible_for_paper")
            if data.get("evaluation_protocol_version") != CURRENT_EVALUATION_PROTOCOL_VERSION:
                reasons.append("model was not evaluated with the current purged exit-time protocol")
            if not data["has_weights"]:
                reasons.append("model weights (.joblib) are missing")
            data["eligibility"] = {"eligible": not reasons, "reasons": reasons}
            models.append(data)
        except Exception as exc:
            logger.warning("Failed to parse model manifest %s (%s)", safe_manifest.name, type(exc).__name__)

    return {"models": models}


@router.get("/generator-models")
def list_saved_generator_models() -> Dict[str, Any]:
    """List only typed independent generator artifacts for the paper selector."""
    try:
        return {"models": list_generator_artifacts(ML_MODELS_DIR)}
    except Exception as exc:
        logger.error("Unable to list generator models (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unable to list ML generator models")


@router.post("/run-experiment")
def trigger_experiment(req: RunExperimentRequest):
    """Executes an execution-aware ML tournament synchronously or in background."""
    try:
        result_path = run_real_experiment(
            symbol=req.symbol.strip().upper(),
            market=req.market.strip().lower(),
            timeframe=req.timeframe.strip(),
            strategy_name=req.strategy_name.strip(),
            model_type=req.model_type.strip().lower(),
            target_type=req.target_type.strip(),
            min_validation_trades=req.min_validation_trades,
        )
        data = json.loads(result_path.read_text(encoding="utf-8"))
        return {
            "status": "success",
            "experiment_id": data.get("experiment_id"),
            "result": data,
        }
    except Exception as exc:
        logger.error("ML experiment execution failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="ML experiment execution failed; check server logs for diagnostics")


@router.post("/generator/run-experiment")
def trigger_generator_experiment(req: RunGeneratorExperimentRequest):
    """Train one independent generator and persist its typed research artifact.

    This endpoint intentionally does not activate paper trading or send
    Telegram notifications. It is separate from the legacy filter tournament.
    """
    try:
        from db.db import fetch_ohlcv_with_features_df
        from ml.dataset import SHARED_FEATURE_COLUMNS
        from ml.generator_experiment import run_generator_experiment

        feature_frame = fetch_ohlcv_with_features_df(
            req.symbol, req.market, req.timeframe, closed_only=True
        )
        if feature_frame is None or len(feature_frame) < 100:
            raise HTTPException(
                status_code=400,
                detail="At least 100 closed candles with shared features are required for generator research.",
            )

        label_config = {
            "horizon_bars": req.horizon_bars,
            "barrier_atr_multiple": req.barrier_atr_multiple,
            "fee_rate": req.fee_rate,
            "slippage_rate": req.slippage_rate,
            "min_net_return": req.min_net_return,
        }
        strategy_params = {
            "symbol": req.symbol,
            "market": req.market,
            "timeframe": req.timeframe,
            "ML_GENERATOR_STOP_ATR_MULT": req.stop_atr_multiple,
            "ML_GENERATOR_RISK_REWARD_RATIO": req.risk_reward_ratio,
            "BACKTEST_FEE_PCT": req.fee_rate,
            "BACKTEST_SLIPPAGE_PCT": req.slippage_rate,
            "BACKTEST_MAX_HOLD_BARS": req.max_hold_bars,
        }
        result = run_generator_experiment(
            feature_frame,
            model_type=req.model_type,
            feature_columns=SHARED_FEATURE_COLUMNS,
            label_config=label_config,
            strategy_params=strategy_params,
            train_fraction=req.train_fraction,
            validation_fraction=req.validation_fraction,
            min_validation_trades=req.min_validation_trades,
            seed=req.seed,
            artifact_dir=ML_MODELS_DIR,
        )
        artifact = result.artifact
        return {
            "status": "success",
            "model_id": artifact.model_id,
            "task_type": artifact.task_type,
            "eligible_for_paper": artifact.eligible_for_paper,
            "decision_policy": artifact.decision_policy,
            "dataset_rows": result.dataset_rows,
            "train_rows": result.train_rows,
            "validation_rows": result.validation_rows,
            "test_rows": result.test_rows,
            "validation_metrics": result.validation_metrics,
            "test_metrics": result.test_metrics,
            "message": (
                "Generator model saved and eligible for paper review."
                if artifact.eligible_for_paper
                else "Generator model saved as research-only; untouched canonical test did not pass paper eligibility."
            ),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        logger.info("Generator experiment rejected (%s)", type(exc).__name__)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("Generator experiment failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Generator experiment failed; check server logs for diagnostics",
        )


@router.post("/deploy-to-paper")
def deploy_ml_model_to_paper(req: DeployMLToPaperRequest):
    """Deploys a trained ML Model as an active filter in Live Paper Trading."""
    if not _MODEL_ID_RE.match(req.model_id) or ".." in req.model_id:
        raise HTTPException(status_code=422, detail="Invalid model_id")
    models_root = ML_MODELS_DIR.resolve()
    manifest_path = (models_root / f"{req.model_id}.json").resolve()
    if manifest_path.parent != models_root:
        raise HTTPException(status_code=409, detail="Model manifest must resolve inside ml_models/")
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail=f"ML model manifest '{req.model_id}' not found in ml_models/")

    try:
        manifest = read_model_manifest_file(manifest_path)
    except Exception as exc:
        logger.warning("Failed to parse model manifest %s (%s)", manifest_path.name, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to parse model manifest")

    if manifest.get("model_id") != req.model_id:
        raise HTTPException(status_code=409, detail="Model manifest ID does not match the requested model ID")

    # Promotion gate: re-evaluate from the stored untouched-test metrics instead of
    # trusting the manifest flag alone (manifests are plain, editable JSON files).
    report = paper_eligibility_report(test_metrics=manifest.get("test_metrics") or {})
    if manifest.get("evaluation_protocol_version") != CURRENT_EVALUATION_PROTOCOL_VERSION:
        raise HTTPException(
            status_code=409,
            detail="Model must be re-evaluated with the current purged exit-time protocol before paper deployment",
        )
    if not manifest.get("eligible_for_paper") or not report["eligible"]:
        reasons = report["reasons"] or ["manifest is not flagged eligible_for_paper"]
        raise HTTPException(
            status_code=409,
            detail="Model is not eligible for paper trading: " + "; ".join(reasons),
        )
    weights_path = (models_root / f"{req.model_id}.joblib").resolve()
    if weights_path.parent != models_root or not weights_path.is_file():
        raise HTTPException(status_code=409, detail="Model weights (.joblib) must be a regular file inside ml_models/; retrain before deploying")

    # Validate all manifest fields used by the activation and notification paths
    # before mutating paper configuration. Manifests are editable JSON files.
    strategy_params = manifest.get("strategy_params")
    strategy_name = manifest.get("base_strategy")
    try:
        threshold = float(manifest.get("threshold"))
    except (TypeError, ValueError, OverflowError):
        threshold = float("nan")
    if not math.isfinite(threshold) or not 0 < threshold < 1:
        raise HTTPException(status_code=409, detail="Model threshold must be a finite number strictly between 0 and 1")
    if not isinstance(strategy_params, dict):
        raise HTTPException(status_code=409, detail="Model strategy_params must be an object")
    if not isinstance(strategy_name, str) or strategy_name not in STRATEGIES:
        raise HTTPException(status_code=409, detail="Model base_strategy is missing or unsupported")
    symbol = strategy_params.get("symbol")
    market = strategy_params.get("market")
    timeframe = strategy_params.get("timeframe")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9]{2,30}", symbol):
        raise HTTPException(status_code=409, detail="Model symbol is missing or invalid")
    if not isinstance(market, str) or market not in {"spot", "futures"}:
        raise HTTPException(status_code=409, detail="Model market must be spot or futures")
    if not isinstance(timeframe, str) or timeframe not in {"15m", "1h", "4h", "1d"}:
        raise HTTPException(status_code=409, detail="Model timeframe is unsupported")

    try:
        # Update paper config with ml_model_id
        res = set_paper_config(
            symbol=symbol,
            market=market,
            timeframe=timeframe,
            strategy_name=strategy_name,
            is_active=True,
            allocated_capital=req.allocated_capital,
            risk_per_trade_pct=req.risk_per_trade_pct,
            ml_model_id=req.model_id,
            telegram_enabled=req.send_telegram,
            report_enabled=True,
        )

        telegram_status = None
        if req.send_telegram:
            msg = (
                f"🧠 <b>AI/ML STRATEGY DEPLOYED TO PAPER TRADING!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🪙 <b>Pair:</b> <code>{symbol}</code> ({market.upper()})\n"
                f"⏱ <b>Timeframe:</b> <code>{timeframe}</code>\n"
                f"📈 <b>Base Strategy:</b> <code>{strategy_name}</code>\n"
                f"🤖 <b>ML Filter Model:</b> <code>{manifest.get('model_type')}</code>\n"
                f"🎯 <b>Probability Threshold:</b> <code>{threshold * 100:.1f}%</code>\n"
                f"💼 <b>Capital:</b> ${req.allocated_capital:,.2f} | <b>Risk:</b> {req.risk_per_trade_pct:.1f}%\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Signals will only be executed if AI confidence exceeds threshold.</i>"
            )
            telegram_status = send_telegram_message(msg)

        sync_res = sync_and_evaluate_paper_trading()

        return {
            "status": "success",
            "message": f"ML Model '{req.model_id}' successfully activated for {symbol} ({timeframe}) {strategy_name}.",
            "config": res,
            "telegram": telegram_status,
            "sync": sync_res,
        }
    except Exception as exc:
        logger.error("Failed to deploy ML model to paper (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="ML model deployment failed; check server logs for diagnostics")


# =====================================================================
# Continuous Online Retraining & Walk-Forward Inference (FreqAI/Qlib)
# =====================================================================

class MLWalkForwardRequest(MLStrategyRequest):
    model_type: str = Field("hist_gradient_boosting", min_length=1, max_length=30)
    n_folds: int = Field(4, ge=2, le=10)
    expanding: bool = Field(False)

    @field_validator("model_type")
    @classmethod
    def validate_model_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_ML_MODELS:
            raise ValueError("model_type is unsupported")
        return normalized


class RegisterRetrainRequest(MLStrategyRequest):
    model_type: str = Field("hist_gradient_boosting", min_length=1, max_length=30)
    auto_promote: bool = Field(True)

    @field_validator("model_type")
    @classmethod
    def validate_model_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_EXPERIMENT_MODELS:
            raise ValueError("model_type is unsupported")
        return normalized




@router.post("/walk-forward")
def execute_ml_walk_forward(req: MLWalkForwardRequest):
    """Run rolling/expanding walk-forward ML evaluation with concept drift tracking."""
    from db.db import fetch_ohlcv_with_features_df
    from ml.dataset import build_signal_dataset, SHARED_FEATURE_COLUMNS
    from ml.walk_forward_engine import run_ml_walk_forward

    df = fetch_ohlcv_with_features_df(req.symbol, req.market, req.timeframe, closed_only=True)
    if len(df) < 100:
        raise HTTPException(status_code=400, detail="Insufficient candles for ML walk-forward")

    strat_fn = STRATEGIES.get(req.strategy_name)
    if not strat_fn:
        raise HTTPException(status_code=400, detail=f"Strategy '{req.strategy_name}' not found")

    trades = strat_fn(df, {"symbol": req.symbol, "market": req.market, "timeframe": req.timeframe})
    dataset = build_signal_dataset(df, trades)
    if len(dataset) < 40:
        raise HTTPException(status_code=400, detail=f"Strategy produced only {len(dataset)} signals, need >= 40 for walk-forward")

    try:
        result = run_ml_walk_forward(
            dataset=dataset,
            feature_columns=SHARED_FEATURE_COLUMNS,
            model_type=req.model_type,
            n_folds=req.n_folds,
            expanding=req.expanding,
        )
        return {
            "symbol": req.symbol,
            "market": req.market,
            "timeframe": req.timeframe,
            "strategy": req.strategy_name,
            **result,
        }
    except Exception as exc:
        logger.exception("ML walk-forward execution failed")
        raise HTTPException(status_code=500, detail="Walk-forward analysis failed; check server logs for diagnostics")


@router.get("/retraining/status")
def get_continuous_retraining_status():
    """Retrieve status, schedule, and drift audit of online continuous retraining."""
    from ml.online_retrainer import get_retraining_status
    return get_retraining_status()


@router.post("/retraining/register")
def register_retrain_model(req: RegisterRetrainRequest):
    """Register a strategy pipeline for continuous automated retraining."""
    from ml.online_retrainer import register_model_for_retraining
    return register_model_for_retraining(
        symbol=req.symbol,
        market=req.market,
        timeframe=req.timeframe,
        strategy_name=req.strategy_name,
        model_type=req.model_type,
        auto_promote=req.auto_promote,
    )


@router.post("/retraining/trigger")
def trigger_continuous_retraining(background_tasks: BackgroundTasks, force: bool = False):
    """Trigger online retraining cycle across registered models."""
    from ml.online_retrainer import execute_retraining_cycle
    background_tasks.add_task(execute_retraining_cycle, force=force)
    return {
        "status": "queued",
        "message": "Continuous online retraining cycle started in background.",
    }


# =====================================================================
# Reinforcement Learning Trading Environment & Agents (FinRL-style)
# =====================================================================

class RLTrainRequest(MLBaseRequest):
    agent_type: str = Field("dqn", min_length=1, max_length=20)
    episodes: int = Field(10, ge=1, le=50)
    reward_type: str = Field("sharpe", min_length=1, max_length=20)

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_RL_AGENTS:
            raise ValueError("agent_type is unsupported")
        return normalized

    @field_validator("reward_type")
    @classmethod
    def validate_reward_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_RL_REWARDS:
            raise ValueError("reward_type is unsupported")
        return normalized


@router.post("/rl/train")
def train_rl_trading_agent(req: RLTrainRequest):
    """Train a reinforcement learning agent with FinRL-style discrete action space."""
    from ml.rl.runner import train_rl_model
    try:
        res = train_rl_model(
            symbol=req.symbol,
            market=req.market,
            timeframe=req.timeframe,
            agent_type=req.agent_type,
            episodes=req.episodes,
            reward_type=req.reward_type,
        )
        return res
    except Exception as exc:
        logger.exception("RL training failed")
        raise HTTPException(status_code=500, detail="RL training failed; check server logs for diagnostics")


@router.get("/rl/models")
def list_rl_models():
    """List trained RL models without following paths outside ml_models/rl."""
    models_root = ML_MODELS_DIR.resolve()
    requested_rl_dir = models_root / "rl"
    try:
        rl_dir = requested_rl_dir.resolve(strict=True)
    except OSError:
        return {"models": []}
    if rl_dir != requested_rl_dir or not rl_dir.is_dir():
        logger.warning("Skipping RL model listing because its artifact directory is not contained in ml_models/")
        return {"models": []}

    safe_audit_files = []
    for candidate in rl_dir.glob("*.json"):
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.parent != rl_dir or not resolved.is_file():
                logger.warning("Skipping RL model audit outside artifact directory: %s", candidate.name)
                continue
            safe_audit_files.append(resolved)
        except (OSError, RuntimeError):
            logger.warning("Skipping unresolved RL model audit file: %s", candidate.name)

    models = []
    for audit_file in sorted(safe_audit_files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(audit_file.read_text(encoding="utf-8"))
            models.append({
                "model_id": data.get("model_id"),
                "symbol": data.get("symbol"),
                "market": data.get("market"),
                "timeframe": data.get("timeframe"),
                "agent_type": data.get("agent_type"),
                "episodes": data.get("episodes"),
                "reward_type": data.get("reward_type"),
                "out_of_sample_metrics": data.get("out_of_sample_metrics"),
            })
        except Exception:
            continue
    return {"models": models}
