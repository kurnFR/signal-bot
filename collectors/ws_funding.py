"""
Funding rate + mark price collector via !markPrice@arr (updates every ~1s
for ALL futures symbols on one socket). We throttle DB writes to one row
per symbol per FUNDING_WRITE_INTERVAL_SECONDS -- writing every second would
create ~86k rows/day/symbol for no analytical benefit.
"""
import asyncio
import json
import logging
import sys
import os
import time

import websockets

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, FUTURES_WS_BASE, FUTURES_MARKPRICE_ARR_STREAM, FUNDING_WRITE_INTERVAL_SECONDS
from db.db import upsert_funding, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ws_funding")

SYMBOL_SET = set(SYMBOLS)


async def run():
    url = f"{FUTURES_WS_BASE}/ws/{FUTURES_MARKPRICE_ARR_STREAM}"
    backoff = 1
    last_write = {}  # symbol -> last write unix ts

    while True:
        try:
            logger.info("connecting...")
            async with websockets.connect(url, ping_interval=180, ping_timeout=60) as ws:
                logger.info("connected")
                backoff = 1
                async for raw_msg in ws:
                    arr = json.loads(raw_msg)
                    now = time.time()
                    rows = []
                    for item in arr:
                        symbol = item["s"]
                        if symbol not in SYMBOL_SET:
                            continue
                        if now - last_write.get(symbol, 0) < FUNDING_WRITE_INTERVAL_SECONDS:
                            continue
                        rows.append((
                            symbol,
                            int(item["E"]),
                            item["p"],   # mark price
                            item.get("i"),  # index price
                            item["r"],   # funding rate
                            int(item["T"]) if item.get("T") else None,  # next funding time
                        ))
                        last_write[symbol] = now
                    if rows:
                        upsert_funding(rows)
                    heartbeat("ws_funding")
        except Exception as e:
            logger.warning(f"disconnected: {e}. Reconnecting in {backoff}s")
            heartbeat("ws_funding", status="error", detail=str(e))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(run())
