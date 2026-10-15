"""Wallet-tracker worker loop with aggregate wallet-quality gates."""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import config
from wallet_tracker import analyzer, store
from wallet_tracker.chains import is_supported_chain
from wallet_tracker.providers import BirdeyeProvider, GeckoTerminalProvider
from wallet_tracker.stablecoins import is_stablecoin_base_pool
from wallet_tracker.telegram import WalletTelegramNotifier

logger = logging.getLogger("wallet_tracker.worker")

# Keep rate limiting isolated per chain. A blocked provider endpoint must not
# prevent other configured networks from being scanned in the same cycle.
_NETWORK_COOLDOWN_UNTIL = {}
_NETWORK_COOLDOWN_SECONDS = 300
_network_rotation_offset = 0


def _is_rate_limited(exc):
    return getattr(getattr(exc, "response", None), "status_code", None) == 429


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
    inactive_days = max(1, min(int(config.WALLET_TRACKER_AUTO_INACTIVE_DAYS), 365))
    expired = store.expire_inactive_auto_wallets(inactive_days)
    if expired:
        logger.info("expired %d inactive auto-follow wallet(s) after %d days", expired, inactive_days)
    logger.info(
        "wallet quality gate: eligible=%d (per-pool net>=%.0f USD, total>=%.0f USD, trades>=3, net/total>=25%%; high-frequency round-trip gate enabled)",
        len(follow_candidates), auto_threshold, min_total_usd,
    )
    return candidates_total


def _pool_matches_tokens(network, pool, settings, selected_tokens=None):
    # GeckoTerminal's buy/sell kind is relative to the base token. Exclude pools
    # whose base asset is a stablecoin, but keep normal volatile-token/stablecoin
    # quote pairs (e.g. ARK/USDT), which are relevant trading markets.
    if is_stablecoin_base_pool(network, pool):
        return False
    if settings.get("token_mode", "all") == "all":
        return True
    address = pool.get("base_token_address")
    if not address:
        return False
    normalized = str(address) if network == "solana" else str(address).lower()
    selector = network + ":" + normalized
    if selected_tokens is None:
        selected_tokens = set(settings.get("token_addresses", []))
    return selector in selected_tokens




def dispatch_telegram_alerts(notifier=None):
    """Queue and deliver fresh qualifying swaps for followed wallets."""
    notifier = notifier or WalletTelegramNotifier()
    if not notifier.configured:
        logger.warning(
            "Wallet Telegram alerts paused: TELEGRAM_TOKEN/TELEGRAM_BOT_TOKEN "
            "or TELEGRAM_CHAT_ID is missing"
        )
        return 0
    settings = store.get_settings()
    cooldown_minutes = max(1, int(config.WALLET_TRACKER_ALERT_COOLDOWN_MINUTES))
    queued = store.queue_new_wallet_alerts(
        settings["min_trade_usd"], max_age_minutes=30,
        cooldown_minutes=cooldown_minutes,
    )
    if queued:
        logger.info("queued %d followed-wallet Telegram alert(s)", queued)
    sent = 0
    for alert in store.get_pending_wallet_alerts(limit=50):
        try:
            ok = notifier.send(alert)
        except Exception as exc:
            ok = False
            # Never persist arbitrary exception text: transport errors can include
            # credential-bearing request URLs.
            error = f"Telegram delivery failed ({type(exc).__name__})"
        else:
            error = None if ok else "Telegram API returned an unsuccessful response"
        try:
            store.mark_wallet_alert_attempt(
                alert["swap_id"], success=ok, error=error,
            )
        except Exception:
            logger.exception(
                "could not persist wallet Telegram delivery for swap_id=%s",
                alert.get("swap_id"),
            )
        if ok:
            sent += 1
    if sent:
        logger.info("sent %d followed-wallet Telegram alert(s)", sent)
    return sent


def run_cycle(provider, fallback_provider=None):
    settings = store.get_settings()
    store.prune_wallet_alerts(config.WALLET_TRACKER_ALERT_RETENTION_DAYS)
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
    global _network_rotation_offset
    networks = list(settings["networks"])
    if networks:
        offset = _network_rotation_offset % len(networks)
        networks = networks[offset:] + networks[:offset]
        _network_rotation_offset = (offset + 1) % len(networks)

    now_mono = time.monotonic()
    selected_tokens = set(settings.get("token_addresses", []))
    for network in networks:
        if not is_supported_chain(network):
            errors.append(f"unsupported network '{network}'")
            continue
        network_in_cooldown = _NETWORK_COOLDOWN_UNTIL.get(network, 0) > now_mono
        if network_in_cooldown and fallback_provider is None:
            logger.info("%s: skipped during rate-limit cooldown (no fallback configured)", network)
            continue
        if network_in_cooldown:
            logger.info("%s: using Birdeye during GeckoTerminal rate-limit cooldown", network)
        try:
            # Fetch extra pool metadata, but cap trade endpoints to the configured
            # pool budget after volume/token filtering.
            pools = provider.hot_pools(network, settings["pools_per_network"] * 2)
        except Exception as exc:
            logger.warning("%s: pool discovery failed (%s)", network, type(exc).__name__)
            errors.append(f"{network}: pool discovery failed ({type(exc).__name__})")
            if _is_rate_limited(exc):
                _NETWORK_COOLDOWN_UNTIL[network] = time.monotonic() + _NETWORK_COOLDOWN_SECONDS
                continue
            continue
        pool_requests = 0
        network_fallback_active = network_in_cooldown
        for pool in pools:
            if pool_requests >= settings["pools_per_network"]:
                break
            if not _pool_matches_tokens(network, pool, settings, selected_tokens):
                continue
            volume_24h = float(pool.get("volume_24h_usd") or 0)
            if 0 < volume_24h < min_pool_volume:
                logger.debug("%s %s skipped: 24h pool volume $%.0f below $%.0f",
                             network, pool.get("name"), volume_24h, min_pool_volume)
                continue
            pool_requests += 1
            active_provider = fallback_provider if network_fallback_active else provider
            try:
                swaps = active_provider.pool_trades(network, pool, settings["min_trade_usd"])
                swaps_new += store.insert_swaps(swaps)
                pools_scanned += 1
            except Exception as exc:
                # Switch to Birdeye for the remainder of this network's cycle
                # when GeckoTerminal fails. The DB's unique key deduplicates
                # overlapping trades from either source.
                if active_provider is provider and fallback_provider is not None:
                    if _is_rate_limited(exc):
                        _NETWORK_COOLDOWN_UNTIL[network] = time.monotonic() + _NETWORK_COOLDOWN_SECONDS
                    network_fallback_active = True
                    try:
                        swaps = fallback_provider.pool_trades(network, pool, settings["min_trade_usd"])
                        swaps_new += store.insert_swaps(swaps)
                        pools_scanned += 1
                        logger.warning(
                            "%s %s: GeckoTerminal trade request failed (%s); Birdeye fallback succeeded",
                            network, pool.get("name"), type(exc).__name__,
                        )
                        continue
                    except Exception as fallback_exc:
                        logger.warning(
                            "%s %s: GeckoTerminal and Birdeye trade fetch failed (%s / %s)",
                            network, pool.get("name"), type(exc).__name__, type(fallback_exc).__name__,
                        )
                        errors.append(
                            f"{network}/{pool.get('name')}: both trade providers failed "
                            f"({type(exc).__name__}/{type(fallback_exc).__name__})"
                        )
                        continue
                logger.warning("%s %s: trade fetch failed (%s)", network, pool.get("name"), type(exc).__name__)
                errors.append(f"{network}/{pool.get('name')}: trade fetch failed ({type(exc).__name__})")
                if _is_rate_limited(exc):
                    _NETWORK_COOLDOWN_UNTIL[network] = time.monotonic() + _NETWORK_COOLDOWN_SECONDS
                    break
    candidates = recompute(settings=settings)
    store.prune_swaps(settings["retention_days"])
    elapsed = time.monotonic() - started
    store.set_status(pools_scanned, swaps_new, candidates, elapsed, "; ".join(errors[:3]) or None)
    logger.info("cycle done: pools=%d new_swaps=%d candidates=%d (%.1fs)",
                pools_scanned, swaps_new, candidates, elapsed)
    dispatch_telegram_alerts()


def run(stop_event=None):
    """Run polling cycles until stopped; optional event is used if lock ownership is lost."""
    store.ensure_tables()
    settings = store.get_settings()
    provider = GeckoTerminalProvider(delay_seconds=settings["api_delay_seconds"])
    try:
        fallback_provider = BirdeyeProvider() if os.getenv("BIRDEYE_API_KEY", "").strip() else None
    except Exception:
        logger.exception("Could not initialize optional Birdeye fallback; GeckoTerminal remains active")
        fallback_provider = None
    if fallback_provider:
        logger.info("Birdeye free-tier pair-trade fallback enabled (1 request/second pacing).")
    else:
        logger.info("Birdeye fallback disabled; set BIRDEYE_API_KEY to enable it.")
    logger.info("Wallet tracker started; settings are loaded from the dashboard.")
    while stop_event is None or not stop_event.is_set():
        try:
            run_cycle(provider, fallback_provider=fallback_provider)
            settings = store.get_settings()
        except Exception:
            logger.exception("cycle failed")
            settings = store.get_settings()
        if stop_event is not None:
            if stop_event.wait(settings["poll_interval_seconds"]):
                break
        else:
            time.sleep(settings["poll_interval_seconds"])
