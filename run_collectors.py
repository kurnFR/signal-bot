"""
Runs all real-time collectors concurrently in one process:
  - kline (spot + futures, all timeframes)
  - funding rate / mark price
  - liquidations
  - open interest (polled)

Run this after backfill_klines.py has completed at least once.

Usage:
    python run_collectors.py
"""
import asyncio
import logging

from config import ENABLE_FUTURES
from collectors.ws_kline import main as ws_kline_main
from collectors.ws_funding import run as ws_funding_run
from collectors.ws_liquidations import run as ws_liquidations_run
from collectors.oi_poller import run as oi_poller_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_collectors")


async def main():
    logger.info("Starting all collectors...")
    tasks = [ws_kline_main()]

    if ENABLE_FUTURES:
        tasks += [ws_funding_run(), ws_liquidations_run(), oi_poller_run()]
    else:
        logger.info(
            "ENABLE_FUTURES is false -- skipping funding/liquidations/open-interest "
            "collectors (these are futures-only). Set ENABLE_FUTURES=true in .env "
            "once you've confirmed futures WS is reachable from your network."
        )

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
