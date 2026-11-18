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


async def _run_collector_supervised(name, collector, restart_delay=5):
    """Keep one collector failure from terminating its sibling collector.

    Cancellation is always propagated so systemd SIGTERM can shut the process
    down cleanly. Unexpected return and exceptions both trigger bounded retry.
    """
    delay = max(1, int(restart_delay))
    while True:
        try:
            await collector()
            logger.error("Collector %s exited unexpectedly; restarting in %ss", name, delay)
        except asyncio.CancelledError:
            logger.info("Collector %s received cancellation", name)
            raise
        except Exception:
            logger.exception("Collector %s failed; restarting in %ss", name, delay)
        await asyncio.sleep(delay)


async def main():
    logger.info("Starting all collectors...")
    collectors = [("kline", ws_kline_main)]

    if ENABLE_FUTURES:
        collectors.append(("open_interest", oi_poller_run))
    else:
        logger.info(
            "ENABLE_FUTURES is false -- skipping open-interest "
            "collector (futures-only). Set ENABLE_FUTURES=true in .env "
            "once you've confirmed futures WS is reachable from your network."
        )

    tasks = [
        asyncio.create_task(_run_collector_supervised(name, collector))
        for name, collector in collectors
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
