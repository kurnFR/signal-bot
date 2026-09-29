"""
Runs the paper trading engine on a continuous poll loop.

sync_and_evaluate_paper_trading() -- the function that manages open paper
positions (SL/TP/trailing) and discovers new entries for every active
strategy in paper_configs -- previously only ran when a human clicked
"Evaluate Market Tick" in the dashboard, when a strategy was deployed, or
once after an ML tournament run. There was no continuous driver at all, so
"Live Paper Trading" never actually evaluated anything on its own between
those manual triggers -- a strategy could sit "ACTIVE LIVE" for hours with
a real entry signal on its latest candle and nothing would open a position
until someone happened to click the button.

This script is that missing driver. Same pattern as run_collectors.py /
run_news_ai.py: a plain poll loop, errors caught per-cycle so one bad
iteration doesn't kill the process, and a collector_heartbeat row
(paper_engine) so the dashboard can show whether it's actually running --
worth wiring a Pipeline Health-style indicator for this the same way the
News AI Overlay panel already does for its four processes.

Usage:
    python3 run_paper_engine.py
"""
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import PAPER_ENGINE_POLL_INTERVAL_SECONDS
from paper.engine import sync_and_evaluate_paper_trading
from db.db import heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_paper_engine")


def poll_once():
    result = sync_and_evaluate_paper_trading()
    detail = (
        f"{result['evaluated_configs']} configs, "
        f"{result['positions_updated']} updated, "
        f"{result['new_positions_opened']} opened, "
        f"{result['positions_closed']} closed"
    )
    heartbeat("paper_engine", detail=detail)
    if result["new_positions_opened"] or result["positions_closed"]:
        logger.info(f"Cycle: {detail}")
    else:
        logger.debug(f"Cycle: {detail}")


def main():
    logger.info("==================================================================")
    logger.info("  Starting Paper Trading Engine")
    logger.info(f"  Poll interval: {PAPER_ENGINE_POLL_INTERVAL_SECONDS}s")
    logger.info("==================================================================")

    while True:
        start = time.time()
        try:
            poll_once()
        except Exception as e:
            logger.error(f"poll_once failed: {e}", exc_info=True)
            heartbeat("paper_engine", status="error", detail=str(e))
        elapsed = time.time() - start
        time.sleep(max(0, PAPER_ENGINE_POLL_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopping Paper Trading Engine...")
