""""Entry point for the on-chain wallet tracker (smart-money accumulation scanner).

    python3 run_wallet_tracker.py
"""
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.db import get_pool
from wallet_tracker.worker import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_LOCK_NAME = "signal_bot_wallet_tracker"
_LOCK_HEARTBEAT_SECONDS = 60


def acquire_single_instance_lock():
    """Hold a MariaDB advisory lock for the worker's entire lifetime."""
    connection = get_pool().get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT GET_LOCK(%s, 0)", (_LOCK_NAME,))
        row = cursor.fetchone()
        cursor.close()
        if not row or row[0] != 1:
            connection.close()
            return None
        return connection
    except Exception:
        connection.close()
        raise


def verify_lock_connection(connection):
    """Refresh the connection and verify it still owns the advisory lock."""
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT IS_USED_LOCK(%s), CONNECTION_ID()", (_LOCK_NAME,))
        row = cursor.fetchone()
        return bool(row and row[0] is not None and int(row[0]) == int(row[1]))
    finally:
        cursor.close()


def _keep_lock_alive(connection, stop_event, lock_lost_event, interval=_LOCK_HEARTBEAT_SECONDS):
    """Periodically keep the DB session alive and detect a lost advisory lock."""
    while not stop_event.wait(interval):
        try:
            if not verify_lock_connection(connection):
                logging.critical("Wallet tracker lost its MariaDB single-instance lock; stopping worker")
                lock_lost_event.set()
                return
        except Exception:
            logging.exception("Wallet tracker could not verify its single-instance lock; stopping worker")
            lock_lost_event.set()
            return


def release_single_instance_lock(connection):
    """Release the worker lock on the same connection that acquired it."""
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
        cursor.close()
    finally:
        connection.close()


def main():
    try:
        lock_connection = acquire_single_instance_lock()
    except Exception:
        logging.exception("Wallet tracker could not acquire its single-instance lock")
        return 1
    if lock_connection is None:
        logging.error("Wallet tracker already has an active instance; refusing to start")
        return 1

    stop_event = threading.Event()
    lock_lost_event = threading.Event()
    heartbeat = threading.Thread(
        target=_keep_lock_alive,
        args=(lock_connection, stop_event, lock_lost_event),
        name="wallet-tracker-lock-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    try:
        run(stop_event=lock_lost_event)
    finally:
        stop_event.set()
        heartbeat.join(timeout=_LOCK_HEARTBEAT_SECONDS + 5)
        release_single_instance_lock(lock_connection)
    return 1 if lock_lost_event.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
"