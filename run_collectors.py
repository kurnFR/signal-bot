"""
Runs all real-time collectors concurrently in one process:
  - kline (spot + futures, all timeframes)
  - open interest (polled)

Operational notes:
  - Funding rate REST collector (funding_poller.py) is exclusively owned by the
    dedicated standalone systemd service 'signal-funding-collector.service'.
    To eliminate dual poller ownership, it is not scheduled here.
  - Liquidations WebSocket collector (ws_liquidations.py) is deprecated and
    no longer started here because Binance Futures !forceOrder@arr stream is
    unusable from the current network origin.

Run this after backfill_klines.py has completed at least once.

Usage:
    python run_collectors.py
"""
import asyncio
import logging

from config import ENABLE_FUTURES
from collectors.ws_kline import main as ws_kline_main
from collectors.oi_poller import run as oi_poller_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_collectors")


async def main():
    logger.info("Starting all collectors...")
    tasks = [ws_kline_main()]

    if ENABLE_FUTURES:
        tasks += [oi_poller_run()]
    else:
        logger.info(
            "ENABLE_FUTURES is false -- skipping open-interest "
            "collector (futures-only). Set ENABLE_FUTURES=true in .env "
            "once you've confirmed futures WS is reachable from your network."
        )

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
