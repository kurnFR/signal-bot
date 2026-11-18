"""
DEPRECATED: Binance Futures Liquidation WebSocket Collector.

Notice:
- Status: Deprecated / Inactive. No longer started by run_collectors.py.
- The Binance Futures liquidation stream (!forceOrder@arr) is currently
  unusable from the existing network origin (Indonesian IP addresses face
  edge broadcast/aggregated stream suppression).
- Liquidation data is unavailable until a verified replacement source is
  implemented.
- This module and the underlying `liquidations` database table are retained
  for reference, architectural documentation, and possible future reactivation.
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
    logger.warning(
        "DEPRECATED: collectors.ws_liquidations is deprecated and no longer started by run_collectors.py. "
        "The Binance Futures !forceOrder@arr stream is unusable from the existing network origin. "
        "Liquidation data is unavailable until a verified replacement source is implemented."
    )
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
