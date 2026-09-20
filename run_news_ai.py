"""
Runs all News + Economic Calendar AI Overlay components concurrently:
  - collectors.news_poller (CryptoPanic / Free RSS feeds)
  - collectors.econ_calendar_poller (Finnhub / ForexFactory calendar)
  - ai.news_strategy_engine (LLM reasoning layer via 9Router / OpenRouter / Anthropic)
  - ai.news_execution (Execution & risk guardrails, paper position opening)

Usage:
    python3 run_news_ai.py
"""
import sys
import os
import time
import logging
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collectors.news_poller import run as news_poller_run
from collectors.econ_calendar_poller import run as econ_calendar_run
from ai.news_strategy_engine import run as news_strategy_run
from ai.news_execution import run as news_execution_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_news_ai")


def start_worker(name, target_fn):
    """Run a worker and automatically restart it after an unexpected crash.

    Individual pollers already report their own errors, but a top-level
    exception must not silently remove a component from the News AI pipeline.
    """
    def wrapper():
        while True:
            logger.info(f"Worker '{name}' started.")
            try:
                target_fn()
                logger.warning(f"Worker '{name}' exited unexpectedly; restarting in 5s")
            except Exception as e:
                logger.error(f"Worker '{name}' crashed: {e}", exc_info=True)
            time.sleep(5)

    t = threading.Thread(target=wrapper, name=name, daemon=True)
    t.start()
    return t


def main():
    logger.info("==================================================================")
    logger.info("  ⚡ Starting News + Economic Calendar AI Overlay Engine")
    logger.info("==================================================================")

    workers = [
        ("news_poller", news_poller_run),
        ("econ_calendar_poller", econ_calendar_run),
        ("news_strategy_engine", news_strategy_run),
        ("news_execution", news_execution_run),
    ]

    threads = [start_worker(name, fn) for name, fn in workers]

    try:
        while True:
            time.sleep(1)
            # Worker wrappers stay alive and restart the underlying
            # poller after an unexpected exception. This is intentionally
            # lightweight; the dashboard heartbeat remains the source of
            # truth for component freshness.
            for t in threads:
                if not t.is_alive():
                    logger.error(f"Worker supervisor thread {t.name} died")
    except KeyboardInterrupt:
        logger.info("Stopping News AI Overlay Engine...")


if __name__ == "__main__":
    main()
