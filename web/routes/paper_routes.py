"""
API Router for Phase B: Live Paper Trading.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query, Path, Request
from web.auth import log_audit
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import math
import re
import time

from paper.engine import (
    get_active_positions, get_paper_trades, get_paper_configs,
    set_paper_config, delete_paper_config, purge_paper_config, PaperConfigActive, PaperConfigHasHistory, PaperDeploymentConflict, batch_update_paper_configs,
    manual_close_position, sync_and_evaluate_paper_trading,
    get_paper_metrics, update_paper_deployment, OVERLAY_STRATEGIES,
    _UNSET,
)
from backtest.strategies import STRATEGIES, PAIRS_STRATEGIES
from config import PAIRS_BASE_SYMBOL
from backtest.custom_strategy import paper_strategy_registry
from paper.orders import (
    OrderTransitionConflict, OrderValidationError, cancel_pending_order, list_orders,
)

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


def _is_known_paper_strategy(strategy_name: str) -> bool:
    name = (strategy_name or "").strip()
    return name in paper_strategy_registry(STRATEGIES) or name in OVERLAY_STRATEGIES


class PaperConfigRequest(BaseModel):
    create_only: bool = False
    symbol: str = Field(..., max_length=20, json_schema_extra={"example": "ETHUSDT"})
    market: str = Field("spot", max_length=7, json_schema_extra={"example": "spot"})
    timeframe: str = Field("1d", max_length=10, json_schema_extra={"example": "1d"})
    strategy_name: str = Field(..., max_length=50, json_schema_extra={"example": "confluence_ensemble_v1"})
    is_active: bool = Field(True)
    allocated_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    ml_model_id: Optional[str] = Field(None, max_length=100, json_schema_extra={"example": "real-btcusdt-spot-1h-trend_ema_v1-logistic_regression-binary_positive_r"})
    ml_generator_model_id: Optional[str] = Field(None, max_length=100, description="Typed independent ML signal-generator artifact; separate from the legacy filter model")
    telegram_enabled: Optional[bool] = Field(None, description="Whether paper entry alerts should be sent to Telegram; omitted updates preserve the current preference")
    params: Optional[Dict[str, Any]] = None
    report_enabled: Optional[bool] = Field(None, description="Whether this strategy appears in per-strategy performance summaries; omitted updates preserve the current preference")


@router.get("/positions")
def list_positions():
    positions = get_active_positions()
    return {"positions": positions}


@router.get("/trades")
def list_trades(limit: int = Query(50, ge=1, le=200)):
    trades = get_paper_trades(limit)
    return {"trades": trades}


@router.get("/orders")
def list_paper_orders(
    status: Optional[str] = Query(None, max_length=12),
    limit: int = Query(50, ge=1, le=200),
):
    try:
        return {"orders": list_orders(status=status, limit=limit)}
    except OrderValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/orders/{order_id}/cancel")
def cancel_paper_order(request: Request, order_id: int = Path(..., gt=0)):
    user = getattr(request.state, "user", {})
    try:
        actor_user_id = int(user["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Authenticated user identity is unavailable")
    try:
        cancel_pending_order(order_id, actor_user_id=actor_user_id)
        return {"status": "cancelled", "order_id": order_id}
    except OrderTransitionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except OrderValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/metrics")
def get_metrics(days: Optional[int] = Query(None, ge=1, le=366)):
    metrics = get_paper_metrics(closed_since_ms=int(time.time() * 1000) - days * 86_400_000) if days else get_paper_metrics()
    return {"metrics": metrics}


@router.get("/configs")
def list_configs():
    configs = get_paper_configs(include_equity=True)
    return {"configs": configs}



def _validate_generator_config(
    symbol: str, market: str, timeframe: str, strategy_name: str,
    generator_model_id: Optional[str], filter_model_id: Optional[str], is_active: bool,
) -> None:
    generator_strategy = "ml_signal_generator_v1"
    if strategy_name == generator_strategy:
        if not generator_model_id:
            raise HTTPException(status_code=400, detail="Select an independent ML generator model for this strategy.")
        if filter_model_id:
            raise HTTPException(status_code=400, detail="The independent generator cannot be combined with a legacy ML filter model.")
        # A preference update or pause must remain possible even if a model file
        # has since gone missing. Revalidate only when creating/reactivating this
        # exact generator config; the paper runtime independently fails closed
        # on every inference load.
        if is_active:
            existing_active = any(
                cfg.get("symbol") == symbol and cfg.get("market") == market
                and cfg.get("timeframe") == timeframe
                and cfg.get("strategy_name") == strategy_name
                and cfg.get("ml_generator_model_id") == generator_model_id
                and bool(cfg.get("is_active"))
                for cfg in get_paper_configs()
            )
            if not existing_active:
                try:
                    from ml.generator_artifacts import load_generator_artifact
                    load_generator_artifact(generator_model_id, require_eligible=True)
                except Exception as exc:
                    logging.getLogger("web.paper_routes").warning(
                        "Generator model validation failed (%s)", type(exc).__name__
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="Generator model must pass the current paper-eligibility gate before activation.",
                    )
    elif generator_model_id:
        raise HTTPException(status_code=400, detail="ml_generator_model_id is only valid for ml_signal_generator_v1.")


@router.post("/configs")
def update_config(req: PaperConfigRequest, request: Request = None):
    if not math.isfinite(req.allocated_capital) or not math.isfinite(req.risk_per_trade_pct):
        raise HTTPException(status_code=400, detail="allocated_capital and risk_per_trade_pct must be finite")
    if not req.symbol.strip() or not req.strategy_name.strip():
        raise HTTPException(status_code=400, detail="symbol and strategy_name must not be blank")
    if req.create_only and not re.fullmatch(r"[A-Za-z0-9]{2,20}", req.symbol.strip()):
        raise HTTPException(status_code=400, detail="Symbol must contain 2–20 letters or digits.")
    strategy_name = req.strategy_name.strip()
    norm_strategy = strategy_name[8:] if strategy_name.startswith("inverse_") else strategy_name
    if norm_strategy in PAIRS_STRATEGIES and req.symbol.strip().upper() == PAIRS_BASE_SYMBOL:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pair comparison: target symbol cannot be the same as comparator ({PAIRS_BASE_SYMBOL}). Choose an altcoin pair.",
        )
    if not _is_known_paper_strategy(strategy_name):
        raise HTTPException(
            status_code=400,
            detail="Unknown executable paper strategy. Refresh the Strategy Library and select a registered strategy.",
        )
    if req.market.strip().lower() not in {"spot", "futures"}:
        raise HTTPException(status_code=400, detail="market must be spot or futures")
    if req.timeframe.strip() not in {"15m", "1h", "4h", "1d"}:
        raise HTTPException(status_code=400, detail="timeframe must be one of: 15m, 1h, 4h, 1d")
    _validate_generator_config(
        req.symbol.strip().upper(), req.market.strip().lower(), req.timeframe.strip(),
        strategy_name, req.ml_generator_model_id, req.ml_model_id, req.is_active
    )
    strategy_params = req.params if "params" in req.model_fields_set else _UNSET
    try:
        res = set_paper_config(
            req.symbol.strip().upper(),
            req.market.strip().lower(),
            req.timeframe.strip(),
            strategy_name,
            req.is_active,
            req.allocated_capital,
            req.risk_per_trade_pct,
            req.ml_model_id.strip() if req.ml_model_id else None,
            telegram_enabled=req.telegram_enabled,
            report_enabled=req.report_enabled,
            ml_generator_model_id=req.ml_generator_model_id.strip() if req.ml_generator_model_id else None,
            strategy_params=strategy_params,
            **({"create_only": True} if req.create_only else {}),
        )
    except PaperDeploymentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger("web.paper_routes").warning("Paper config save failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Paper configuration could not be saved.") from exc
    if req.create_only:
        user = request.state.user
        log_audit("PAPER_CONFIG_CLONED", user["username"], user["id"], {"config_id": res["id"]},
                  request.client.host if request.client else "unknown")
    return res


class BatchPaperConfigRequest(BaseModel):
    strategy_name: str = Field(..., min_length=1, max_length=50)
    action: str = Field(..., min_length=1, max_length=30)


@router.post("/configs/batch")
def batch_update_configs(req: BatchPaperConfigRequest):
    strategy_name = req.strategy_name.strip()
    action = req.action.strip().lower()
    try:
        count = batch_update_paper_configs(strategy_name, action)
        return {
            "status": "success",
            "strategy": strategy_name,
            "action": action,
            "updated": count,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PaperReportPreferences(BaseModel):
    symbol: Optional[str] = Field(None, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    strategy_name: Optional[str] = Field(None, min_length=1, max_length=50)
    report_enabled: Optional[bool] = None
    telegram_enabled: Optional[bool] = None


@router.post("/configs/preferences")
def update_report_preferences(req: PaperReportPreferences):
    symbol = req.symbol.upper() if req.symbol else None
    strategy = req.strategy_name.strip() if req.strategy_name else None
    if not symbol and not strategy:
        raise HTTPException(status_code=400, detail="Select a pair or strategy")
    if (req.report_enabled is None) == (req.telegram_enabled is None):
        raise HTTPException(status_code=400, detail="Set exactly one preference")
    if req.report_enabled is not None:
        action = "report_all_on" if req.report_enabled else "report_all_off"
    else:
        action = "telegram_all_on" if req.telegram_enabled else "telegram_all_off"
    count = batch_update_paper_configs(strategy, action, symbol=symbol)
    return {"status": "success", "updated": count}


class PaperDeploymentPatch(BaseModel):
    model_config = {"extra": "forbid"}

    ml_model_id: Optional[str] = Field(None, max_length=100)
    ml_generator_model_id: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    allocated_capital: Optional[float] = Field(None, ge=100, allow_inf_nan=False)
    risk_per_trade_pct: Optional[float] = Field(None, ge=0.1, le=10, allow_inf_nan=False)
    telegram_enabled: Optional[bool] = None
    report_enabled: Optional[bool] = None
    params: Optional[Dict[str, Any]] = None


@router.patch("/configs/{config_id}")
def edit_deployment(req: PaperDeploymentPatch, config_id: int = Path(..., gt=0), request: Request = None):
    if req.is_active is True:
        config = next((row for row in get_paper_configs() if row["id"] == config_id), None)
        if config is None:
            raise HTTPException(status_code=404, detail="Paper deployment not found")
        strategy = config["strategy_name"]
        if not _is_known_paper_strategy(strategy):
            raise HTTPException(status_code=400, detail="This strategy is no longer available in the Strategy Library.")
        _validate_generator_config(
            config["symbol"], config["market"], config["timeframe"], strategy,
            req.ml_generator_model_id if "ml_generator_model_id" in req.model_fields_set else config.get("ml_generator_model_id"),
            req.ml_model_id if "ml_model_id" in req.model_fields_set else config.get("ml_model_id"), True,
        )
    patch_data = req.model_dump(exclude_none=True)
    if "params" in req.model_fields_set:
        patch_data["strategy_params"] = req.params if req.params else None
    patch_data.pop("params", None)
    model_fields = {"ml_model_id", "ml_generator_model_id"} & req.model_fields_set
    patch_data.update({field: getattr(req, field) for field in model_fields})
    changes = {}
    if model_fields:
        patch_data["model_changes"] = changes
    try:
        updated = update_paper_deployment(config_id, **patch_data)
    except PaperDeploymentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger("web.paper_routes").warning("Paper deployment update failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Paper deployment could not be saved.") from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Paper deployment not found")
    if changes:
        user = request.state.user
        log_audit("PAPER_CONFIG_MODELS_UPDATED", user["username"], user["id"],
                  {"config_id": config_id, "models": changes}, request.client.host if request.client else "unknown")
    updated_config = next((row for row in get_paper_configs() if row["id"] == config_id), None)
    return {
        "status": "success",
        "id": config_id,
        "strategy_params": updated_config.get("strategy_params") if updated_config else None,
    }


@router.delete("/configs/{config_id}")
def delete_config(config_id: int = Path(..., gt=0)):
    try:
        deleted = delete_paper_config(config_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Paper config not found")
        return {"status": "success", "id": config_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/configs/{config_id}/permanent")
def purge_config(request: Request, config_id: int = Path(..., gt=0), allow_unarchived: bool = False):
    """Hard-delete a deployment with no ledger history. Require explicit opt-in before archive."""
    try:
        deleted = (purge_paper_config(config_id, allow_unarchived=True)
                   if allow_unarchived else purge_paper_config(config_id))
    except (PaperConfigActive, PaperConfigHasHistory) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Paper config not found")
    user = getattr(request.state, "user", {}) or {}
    log_audit("PAPER_CONFIG_PURGED", str(user.get("username", "unknown")), user.get("id"),
              {"config_id": config_id}, request.client.host if request.client else "unknown")
    return {"status": "success", "id": config_id}


@router.post("/positions/{position_id}/close")
def close_position(position_id: int = Path(..., gt=0)):
    try:
        res = manual_close_position(position_id)
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sync")
def sync_market_tick():
    try:
        eval_res = sync_and_evaluate_paper_trading()
        return {"status": "success", "results": eval_res}
    except Exception as e:
        # Without this, an unhandled exception here falls through to
        # Starlette's default 500 handler, which returns a *plain-text*
        # body ("Internal Server Error") rather than JSON -- the frontend's
        # res.json() call then throws its own confusing
        # "Unexpected token... is not valid JSON" error, masking whatever
        # the real backend bug was. Return JSON with the real detail instead.
        # Preserve JSON errors without exposing exception text that may contain
        # credentials, internal paths, or database metadata.
        logging.getLogger("web.paper_routes").error(
            "sync_market_tick failed (%s)", type(e).__name__
        )
        raise HTTPException(
            status_code=500,
            detail="Paper-trading sync failed; check server logs for diagnostics",
        )


class DeployStrategyRequest(BaseModel):
    symbol: str = Field(..., max_length=20, json_schema_extra={"example": "BTCUSDT"})
    market: str = Field("spot", max_length=7, json_schema_extra={"example": "spot"})
    timeframe: str = Field("1d", max_length=10, json_schema_extra={"example": "1d"})
    strategy_name: str = Field(..., max_length=50, json_schema_extra={"example": "confluence_ensemble_v1"})
    allocated_capital: float = Field(5000.0, ge=100.0)
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0)
    send_telegram: bool = Field(False)
    rank: Optional[int] = None
    rank_score: Optional[float] = None
    ml_model_id: Optional[str] = Field(None, max_length=100)
    params: Optional[Dict[str, Any]] = None


@router.post("/deploy")
def deploy_strategy(req: DeployStrategyRequest):
    if not math.isfinite(req.allocated_capital) or not math.isfinite(req.risk_per_trade_pct):
        raise HTTPException(status_code=400, detail="allocated_capital and risk_per_trade_pct must be finite")
    if req.rank_score is not None and not math.isfinite(req.rank_score):
        raise HTTPException(status_code=400, detail="rank_score must be finite")
    if not req.symbol.strip() or not req.strategy_name.strip():
        raise HTTPException(status_code=400, detail="symbol and strategy_name must not be blank")
    strategy_name = req.strategy_name.strip()
    norm_strategy = strategy_name[8:] if strategy_name.startswith("inverse_") else strategy_name
    if strategy_name == "ml_signal_generator_v1":
        raise HTTPException(
            status_code=400,
            detail="Use /api/paper/configs with an explicit generator model and independent Telegram preference.",
        )
    market = req.market.strip().lower()
    timeframe = req.timeframe.strip()
    if norm_strategy in PAIRS_STRATEGIES and req.symbol.strip().upper() == PAIRS_BASE_SYMBOL:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pair comparison: target symbol cannot be the same as comparator ({PAIRS_BASE_SYMBOL}). Choose an altcoin pair.",
        )
    if not _is_known_paper_strategy(strategy_name):
        raise HTTPException(
            status_code=400,
            detail="Unknown executable paper strategy. Refresh the Strategy Library and select a registered strategy.",
        )
    if market not in {"spot", "futures"}:
        raise HTTPException(status_code=400, detail="market must be spot or futures")
    if timeframe not in {"15m", "1h", "4h", "1d"}:
        raise HTTPException(status_code=400, detail="timeframe must be one of: 15m, 1h, 4h, 1d")
    # 1. Update/Add paper config
    res = set_paper_config(
        req.symbol.strip().upper(),
        market,
        timeframe,
        strategy_name,
        is_active=True,
        allocated_capital=req.allocated_capital,
        risk_per_trade_pct=req.risk_per_trade_pct,
        ml_model_id=req.ml_model_id.strip() if req.ml_model_id else None,
        telegram_enabled=req.send_telegram,
        report_enabled=True,
        strategy_params=req.params,
    )

    # 2. Telegram deployment alert
    telegram_res = None
    if req.send_telegram:
        try:
            from notifications.telegram_notifier import send_deployment_alert
            telegram_res = send_deployment_alert(
                symbol=req.symbol.strip().upper(),
                market=market,
                timeframe=timeframe,
                strategy_name=strategy_name,
                allocated_capital=req.allocated_capital,
                risk_per_trade_pct=req.risk_per_trade_pct,
                rank=req.rank,
                rank_score=req.rank_score,
                max_retries=1,
            )
        except Exception as e:
            # Raw exceptions may contain token-bearing URLs or request details.
            logging.getLogger("web.paper_routes").error(
                "Deployment Telegram alert failed (%s)", type(e).__name__
            )
            telegram_res = {"success": False, "error": "Telegram deployment alert failed"}

    # 3. Immediate evaluation against latest candle
    eval_res = {}
    try:
        eval_res = sync_and_evaluate_paper_trading()
    except Exception as eval_err:
        logging.getLogger("web.paper_routes").error(
            "Paper-trading evaluation after deployment failed (%s)", type(eval_err).__name__
        )
        eval_res = {"error": "Paper-trading evaluation failed"}

    deployment_status = "success" if "error" not in eval_res else "partial"
    return {
        "status": deployment_status,
        "message": f"Strategy '{req.strategy_name}' deployed to Live Paper Trading on {req.symbol} ({req.timeframe}).",
        "config": res,
        "telegramAlert": telegram_res,
        "telegram": telegram_res,
        "syncResult": eval_res
    }


# ============================================================================
# Circuit Breaker & Risk Safety Endpoints
# ============================================================================
@router.get("/circuit-breakers")
def get_circuit_breakers():
    from paper.circuit_breaker import get_circuit_breaker_status
    return get_circuit_breaker_status()


@router.post("/circuit-breakers/emergency-stop")
def trip_circuit_breaker(reason: str = "Manual emergency stop via Web Dashboard"):
    from paper.circuit_breaker import trip_emergency_stop, get_circuit_breaker_status
    trip_emergency_stop(reason)
    return {"status": "success", "message": reason, "breaker": get_circuit_breaker_status()}


@router.post("/circuit-breakers/reset")
def reset_circuit_breaker():
    from paper.circuit_breaker import reset_emergency_stop, get_circuit_breaker_status
    reset_emergency_stop()
    return {"status": "success", "message": "Circuit breaker reset", "breaker": get_circuit_breaker_status()}


