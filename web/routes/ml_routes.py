"""
API Router for Machine Learning Strategy Tournaments & Forecasting.
Connects the ML research pipeline with the Web Dashboard and Paper Trading Engine.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from ml.run_real_experiment import run_real_experiment
from ml.dataset import TARGET_TYPES
from paper.engine import set_paper_config, sync_and_evaluate_paper_trading
from notifications.telegram_notifier import send_telegram_message

router = APIRouter(prefix="/api/ml", tags=["machine-learning"])
logger = logging.getLogger("web.ml_routes")

ML_RESULTS_DIR = Path("ml_results")
ML_MODELS_DIR = Path("ml_models")


class RunExperimentRequest(BaseModel):
    symbol: str = Field("BTCUSDT", example="BTCUSDT")
    market: str = Field("spot", example="spot")
    timeframe: str = Field("1h", example="1h")
    strategy_name: str = Field("trend_ema_v1", example="trend_ema_v1")
    model_type: str = Field("all", example="all")  # all, logistic_regression, random_forest, hist_gradient_boosting
    target_type: str = Field("binary_positive_r", example="binary_positive_r")
    min_validation_trades: int = Field(5, ge=1, le=100)


class DeployMLToPaperRequest(BaseModel):
    model_id: str = Field(...)
    allocated_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    send_telegram: bool = Field(True)


@router.get("/experiments")
def list_experiments() -> Dict[str, Any]:
    """List all completed ML tournaments and experiment results."""
    ML_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
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
            logger.warning("Error reading experiment file %s: %s", file, exc)

    return {"experiments": results}


@router.get("/experiments/{experiment_id}")
def get_experiment_details(experiment_id: str) -> Dict[str, Any]:
    """Retrieve the full JSON result for an ML tournament."""
    file_path = ML_RESULTS_DIR / f"{experiment_id}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse experiment data: {exc}")


@router.get("/models")
def list_saved_models() -> Dict[str, Any]:
    """List trained model artifacts eligible for paper trading."""
    ML_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    models: List[Dict[str, Any]] = []

    for manifest_file in sorted(ML_MODELS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            joblib_file = ML_MODELS_DIR / f"{manifest_file.stem}.joblib"
            data["has_weights"] = joblib_file.exists()
            models.append(data)
        except Exception as exc:
            logger.warning("Failed to parse model manifest %s: %s", manifest_file, exc)

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
        logger.error("ML experiment execution failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/deploy-to-paper")
def deploy_ml_model_to_paper(req: DeployMLToPaperRequest):
    """Deploys a trained ML Model as an active filter in Live Paper Trading."""
    manifest_path = ML_MODELS_DIR / f"{req.model_id}.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"ML model manifest '{req.model_id}' not found in ml_models/")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        strategy_params = manifest.get("strategy_params", {})
        symbol = strategy_params.get("symbol") or manifest.get("model_id").split("-")[1].upper()
        market = strategy_params.get("market") or manifest.get("model_id").split("-")[2].lower()
        timeframe = strategy_params.get("timeframe") or manifest.get("model_id").split("-")[3]
        strategy_name = manifest.get("base_strategy")

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
                f"🎯 <b>Probability Threshold:</b> <code>{manifest.get('threshold') * 100:.1f}%</code>\n"
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
        logger.error("Failed to deploy ML model to paper: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
