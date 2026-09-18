"""
Macro economic calendar poller (Finnhub) -- Phase 1 "data plumbing" for the
News AI Overlay strategy. Stores scheduled macro events (CPI, FOMC, NFP,
rate decisions, etc.) that move crypto even though they're not "crypto news"
themselves. See NEWS_AI_STRATEGY_PLAN.md for the full design.

Runs on a much slower cadence than news_poller (release times are known in
advance -- default hourly, see ECON_CALENDAR_POLL_INTERVAL_SECONDS). No AI
reasoning happens here; this only fetches and stores.

Finnhub API docs: https://finnhub.io/docs/api/economic-calendar

Usage:
    python3 -m collectors.econ_calendar_poller
"""
import logging
import sys
import os
import time
import json
from datetime import datetime, timedelta, timezone

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    FINNHUB_API_KEY, FINNHUB_ECON_CALENDAR_URL,
    ECON_CALENDAR_POLL_INTERVAL_SECONDS, ECON_CALENDAR_MIN_IMPACT,
)
from db.db import upsert_news_events, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("econ_calendar_poller")

SESSION = requests.Session()

_IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}

# Finnhub's calendar is global/all-asset -- these are the countries whose
# macro releases are most established as crypto (esp. BTC) volatility
# drivers. Extend this list deliberately, not by widening to "all countries",
# to keep Phase 2 signal density manageable.
_RELEVANT_COUNTRIES = {"US", "EU", "CN", "JP", "GB"}


def _meets_min_impact(impact):
    return _IMPACT_RANK.get((impact or "").lower(), 0) >= _IMPACT_RANK.get(ECON_CALENDAR_MIN_IMPACT, 1)


def fetch_calendar(start_date, end_date, retries=3):
    params = {"from": start_date, "to": end_date, "token": FINNHUB_API_KEY}
    for attempt in range(retries):
        try:
            resp = SESSION.get(FINNHUB_ECON_CALENDAR_URL, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                logger.warning(f"Rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("economicCalendar", [])
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"Failed to fetch economic calendar after {retries} retries")
    return []


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def rows_from_response(raw_items):
    rows = []
    for item in raw_items:
        country = item.get("country")
        impact = item.get("impact")
        if country not in _RELEVANT_COUNTRIES or not _meets_min_impact(impact):
            continue
        event_name = item.get("event", "")
        scheduled_at = _parse_datetime(item.get("time"))
        # Finnhub doesn't provide a stable per-event id -- build one from the
        # fields that uniquely identify a single scheduled release, so
        # re-polling the same window is idempotent via ON DUPLICATE KEY.
        external_id = f"{country}:{event_name}:{item.get('time')}"
        rows.append({
            "source": "finnhub_econ_calendar",
            "category": "macro_calendar",
            "external_id": external_id,
            "headline": f"{country} {event_name}",
            "url": None,
            "symbols": None,  # macro events aren't per-symbol; Phase 2 applies them broadly
            "country": country,
            "impact": impact,
            "published_at": None,
            "scheduled_at": scheduled_at,
            "actual_value": item.get("actual"),
            "forecast_value": item.get("estimate"),
            "previous_value": item.get("prev"),
            "raw_payload": json.dumps(item),
        })
    return rows


def poll_once():
    today = datetime.now(timezone.utc).date()
    # Fetch a window covering the recent past (so actual_value updates for
    # already-scheduled events get picked up) through 2 weeks ahead.
    start_date = (today - timedelta(days=2)).isoformat()
    end_date = (today + timedelta(days=14)).isoformat()
    raw_items = fetch_calendar(start_date, end_date)
    rows = rows_from_response(raw_items)
    if rows:
        upsert_news_events(rows)
    heartbeat("econ_calendar_poller", detail=f"{len(rows)} events stored ({len(raw_items)} fetched)")
    logger.info(f"Stored {len(rows)}/{len(raw_items)} calendar events for {start_date}..{end_date}")


def run():
    if not FINNHUB_API_KEY:
        logger.warning(
            "FINNHUB_API_KEY not set -- econ_calendar_poller will not run. "
            "See .env.example. Exiting cleanly (this feature is additive)."
        )
        return
    while True:
        start = time.time()
        try:
            poll_once()
        except Exception as e:
            logger.error(f"poll_once failed: {e}")
            heartbeat("econ_calendar_poller", status="error", detail=str(e))
        elapsed = time.time() - start
        time.sleep(max(0, ECON_CALENDAR_POLL_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    run()
