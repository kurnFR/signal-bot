"""
Open interest poller. Binance has NO websocket stream for open interest --
it must be polled via REST. We poll all symbols every OI_POLL_INTERVAL_SECONDS
concurrently with aiohttp.
"""
import asyncio
import logging
import sys
import os
import time

import aiohttp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, FUTURES_OI_URL, OI_POLL_INTERVAL_SECONDS
from db.db import upsert_open_interest, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("oi_poller")


async def fetch_oi(session, symbol):
    try:
        async with session.get(FUTURES_OI_URL, params={"symbol": symbol}, timeout=10) as resp:
            data = await resp.json()
            return (symbol, int(data["time"]), data["openInterest"], None)
    except Exception as e:
        logger.warning(f"Failed to fetch OI for {symbol}: {e}")
        return None


async def poll_once(session):
    tasks = [fetch_oi(session, s) for s in SYMBOLS]
    results = await asyncio.gather(*tasks)
    rows = [r for r in results if r is not None]
    if rows:
        upsert_open_interest(rows)
    heartbeat("oi_poller", detail=f"{len(rows)}/{len(SYMBOLS)} symbols ok")


async def run():
    async with aiohttp.ClientSession() as session:
        while True:
            start = time.time()
            await poll_once(session)
            elapsed = time.time() - start
            await asyncio.sleep(max(0, OI_POLL_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    asyncio.run(run())
