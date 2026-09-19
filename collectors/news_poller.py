"""
Crypto news poller (CryptoPanic) -- Phase 1 "data plumbing" for the News AI
Overlay strategy. This collector only fetches and stores news; no AI
reasoning happens here (see NEWS_AI_STRATEGY_PLAN.md for the full design and
phased rollout). It's additive and independent of the existing OHLCV/funding
collectors -- if CRYPTOPANIC_API_KEY is unset, this poller logs a warning and
exits cleanly rather than crashing anything else.

CryptoPanic API docs: https://cryptopanic.com/developers/api/

Usage:
    python3 -m collectors.news_poller
"""
import logging
import sys
import os
import time
import json
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    SYMBOLS, CRYPTOPANIC_API_KEY, CRYPTOPANIC_API_URL,
    NEWS_POLL_INTERVAL_SECONDS,
)
# NEWS_RELEVANCE_KEYWORDS (config.py) is reserved for _is_relevant() below
# once the keyword filter is enabled -- not imported yet since it's unused
# while that filter is a no-op.
from db.db import upsert_news_events, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("news_poller")

SESSION = requests.Session()

# CryptoPanic wants base currency codes (BTC, ETH), not exchange pair
# symbols (BTCUSDT) -- strip the quote asset for the API call.
_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")


def _base_currency(symbol):
    for suffix in _QUOTE_SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _is_relevant(title):
    """
    Placeholder for the NEWS_RELEVANCE_KEYWORDS filter (config.py). Phase 1
    deliberately stores everything CryptoPanic returns for the configured
    currencies -- unconditionally True for now -- so the keyword list can be
    validated/tuned against real stored data before it starts silently
    dropping stories. Wire in the real filter here once that's done:
        return any(kw in title.lower() for kw in NEWS_RELEVANCE_KEYWORDS)
    """
    return True


def fetch_news(currencies, retries=3):
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "currencies": ",".join(currencies),
        "public": "true",
        "kind": "news",
    }
    for attempt in range(retries):
        try:
            resp = SESSION.get(CRYPTOPANIC_API_URL, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"Failed to fetch news after {retries} retries")
    return []


def _parse_published_at(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def rows_from_response(raw_items):
    rows = []
    for item in raw_items:
        title = item.get("title", "")
        if not title or not _is_relevant(title):
            continue
        tagged_symbols = ",".join(
            c.get("code", "") for c in item.get("currencies", []) if c.get("code")
        )
        rows.append({
            "source": "cryptopanic",
            "category": "crypto_news",
            "external_id": str(item["id"]),
            "headline": title,
            "url": item.get("url"),
            "symbols": tagged_symbols or None,
            "country": None,
            "impact": None,
            "published_at": _parse_published_at(item.get("published_at")),
            "scheduled_at": None,
            "actual_value": None,
            "forecast_value": None,
            "previous_value": None,
            "raw_payload": json.dumps(item),
        })
    return rows


_FREE_RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]


def fetch_free_rss_news(currencies):
    rows = []
    for feed_url in _FREE_RSS_FEEDS:
        try:
            resp = SESSION.get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if not resp.ok:
                continue
            root = ET.fromstring(resp.content)
            for item in root.findall("./channel/item"):
                title_el = item.find("title")
                link_el = item.find("link")
                pub_date_el = item.find("pubDate")
                guid_el = item.find("guid")

                title = title_el.text.strip() if title_el is not None and title_el.text else ""
                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                ext_id = (guid_el.text.strip() if guid_el is not None and guid_el.text else link)[:100]

                if not title or not _is_relevant(title):
                    continue

                published_at = None
                if pub_date_el is not None and pub_date_el.text:
                    try:
                        published_at = parsedate_to_datetime(pub_date_el.text).astimezone(timezone.utc)
                    except Exception:
                        pass

                title_upper = title.upper()
                tagged = [c for c in currencies if c in title_upper]
                tagged_symbols = ",".join(tagged) if tagged else None

                rows.append({
                    "source": "rss_news",
                    "category": "crypto_news",
                    "external_id": ext_id,
                    "headline": title,
                    "url": link or None,
                    "symbols": tagged_symbols,
                    "country": None,
                    "impact": None,
                    "published_at": published_at,
                    "scheduled_at": None,
                    "actual_value": None,
                    "forecast_value": None,
                    "previous_value": None,
                    "raw_payload": json.dumps({"title": title, "url": link, "source": feed_url}),
                })
        except Exception as e:
            logger.warning(f"Error reading free RSS feed {feed_url}: {e}")
    return rows


def poll_once():
    currencies = sorted({_base_currency(s) for s in SYMBOLS})
    if CRYPTOPANIC_API_KEY:
        raw_items = fetch_news(currencies)
        rows = rows_from_response(raw_items)
        detail_msg = f"{len(rows)}/{len(raw_items)} stories stored via CryptoPanic"
    else:
        rows = fetch_free_rss_news(currencies)
        detail_msg = f"{len(rows)} stories stored via Free RSS (CoinTelegraph/CoinDesk)"

    if rows:
        upsert_news_events(rows)
    heartbeat("news_poller", detail=detail_msg)
    logger.info(f"{detail_msg} for {currencies}")


def run():
    if not CRYPTOPANIC_API_KEY:
        logger.info(
            "CRYPTOPANIC_API_KEY not set -- operating in FREE mode using live RSS feeds "
            "(CoinTelegraph, CoinDesk). Free & no API key required."
        )
    else:
        logger.info("Starting news_poller using CryptoPanic API.")

    while True:
        start = time.time()
        try:
            poll_once()
        except Exception as e:
            logger.error(f"poll_once failed: {e}")
            heartbeat("news_poller", status="error", detail=str(e))
        elapsed = time.time() - start
        time.sleep(max(0, NEWS_POLL_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    run()

