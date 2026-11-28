# Architecture

How the system is put together and the rules that keep it correct. For setup see the [README](../README.md) and [deploy/PRODUCTION_RUNBOOK.md](../deploy/PRODUCTION_RUNBOOK.md); for implemented status and known caveats see [STATUS.md](STATUS.md).

## 1. Processes & Containers

In production, all core services run as isolated containers via `docker-compose.yml` (with optional automatic HTTPS via `docker-compose.prod.yml` + `deploy/Caddyfile`). For bare-metal runs, `run_all.py` starts and supervises the Phase 2A default set (restarting after 5 s on crash). Each process can also be run standalone.

The Telegram outbox consumer is separate from the paper engine. It is added to `run_all.py` only when both `RETAILBOT2_OUTBOX_WORKER_ENABLED=1` and `TELEGRAM_DELIVERY_ENABLED=1` (deprecated alias `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`) are explicitly set. Without both flags, trade alerts remain persisted in `rdt_telegram_outbox` and are not sent. The supervisor sends `SIGTERM` during shutdown but refuses to `SIGKILL` this worker so it can cleanly release its MariaDB advisory lock (`rdt_outbox_worker_lock`) after child transport cleanup. (The RetailBot2 worker itself is retired and deleted -- see [RETAILBOT2_RETIREMENT.md](RETAILBOT2_RETIREMENT.md). Its strategies live in the strategy library and run through Paper Trading.)

| Process / Service | Entry point | Role |
|---|---|---|
| `web` / `web_server` | `uvicorn web.app:app` / `run_web.py` (port 8050) | FastAPI REST + WebSocket API, security middleware, and React Modern UI (`frontend/dist`) |
| `collectors` | `run_collectors.py` | Supervised async spot/futures klines and open interest collectors (the liquidations collector is deprecated and not started) |
| `funding-collector` | `collectors/funding_poller.py` | Advisory-locked Binance Futures 8-hour funding rate REST poller with HTTP 418/429 cooldown defense |
| `paper_engine` | `run_paper_engine.py` | Evaluates deployed strategies on new closed candles, manages paper positions/orders, and accrues funding |
| `news_ai` | `run_news_ai.py` | News + macro-calendar pollers, LLM reasoning, guarded paper execution |
| `retailbot2` *(retired)* | -- | Worker deleted; strategies live in `backtest/strategies/retail_death_trap.py` and run through Paper Trading; `rdt_*` tables are a read-only archive (see [RETAILBOT2_RETIREMENT.md](RETAILBOT2_RETIREMENT.md)) |
| `outbox-worker` | `python -m paper.outbox_worker` | Advisory-locked Telegram outbox consumer (explicit opt-in) |
| `screener` | `Screen/screening.py` | Smart-money volume detector with independent `telegram_enabled` control |
| `wallet_tracker` | `run_wallet_tracker.py` | On-chain swap scanner, wallet candidates, per-wallet Telegram alerts |
| `caddy` *(prod overlay)* | `docker-compose.prod.yml` + `deploy/Caddyfile` | Automatic Let's Encrypt HTTPS/TLS termination and WebSocket reverse proxy |

## 2. Data flow

```
collectors & funding_poller ─▶ ohlcv / funding_rate / open_interest / liquidations
                                              │
                                              ▼
                                     features/ (indicators) ─▶ features table
                                              │
                        ┌─────────────────────┴──────────────────────┐
                        ▼                                            ▼
                 backtest/ (simulate.py,                      paper/engine.py ◀── ml/ filters, ai/news_execution.py
                  accounting.py, metrics)                            │
                        │                                            ▼
                        ▼                                    paper_positions / paper_trades ─▶ Telegram
                 backtest_runs / backtest_trades
                        └──────────────────▶ web/ (FastAPI REST + /ws/* + React Modern UI)
```

**Core invariant:** strategies are pure functions `run(df, params) -> list[trade]` over a DataFrame, and backtest and paper trading call the *same* strategy registry (`backtest/strategies/` + `paper_strategy_registry`), feature columns, and accounting module (`backtest/accounting.py`). Never reimplement strategy or accounting logic for a new consumer; import the existing one.

## 3. Scope of the data

Defined in `config.py`: default symbols `BTCUSDT`, `ETHUSDT`, `BNBUSDT` (plus any symbol backfilled via Master Data); timeframes `15m`, `1h`, `4h`, `1d` (exact Binance klines, not resampled); 5 years of history by default (`BACKFILL_YEARS`); spot always, futures with `ENABLE_FUTURES=true`. Timestamps are stored as UTC millisecond epochs.

## 4. Database (Migrations `000`–`035`)

Managed via `python -m db.migrate` (versioned, checksummed, advisory-locked in `schema_migrations`):

| Group | Tables | Key Migrations |
|---|---|---|
| Market data | `ohlcv`, `funding_rate`, `open_interest`, `liquidations`, `features`, `collector_heartbeat` | `000_baseline`, `004`, `010`–`012` |
| Backtests | `backtest_runs`, `backtest_trades` | `000_baseline`, `011` |
| Users & compliance | `users` (`session_version`, `terms_accepted_at`), `app_telegram_settings` | `001`, `020`, `025`, `034` |
| Security & multi-worker state | `audit_logs`, `revoked_tokens`, `login_attempts`, `ws_auth_tickets`, `compute_rate_limits`, `system_flags`, `background_jobs` | `005`, `013`–`015`, `019`, `035` |
| Paper trading | `paper_configs` (`strategy_params`, archive status), `paper_positions`, `paper_trades`, `paper_orders`, promotion policy/audit | `001`–`003`, `016`–`018`, `024`, `029`, `032`–`033` |
| News AI | `news_events`, `news_ai_signals` | `006`–`007` |
| Screener | `smart_money_signals`, `screener_control`, `screener_heartbeat` | `008`, `023`, `031` |
| Research bot & outbox | `rdt_trades`, `rdt_strategy_stats`, `bot_control`, `rdt_strategy_lifecycle`, `rdt_telegram_outbox` | `022`, `026`–`028` |
| Wallet tracking | `wallet_swaps`, `wallet_positions`, `tracked_wallets`, `wallet_tracker_settings`, `wallet_tracker_status`, `wallet_telegram_alerts`, `wallet_tracker_alert_cursor` | `009`, `021`, `030` |

## 5. Backtest and validation engine

- `backtest/simulate.py` is the single execution engine every strategy delegates to: entry at the **next bar's open**, ATR-based stop and target, optional trailing stop, gap-aware OHLC fill resolution, accurate `TIMEOUT`/`END_OF_DATA` vs gap-open funding settlement boundaries, and one position at a time.
- `backtest/accounting.py` holds the financial primitives (position size, fees, directional slippage, signed futures funding accrual, R-multiple), shared by backtest and paper trading.
- Costs come from `config.py`: `BACKTEST_FEE_PCT = 0.001` (0.1% per side) and `BACKTEST_SLIPPAGE_PCT = 0.0005` (0.05% per side).
- `backtest/holdout.py`, `walk_forward.py` (with fold deduplication), `optimize.py`, `pnl_report.py`, and `equity.py` (with bankruptcy ruin floor at `0.0`) provide strict out-of-sample validation and equity analytics.
- `ai/insight_engine.py` turns ranked backtest results into a verdict, risk profile, and deployment notes in Backtest Studio.

## 6. Paper trading

`paper/engine.py` sizes positions from `allocated_capital` and `risk_per_trade_pct`, merges persisted `strategy_params` overrides (protecting `symbol`/`market`/`timeframe`), and computes fees, slippage, settled futures funding (`db.fetch_settled_funding_events`), and R through `backtest/accounting.py`. Both entry and position-close Telegram alerts are gated per deployment by `telegram_enabled`.

`paper/circuit_breaker.py` guards new entries and **fails closed** when it cannot verify state: max concurrent positions (`PAPER_MAX_CONCURRENT_POSITIONS`, default 5), max daily realized loss (`PAPER_MAX_DAILY_LOSS_USD`, default 500, UTC day), total open notional (`PAPER_MAX_TOTAL_NOTIONAL_USD`, default 25,000), and a cross-process emergency stop in `system_flags`.

## 7. Web layer & Modern UI

FastAPI app in `web/app.py` with centralized fail-closed RBAC, security headers middleware, compute rate limiting, unauthenticated `/healthz` + `/readyz` probes, and 14 routers:

| Prefix / Channel | Router | Area |
|---|---|---|
| `/api/auth`, `/api/users` | `auth_routes.py` | Login, logout, token refresh, WS tickets, terms acceptance, password change, filterable audit logs, user admin (with license seat enforcement) |
| `/ws/market`, `/ws/paper`, `/ws/system`, `/api/market/candles` | `ws_routes.py` | Real-time authenticated WebSocket streams & historical OHLCV + funding endpoint |
| `/api/backtest` | `backtest.py` | Strategies, run, run-all, walk-forward, AI insight, history |
| `/api/paper` | `paper_routes.py` | Positions, trades, metrics, configs (with `strategy_params`), order history/cancellation, deploy, circuit breakers |
| `/api/master-data`, `/api/crypto` | `master_data.py`, `search.py` | Coverage, backfill and feature jobs, Binance search |
| `/api/custom-strategy` | `custom_strategy.py` | Custom JSON strategies, AI generation, quick test, safe delete (pauses active paper configs) |
| `/api/ml` | `ml_routes.py` | Experiments, saved models (`/api/ml/models/{model_id}`), generator models, deploy-to-paper |
| `/api/news` | `news_routes.py` | News AI stats, signals, events |
| `/api/research` | `research_routes.py` | RetailBot2 historical archive (read-only GETs) and Screener data/controls (including independent `telegram_enabled`) |
| `/api/wallets` | `wallet_routes.py` | Wallet tracking, manual poll (`POST /api/wallets/poll`), per-wallet Telegram toggle |
| `/api/system/status`, `/api/settings`, `/api/telegram` | `system_status.py`, `settings.py`, `telegram_routes.py` | Public status + branding + license status, admin diagnostics, DB-backed encrypted Telegram config & test |

The primary UI is the React 19 + TypeScript Modern UI in `frontend/` (served at `/` and `/modern/{view}` across all 12 views, with `web/static/` retained as a `SIGNAL_BOT_USE_MODERN_UI=0` fallback). Commercial licensing and white-label branding live in `web/licensing.py`.

## 8. Engineering rules

1. **No lookahead.** Decisions use the current *closed* candle; execution is at the next open. Rolling levels use `.shift(1)` before `.rolling()`. Cross-timeframe and funding joins use `pd.merge_asof(direction="backward")` with an availability timestamp.
2. **Holdout is single-use.** Research (`run_backtest`, `walk_forward`, `optimize`) only sees the train segment. `run_holdout_check` requires `--confirm`.
3. **Costs are never optional.** Fees, slippage, and settled futures funding apply to every trade.
4. **One accounting formula.** Backtest and paper both use `backtest/accounting.py`.
5. **Pandas reads use the SQLAlchemy engine** (`db.get_sqlalchemy_engine()`, `pool_pre_ping=True`); writes and simple reads use `_owned_cursor` on pooled `mysql-connector` connections.
6. **Timestamps:** store UTC ms epoch; compute WIB (UTC+7) display columns with calendar arithmetic, never `FROM_UNIXTIME()`.
7. **Batch bulk inserts** in chunks of 2,000 rows with a commit per chunk.
8. **Extend the registry, do not special-case.** New strategies are registered in `backtest/strategies/__init__.py` (see [STRATEGIES.md](STRATEGIES.md)).
9. **Risk checks fail closed.** If risk or auth storage cannot be verified, actions are blocked.
10. **Test with synthetic data first.** Hand-built fixtures define expected behavior before real-data runs.

## 9. Module map

| Path | Purpose |
|---|---|
| `frontend/` | React 19 + TypeScript + Vite Modern UI (`lightweight-charts`, Trading Desk, Command Palette, Audit Logs, License UI) |
| `web/` | FastAPI app, REST & WebSocket routers, auth/rate-limiting, `licensing.py`, legacy `static/` fallback |
| `collectors/` | Backfill scripts, live WebSocket/REST collectors, `funding_poller.py`, news & calendar pollers |
| `features/` | Shared technical indicators and `features` table builder |
| `backtest/` | Execution simulator, shared accounting, metrics, holdout, walk-forward, optimizer, `strategies/` |
| `paper/` | Paper engine, circuit breakers, order ledger, transactional Telegram outbox worker (`paper/outbox_store.py`) |
| `ml/` | ML signal-filter pipeline, purged splits, generator artifacts (see [ML.md](ML.md)) |
| `ai/` | Quantitative insight engine, News AI reasoning and execution |
| `wallet_tracker/` | On-chain swap scanner, rankings, and Telegram alert dispatcher |
| `Screen/` | Smart-money volume screener and momentum confirmation |
| `notifications/` | Shared Telegram notifier and Fernet-encrypted DB config store |
| `db/` | `schema.sql`, versioned migrations `000`–`035`, `migrate.py`, DB helpers |
| `deploy/` | Production runbook, Caddy TLS config (`Caddyfile`), systemd units, package manifest |
| `scripts/` | Operational utilities including `generate_license.py` commercial license CLI |
| `tests/` | Pytest unit, security, accounting, and MariaDB integration suites |

## 10. Tests and CI

GitHub Actions (`.github/workflows/`) runs five workflows:
- `test.yml` (`Test`): Full `pytest` suite against MariaDB 10.11 on Python 3.11.
- `tests.yml` (`tests`): Fast unit tests + Wallet Tracker MariaDB integration.
- `retailbot-accounting.yml` (`RetailBot accounting regression`): Paper accounting regression tests (the RetailBot2 worker accounting tests were deleted with the worker).
- `frontend.yml` (`Frontend foundation`): `tsc --noEmit`, Vitest unit suite, Vite production build, and Playwright browser smoke tests.
- `release.yml` (`Release & Container Publish`): Triggered on `v*.*.*` tags — runs the MariaDB-backed release verification gate, builds and pushes the multi-stage Docker image to GHCR, and publishes the GitHub Release with deployment assets.
