"""API for the Wallet Tracking page.

GET endpoints are readable by any authenticated user. Mutations (track /
untrack a wallet) require the trader or admin role -- see TRADER_OR_ADMIN_PREFIXES
in web/app.py.
"""
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import config
from wallet_tracker import analyzer, store
from wallet_tracker.chains import CHAINS, is_supported_chain, normalize_wallet

router = APIRouter(prefix="/api/wallets", tags=["wallet-tracking"])
logger = logging.getLogger("web.wallet_routes")


class WalletSettingsRequest(BaseModel):
    enabled: bool = True
    networks: list[str] = Field(min_length=1, max_length=6)
    pools_per_network: int = Field(8, ge=1, le=100)
    min_trade_usd: float = Field(2000, ge=0, le=1e12)
    min_net_usd: float = Field(100000, ge=0, le=1e12)
    auto_track_net_usd: float = Field(250000, ge=0, le=1e12)
    poll_interval_seconds: int = Field(120, ge=15, le=3600)
    retention_days: int = Field(8, ge=1, le=90)
    api_delay_seconds: float = Field(2.2, ge=0.5, le=60)
    token_mode: Literal["all", "selected"] = "all"
    token_addresses: list[str] = Field(default_factory=list, max_length=500)


class TrackRequest(BaseModel):
    chain: str
    wallet: str
    label: Optional[str] = Field(default=None, max_length=80)


def _check_period(hours: int) -> int:
    if hours not in config.WALLET_TRACKER_PERIODS_HOURS:
        raise HTTPException(status_code=400, detail=f"period_hours must be one of {config.WALLET_TRACKER_PERIODS_HOURS}")
    return hours


def _check_chain(chain: Optional[str]) -> Optional[str]:
    if chain and not is_supported_chain(chain):
        raise HTTPException(status_code=400, detail=f"Unsupported chain. Allowed: {sorted(CHAINS)}")
    return chain or None


@router.get("/meta")
def wallets_meta():
    settings = store.get_settings()
    return {
        "periods_hours": config.WALLET_TRACKER_PERIODS_HOURS,
        "chains": {k: v["label"] for k, v in CHAINS.items()},
        "min_net_usd": settings["min_net_usd"],
        "auto_track_net_usd": settings["auto_track_net_usd"],
    }


@router.get("/settings")
def wallets_get_settings():
    try:
        return store.get_settings()
    except Exception as e:
        logger.exception("wallets_get_settings failed")
        raise HTTPException(status_code=500, detail=f"Failed to load wallet tracker settings: {e}")


@router.put("/settings")
def wallets_save_settings(req: WalletSettingsRequest):
    networks = list(dict.fromkeys(n.strip().lower() for n in req.networks))
    invalid = [n for n in networks if not is_supported_chain(n)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported network(s): {invalid}. Allowed: {sorted(CHAINS)}")
    selectors = []
    for raw in req.token_addresses:
        parts = raw.strip().split(":", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail=f"Token selector must be chain:address: {raw}")
        chain, address = parts[0].lower(), parts[1].strip()
        normalized = normalize_wallet(chain, address)
        if normalized is None:
            raise HTTPException(status_code=400, detail=f"Invalid token address for {chain}: {address}")
        selector = f"{chain}:{normalized}"
        if chain not in networks:
            raise HTTPException(status_code=400, detail=f"Token selector chain {chain} is not enabled in networks")
        if selector not in selectors:
            selectors.append(selector)
    if req.token_mode == "selected" and not selectors:
        raise HTTPException(status_code=400, detail="Add at least one chain:token-address selector or choose All tokens")
    settings = req.model_dump()
    settings["networks"] = networks
    settings["token_addresses"] = selectors
    try:
        store.save_settings(settings)
        return store.get_settings()
    except Exception as e:
        logger.exception("wallets_save_settings failed")
        raise HTTPException(status_code=500, detail=f"Failed to save wallet tracker settings: {e}")

@router.get("/status")
def wallets_status():
    try:
        status = store.get_status()
        settings = store.get_settings()
        status["enabled"] = settings["enabled"]
        status["networks"] = settings["networks"]
        return status
    except Exception as e:
        logger.exception("wallets_status failed")
        raise HTTPException(status_code=500, detail=f"Failed to load wallet tracker status: {e}")


@router.get("/positions")
def wallets_positions(
    period_hours: int = Query(24),
    side: Optional[str] = Query(None, pattern="^(BUY|SELL)$"),
    chain: Optional[str] = Query(None),
    min_net_usd: float = Query(0, ge=0, le=1e12),
    limit: int = Query(100, ge=1, le=300),
):
    _check_period(period_hours)
    _check_chain(chain)
    try:
        return {"positions": store.get_positions(period_hours, side, chain, min_net_usd, limit)}
    except Exception as e:
        logger.exception("wallets_positions failed")
        raise HTTPException(status_code=500, detail=f"Failed to load wallet positions: {e}")


@router.get("/token-flow")
def wallets_token_flow(period_hours: int = Query(24), limit: int = Query(20, ge=1, le=100)):
    _check_period(period_hours)
    try:
        positions = store.get_all_positions_for_period(period_hours)
        return {"flows": analyzer.token_flow(positions)[:limit]}
    except Exception as e:
        logger.exception("wallets_token_flow failed")
        raise HTTPException(status_code=500, detail=f"Failed to load token flow: {e}")


@router.get("/events")
def wallets_events(
    limit: int = Query(100, ge=1, le=300),
    since_id: int = Query(0, ge=0),
    chain: Optional[str] = Query(None),
):
    _check_chain(chain)
    try:
        return {"events": store.get_events(limit=limit, since_id=since_id, chain=chain)}
    except Exception as e:
        logger.exception("wallets_events failed")
        raise HTTPException(status_code=500, detail=f"Failed to load wallet events: {e}")


@router.get("/tracked")
def wallets_tracked():
    try:
        return {"tracked": store.get_tracked("ACTIVE")}
    except Exception as e:
        logger.exception("wallets_tracked failed")
        raise HTTPException(status_code=500, detail=f"Failed to load monitored wallets: {e}")


@router.post("/tracked")
def wallets_track(req: TrackRequest):
    _check_chain(req.chain or "invalid")
    wallet = normalize_wallet(req.chain, req.wallet)
    if wallet is None:
        raise HTTPException(status_code=400, detail=f"Not a valid {CHAINS.get(req.chain, {}).get('label', req.chain)} wallet address")
    label = (req.label or "").strip() or None
    try:
        store.track_wallet(req.chain, wallet, label)
        return {"status": "ok", "chain": req.chain, "wallet": wallet}
    except Exception as e:
        logger.exception("wallets_track failed")
        raise HTTPException(status_code=500, detail=f"Failed to monitor wallet: {e}")


@router.delete("/tracked/{tracked_id}")
def wallets_untrack(tracked_id: int):
    try:
        if not store.untrack_wallet(tracked_id):
            raise HTTPException(status_code=404, detail="Monitored wallet not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wallets_untrack failed")
        raise HTTPException(status_code=500, detail=f"Failed to stop monitoring wallet: {e}")
