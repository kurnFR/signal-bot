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
from pydantic import BaseModel, Field, field_validator

from ml.artifacts import paper_eligibility_report
from ml.run_real_experiment import run_real_experiment
from ml.dataset import TARGET_TYPES
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
    timeframe: str = Field("1h", min_length=1, max_length=3)

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




_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_EXPERIMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


class DeployMLToPaperRequest(BaseModel):
    # Restricted charset: the id is used to build a filesystem path.
    model_id: str = Field(..., max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    allocated_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    send_telegram: bool = Field(True)


@router.get("/experiments")
def list_experiments() -> Dict[str, Any]:
    """List all completed ML tournaments and experiment results."""
    if not ML_RESULTS_DIR.exists():
        return {"experiments": []}
    results: List[Dict[str, Any]] = []

    for file in sorted(ML_RESULTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
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
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return data
    except Exception as exc:
        logger.warning("Failed to parse experiment data %s (%s)", file_path.name, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to parse experiment data")


@router.get("/models")
def list_saved_models() -> Dict[str, Any]:
    """List trained model artifacts eligible for paper trading."""
    if not ML_MODELS_DIR.exists():
        return {"models": []}
    models: List[Dict[str, Any]] = []

    models_root = ML_MODELS_DIR.resolve()
    for manifest_file in sorted(ML_MODELS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            safe_manifest = manifest_file.resolve()
            if safe_manifest.parent != models_root or not safe_manifest.is_file():
                logger.warning("Skipping model manifest outside model directory: %s", manifest_file.name)
                continue
            data = json.loads(safe_manifest.read_text(encoding="utf-8"))
            joblib_file = (models_root / f"{manifest_file.stem}.joblib").resolve()
            data["has_weights"] = joblib_file.parent == models_root and joblib_file.is_file()
            report = paper_eligibility_report(test_metrics=data.get("test_metrics") or {})
            reasons = list(report["reasons"])
            if not data.get("eligible_for_paper"):
                reasons.append("manifest is not flagged eligible_for_paper")
            if not data["has_weights"]:
                reasons.append("model weights (.joblib) are missing")
            data["eligibility"] = {"eligible": not reasons, "reasons": reasons}
            models.append(data)
        except Exception as exc:
            logger.warning("Failed to parse model manifest %s (%s)", manifest_file.name, type(exc).__name__)

    return {"models": models}


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
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse model manifest %s (%s)", manifest_path.name, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to parse model manifest")

    if manifest.get("model_id") != req.model_id:
        raise HTTPException(status_code=409, detail="Model manifest ID does not match the requested model ID")

    # Promotion gate: re-evaluate from the stored untouched-test metrics instead of
    # trusting the manifest flag alone (manifests are plain, editable JSON files).
    report = paper_eligibility_report(test_metrics=manifest.get("test_metrics") or {})
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
def trigger_continuous_retraining(background_tasks: BackgroundTasks, force: bool = True):
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
    """List trained reinforcement learning models and performance audits."""
    rl_dir = Path("ml_models") / "rl"
    if not rl_dir.exists():
        return {"models": []}
    models = []
    for audit_file in sorted(rl_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
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
