"""
Real-time kline collector. Opens ONE combined-stream websocket per market
(spot / futures) covering all symbols x all timeframes, rather than one
socket per symbol -- keeps us well inside Binance's connection limits.

Writes every message, including the still-forming candle (is_closed=0), so
you always have an up-to-the-second latest candle. When Binance marks a
candle closed ("x": true) it's the final, immutable write for that open_time.

Auto-reconnects with exponential backoff on any disconnect (Binance closes
idle/long-lived streams periodically -- this is expected, not an error).
"""
import asyncio
import json
import logging
import sys
import os

import websockets

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SYMBOLS, TIMEFRAMES, MARKETS, SPOT_WS_COMBINED, FUTURES_WS_COMBINED
from db.db import upsert_ohlcv, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ws_kline")


def build_stream_url(base_url, symbols, timeframes):
    streams = [f"{s.lower()}@kline_{tf}" for s in symbols for tf in timeframes]
    return base_url + "/".join(streams)


def kline_msg_to_row(market, msg):
    k = msg["data"]["k"]
    return (
        k["s"], market, k["i"],
        int(k["t"]), int(k["T"]),
        k["o"], k["h"], k["l"], k["c"], k["v"], k["q"],
        int(k["n"]), k["V"], k["Q"],
        1 if k["x"] else 0,
    )


async def run_market(market: str, base_url: str):
    if market not in MARKETS:
        logger.info(f"[{market}] skipped (not in MARKETS -- see ENABLE_FUTURES in config.py)")
        return
    url = build_stream_url(base_url, SYMBOLS, TIMEFRAMES)
    backoff = 1
    while True:
        try:
            logger.info(f"[{market}] connecting...")
            async with websockets.connect(url, ping_interval=180, ping_timeout=60) as ws:
                logger.info(f"[{market}] connected, subscribed to {len(SYMBOLS)*len(TIMEFRAMES)} streams")
                backoff = 1
                async for raw_msg in ws:
                    msg = json.loads(raw_msg)
                    if "data" not in msg or msg["data"].get("e") != "kline":
                        continue
                    row = kline_msg_to_row(market, msg)
                    upsert_ohlcv([row])
                    heartbeat(f"ws_kline_{market}")
        except Exception as e:
            logger.warning(f"[{market}] disconnected: {e}. Reconnecting in {backoff}s")
            heartbeat(f"ws_kline_{market}", status="error", detail=str(e))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def main():
    await asyncio.gather(
        run_market("spot", SPOT_WS_COMBINED),
        run_market("futures", FUTURES_WS_COMBINED),
    )


if __name__ == "__main__":
    asyncio.run(main())
