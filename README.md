# Crypto Signal Bot

A production-ready quantitative research, backtesting, and paper-trading platform for crypto strategies on Binance data. It collects spot/futures market data and 8-hour funding rates, computes shared indicators, backtests 24+ strategy variants with strict train/holdout discipline, runs deployed strategies as paper trades with fail-closed circuit breakers and Telegram alerts, and integrates ML signal filters, a News/Macro AI overlay, and on-chain wallet tracking — all operated from a real-time React/TypeScript Modern UI workstation.

> **Not a live-trading system.** Execution is simulated (paper). Nothing here places real exchange orders (`RETAILBOT2_ALLOW_TRADING=0` by default).

## What is in the dashboard

The **Modern React/TypeScript UI** (`frontend/`) is served by default at `/` (and `/modern/*`) whenever `frontend/dist` is built (set `SIGNAL_BOT_USE_MODERN_UI=0` to revert to the legacy static dashboard). Press **`Cmd+K` / `Ctrl+K`** anywhere to open the **Command Palette**.

| View | URL | What it does |
|---|---|---|
| Overview | `/modern/overview` | Live system health, MariaDB status, and collector heartbeats |
| Trading Desk | `/modern/trading-desk` | Multi-pane Pro Trader workstation with interactive `lightweight-charts`, live WebSocket tickers/PnL, funding/OI microstructure strip, execution blotter, and portfolio risk desk |
| Market Screener | `/modern/market-screener` | Smart-money volume screener (`Screen/`): live signals, chart inspection, independent Telegram alert toggle, and role-gated detection overrides |
| Paper Trading | `/modern/paper-trading` | Deploy strategies (built-in, custom/AI, ML generator), manage active positions/orders/blotter, funding PnL accounting, portfolio risk desk, and fail-closed circuit breakers |
| Strategy Library | `/modern/strategy-library` | Built-in, RetailBot2, and custom JSON/AI strategies with quick-test and one-click send to Backtest or Paper Trading |
| Wallet Tracking | `/modern/wallet-tracking` | On-chain whale wallet tracking, token flow monitor, manual poll trigger, and per-wallet Telegram alerts |
| Backtest Studio | `/modern/backtest-studio` | Single-strategy runs, **Run All** benchmark, **Walk-Forward Analysis**, synchronized Equity & Underwater Drawdown charts, AI quantitative insights, and Deploy to Paper |
| ML Studio | `/modern/ml-studio` | Purged-split ML signal filter tournaments, saved model inspection, drift/retraining status, and eligible deploy-to-paper |
| Research | `/modern/research` | Historical RetailBot2 Shadow and Inverse results |
| News AI | `/modern/news-ai` | Crypto news sentiment scoring, macroeconomic calendar events, and guarded paper execution |
| Master Data | `/modern/master-data` | Historical OHLCV candle and feature coverage, on-demand backfill, and feature pipeline jobs |
| Settings | `/modern/settings` | Appearance, password & session security, Admin User/Role management, **Encrypted DB-backed Telegram Config & Test Ping**, **Commercial License & Plan status**, **Operator Audit Logs (CSV export)**, and **Onboarding Setup Wizard** |

### Strategy workflow

Select a built-in, RetailBot2 variant, ML generator, or saved custom/AI strategy in **Strategy Library**. Evaluate it in **Backtest Studio** (train/holdout split, walk-forward, equity/drawdown charts), then click **Deploy to Paper** to configure symbol, market, timeframe (`15m`, `1h`, `4h`, `1d`), capital, risk %, and strategy parameter overrides (`strategy_params`). Paper deployments have independent controls for active entries, Telegram trade alerts (entry + close), and reporting. Archiving stops new entries and cancels pending orders while allowing open positions to reach normal exits.

## How it fits together

```
Binance REST/WS ─▶ collectors & funding_poller ─▶ MariaDB 10.11 ─▶ features ─▶ backtest engine ─┐
                                                                                    │            │
                         news / calendar / on-chain APIs                            ▼            ▼
                                    │                                       paper engine ◀─ ML filters
                                    ▼                                              │
                          News AI · Wallet Tracker                                 ▼
                                    └──────────────────▶ FastAPI (REST + /ws/*) ──▶ React Modern UI & Telegram
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (Docker — Recommended for Production)

Requirements: Docker Engine 24+ and Docker Compose v2.20.2+ (the optional bundled-database dependency uses `depends_on.required: false`).

```bash
cp .env.example .env
# Generate a 32+ char APP_SECRET_KEY and set MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD in .env:
python3 -c "import secrets; print(secrets.token_hex(32))"

# Build and start MariaDB, FastAPI + Modern UI, collectors, and the funding poller
# (the bundled MariaDB runs because .env sets COMPOSE_PROFILES=bundled-db;
# the Telegram outbox worker is a separate opt-in, see Settings/docs):
docker compose up -d --build

# Apply all versioned database migrations (000–035):
docker compose exec web python -m db.migrate

# Verify liveness and readiness probes:
curl -s http://127.0.0.1:8050/healthz
curl -s http://127.0.0.1:8050/readyz
```

**External MySQL / MariaDB (database already running outside Docker):**

If MySQL/MariaDB already runs on the host server (or another server), skip the bundled database:

```bash
# In .env:
#   1. Remove bundled-db from COMPOSE_PROFILES (leave it empty or drop the line)
#   2. Point the app at the external database:
#        MYSQL_HOST=host.docker.internal   # host server; or the server's hostname/IP
#        MYSQL_PORT=3306
#        FUNDING_DB_HOST=host.docker.internal
#   3. Make sure the DB users (MYSQL_USER and the dedicated FUNDING_DB_USER)
#      may connect from the Docker network -- a grant for 'localhost' only
#      is not enough; the server must also listen beyond localhost if the
#      app containers should reach it via the host address.
docker compose up -d --build
docker compose exec web python -m db.migrate
```

**Production HTTPS / TLS (Automatic Let's Encrypt via Caddy):**

```bash
SIGNAL_BOT_DOMAIN=desk.yourdomain.com \
SIGNAL_BOT_ALLOWED_ORIGINS=https://desk.yourdomain.com \
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

See [deploy/PRODUCTION_RUNBOOK.md](deploy/PRODUCTION_RUNBOOK.md) for backup/restore, commercial license key minting (`scripts/generate_license.py`), and tagged GHCR releases (`v*.*.*`).

## Quick start (Local Development)

Requirements: Python 3.11+, Node.js 20+, and MariaDB 10.11 / MySQL.

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # set MYSQL_* credentials; the rest is optional
```

**1. Create / upgrade the database:**

```bash
python -m db.migrate            # apply all pending migrations (000–035)
python -m db.migrate --status   # inspect applied / pending / changed
python -m db.migrate --dry-run  # preview without mutating schema
```

`run_all.py` does **not** apply migrations automatically by default (`SIGNAL_BOT_AUTO_MIGRATE=1` to opt in; `SKIP_DB_MIGRATE=1` to force skip).

**2. Build the Modern UI (or run Vite dev server):**

```bash
cd frontend && npm ci && npm run build && cd ..
# Or for hot-reload dev at http://127.0.0.1:5173 (proxies /api and /ws to :8050):
# cd frontend && npm run dev
```

**3. Load history & build features:**

```bash
python -m collectors.backfill_klines                 # add --symbol/--timeframe/--years to test small
python -m features.build_features                    # indicators shared by backtest and paper
```

**4. Start the safe Phase 2A default process set:**

```bash
python3 run_all.py                 # starts web_server, collectors, screener, wallet_tracker
python3 run_all.py --skip screener # omit the screener
```

Optional execution workers require explicit opt-in flags:

```bash
PAPER_ENGINE_ENABLED=1 python3 run_all.py --only paper_engine
NEWS_AI_ENABLED=1 python3 run_all.py --only news_ai
RETAILBOT2_ENABLED=1 python3 run_all.py --only retailbot2  # requires RETAILBOT2_ALLOW_TRADING=1 to trade
```

Standalone funding collector: `python collectors/funding_poller.py`.  
Outbox worker: requires `RETAILBOT2_OUTBOX_WORKER_ENABLED=1` and `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1` (or DB-backed Telegram authorization via **Settings**).

**5. Log in.** On first start, an admin account is seeded (`ADMIN_DEFAULT_PASSWORD` or a random one-time password written to `.initial_admin_credential`, mode `0600`). First login enforces password rotation and Terms & Risk Disclaimer acceptance, with an optional **Onboarding Setup Wizard**. See [docs/SECURITY.md](docs/SECURITY.md).

### Command-line research & licensing tools

```bash
python -m backtest.run_backtest --symbol BTCUSDT --timeframe 1d --market spot
python -m backtest.report --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1
python -m backtest.walk_forward --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --folds 4
python -m backtest.run_holdout_check --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --confirm

# Mint or verify a commercial SB1 license key:
python scripts/generate_license.py --customer "Acme Quant Desk" --tier pro --max-users 10 --days 365
python scripts/generate_license.py --verify "SB1...."
```

## Configuration

See `.env.example` for every supported key. Key production settings:

| Need | Keys |
|---|---|
| Database | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `MYSQL_DATABASE` |
| Production security | `SIGNAL_BOT_ENV=production`, `APP_SECRET_KEY` (required, ≥32 chars), `SIGNAL_BOT_ALLOWED_ORIGINS`, `ACCESS_TOKEN_EXPIRE_SECONDS` (default `3600` in prod) |
| Commercial licensing & branding | `SIGNAL_BOT_LICENSE_KEY`, `SIGNAL_BOT_LICENSE_SIGNING_KEY`, `SIGNAL_BOT_BRAND_NAME`, `SIGNAL_BOT_LOGO_URL` |
| HTTPS / TLS overlay | `SIGNAL_BOT_DOMAIN` (used by `docker-compose.prod.yml` + `deploy/Caddyfile`) |
| Telegram alerts | Configurable in **Settings** (encrypted DB `app_telegram_settings`) or env fallback (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`) |
| Futures & paper risk | `ENABLE_FUTURES=True`, `PAPER_ENTRY_MAX_DELAY_SECONDS=120`, `PAPER_MAX_CONCURRENT_POSITIONS=5`, `PAPER_MAX_DAILY_LOSS_USD=500`, `PAPER_MAX_TOTAL_NOTIONAL_USD=25000` |
| News AI | `CRYPTOPANIC_API_KEY`, `FINNHUB_API_KEY`, `ANTHROPIC_API_KEY` / `LLM_PROVIDER` |

## Documentation

| Doc | Read it for |
|---|---|
| [deploy/PRODUCTION_RUNBOOK.md](deploy/PRODUCTION_RUNBOOK.md) | Production Docker & Caddy TLS quickstart, backup/restore, licensing CLI, and `v*.*.*` GHCR releases |
| [frontend/README.md](frontend/README.md) | React/TypeScript Modern UI architecture, dev server, testing, and production build |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes, data flow, MariaDB schema (000–035), REST + WebSocket API map, engineering rules |
| [docs/SECURITY.md](docs/SECURITY.md) | Authentication, RBAC, security headers, durable WS tickets, rate limits, Telegram boundaries, licensing |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | Strategy library, validation workflow, validated holdout results, adding a strategy |
| [docs/ML.md](docs/ML.md) | ML signal-filter pipeline, purged splits, and tournament workflow |
| [docs/NEWS_AI.md](docs/NEWS_AI.md) | News and macro-calendar AI overlay |
| [docs/WALLET_TRACKER.md](docs/WALLET_TRACKER.md) | On-chain wallet tracking, settings, and limits |
| [docs/STATUS.md](docs/STATUS.md) | Implemented production capabilities and remaining research/cost-realism notes |
| [docs/DEPLOYMENT_AND_ROLLBACK_RUNBOOK.md](docs/DEPLOYMENT_AND_ROLLBACK_RUNBOOK.md) | Bare-metal supervisor startup, verification, and rollback procedures |

## Tests

```bash
# Backend suite
python -m pytest -q

# Frontend typecheck, unit tests, and production build
cd frontend && npm run typecheck && npm test && npm run build
```

MariaDB integration tests are opt-in: `WALLET_TRACKER_MARIADB_TESTS=1` with a disposable database.

## Runtime data

`custom_strategies.json` (saved custom strategies) and `Screen/signals.jsonl` (screener output) are runtime data, not source. Always back up both files outside the repository before running `git rm --cached custom_strategies.json Screen/signals.jsonl` on an existing server.
