"""
Liquidation event collector via !forceOrder@arr (all futures symbols, one
socket). Every liquidation order is written -- volume here is naturally
low compared to trades/klines, no throttling needed.
"""
import asyncio
import json
import logging
import sys
import os

import websockets

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, FUTURES_WS_BASE, FUTURES_LIQUIDATION_ARR_STREAM
from db.db import insert_liquidations, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ws_liquidations")

SYMBOL_SET = set(SYMBOLS)


def event_to_row(event):
    o = event["o"]
    return (
        o["s"], o["S"], o["o"], o.get("f"),
        o["q"], o["p"], o.get("ap"), o.get("X"),
        int(event["E"]),
    )


async def run():
    url = f"{FUTURES_WS_BASE}/ws/{FUTURES_LIQUIDATION_ARR_STREAM}"
    backoff = 1
    while True:
        try:
            logger.info("connecting...")
            async with websockets.connect(url, ping_interval=180, ping_timeout=60) as ws:
                logger.info("connected")
                backoff = 1
                async for raw_msg in ws:
                    event = json.loads(raw_msg)
                    if event.get("e") != "forceOrder":
                        continue
                    if event["o"]["s"] not in SYMBOL_SET:
                        continue
                    insert_liquidations([event_to_row(event)])
                    heartbeat("ws_liquidations")
        except Exception as e:
            logger.warning(f"disconnected: {e}. Reconnecting in {backoff}s")
            heartbeat("ws_liquidations", status="error", detail=str(e))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(run())
