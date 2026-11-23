import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException, status
from db.db import get_pool, _owned_cursor
from web.auth import verify_access_token
from paper.engine import get_active_positions
from paper.circuit_breaker import get_circuit_breaker_status
from web.routes.system_status import get_public_system_status

logger = logging.getLogger("web.ws")

router = APIRouter(tags=["streaming"])


async def _authenticate_ws(websocket: WebSocket, query_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    token = query_token
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if token:
        user = verify_access_token(token)
        if user:
            await websocket.accept()
            return user

    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    return None


def _get_candles_from_db(symbol: str, market: str, timeframe: str, limit: int) -> Dict[str, Any]:
    pool = get_pool()
    conn = pool.get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = %s AND market = %s AND timeframe = %s AND is_closed = 1
            ORDER BY open_time DESC
            LIMIT %s
            """,
            (symbol, market, timeframe, limit),
        )
        raw_candles = cur.fetchall()
        raw_candles.reverse()

        candles: List[Dict[str, Any]] = []
        for r in raw_candles:
            open_time_s = int(r["open_time"] / 1000) if r["open_time"] > 10_000_000_000 else int(r["open_time"])
            candles.append({
                "time": open_time_s,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            })

        # Fetch latest funding rate and mark price if available
        cur.execute(
            """
            SELECT funding_rate, mark_price, event_time
            FROM funding_rate
            WHERE symbol = %s
            ORDER BY event_time DESC
            LIMIT 1
            """,
            (symbol,),
        )
        funding_row = cur.fetchone()
        funding_rate = float(funding_row["funding_rate"]) if funding_row and funding_row.get("funding_rate") is not None else None
        mark_price = float(funding_row["mark_price"]) if funding_row and funding_row.get("mark_price") is not None else (candles[-1]["close"] if candles else None)

        return {
            "symbol": symbol,
            "market": market,
            "timeframe": timeframe,
            "candles": candles,
            "mark_price": mark_price,
            "funding_rate": funding_rate,
        }


@router.get("/api/market/candles")
def get_market_candles(
    symbol: str = Query("BTCUSDT", max_length=20),
    market: str = Query("spot", max_length=10),
    timeframe: str = Query("1h", max_length=5),
    limit: int = Query(200, ge=10, le=1000),
):
    """Returns historical OHLCV candles formatted for lightweight-charts with mark price and funding rate."""
    symbol = symbol.strip().upper()
    market = market.strip().lower()
    timeframe = timeframe.strip().lower()
    if market not in ("spot", "futures"):
        raise HTTPException(status_code=400, detail="Market must be 'spot' or 'futures'")
    try:
        return _get_candles_from_db(symbol, market, timeframe, limit)
    except Exception as exc:
        logger.exception("Failed to fetch market candles for %s", symbol)
        raise HTTPException(status_code=500, detail="Database error retrieving candles")


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket, token: Optional[str] = Query(None)):
    user = await _authenticate_ws(websocket, query_token=token)
    if not user:
        return

    active_symbol = "BTCUSDT"
    active_market = "spot"
    active_timeframe = "1h"

    try:
        initial_data = await asyncio.to_thread(_get_candles_from_db, active_symbol, active_market, active_timeframe, 100)
        await websocket.send_text(json.dumps({
            "type": "market_snapshot",
            "symbol": active_symbol,
            "market": active_market,
            "timeframe": active_timeframe,
            "candles": initial_data["candles"],
            "mark_price": initial_data["mark_price"],
            "funding_rate": initial_data["funding_rate"],
            "timestamp": time.time(),
        }))

        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            action = msg.get("action")
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "timestamp": time.time()}))
            elif action == "subscribe":
                sym = str(msg.get("symbol", active_symbol)).strip().upper()
                mkt = str(msg.get("market", active_market)).strip().lower()
                tf = str(msg.get("timeframe", active_timeframe)).strip().lower()
                if mkt in ("spot", "futures"):
                    active_symbol = sym
                    active_market = mkt
                    active_timeframe = tf
                    sub_data = await asyncio.to_thread(_get_candles_from_db, active_symbol, active_market, active_timeframe, 100)
                    await websocket.send_text(json.dumps({
                        "type": "market_snapshot",
                        "symbol": active_symbol,
                        "market": active_market,
                        "timeframe": active_timeframe,
                        "candles": sub_data["candles"],
                        "mark_price": sub_data["mark_price"],
                        "funding_rate": sub_data["funding_rate"],
                        "timestamp": time.time(),
                    }))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket /ws/market exception")


@router.websocket("/ws/paper")
async def ws_paper(websocket: WebSocket, token: Optional[str] = Query(None)):
    user = await _authenticate_ws(websocket, query_token=token)
    if not user:
        return

    try:
        # Send initial paper snapshot
        positions = await asyncio.to_thread(get_active_positions)
        breaker = await asyncio.to_thread(get_circuit_breaker_status)
        await websocket.send_text(json.dumps({
            "type": "paper_snapshot",
            "positions": positions,
            "breaker": breaker,
            "timestamp": time.time(),
        }))

        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            action = msg.get("action")
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "timestamp": time.time()}))
            elif action in ("refresh", "poll"):
                positions = await asyncio.to_thread(get_active_positions)
                breaker = await asyncio.to_thread(get_circuit_breaker_status)
                await websocket.send_text(json.dumps({
                    "type": "paper_snapshot",
                    "positions": positions,
                    "breaker": breaker,
                    "timestamp": time.time(),
                }))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket /ws/paper exception")


@router.websocket("/ws/system")
async def ws_system(websocket: WebSocket, token: Optional[str] = Query(None)):
    user = await _authenticate_ws(websocket, query_token=token)
    if not user:
        return

    try:
        status_data = await asyncio.to_thread(get_public_system_status)
        await websocket.send_text(json.dumps({
            "type": "system_snapshot",
            "status": status_data,
            "timestamp": time.time(),
        }))

        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            action = msg.get("action")
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "timestamp": time.time()}))
            elif action in ("refresh", "poll"):
                status_data = await asyncio.to_thread(get_public_system_status)
                await websocket.send_text(json.dumps({
                    "type": "system_snapshot",
                    "status": status_data,
                    "timestamp": time.time(),
                }))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket /ws/system exception")
