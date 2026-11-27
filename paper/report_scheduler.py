"""Queue one Paper Trading performance report per hour, without a strategy worker."""

import logging
import os
import time

from notifications.telegram_notifier import is_telegram_delivery_authorized
from paper.outbox_store import OutboxConfig, OutboxStore
from paper.paper_report import format_paper_report

logger = logging.getLogger(__name__)


def queue_current_hour(database: OutboxStore, now: float) -> bool:
    """Use a durable hourly key so supervisor restarts cannot duplicate reports."""
    if not is_telegram_delivery_authorized():
        return False
    hour = int(now) // 3600
    return database.enqueue_telegram_outbox_event(
        event_key=f"paper-performance:{hour}",
        event_type="operational",
        payload={"text": format_paper_report()},
        deduplicate=True,
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    last_hour = None
    database = None
    while True:
        now = time.time()
        hour = int(now) // 3600
        if hour != last_hour:
            try:
                if database is None or database.pool is None:
                    database = OutboxStore(OutboxConfig(), logger)
                if queue_current_hour(database, now):
                    last_hour = hour
                    logger.info("Paper performance report queued for hour %s", hour)
            except Exception as exc:
                logger.warning("Paper performance report scheduler failed (%s)", type(exc).__name__)
        time.sleep(30)


if __name__ == "__main__":
    main()
