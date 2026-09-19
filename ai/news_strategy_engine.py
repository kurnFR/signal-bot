"""
News AI Overlay strategy -- Phase 2: AI reasoning layer.

Reads unprocessed rows from `news_events` (populated by collectors/news_poller.py
and collectors/econ_calendar_poller.py), scopes them to whichever symbols are
currently promoted to active paper trading (paper_configs.is_active), and asks
Claude for a structured bias/confidence read. High-confidence signals are
stored in `news_ai_signals`.

IMPORTANT -- this phase is log-only by design (see NEWS_AI_STRATEGY_PLAN.md):
it does NOT open paper positions and does NOT send Telegram alerts. That's
Phase 3, deliberately gated on watching this phase's signal quality first.
Like the Phase 1 collectors, this is additive: if ANTHROPIC_API_KEY is unset,
this exits cleanly with a warning rather than breaking anything else.

Usage:
    python3 -m ai.news_strategy_engine          # one continuous poll loop
    python3 -m ai.news_strategy_engine --once    # single pass, for cron/testing
"""
import logging
import sys
import os
import time
import json
import argparse
from collections import defaultdict

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    LLM_PROVIDER,
    NINEROUTER_API_KEY, NINEROUTER_BASE_URL, NINEROUTER_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_API_URL, ANTHROPIC_MODEL,
    NEWS_AI_POLL_INTERVAL_SECONDS, NEWS_AI_MIN_CONFIDENCE, NEWS_AI_MAX_EVENTS_PER_SYMBOL,
)
from db.db import (
    get_unprocessed_news_events, mark_news_events_processed, insert_news_ai_signal,
    fetch_ohlcv_with_features_df, get_active_paper_symbols, heartbeat,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("news_strategy_engine")

SESSION = requests.Session()

VALID_BIAS = {"long", "short", "neutral"}
VALID_TIME_HORIZON = {"scalp", "intraday", "swing"}
VALID_TRADE_TIMING = {"wait_for_reaction", "trade_immediately"}

_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")

SYSTEM_PROMPT = """You are a risk-aware crypto trading analyst. You will be given \
a target trading symbol, its current price/volatility context, and a list of \
recent news items and/or scheduled macro-economic events. Assess whether these \
events create a high-probability directional trade setup for the symbol.

Be conservative. Most news does not constitute a high-probability trade setup -- \
neutral/low-confidence is the correct answer far more often than a confident \
directional call. Do not manufacture conviction to seem useful.

Respond with ONLY a single JSON object, no markdown fences, no commentary before \
or after it, matching exactly this schema:
{
  "bias": "long" | "short" | "neutral",
  "confidence": <integer 0-100>,
  "reasoning": "<one or two sentences, concrete, referencing which event(s) drove this>",
  "invalidation_condition": "<what would prove this wrong>",
  "time_horizon": "scalp" | "intraday" | "swing",
  "trade_timing": "wait_for_reaction" | "trade_immediately"
}
"trade_timing" should be "wait_for_reaction" whenever a cited event is a \
*scheduled but not-yet-released* macro event (actual_value is null/empty) -- \
trading before the print is a spread/slippage trap, not a high-probability setup.
"""


def _base_currency(symbol):
    for suffix in _QUOTE_SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _get_active_symbols():
    """(symbol, market, timeframe) tuples currently promoted to active paper
    trading -- i.e. the Battle Royale winners this feature is scoped to."""
    return list(get_active_paper_symbols())


def _group_relevant_events(events, base_currency):
    """macro_calendar events apply broadly (kept for every active symbol);
    crypto_news events only apply if tagged with this symbol's base currency."""
    relevant = []
    for e in events:
        if e["category"] == "macro_calendar":
            relevant.append(e)
        elif e["category"] == "crypto_news":
            tagged = (e.get("symbols") or "").split(",")
            if base_currency in tagged:
                relevant.append(e)
    return relevant


def _market_context(symbol, market, timeframe):
    try:
        df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, limit=50)
        if df is None or len(df) < 5:
            return None
        latest = df.iloc[-1]
        atr = latest.get("atr")
        return {
            "last_close": float(latest["close"]),
            "atr": float(atr) if atr is not None and atr == atr else None,  # NaN check
        }
    except Exception as e:
        logger.warning(f"Could not fetch market context for {symbol}: {e}")
        return None


def _format_event_for_prompt(e):
    if e["category"] == "macro_calendar":
        return (
            f"- [MACRO | {e.get('country')} | impact={e.get('impact')}] {e['headline']} "
            f"| scheduled={e.get('scheduled_at')} "
            f"| forecast={e.get('forecast_value')} actual={e.get('actual_value')} previous={e.get('previous_value')}"
        )
    return f"- [NEWS | {e.get('symbols')}] {e['headline']} | published={e.get('published_at')} | url={e.get('url')}"


def build_user_prompt(symbol, market, timeframe, market_ctx, events):
    lines = [f"Target symbol: {symbol} ({market}, {timeframe} timeframe)"]
    if market_ctx:
        lines.append(
            f"Current context: last_close={market_ctx['last_close']}, "
            f"ATR={market_ctx['atr']}"
        )
    else:
        lines.append("Current market context: unavailable")
    lines.append(f"\n{len(events)} relevant event(s):")
    lines.extend(_format_event_for_prompt(e) for e in events)
    return "\n".join(lines)


def call_claude(user_prompt, retries=3):
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    for attempt in range(retries):
        try:
            resp = SESSION.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("retry-after", 20))
                logger.warning(f"Anthropic rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            text = "".join(
                block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
            )
            return text.strip()
        except requests.RequestException as e:
            logger.warning(f"Claude API attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    logger.error("Claude API call failed after retries")
    return None


def call_openai_compatible(url, api_key, model, user_prompt, retries=3, provider_name="LLM"):
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "max_tokens": 600,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
    }
    for attempt in range(retries):
        try:
            resp = SESSION.post(url, headers=headers, json=body, timeout=35)
            if resp.status_code == 429:
                wait = int(resp.headers.get("retry-after", 20))
                logger.warning(f"{provider_name} rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            raw_text = resp.text.strip()
            if "data: [DONE]" in raw_text:
                raw_text = raw_text.split("data: [DONE]")[0].strip()
            data = json.loads(raw_text)
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            text = msg.get("content") or msg.get("reasoning") or ""
            if isinstance(text, (dict, list)):
                return json.dumps(text)
            if not isinstance(text, str):
                text = str(text)
            return text.strip()
        except Exception as e:
            logger.warning(f"{provider_name} API attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"{provider_name} API call failed after retries")
    return None



def call_llm(user_prompt, retries=3):
    if LLM_PROVIDER == "9router":
        return call_openai_compatible(
            url=f"{NINEROUTER_BASE_URL.rstrip('/')}/chat/completions",
            api_key=NINEROUTER_API_KEY,
            model=NINEROUTER_MODEL,
            user_prompt=user_prompt,
            retries=retries,
            provider_name="9Router",
        )
    elif LLM_PROVIDER == "openrouter":
        return call_openai_compatible(
            url=f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
            api_key=OPENROUTER_API_KEY,
            model=OPENROUTER_MODEL,
            user_prompt=user_prompt,
            retries=retries,
            provider_name="OpenRouter",
        )
    else:
        return call_claude(user_prompt, retries=retries)



def parse_model_output(text):
    """Strict-ish JSON parse with a couple of defensive fallbacks for the
    model wrapping output in a fenced code block despite instructions not
    to. Returns None (not a raised exception) on anything malformed --
    a malformed AI response should never take down the poll loop."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(f"Could not parse model output as JSON: {text[:200]}")
        return None

    if parsed.get("bias") not in VALID_BIAS:
        logger.warning(f"Model returned invalid bias: {parsed.get('bias')}")
        return None
    try:
        confidence = int(parsed.get("confidence"))
    except (TypeError, ValueError):
        logger.warning(f"Model returned non-integer confidence: {parsed.get('confidence')}")
        return None
    if not (0 <= confidence <= 100):
        logger.warning(f"Model confidence out of range: {confidence}")
        return None
    if parsed.get("time_horizon") not in VALID_TIME_HORIZON:
        parsed["time_horizon"] = None
    if parsed.get("trade_timing") not in VALID_TRADE_TIMING:
        parsed["trade_timing"] = None

    parsed["confidence"] = confidence
    return parsed


def evaluate_symbol(symbol, market, timeframe, events):
    """Runs one Claude call for this symbol's relevant events, stores a
    news_ai_signals row per event if confidence clears the threshold, and
    returns the list of event ids that were evaluated (so the caller can
    mark them processed regardless of outcome)."""
    events = events[:NEWS_AI_MAX_EVENTS_PER_SYMBOL]
    market_ctx = _market_context(symbol, market, timeframe)
    prompt = build_user_prompt(symbol, market, timeframe, market_ctx, events)
    raw = call_llm(prompt)
    parsed = parse_model_output(raw)
    event_ids = [e["id"] for e in events]


    if parsed is None:
        logger.info(f"{symbol}: no usable AI output this cycle ({len(events)} events sent)")
        return event_ids, 0

    stored = 0
    if parsed["confidence"] >= NEWS_AI_MIN_CONFIDENCE and parsed["bias"] != "neutral":
        # Link the signal to the most recent/highest-signal event in the
        # batch (macro events, if present, take priority as the likely driver).
        primary_event = next((e for e in events if e["category"] == "macro_calendar"), events[0])
        insert_news_ai_signal({
            "news_event_id": primary_event["id"],
            "symbol": symbol,
            "market": market,
            "timeframe": timeframe,
            "bias": parsed["bias"],
            "confidence": parsed["confidence"],
            "reasoning": parsed.get("reasoning"),
            "invalidation_condition": parsed.get("invalidation_condition"),
            "time_horizon": parsed.get("time_horizon"),
            "trade_timing": parsed.get("trade_timing"),
        })
        stored = 1
        logger.info(
            f"{symbol}: HIGH-PROBABILITY signal stored -- bias={parsed['bias']} "
            f"confidence={parsed['confidence']} timing={parsed.get('trade_timing')}"
        )
    else:
        logger.info(
            f"{symbol}: below threshold or neutral -- bias={parsed['bias']} "
            f"confidence={parsed['confidence']} (min={NEWS_AI_MIN_CONFIDENCE})"
        )
    return event_ids, stored


def poll_once():
    active_symbols = _get_active_symbols()
    if not active_symbols:
        logger.info("No active paper_configs -- nothing to evaluate this cycle")
        return

    events = get_unprocessed_news_events(limit=200)
    if not events:
        logger.info("No unprocessed news_events this cycle")
        return

    all_evaluated_ids = set()
    signals_stored = 0

    for symbol, market, timeframe in active_symbols:
        base = _base_currency(symbol)
        relevant = _group_relevant_events(events, base)
        if not relevant:
            continue
        event_ids, stored = evaluate_symbol(symbol, market, timeframe, relevant)
        all_evaluated_ids.update(event_ids)
        signals_stored += stored

    if all_evaluated_ids:
        mark_news_events_processed(list(all_evaluated_ids))

    heartbeat(
        "news_strategy_engine",
        detail=f"{len(all_evaluated_ids)} events evaluated, {signals_stored} signals stored",
    )
    logger.info(
        f"Cycle complete: {len(active_symbols)} active symbols, "
        f"{len(all_evaluated_ids)} events evaluated, {signals_stored} high-probability signals stored"
    )


def run(once=False):
    if LLM_PROVIDER == "9router":
        if not NINEROUTER_API_KEY and not NINEROUTER_BASE_URL:
            logger.warning(
                "LLM_PROVIDER=9router but NINEROUTER_BASE_URL or NINEROUTER_API_KEY is unset. "
                "See .env. Exiting cleanly."
            )
            return
        logger.info(f"Using 9Router gateway at {NINEROUTER_BASE_URL} with model '{NINEROUTER_MODEL}'")
    elif LLM_PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            logger.warning(
                "LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set. "
                "See .env. Exiting cleanly."
            )
            return
        logger.info(f"Using OpenRouter with model '{OPENROUTER_MODEL}'")
    else:
        if not ANTHROPIC_API_KEY:
            logger.warning(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set -- news_strategy_engine will not run. "
                "See .env.example. Exiting cleanly (this feature is additive)."
            )
            return
        logger.info(f"Using Anthropic API with model '{ANTHROPIC_MODEL}'")

    while True:

        start = time.time()
        try:
            poll_once()
        except Exception as e:
            logger.error(f"poll_once failed: {e}")
            heartbeat("news_strategy_engine", status="error", detail=str(e))
        if once:
            return
        elapsed = time.time() - start
        time.sleep(max(0, NEWS_AI_POLL_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass instead of polling forever")
    args = parser.parse_args()
    run(once=args.once)
