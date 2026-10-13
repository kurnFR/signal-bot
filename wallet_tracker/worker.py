"""Wallet-tracker worker loop.

Each cycle:
  1. for every configured network, pick hot pools (trending + top volume)
  2. pull recent large swaps per pool and store them (idempotent)
  3. re-aggregate swaps per wallet for each period (1h/6h/24h/7d)
  4. store candidates (|net USD| >= threshold) and auto-monitor the big ones
  5. prune old swaps, publish a status row for the dashboard
A failure in one pool/network is logged and skipped; it never kills the loop.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import config
from wallet_tracker import analyzer, store
from wallet_tracker.chains import is_supported_chain
from wallet_tracker.providers import GeckoTerminalProvider

logger = logging.getLogger("wallet_tracker.worker")


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def recompute(now=None):
    """Steps 3-4. Returns number of distinct candidate positions (longest period)."""
    now = now or _utcnow()
    periods = sorted(config.WALLET_TRACKER_PERIODS_HOURS)
    swaps = store.load_swaps_since(now - timedelta(hours=periods[-1]))
    candidates_total = 0
    for hours in periods:
        positions = analyzer.aggregate_swaps(swaps, hours, now, config.WALLET_TRACKER_MIN_NET_USD)
        store.replace_positions(hours, positions)
        store.auto_flag_wallets(positions, config.WALLET_TRACKER_AUTO_TRACK_NET_USD)
        candidates_total = len(positions)
    return candidates_total


def run_cycle(provider):
    started = time.monotonic()
    pools_scanned = swaps_new = 0
    errors = []
    for network in config.WALLET_TRACKER_NETWORKS:
        if not is_supported_chain(network):
            errors.append(f"unsupported network '{network}'")
            continue
        try:
            pools = provider.hot_pools(network, config.WALLET_TRACKER_POOLS_PER_NETWORK)
        except Exception as exc:  # network down, bad payload, ...
            logger.warning("%s: pool discovery failed: %s", network, exc)
            errors.append(f"{network}: {exc}")
            continue
        for pool in pools:
            try:
                swaps = provider.pool_trades(network, pool, config.WALLET_TRACKER_MIN_TRADE_USD)
                swaps_new += store.insert_swaps(swaps)
                pools_scanned += 1
            except Exception as exc:
                logger.warning("%s %s: trade fetch failed: %s", network, pool.get("name"), exc)
                errors.append(f"{network}/{pool.get('name')}: {exc}")
    candidates = recompute()
    store.prune_swaps(config.WALLET_TRACKER_RETENTION_DAYS)
    elapsed = time.monotonic() - started
    store.set_status(pools_scanned, swaps_new, candidates, elapsed, "; ".join(errors[:3]) or None)
    logger.info("cycle done: pools=%d new_swaps=%d candidates=%d (%.1fs)",
                pools_scanned, swaps_new, candidates, elapsed)


def run():
    store.ensure_tables()
    if not config.WALLET_TRACKER_ENABLED:
        logger.warning("WALLET_TRACKER_ENABLED=false -- idle (set it to true to scan).")
        while True:
            time.sleep(300)
    provider = GeckoTerminalProvider(delay_seconds=config.WALLET_TRACKER_API_DELAY_SECONDS)
    logger.info("Wallet tracker started: networks=%s pools/network=%d min_trade=$%.0f min_net=$%.0f",
                config.WALLET_TRACKER_NETWORKS, config.WALLET_TRACKER_POOLS_PER_NETWORK,
                config.WALLET_TRACKER_MIN_TRADE_USD, config.WALLET_TRACKER_MIN_NET_USD)
    while True:
        try:
            run_cycle(provider)
        except Exception:
            logger.exception("cycle failed")
        time.sleep(config.WALLET_TRACKER_POLL_INTERVAL_SECONDS)
