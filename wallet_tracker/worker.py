"""Wallet-tracker worker loop with aggregate wallet-quality gates."""
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
    """Refresh pool-level positions and auto-follow only quality wallet flows."""
    now = now or _utcnow()
    settings = settings or store.get_settings()
    periods = sorted(config.WALLET_TRACKER_PERIODS_HOURS)
    swaps = store.load_swaps_since(now - timedelta(hours=periods[-1]))
    candidates_total = 0
    for hours in periods:
        positions = analyzer.aggregate_swaps(swaps, hours, now, settings["min_net_usd"])
        store.replace_positions(hours, positions)
        candidates_total = len(positions)

    # Follow decisions aggregate USD flow across all pools for a wallet. Use
    # 24h and 7d evidence, not a single large swap or a single pool snapshot.
    auto_threshold = float(settings["auto_track_net_usd"])
    min_total_usd = max(auto_threshold, float(settings["min_net_usd"]) * 2)
    follow_candidates = analyzer.quality_follow_candidates(
        swaps=swaps,
        periods=[h for h in (24, 168) if h in periods],
        now=now,
        min_net_usd=auto_threshold,
        min_total_usd=min_total_usd,
        min_trade_count=3,
        min_net_flow_ratio=0.25,
    )
    store.auto_flag_wallets(follow_candidates, auto_threshold)
    logger.info(
        "wallet quality gate: eligible=%d (net>=%.0f USD, total>=%.0f USD, trades>=3, net/total>=25%%)",
        len(follow_candidates), auto_threshold, min_total_usd,
    )
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
    provider.set_base_delay(settings["api_delay_seconds"])
    started = time.monotonic()
    pools_scanned = swaps_new = 0
    errors = []
    # A volume floor avoids spending a trades request on clearly illiquid pools.
    # Unknown volume is retained because some provider payloads omit this field.
    min_pool_volume = max(float(settings["min_trade_usd"]) * 10, 100_000)
    for network in settings["networks"]:
        if not is_supported_chain(network):
            errors.append(f"unsupported network '{network}'")
            continue
        try:
            pools = provider.hot_pools(network, settings["pools_per_network"])
        except Exception as exc:
            logger.warning("%s: pool discovery failed: %s", network, exc)
            errors.append(f"{network}: {exc}")
            continue
        for pool in pools:
            if not _pool_matches_tokens(network, pool, settings):
                continue
            volume_24h = float(pool.get("volume_24h_usd") or 0)
            if 0 < volume_24h < min_pool_volume:
                logger.debug("%s %s skipped: 24h pool volume $%.0f below $%.0f",
                             network, pool.get("name"), volume_24h, min_pool_volume)
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
