# Architecture

How the system is put together and the rules that keep it correct. For setup see the [README](../README.md); for what is done and what is open see [STATUS.md](STATUS.md).

## 1. Processes

`run_all.py` starts and supervises these (restart after 5 s if one crashes). Each can also be run alone.

| Process | Entry point | Role |
|---|---|---|
| `web_server` | `run_web.py` (port 8050) | FastAPI app and static dashboard |
| `collectors` | `run_collectors.py` | Live klines (spot, and futures if enabled), funding/mark price, liquidations, open interest |
| `paper_engine` | `run_paper_engine.py` | Evaluates deployed strategies on new closed candles and manages paper positions |
| `news_ai` | `run_news_ai.py` | News + macro-calendar pollers, LLM reasoning, guarded paper execution |
| `retailbot2` | `paper/retailbot2.py` | Experimental research bot (own tables) |
| `screener` | `Screen/screening.py` | Smart-money volume detector (own tables) |
| `wallet_tracker` | `run_wallet_tracker.py` | On-chain swap scanner, wallet candidates, Telegram alerts |

## 2. Data flow

```
collectors ─▶ ohlcv / funding_rate / open_interest / liquidations
                       │
                       ▼
              features/ (indicators) ─▶ features table
                       │
        ┌──────────────┴───────────────┐
        ▼                              ▼
 backtest/ (simulate.py,         paper/engine.py ◀── ml/ filters, ai/news_execution.py
  accounting.py, metrics)               │
        │                               ▼
        ▼                       paper_positions / paper_trades ─▶ Telegram
 backtest_runs / backtest_trades
        └──────────────▶ web/ (FastAPI routers + static UI)
```

**Core invariant:** strategies are pure functions `run(df, params) -> list[trade]` over a DataFrame, and backtest and paper trading call the *same* strategy registry, feature columns and accounting code. Never reimplement strategy or accounting logic for a new consumer; import the existing one.

## 3. Scope of the data

Defined in `config.py`: symbols `BTCUSDT`, `ETHUSDT`, `BNBUSDT`; timeframes `15m`, `1h`, `4h`, `1d` (exact Binance klines, not resampled); 5 years of history by default (`BACKFILL_YEARS`); spot always, futures only with `ENABLE_FUTURES=true`. Timestamps are stored as UTC millisecond epochs.

## 4. Database

| Group | Tables | Created by |
|---|---|---|
| Market data | `ohlcv`, `funding_rate`, `open_interest`, `liquidations`, `features`, `collector_heartbeat` | `db/schema.sql` |
| Backtests | `backtest_runs`, `backtest_trades` | `db/schema.sql` |
| Users | `users` | `migrate_add_users_and_paper.sql` |
| Security | `audit_logs`, `revoked_tokens` | `migrate_pro_trader_and_ml.sql` |
| Paper trading | `paper_configs`, `paper_positions`, `paper_trades` | `migrate_add_users_and_paper.sql`, `migrate_p0_paper_accounting.sql` and follow-ups |
| News AI | `news_events`, `news_ai_signals` | `migrate_news_events.sql`, `migrate_news_execution.sql` |
| Screener | `smart_money_signals`, `screener_control`, `screener_heartbeat` | `migrate_smart_money.sql` / the screener itself |
| Research bot | `rdt_trades`, `rdt_strategy_stats`, `bot_control` | `retailbot2` at start |
| Wallet tracking | `wallet_swaps`, `wallet_positions`, `tracked_wallets`, `wallet_tracker_settings`, `wallet_tracker_status`, `wallet_telegram_alerts`, `wallet_tracker_alert_cursor` | created automatically by the web app and worker |

## 5. Backtest and validation engine

- `backtest/simulate.py` is the single execution engine every strategy delegates to: entry at the **next bar's open**, ATR-based stop and target, optional trailing stop, one position at a time.
- `backtest/accounting.py` holds the financial primitives (position size, fees, slippage, funding, R-multiple), shared by backtest and paper trading, with no DB or strategy logic in it.
- Costs come from `config.py`: `BACKTEST_FEE_PCT = 0.001` (0.1% per side) and `BACKTEST_SLIPPAGE_PCT = 0.0005` (0.05% per side).
- `backtest/holdout.py` splits each symbol/timeframe: the oldest 80% is "train", the newest `HOLDOUT_FRACTION = 0.2` is reserved.
- `backtest/walk_forward.py`, `optimize.py`, `pnl_report.py` and `equity.py` provide fold stability, parameter search, dollar PnL and compounding equity curves.
- `ai/insight_engine.py` turns ranked backtest results into a verdict, risk profile and deployment notes (shown in Backtest Studio).

## 6. Paper trading

`paper/engine.py` sizes positions from `allocated_capital` and `risk_per_trade_pct`, and computes fees, slippage, funding and R through `backtest/accounting.py`. Duplicate syncs are treated as idempotent. Signal discovery reuses the strategy runner; this is a shadow engine, not live execution.

`paper/circuit_breaker.py` guards new entries and **fails closed** when it cannot verify state: max concurrent positions (`PAPER_MAX_CONCURRENT_POSITIONS`, default 5), max daily realized loss (`PAPER_MAX_DAILY_LOSS_USD`, default 500, UTC day), and a manual emergency stop. An empty database counts as zero positions and zero PnL.

Telegram delivery for signals and closed trades is in `notifications/telegram_notifier.py` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

## 7. Web layer

FastAPI app in `web/app.py` with one router per area (66 paths):

| Prefix | Router | Area |
|---|---|---|
| `/api/auth`, `/api/users` | `auth_routes.py` | Login, logout, password change, audit log, user admin |
| `/api/backtest` | `backtest.py` | Strategies, run, run-all, AI insight, history |
| `/api/paper` | `paper_routes.py` | Positions, trades, metrics, configs, deploy, circuit breakers |
| `/api/master-data`, `/api/crypto` | `master_data.py`, `search.py` | Coverage, backfill and feature jobs, Binance search |
| `/api/custom-strategy` | `custom_strategy.py` | Custom JSON strategies, AI generation, quick test |
| `/api/ml` | `ml_routes.py` | Experiments, models, deploy-to-paper |
| `/api/news` | `news_routes.py` | News AI stats, signals, events |
| `/api/research` | `research_routes.py` | retailbot2 and screener data and control |
| `/api/wallets` | `wallet_routes.py` | Wallet tracking (see [WALLET_TRACKER.md](WALLET_TRACKER.md)) |
| `/api/settings`, `/api/telegram` | `settings.py`, `telegram_routes.py` | System status, Telegram status/test |

Authentication is enforced centrally (fail-closed) for every `/api/*` route except login; see [SECURITY.md](SECURITY.md). The UI is static HTML/JS in `web/static/`.

## 8. Engineering rules

Each exists because breaking it caused a real bug or invalid result.

1. **No lookahead.** Decisions use the current *closed* candle; execution is at the next open. Rolling levels use `.shift(1)` before `.rolling()`. Cross-timeframe and funding joins use `pd.merge_asof(direction="backward")` with an availability timestamp (a daily candle is usable only from the next day). A new strategy should be tested by mutating future bars and asserting earlier results are unchanged.
2. **Holdout is single-use.** Research (`run_backtest`, `walk_forward`, `optimize`) only sees the train segment. `run_holdout_check` is the only tool that reads holdout and needs `--confirm`; run it once per fully decided candidate. Re-checking after a bad result is multiple-comparisons p-hacking.
3. **Costs are never optional.** Fees and slippage apply to every trade. On 15m and 1h, ATR-sized stops are often comparable to round-trip costs, which erases most apparent edge.
4. **One accounting formula.** Backtest and paper both use `backtest/accounting.py`.
5. **Pandas reads use the SQLAlchemy engine** (`db.get_sqlalchemy_engine()`, `pool_pre_ping=True`); plain `mysql-connector` pooled connections are for writes and simple reads. Raw pooled connections with `pd.read_sql` intermittently failed.
6. **Timestamps:** store UTC ms epoch. Compute display columns (WIB, UTC+7) with calendar arithmetic, never `FROM_UNIXTIME()` (it depends on the session timezone).
7. **Batch bulk inserts** in chunks of 2,000 rows with a commit per chunk to stay under `max_allowed_packet`.
8. **Extend the registry, do not special-case.** New strategies are registered in `backtest/strategies/__init__.py` (see [STRATEGIES.md](STRATEGIES.md)).
9. **Risk checks fail closed.** If risk cannot be verified, new entries are blocked.
10. **Test with synthetic data first.** Hand-built fixtures define the expected result before a feature is trusted on real data.

## 9. Module map

| Path | Purpose |
|---|---|
| `collectors/` | Backfill scripts and live WebSocket/REST collectors, news and calendar pollers |
| `features/` | Indicators and the `features` table builder |
| `backtest/` | Engine, accounting, metrics, holdout, walk-forward, optimizer, reports; `strategies/` |
| `paper/` | Paper engine, circuit breakers, `retailbot2` |
| `ml/` | ML signal-filter pipeline, including the shared feature list `SHARED_FEATURE_COLUMNS` (see [ML.md](ML.md)) |
| `ai/` | Insight engine, News AI reasoning and execution |
| `wallet_tracker/` | On-chain tracker package |
| `Screen/` | Smart-money volume detector |
| `notifications/` | Telegram notifier |
| `web/` | FastAPI app, routers, static UI |
| `db/` | `schema.sql`, migrations, DB helpers |
| `tests/` | Pytest suite (accounting, paper parity, auth, ML, screener, wallet tracker) |

## 10. Tests and CI

`python -m pytest -q` runs the suite on Python 3.11 (`.github/workflows/test.yml`). Separate workflows cover paper/RetailBot accounting and screener regressions, and a MariaDB job runs the wallet-tracker concurrency tests.
