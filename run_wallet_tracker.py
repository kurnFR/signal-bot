"""Entry point for the on-chain wallet tracker (smart-money accumulation scanner).

    python3 run_wallet_tracker.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.db import get_pool
from wallet_tracker.worker import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_LOCK_NAME = "signal_bot_wallet_tracker"


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
    try:
        run()
    finally:
        release_single_instance_lock(lock_connection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
