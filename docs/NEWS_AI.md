# News AI Signals

An overlay that reads crypto news and the macro-economic calendar, asks an LLM for a structured directional view, and, when confidence is high enough and the guardrails pass, opens a **paper** position. It cannot be backtested (there is no point-in-time news history), so it is validated forward only, in paper trading.

```
news poller ─┐
             ├▶ news_events ─▶ LLM reasoning ─▶ news_ai_signals ─▶ guarded execution ─▶ paper position + Telegram
calendar ────┘   (relevance-filtered)  (structured JSON)
```

## 1. Components

| Stage | Module | What it does |
|---|---|---|
| News ingest | `collectors/news_poller.py` | CryptoPanic when `CRYPTOPANIC_API_KEY` is set, otherwise free RSS feeds; stores `news_events` (poll every 120 s) |
| Calendar ingest | `collectors/econ_calendar_poller.py` | Finnhub economic calendar when `FINNHUB_API_KEY` is set, otherwise the free ForexFactory weekly feed; keeps events at or above `ECON_CALENDAR_MIN_IMPACT` (default `medium`); poll hourly |
| Reasoning | `ai/news_strategy_engine.py` | For each symbol with an **active paper config**, selects recent relevant events (keyword and recency filter, at most 15 per symbol) and requests structured JSON: bias, confidence 0–100, reasoning, invalidation condition, time horizon, and trade timing |
| Execution | `ai/news_execution.py` | Turns eligible signals into paper positions (strategy name `news_ai_overlay`) |
| Runner | `run_news_ai.py` | Runs the pollers, the reasoning loop (every 300 s) and the execution loop (every 60 s) |

## 2. Guardrails

A signal becomes a position only when all of these hold. Anything else is recorded with a `skip_reason`.

1. **Confidence** at least `NEWS_AI_MIN_CONFIDENCE` (default 75) and bias is not neutral.
2. **No pre-release trading.** If the signal is `wait_for_reaction` and the scheduled macro event has no actual value yet, it is left pending and retried next cycle.
3. **No stacking.** If a technical strategy already has an open position on that symbol, market and timeframe, the signal is blocked (`confluence_blocked`).
4. **Daily cap:** at most `NEWS_AI_MAX_TRADES_PER_DAY` (default 3) news trades per UTC day.
5. **Circuit breakers:** the same account-wide guard the technical strategies use (`paper/circuit_breaker.py`): max concurrent positions, max daily loss, emergency stop.
6. **Market data available** for ATR on that symbol/market/timeframe.
7. **Atomic claim.** Each signal is claimed in the database, so several workers cannot open it twice. A crash releases the claim after 10 minutes for a retry.

## 3. Trade construction

Entry is the latest close (no same-candle lookahead). The stop is `NEWS_AI_STOP_ATR_MULT` × ATR (default 1.5). The target is an R-multiple chosen by the model's time horizon (`NEWS_AI_TP_R_MULTIPLE`: scalp 1.5, intraday 2.0, swing 3.0; default 2.0). Capital and risk per trade mirror the symbol's technical paper config (fallback: $1,000 and 0.5%), and sizing and costs go through `backtest/accounting.py`. A distinct Telegram template (`send_news_signal_alert`) marks these alerts as news-driven.

## 4. Configuration

| Setting | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `9router` | `9router`, `openrouter` or `anthropic` |
| `NINEROUTER_*`, `OPENROUTER_*`, `ANTHROPIC_*` | see `config.py` | API key, base URL and model for the chosen provider |
| `NEWS_AI_MIN_CONFIDENCE` | 75 | Minimum confidence to act |
| `NEWS_AI_MAX_TRADES_PER_DAY` | 3 | Daily cap |
| `NEWS_AI_STOP_ATR_MULT` | 1.5 | Stop distance |
| `NEWS_POLL_INTERVAL_SECONDS` | 120 | News poll |
| `ECON_CALENDAR_POLL_INTERVAL_SECONDS` | 3600 | Calendar poll |
| `NEWS_AI_POLL_INTERVAL_SECONDS` | 300 | Reasoning loop |
| `NEWS_AI_EXECUTION_POLL_INTERVAL_SECONDS` | 60 | Execution loop |

The reasoning loop does nothing until at least one paper config is active, and nothing happens without an LLM key for the selected provider.

## 5. Dashboard and API

**News AI Signals** tab, backed by `GET /api/news/stats`, `/api/news/signals` and `/api/news/events`. Tables: `news_events`, `news_ai_signals` (created by `migrate_news_events.sql` and `migrate_news_execution.sql`).

## 6. Using it safely

Run it in paper trading first and judge it on forward results, not on how plausible the model's reasoning reads. Start from the defaults; raise the confidence threshold rather than the daily cap if signals look noisy. Keep the circuit breakers enabled.
