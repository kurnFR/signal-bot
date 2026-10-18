"""GeckoTerminal public API client (no API key).

Docs: https://www.geckoterminal.com/dex-api  (free tier ~30 calls/minute, so
every request is throttled). Trades are returned per pool, newest first, for
the last 24h (max ~300). `kind` is relative to the pool's BASE token:
  buy  -> wallet received the base token (to_token)
  sell -> wallet sent the base token     (from_token)
"""
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from wallet_tracker.chains import normalize_wallet

logger = logging.getLogger("wallet_tracker.provider")

BASE_URL = "https://api.geckoterminal.com/api/v2"


def _to_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _retry_after_seconds(value, now=None):
    """Parse Retry-After as seconds or an HTTP date; return None if invalid."""
    if value is None:
        return None
    seconds = _to_float(value)
    if seconds is not None:
        return max(0.0, seconds)
    try:
        target = parsedate_to_datetime(str(value))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (target - current).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_time(value):
    """ISO-8601 -> naive UTC datetime (the DB session runs in UTC)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_trade(chain, pool_address, pair, attrs):
    """Normalise one GeckoTerminal trade `attributes` dict into a swap dict.
    Returns None for anything incomplete or invalid (never raises)."""
    if not isinstance(attrs, dict):
        return None
    kind = str(attrs.get("kind", "")).lower()
    if kind not in ("buy", "sell"):
        return None
    wallet = normalize_wallet(chain, attrs.get("tx_from_address") or "")
    usd = _to_float(attrs.get("volume_in_usd"))
    block_time = _parse_time(attrs.get("block_timestamp"))
    tx_hash = attrs.get("tx_hash")
    if not (wallet and tx_hash and block_time and usd and usd > 0):
        return None

    if kind == "buy":
        tokens = _to_float(attrs.get("to_token_amount"))
        price = _to_float(attrs.get("price_to_in_usd"))
    else:
        tokens = _to_float(attrs.get("from_token_amount"))
        price = _to_float(attrs.get("price_from_in_usd"))
    if not tokens or tokens <= 0:
        return None
    if not price or price <= 0:
        price = usd / tokens

    return {
        "chain": chain,
        "wallet": wallet,
        "pool_address": str(pool_address).lower() if chain != "solana" else str(pool_address),
        "pair": (pair or "")[:80],
        "side": kind,
        "usd_value": usd,
        "token_amount": tokens,
        "price_usd": price,
        "tx_hash": str(tx_hash)[:100],
        "block_time": block_time,
    }


class GeckoTerminalProvider:
    def __init__(self, delay_seconds=4.0, timeout=15, session=None, fast_fail_429=False):
        self.base_delay = max(float(delay_seconds), 0.5)
        self.delay = self.base_delay
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json;version=20230302"})
        self._last_call = 0.0
        self._pool_cache = {}
        self.pool_cache_ttl = 300.0
        self.fast_fail_429 = bool(fast_fail_429)

    def set_base_delay(self, delay_seconds):
        """Apply dashboard delay changes without discarding adaptive throttling."""
        self.base_delay = max(float(delay_seconds), 0.5)
        self.delay = max(self.delay, self.base_delay)

    def _get(self, path, params=None, fast_fail_429=None):
        should_fast_fail = self.fast_fail_429 if fast_fail_429 is None else bool(fast_fail_429)
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(1 if should_fast_fail else 3):
            self._last_call = time.monotonic()
            resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=self.timeout)
            if resp.status_code == 429:
                # Exponential cooldown and adaptive request pacing. Honor a
                # provider Retry-After header when available, but cap one wait.
                self.delay = min(max(self.delay * 2, self.delay + 1), 60.0)
                if should_fast_fail:
                    logger.warning(
                        "GeckoTerminal 429 rate limit hit on %s; failing fast for fallback provider (request delay now %.1fs)",
                        path, self.delay,
                    )
                    resp.raise_for_status()
                retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                # If the provider asks us to wait longer than our synchronous
                # retry budget, defer to the worker's network cooldown instead
                # of retrying early and worsening the rate limit.
                if retry_after is not None and retry_after > 120:
                    logger.warning(
                        "GeckoTerminal Retry-After %.0fs exceeds retry budget; deferring request",
                        retry_after,
                    )
                    resp.raise_for_status()
                backoff = min(max(retry_after if retry_after is not None else (20 * (2 ** attempt)), 1), 120)
                if attempt < 2:
                    logger.warning(
                        "GeckoTerminal rate limit hit; retry %d/2 in %.0fs (no trade fallback active; request delay now %.1fs)",
                        attempt + 1, backoff, self.delay,
                    )
                    time.sleep(backoff)
                    continue
                logger.error("GeckoTerminal rate limit persists after retries for %s", path)
            resp.raise_for_status()
            # Recover pacing slowly, never instantly return to the burst rate.
            if self.delay > self.base_delay:
                self.delay = max(self.base_delay, self.delay * 0.9)
            return resp.json()
        return {}

    @staticmethod
    def _pools_from(payload):
        """Extract valid pools while tolerating malformed provider records."""
        if not isinstance(payload, dict):
            return []
        pools = []
        included_items = payload.get("included") or []
        included = {
            item.get("id"): item
            for item in included_items
            if isinstance(item, dict) and item.get("id")
        } if isinstance(included_items, list) else {}
        data_items = payload.get("data") or []
        if not isinstance(data_items, list):
            return []
        for item in data_items:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes")
            if not isinstance(attrs, dict):
                continue
            address = attrs.get("address")
            if not isinstance(address, str) or not address.strip():
                continue
            relationships = item.get("relationships")
            if not isinstance(relationships, dict):
                relationships = {}
            tokens = {}
            for side in ("base_token", "quote_token"):
                rel = relationships.get(side)
                rel = rel.get("data") if isinstance(rel, dict) else None
                rel = rel if isinstance(rel, dict) else {}
                token_obj = included.get(rel.get("id"), {})
                token_attrs = token_obj.get("attributes") if isinstance(token_obj, dict) else {}
                if not isinstance(token_attrs, dict):
                    token_attrs = {}
                token_id = str(rel.get("id", ""))
                token_address = (attrs.get(side + "_address") or token_attrs.get("address") or
                                 (token_id.split("_", 1)[1] if "_" in token_id else None))
                tokens[side + "_address"] = str(token_address) if token_address else None
                tokens[side + "_symbol"] = token_attrs.get("symbol") or attrs.get(side + "_symbol")
            volume = attrs.get("volume_usd")
            volume = volume.get("h24") if isinstance(volume, dict) else None
            vol = _to_float(volume)
            pools.append({
                "address": address.strip(), "name": attrs.get("name") or address.strip(),
                "volume_24h_usd": vol or 0.0, **tokens,
            })
        return pools
    def _find_cached_pools(self, network):
        """Find any cached pools for a network regardless of limit."""
        for (k_net, _), c_val in self._pool_cache.items():
            if str(k_net) == str(network) and c_val and c_val[1]:
                return c_val[1]
        return None

    def hot_pools(self, network, limit):
        """Trending pools first, cached briefly to reduce discovery API calls."""
        cache_key = (str(network), int(limit))
        now = time.monotonic()
        cached = self._pool_cache.get(cache_key)
        if cached and cached[1] and now - cached[0] < self.pool_cache_ttl:
            return [dict(pool) for pool in cached[1]]

        seen, result = set(), []
        for path in (f"/networks/{network}/trending_pools", f"/networks/{network}/pools"):
            if len(result) >= limit:
                break
            try:
                payload = self._get(path, fast_fail_429=self.fast_fail_429)
            except requests.RequestException as exc:
                logger.warning("pool list %s failed: %s", path, exc)
                # Once throttled, do not immediately call the alternate endpoint.
                if getattr(getattr(exc, "response", None), "status_code", None) == 429:
                    # Preserve the last known pool universe during a provider
                    # outage; the worker can still scan it with the fallback feed.
                    fallback_pools = (cached[1] if cached and cached[1] else self._find_cached_pools(network))
                    if fallback_pools:
                        logger.warning(
                            "GeckoTerminal discovery rate-limited; using %d cached pools for %s",
                            len(fallback_pools), network,
                        )
                        return [dict(pool) for pool in fallback_pools]
                    raise
                continue
            for pool in self._pools_from(payload):
                key = pool["address"].lower()
                if key not in seen:
                    seen.add(key)
                    result.append(pool)
                if len(result) >= limit:
                    break
        if result:
            self._pool_cache[cache_key] = (time.monotonic(), [dict(pool) for pool in result])
        else:
            fallback_pools = (cached[1] if cached and cached[1] else self._find_cached_pools(network))
            if fallback_pools:
                logger.warning("GeckoTerminal discovery returned no pools; keeping %d cached pools for %s",
                               len(fallback_pools), network)
                return [dict(pool) for pool in fallback_pools]
        return result

    def pool_trades(self, network, pool, min_trade_usd):
        payload = self._get(
            f"/networks/{network}/pools/{pool['address']}/trades",
            params={"trade_volume_in_usd_greater_than": int(min_trade_usd)},
            fast_fail_429=self.fast_fail_429,
        )
        swaps = []
        for item in (payload or {}).get("data", []) or []:
            swap = parse_trade(network, pool["address"], pool["name"], item.get("attributes"))
            if swap and swap["usd_value"] >= min_trade_usd:
                swaps.append(swap)
        return swaps


# Birdeye's free Standard plan includes pair-level swap history on supported
# chains. It requires an API key; do not instantiate this provider without one.
BIRDEYE_BASE_URL = "https://public-api.birdeye.so"
BIRDEYE_CHAIN_IDS = {
    "eth": "ethereum",
    "solana": "solana",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "polygon_pos": "polygon",
}


def _birdeye_time(value):
    """Convert Birdeye Unix timestamps or ISO timestamps to naive UTC."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return _parse_time(value)


def _nested_trade_token(value):
    """Return a normalized view of a provider token object."""
    if not isinstance(value, dict):
        return {}
    amount = _to_float(value.get("uiAmount"))
    if amount is None:
        amount = _to_float(value.get("ui_amount"))
    if amount is None:
        amount = _to_float(value.get("ui_change_amount"))
    if amount is None:
        amount = _to_float(value.get("amount"))
    price = _to_float(value.get("price"))
    address = value.get("address") or value.get("token_address") or value.get("mint")
    return {"address": str(address) if address else None, "amount": amount, "price": price,
            "type_swap": str(value.get("type_swap") or "").lower()}


def parse_birdeye_trade(chain, pool, item):
    """Normalize a Birdeye pair-trade row; reject incomplete/ambiguous rows."""
    if not isinstance(item, dict) or not isinstance(pool, dict):
        return None
    wallet = normalize_wallet(chain, item.get("owner") or item.get("tx_from_address") or "")
    tx_hash = item.get("txHash") or item.get("tx_hash")
    block_time = _birdeye_time(item.get("blockUnixTime", item.get("block_unix_time",
                                item.get("block_timestamp", item.get("blockTime")))))
    usd = _to_float(item.get("volumeUSD", item.get("volume_usd",
                      item.get("volume_in_usd", item.get("usd_value")))))
    if not (wallet and tx_hash and block_time and usd is not None and usd > 0):
        return None

    base_address = pool.get("base_token_address")
    from_token = _nested_trade_token(item.get("from"))
    to_token = _nested_trade_token(item.get("to"))
    base = _nested_trade_token(item.get("base"))
    quote = _nested_trade_token(item.get("quote"))

    side = str(item.get("side") or item.get("kind") or "").lower()
    if side not in ("buy", "sell"):
        # V3 responses describe the base token's movement explicitly.
        if base.get("type_swap") in ("to", "from"):
            side = "buy" if base["type_swap"] == "to" else "sell"
        elif base_address and to_token.get("address") and (
                to_token["address"].lower() == str(base_address).lower()):
            side = "buy"
        elif base_address and from_token.get("address") and (
                from_token["address"].lower() == str(base_address).lower()):
            side = "sell"
        elif base_address and base.get("address") and (
                base["address"].lower() == str(base_address).lower()):
            if base.get("type_swap") in ("to", "from"):
                side = "buy" if base["type_swap"] == "to" else "sell"
    if side not in ("buy", "sell"):
        return None

    # Prefer the pool's base token amount; only accept generic from/to fields
    # when their token address matches the pool base token.
    if base.get("type_swap") in ("to", "from"):
        selected = base
    elif base_address and to_token.get("address") and (
            to_token["address"].lower() == str(base_address).lower()):
        selected = to_token
    elif base_address and from_token.get("address") and (
            from_token["address"].lower() == str(base_address).lower()):
        selected = from_token
    else:
        # Never guess which token amount represents the pool's base asset.
        selected = base if base.get("address") and base.get("address").lower() == str(base_address or "").lower() else {}
    tokens = selected.get("amount")
    price = selected.get("price")
    if not tokens or tokens <= 0:
        return None
    if not price or price <= 0:
        price = usd / tokens

    address = pool.get("address") or item.get("pool_id") or item.get("poolAddress")
    if not address:
        return None
    return {
        "chain": chain,
        "wallet": wallet,
        "pool_address": str(address).lower() if chain != "solana" else str(address),
        "pair": str(pool.get("name") or pool.get("pair") or address)[:80],
        "side": side,
        "usd_value": usd,
        "token_amount": tokens,
        "price_usd": price,
        "tx_hash": str(tx_hash)[:100],
        "block_time": block_time,
    }


class BirdeyeProvider:
    """Optional free-tier pair-trade fallback (requires BIRDEYE_API_KEY)."""

    def __init__(self, api_key=None, delay_seconds=1.05, timeout=15, session=None):
        import os
        self.api_key = (api_key if api_key is not None else os.getenv("BIRDEYE_API_KEY", "")).strip()
        if not self.api_key:
            raise ValueError("BIRDEYE_API_KEY is required for the Birdeye fallback")
        self.delay = max(float(delay_seconds), 1.0)  # Standard plan: 1 request/second.
        self.timeout = timeout
        self.session = session or requests.Session()
        self._last_call = 0.0

    def _get(self, path, network, params=None):
        chain = BIRDEYE_CHAIN_IDS.get(network)
        if not chain:
            raise ValueError(f"Birdeye does not support Wallet Tracker network {network!r}")
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        response = self.session.get(
            f"{BIRDEYE_BASE_URL}{path}",
            params=params,
            headers={"X-API-KEY": self.api_key, "x-chain": chain, "accept": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise ValueError("Birdeye returned an unsuccessful trade response")
        return payload

    def pool_trades(self, network, pool, min_trade_usd):
        payload = self._get(
            "/defi/txs/pair",
            network,
            params={"address": pool["address"], "offset": 0, "limit": 50,
                    "tx_type": "swap", "sort_type": "desc"},
        )
        data = payload.get("data") or {}
        items = data.get("items", []) if isinstance(data, dict) else []
        swaps = []
        for item in items or []:
            swap = parse_birdeye_trade(network, pool, item)
            if swap and swap["usd_value"] >= float(min_trade_usd):
                swaps.append(swap)
        if items and not swaps:
            logger.debug(
                "Birdeye returned %d items for %s %s but none passed parsing or min_trade_usd ($%.0f)",
                len(items), network, pool.get("name"), float(min_trade_usd),
            )
        return swaps


# -----------------------------------------------------------------------------
# DexScreener Public Provider (Keyless Pool Discovery Fallback)
# -----------------------------------------------------------------------------
DEXSCREENER_CHAIN_IDS = {
    "eth": ("ethereum", ["WETH", "ETH", "USDC"]),
    "solana": ("solana", ["SOL", "USDC", "solana"]),
    "base": ("base", ["base", "USDC", "AERO"]),
    "bsc": ("bsc", ["bsc", "BNB", "USDT"]),
    "arbitrum": ("arbitrum", ["arbitrum", "ARB", "USDC"]),
    "polygon_pos": ("polygon", ["polygon", "MATIC", "USDC"]),
}


class DexScreenerProvider:
    """Keyless public DexScreener provider for fallback pool discovery.

    Used when GeckoTerminal hits rate limits or is in network cooldown.
    Requires no API key and queries DexScreener's public search API.
    """

    def __init__(self, timeout=10, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "signal-bot/1.0"})
        self._pool_cache = {}
        self.pool_cache_ttl = 300.0

    def hot_pools(self, network, limit=10):
        """Discover active DEX pools on the network using DexScreener search."""
        chain_info = DEXSCREENER_CHAIN_IDS.get(network)
        if not chain_info:
            logger.debug("DexScreener does not have chain mapping for %s", network)
            return []
        ds_chain, queries = chain_info

        cache_key = (str(network), int(limit))
        now = time.monotonic()
        cached = self._pool_cache.get(cache_key)
        if cached and cached[1] and now - cached[0] < self.pool_cache_ttl:
            return [dict(p) for p in cached[1]]

        seen = set()
        pools = []
        for q in queries:
            if len(pools) >= limit * 2:
                break
            try:
                resp = self.session.get(
                    f"https://api.dexscreener.com/latest/dex/search?q={q}",
                    timeout=self.timeout,
                )
                if resp.status_code != 200:
                    continue
                pairs = (resp.json() or {}).get("pairs") or []
                for p in pairs:
                    if p.get("chainId") != ds_chain:
                        continue
                    addr = p.get("pairAddress")
                    if not addr:
                        continue
                    key = addr.lower() if network != "solana" else addr
                    if key in seen:
                        continue
                    seen.add(key)
                    base = p.get("baseToken") or {}
                    quote = p.get("quoteToken") or {}
                    vol_24h = float((p.get("volume") or {}).get("h24") or 0.0)
                    pools.append({
                        "address": addr,
                        "name": f"{base.get('symbol', '')} / {quote.get('symbol', '')}".strip(" /") or addr,
                        "volume_24h_usd": vol_24h,
                        "base_token_address": base.get("address"),
                        "base_token_symbol": base.get("symbol"),
                        "quote_token_address": quote.get("address"),
                        "quote_token_symbol": quote.get("symbol"),
                    })
            except Exception as exc:
                logger.debug("DexScreener search query %r for %s failed: %s", q, network, exc)

        pools.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
        result = pools[:limit]
        if result:
            self._pool_cache[cache_key] = (now, [dict(p) for p in result])
        elif cached and cached[1]:
            return [dict(p) for p in cached[1]]
        return result
