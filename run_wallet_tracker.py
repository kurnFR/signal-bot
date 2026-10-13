"""Entry point for the on-chain wallet tracker (smart-money accumulation scanner).

    python3 run_wallet_tracker.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet_tracker.worker import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    run()
