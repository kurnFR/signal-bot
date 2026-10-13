"""API for the Wallet Tracking page.

GET endpoints are readable by any authenticated user. Mutations (track /
untrack a wallet) require the trader or admin role -- see TRADER_OR_ADMIN_PREFIXES
in web/app.py.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import config
from wallet_tracker import analyzer, store
from wallet_tracker.chains import CHAINS, is_supported_chain, normalize_wallet

router = APIRouter(prefix="/api/wallets", tags=["wallet-tracking"])
logger = logging.getLogger("web.wallet_routes")


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
    return {
        "periods_hours": config.WALLET_TRACKER_PERIODS_HOURS,
        "chains": {k: v["label"] for k, v in CHAINS.items()},
        "min_net_usd": config.WALLET_TRACKER_MIN_NET_USD,
        "auto_track_net_usd": config.WALLET_TRACKER_AUTO_TRACK_NET_USD,
    }


@router.get("/status")
def wallets_status():
    try:
        status = store.get_status()
        status["enabled"] = config.WALLET_TRACKER_ENABLED
        status["networks"] = config.WALLET_TRACKER_NETWORKS
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
