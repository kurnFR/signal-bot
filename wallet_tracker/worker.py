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


def recompute(now=None, settings=None):
    """Steps 3-4. Returns number of distinct candidate positions (longest period)."""
    now = now or _utcnow()
    settings = settings or store.get_settings()
    periods = sorted(config.WALLET_TRACKER_PERIODS_HOURS)
    swaps = store.load_swaps_since(now - timedelta(hours=periods[-1]))
    candidates_total = 0
    for hours in periods:
        positions = analyzer.aggregate_swaps(swaps, hours, now, settings["min_net_usd"])
        store.replace_positions(hours, positions)
        store.auto_flag_wallets(positions, settings["auto_track_net_usd"])
        candidates_total = len(positions)
    return candidates_total


def _pool_matches_tokens(network, pool, settings):
    if settings.get("token_mode", "all") == "all":
        return True
    address = pool.get("base_token_address")
    if not address:
        return False
    normalized = str(address) if network == "solana" else str(address).lower()
    selector = network + ":" + normalized
    return selector in set(settings.get("token_addresses", []))


def run_cycle(provider):
    settings = store.get_settings()
    if not settings["enabled"]:
        store.set_status(0, 0, 0, 0, "Scanner disabled in Wallet Tracking settings")
        return
    provider.delay = settings["api_delay_seconds"]
    started = time.monotonic()
    pools_scanned = swaps_new = 0
    errors = []
    for network in settings["networks"]:
        if not is_supported_chain(network):
            errors.append(f"unsupported network '{network}'")
            continue
        try:
            pools = provider.hot_pools(network, settings["pools_per_network"])
        except Exception as exc:  # network down, bad payload, ...
            logger.warning("%s: pool discovery failed: %s", network, exc)
            errors.append(f"{network}: {exc}")
            continue
        for pool in pools:
            if not _pool_matches_tokens(network, pool, settings):
                continue
            try:
                swaps = provider.pool_trades(network, pool, settings["min_trade_usd"])
                swaps_new += store.insert_swaps(swaps)
                pools_scanned += 1
            except Exception as exc:
                logger.warning("%s %s: trade fetch failed: %s", network, pool.get("name"), exc)
                errors.append(f"{network}/{pool.get('name')}: {exc}")
    candidates = recompute(settings=settings)
    store.prune_swaps(settings["retention_days"])
    elapsed = time.monotonic() - started
    store.set_status(pools_scanned, swaps_new, candidates, elapsed, "; ".join(errors[:3]) or None)
    logger.info("cycle done: pools=%d new_swaps=%d candidates=%d (%.1fs)",
                pools_scanned, swaps_new, candidates, elapsed)


def run():
    store.ensure_tables()
    settings = store.get_settings()
    provider = GeckoTerminalProvider(delay_seconds=settings["api_delay_seconds"])
    logger.info("Wallet tracker started; settings are loaded from the dashboard.")
    while True:
        try:
            run_cycle(provider)
            settings = store.get_settings()
        except Exception:
            logger.exception("cycle failed")
            settings = store.get_settings()
        time.sleep(settings["poll_interval_seconds"])
