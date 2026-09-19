# News + Economic Calendar AI Overlay Strategy — Design Plan

> **Status:** Phases 1–3 are implemented. Phase 1: `db/migrate_news_events.sql`,
> `collectors/news_poller.py` (CryptoPanic), `collectors/econ_calendar_poller.py`
> (Finnhub economic calendar). Phase 2: `ai/news_strategy_engine.py` — calls
> Claude, scores news/calendar events per currently-active paper symbol,
> stores high-confidence bias/reasoning in `news_ai_signals`. **Phase 3
> (NEW): `ai/news_execution.py`** — turns those high-confidence signals into
> real paper positions (entry/SL/TP via ATR + R-multiple, same accounting
> path every strategy uses) and sends Telegram alerts, with the confluence/
> daily-cap/circuit-breaker/pre-release guardrails from §4 all implemented
> and enforced. All new modules unit-tested offline (20+ scenarios across
> parsing, filtering, prompt-building, trade math, and every guardrail, with
> DB/API dependencies stubbed); live API/DB behavior against real keys not
> yet verified — no Anthropic/CryptoPanic/Finnhub key is available in the
> build sandbox. Run each module once with real keys and eyeball the output
> before trusting it, and see §8 for two upstream bugs that were fixed along
> the way. Decisions locked in for this build: **Claude (Anthropic API,
> model `claude-sonnet-5`)** for reasoning, **CryptoPanic** for crypto news,
> **Finnhub** for the economic calendar (substituted for the
> originally-suggested TradingEconomics, whose free tier only returns sample
> data — Finnhub has a genuinely usable free-tier economic calendar).


## 1. What you're asking for, restated
A new signal source that:
1. Pulls live crypto news + macro economic-calendar events (CPI, FOMC, NFP, rate decisions, ETF flow reports, etc.)
2. Runs them through an AI reasoning step to judge directional bias and confidence
3. When confidence is high, computes an **entry / TP / SL** for the crypto symbol that's *already been selected* by your existing backtest "Battle Royale" as the current best-performing strategy/pair
4. Opens a **paper position** for it (same engine as your technical strategies)
5. Sends an alert to the **same Telegram channel** you already use, as an additional message type — not a separate bot

That maps cleanly onto the existing architecture. Below is how it plugs in, plus the design decisions and guardrails that matter for something news-driven specifically.

## 2. How this fits the current codebase
Confirmed by reading the current source (not guessing from README):

- `ai/insight_engine.py` today is **rule-based Python heuristics** (regime/overfitting scoring on backtest stats) — it doesn't call an LLM. This feature would be the first place an actual AI model gets called.
- `notifications/telegram_notifier.py` already has `send_signal_alert()`, `send_trade_close_alert()`, `send_deployment_alert()`, with retry built in. We add a fourth: `send_news_signal_alert()` — same chat, same bot, new message template.
- `paper/engine.py` opens/closes positions keyed by `(symbol, market, timeframe, strategy_name)`, with `backtest/accounting.py` now handling real position sizing (`allocated_capital` × `risk_per_trade_pct`) and funding cost. The news strategy reuses this — it just needs its own `strategy_name` (e.g. `news_ai_overlay`) so it doesn't collide with the technical strategy already running on that symbol.
- `db/migrate_p0_paper_accounting.sql` added a unique-index guard against duplicate open positions per key — this protects the news strategy the same way, for free.
- Collectors (`collectors/ws_funding.py`, `backfill_klines.py`, etc.) establish the existing pattern for a new `collectors/news_poller.py` / `collectors/econ_calendar_poller.py` to follow.

## 3. New components

### 3.1 Data collectors
Two feeds, polled independently since they're different cadences (news is continuous, calendar is scheduled):

| Feed | Purpose | Suggested source | Notes |
|---|---|---|---|
| Economic calendar | Scheduled macro events with a known release time | ~~TradingEconomics~~ **Finnhub** `/calendar/economic` (switched — see status note above) | These move BTC/majors even though they're not "crypto news" — CPI/FOMC surprises are some of the biggest crypto volatility events |
| Crypto-specific news | Regulatory actions, ETF flows, exchange incidents, protocol news | **CryptoPanic API** (implemented) | CryptoPanic is purpose-built for this and already tags sentiment/source votes, which is a useful pre-filter |

Both write into a new `news_events` table (headline/title, source, published_at, scheduled_at for calendar items, category, raw payload). Poll interval: news every 1–5 min, calendar once daily (release times are known in advance) plus a tighter poll in the hour around a scheduled release.

### 3.2 AI reasoning layer — `ai/news_strategy_engine.py`
This is the actual new "AI" component (unlike the current heuristic insight engine). Design:

- **Input:** the currently active/promoted symbol + market + timeframe (i.e., whatever the Battle Royale winner is), the most recent unprocessed `news_events` rows, and the current price/ATR context.
- **Filter before calling the model** — don't send every headline to an LLM. Pre-filter on keyword relevance to the active symbol (BTC/ETH/regulatory/macro terms) and recency, to control cost and noise. This alone probably cuts 80%+ of irrelevant items.
- **Model call:** structured-output prompt (JSON schema, not free text) asking for:
  ```json
  {
    "bias": "long | short | neutral",
    "confidence": 0-100,
    "reasoning": "short justification",
    "invalidation_condition": "what would prove this wrong",
    "time_horizon": "scalp | intraday | swing",
    "trade_pre_or_post_release": "wait_for_reaction | trade_immediately"
  }
  ```
- **High-probability threshold:** only proceed to position sizing when `confidence >= threshold` (start conservative, e.g. 75+, tunable) **and** the bias doesn't outright contradict the technical strategy's current regime read from `ai/insight_engine.py` (a news long signal while the deployed technical strategy is in a strong downtrend regime should reduce confidence or be suppressed, not override blindly).
- Recommend **Claude via the Anthropic API** for this — same provider pattern already used for "Claude in Claude" style reasoning tasks, supports strict structured JSON output, and keeps you on one AI vendor for auth/billing.

### 3.3 Entry / TP / SL computation
Reuse what already exists rather than inventing a parallel risk model:
- **Entry:** next available price after signal confirmation (same convention as `backtest/simulate.py` — no same-candle lookahead).
- **Stop:** ATR-based initial stop (same helper the technical strategies use), *not* a fixed percentage — news-driven moves have wildly different volatility than technical setups, and ATR keeps sizing consistent with `accounting.py`'s `calculate_position_size()`.
- **Target:** either a fixed R-multiple (2R/3R, matching your existing convention) or, if the model's `time_horizon` is `scalp`, a tighter target — worth exposing this as a parameter rather than hardcoding.
- **Position size:** unchanged — flows through `calculate_position_size(equity, risk_per_trade_pct, entry, stop)` exactly like every other strategy, so it inherits the funding-cost and fee accounting already built.

### 3.4 Telegram alert
New `send_news_signal_alert()` in `notifications/telegram_notifier.py`, same chat/bot, visually distinct template so it doesn't get confused with technical-strategy alerts, e.g.:

```
📰 NEWS-DRIVEN SIGNAL — BTC/USDT (futures)
Trigger: "Fed signals rate pause" (FOMC, 14:00 UTC)
AI Confidence: 82%  |  Bias: LONG
Entry: 64,250  |  SL: 63,100 (-1.8%)  |  TP: 66,550 (+3.6%, 2R)
Reasoning: <model's short justification>
⚠️ Paper trade — no live execution
```

## 4. Guardrails specific to news trading (these matter more here than for technical strategies)
- **Don't trade the pre-release gap.** Scheduled high-impact events (CPI/FOMC) often see spread/slippage spikes right at release. Default to `wait_for_reaction` unless you deliberately want pre-positioning — the model output above includes this as a field so it's a policy switch, not hardcoded.
- **Confidence threshold + daily cap.** Cap news-driven trades per day (e.g. max 2–3) regardless of how many "high confidence" signals fire — headline-driven models can cluster false signals around a single narrative.
- **Confluence check against the deployed technical strategy**, not just standalone. This avoids the exact portfolio-risk gap flagged in the earlier review (no aggregate exposure view across concurrent configs) — opening a news-driven position on the *same* symbol the technical strategy already has a position on is exactly the scenario that gap makes dangerous. Recommend: block a new news-driven entry if a technical-strategy position is already open on that symbol/market, or treat it as a position-scaling decision rather than an independent second position.
- **This can't be backtested the normal way.** Your other 24 strategies are validated against years of OHLCV history bar-by-bar. There's no equivalent historical archive of "headline + timestamp + market reaction" to replay this against with integrity — and using an LLM's training-data knowledge of historical news to "backtest" would leak future information into the test. Treat this as **forward-only / paper-tracked from day one**, and build a track record before trusting it, rather than trying to force it into the existing Battle Royale ranking (which assumes backtestability).

## 5. New DB objects (sketch)
```sql
CREATE TABLE news_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source VARCHAR(50),
  category ENUM('macro_calendar','crypto_news'),
  headline TEXT,
  published_at DATETIME,
  scheduled_at DATETIME NULL,      -- for calendar events, known in advance
  raw_payload JSON,
  processed_at DATETIME NULL
);

CREATE TABLE news_ai_signals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  news_event_id BIGINT REFERENCES news_events(id),
  symbol VARCHAR(20),
  bias ENUM('long','short','neutral'),
  confidence TINYINT,
  reasoning TEXT,
  time_horizon VARCHAR(20),
  acted_on BOOLEAN DEFAULT FALSE,
  paper_position_id BIGINT NULL,   -- links to the position it opened, if any
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 6. Phased implementation
1. **Data plumbing:** `news_events` table + the two collectors, no AI yet — just get clean, deduped news/calendar data flowing. ✅ **Done**.
2. **AI reasoning layer, log-only:** call the model, store `news_ai_signals`, but don't open positions yet — build confidence the signal quality is real before it touches capital (even paper capital). ✅ **Done** — `ai/news_strategy_engine.py`. Confidence threshold (`NEWS_AI_MIN_CONFIDENCE`, default 75) and per-symbol event cap (`NEWS_AI_MAX_EVENTS_PER_SYMBOL`, default 15) are both tunable via `.env`. Scoped automatically to whichever symbols are currently `is_active=1` in `paper_configs` — i.e. whatever the Battle Royale promoted, no manual symbol config needed.
3. **Wire to paper engine + Telegram:** once signal quality looks reasonable over a couple weeks of shadow logging, enable actual position opening with the guardrails from §4. ✅ **Done** — `ai/news_execution.py`. All four guardrails from §4 are implemented: confluence block (no stacking on a symbol the technical strategy already holds), `NEWS_AI_MAX_TRADES_PER_DAY` daily cap (default 3), the account-wide circuit breaker (`paper/circuit_breaker.py` — max concurrent positions / max daily loss / emergency stop, shared with the technical strategies), and pre-release-gap avoidance (a `wait_for_reaction` signal tied to a not-yet-released scheduled macro event is retried next cycle rather than traded blind). Entry = latest close, stop = `NEWS_AI_STOP_ATR_MULT` × ATR (default 1.5), target = R-multiple by the model's reported `time_horizon` (`NEWS_AI_TP_R_MULTIPLE`: scalp 1.5R, intraday 2R, swing 3R) — same accounting path (`backtest/accounting.py`) as every technical strategy, via a public `insert_paper_position()` entry point in `paper/engine.py`.
4. **Feedback loop:** track win rate / expectancy of `news_ai_overlay` trades the same way technical strategies are tracked, so it earns its place next to them rather than being trusted by default. ⬜ Not started — this is genuinely the next step. `news_ai_overlay` positions already flow through the same `paper_positions`/`paper_trades` tables as everything else, so this is a reporting/dashboard task, not a new data-collection one.

### What was checked before/while building Phase 3
- Ran the same offline-unit-test discipline as Phases 1–2: 20+ scenarios covering the pending-on-release retry path, confluence blocking, the daily cap, ATR-based stop/target math for both directions and all three time horizons, the happy-path open+alert flow, and the market-data-unavailable fallback — all with DB/paper-engine/Telegram dependencies stubbed out (no live DB/API in the build sandbox).
- Still outstanding before trusting this with anything beyond a first look: run all three modules with real keys, confirm `news_ai_signals` and `paper_positions` rows look sane end-to-end, and specifically watch the confidence-score distribution and how often the daily cap actually gets hit.

## 7. Decisions needed from you before implementation
- ~~News/economic-calendar data provider (cost, API key) — see §3.1 for recommendations.~~ ✅ Decided (§3.1): CryptoPanic + Finnhub.
- ~~Confirm Claude (Anthropic API) as the reasoning model, or a different provider.~~ ✅ Decided: Claude (`claude-sonnet-5`).
- ~~Confidence threshold and daily trade cap to start with (defaults above are a starting point, not fixed).~~ ✅ Implemented as defaults (`NEWS_AI_MIN_CONFIDENCE=75`, `NEWS_AI_MAX_TRADES_PER_DAY=3`) — still yours to tune once you've watched it run for a while.
- ~~Whether a news-driven signal should be blocked entirely when the technical strategy already has an open position on that symbol, or allowed to scale it (§4).~~ ✅ Decided: blocked entirely (no scaling) — implemented in `ai/news_execution.py::_has_open_technical_position()`.

## 8. Fixes made to existing code along the way

Two pre-existing issues in `paper/engine.py` would have made Phase 3 fragile or
wrong if left as-is, so they were fixed rather than worked around:

- **Overlay-strategy entries no longer risk being auto-generated by the
  technical signal loop.** `sync_and_evaluate_paper_trading()` previously
  gated *both* entry generation and exit management on `strategy_name in
  STRATEGIES`. A `news_ai_overlay` config would have been silently skipped
  entirely — meaning Phase 3's positions would open correctly but then never
  get their SL/TP/trailing-stop managed by the existing exit machinery. Fixed
  by introducing `OVERLAY_STRATEGIES` (currently `{"news_ai_overlay"}`):
  overlay strategies still get exit management, but are explicitly excluded
  from `STRATEGIES` (and therefore from backtesting/the Battle Royale
  ranking, which is correct — see §4, this can't be backtested honestly) and
  from ever having an entry auto-generated from price action.
- **`_insert_position()` now returns the new row's id, not just `True`.**
  Needed so `news_ai_signals.paper_position_id` can actually be linked to the
  position it opened, for auditability. Backward compatible — existing
  callers only checked truthiness, and `0` is never a valid auto-increment
  id. Exposed as a public `insert_paper_position` alias so Phase 3 doesn't
  need to import a leading-underscore "private" function across modules.

One earlier-flagged issue was deliberately *not* touched: `PROFESSIONAL_TRADER_REVIEW.md`
§2.6 (module-level DB side effects at import time) turned out to already be
fixed independently — confirmed by scanning `paper/engine.py` for any
top-level function calls (there are none). `ai/news_execution.py` still
avoids importing `paper.engine.get_paper_configs()` for symbol lookups
(uses a direct `get_active_paper_symbols()` query in `db/db.py` instead, per
the Phase 2 commit) simply because that's a cleaner, more minimal dependency
— not because the import-time bug is still live.
