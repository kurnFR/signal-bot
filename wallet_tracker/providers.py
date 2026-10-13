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
    def __init__(self, delay_seconds=4.0, timeout=15, session=None):
        self.base_delay = max(float(delay_seconds), 0.5)
        self.delay = self.base_delay
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json;version=20230302"})
        self._last_call = 0.0

    def set_base_delay(self, delay_seconds):
        """Apply dashboard delay changes without discarding adaptive throttling."""
        self.base_delay = max(float(delay_seconds), 0.5)
        self.delay = max(self.delay, self.base_delay)

    def _get(self, path, params=None):
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self._last_call = time.monotonic()
            resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=self.timeout)
            if resp.status_code == 429:
                # Exponential cooldown and adaptive request pacing. Honor a
                # provider Retry-After header when available, but cap one wait.
                self.delay = min(max(self.delay * 2, self.delay + 1), 60.0)
                retry_after = _to_float(resp.headers.get("Retry-After"))
                backoff = min(max(retry_after or (20 * (2 ** attempt)), 1), 120)
                if attempt < 2:
                    logger.warning(
                        "GeckoTerminal rate limit hit; retry %d/2 in %.0fs (request delay now %.1fs)",
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
        pools = []
        included = {item.get("id"): item for item in (payload or {}).get("included", []) or [] if item.get("id")}
        for item in (payload or {}).get("data", []) or []:
            attrs = item.get("attributes") or {}
            address = attrs.get("address")
            if not address:
                continue
            relationships = item.get("relationships") or {}
            tokens = {}
            for side in ("base_token", "quote_token"):
                rel = (relationships.get(side) or {}).get("data") or {}
                token_obj = included.get(rel.get("id"), {})
                token_attrs = token_obj.get("attributes") or {}
                token_address = (attrs.get(side + "_address") or token_attrs.get("address") or
                                 (str(rel.get("id", "")).split("_", 1)[1] if "_" in str(rel.get("id", "")) else None))
                tokens[side + "_address"] = str(token_address) if token_address else None
                tokens[side + "_symbol"] = token_attrs.get("symbol") or attrs.get(side + "_symbol")
            vol = _to_float(((attrs.get("volume_usd") or {}).get("h24")))
            pools.append({
                "address": address, "name": attrs.get("name") or address,
                "volume_24h_usd": vol or 0.0, **tokens,
            })
        return pools
    def hot_pools(self, network, limit):
        """Trending pools first (early-detection bias), then top pools, de-duplicated."""
        seen, result = set(), []
        for path in (f"/networks/{network}/trending_pools", f"/networks/{network}/pools"):
            if len(result) >= limit:
                break
            try:
                payload = self._get(path)
            except requests.RequestException as exc:
                logger.warning("pool list %s failed: %s", path, exc)
                continue
            for pool in self._pools_from(payload):
                key = pool["address"].lower()
                if key not in seen:
                    seen.add(key)
                    result.append(pool)
                if len(result) >= limit:
                    break
        return result

    def pool_trades(self, network, pool, min_trade_usd):
        payload = self._get(
            f"/networks/{network}/pools/{pool['address']}/trades",
            params={"trade_volume_in_usd_greater_than": int(min_trade_usd)},
        )
        swaps = []
        for item in (payload or {}).get("data", []) or []:
            swap = parse_trade(network, pool["address"], pool["name"], item.get("attributes"))
            if swap and swap["usd_value"] >= min_trade_usd:
                swaps.append(swap)
        return swaps
