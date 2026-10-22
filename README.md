# Crypto Signal Bot

A research and paper-trading platform for crypto strategies on Binance data. It collects market data, computes features, backtests 24 strategy variants with strict train/holdout discipline, runs selected strategies as paper trades with risk guards and Telegram alerts, and adds three optional signal sources: ML signal filters, a news/macro AI overlay, and on-chain wallet tracking. Everything is operated from one web dashboard.

> **Not a live-trading system.** Execution is simulated (paper). Nothing here places real orders.

## What is in the dashboard

| Tab | What it does |
|---|---|
| Home & Overview | System summary |
| Master Data | Symbol coverage, on-demand backfill and feature build |
| Backtest Studio | Run one strategy or **all 24 variants** (ranked "Battle Royale"), charts, equity curve, AI insight |
| Paper Trading | Deploy a strategy to the shadow engine, positions/trades/metrics, circuit breakers |
| ML Forecast Studio | Train and evaluate ML signal filters, deploy a model to paper trading |
| Strategy Library | Built-in and custom (JSON) strategies |
| News AI Signals | LLM reading of crypto news and the macro calendar, with guarded paper execution |
| Research Bots | `retailbot2` experimental paper bot: positions, trades, stats, start/stop |
| Market Screener | Smart-money volume screener (`Screen/`): signals, stats, status, start/stop |
| Wallet Tracking | Large on-chain wallet flows and a monitored-wallet activity feed |
| Settings & Users | System status, user management (admin) |

## How it fits together

```
Binance REST/WS ─▶ collectors ─▶ MySQL/MariaDB ─▶ features ─▶ backtest engine ─┐
                                                                  │            │
                       news / calendar / on-chain APIs            ▼            ▼
                                  │                       paper engine ◀─ ML filters
                                  ▼                              │
                        News AI · Wallet Tracker                 ▼
                                  └────────────▶ FastAPI + web UI ──▶ Telegram
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

Requirements: Python 3.11 (what CI uses) and MySQL or MariaDB.

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # set MYSQL_* credentials; the rest is optional
```

**1. Create / upgrade the database.** One command does it, for fresh installs and upgrades alike. It creates the database if missing, applies `schema.sql` plus every migration in order, records each in `schema_migrations`, and is safe to re-run:

```bash
python -m db.migrate            # apply everything pending
python -m db.migrate --status   # applied / pending / changed
python -m db.migrate --dry-run  # show what would run
```

`run_all.py` runs this automatically before starting any process (set `SKIP_DB_MIGRATE=1` if a DBA owns the schema). An existing hand-migrated database is adopted: already-present columns are tolerated and recorded as `applied-idempotent`. Wallet-tracker tables are also created by this runner.

**2. Load history** (default 5 years for BTC/ETH/BNB on 15m, 1h, 4h, 1d; safe to re-run, it upserts):

```bash
python -m collectors.backfill_klines                 # add --symbol/--timeframe/--years to test small
python -m features.build_features                    # indicators used by backtest and paper
```

**3. Start everything:**

```bash
python3 run_all.py                                   # web + all workers, restarts crashed ones
python3 run_all.py --skip retailbot2 --skip screener # skip the experimental bots
```

Open `http://localhost:8050`. `run_all.py --help` lists `--skip` / `--only`. To run one piece by hand: `run_web.py`, `run_collectors.py`, `run_paper_engine.py` (required for paper trading to act on signals), `run_news_ai.py`, `run_wallet_tracker.py`.

**4. Log in.** On first start an admin account is created. Use `ADMIN_DEFAULT_PASSWORD` if you set it; otherwise a random one-time password is written to `.initial_admin_credential` (mode 0600). Accounts flagged `must_change_password` are blocked from other API actions until the password is changed, and the dashboard shows a blocking dialog. See [docs/SECURITY.md](docs/SECURITY.md).

### Command-line research tools

```bash
python -m backtest.run_backtest --symbol BTCUSDT --timeframe 1d --market spot
python -m backtest.report --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1
python -m backtest.walk_forward --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --folds 4
python -m backtest.run_holdout_check --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --confirm
```

See [docs/STRATEGIES.md](docs/STRATEGIES.md) for the validation workflow (the holdout check is single-use by design).

## Configuration

`.env` (see `.env.example` for every key). The ones you will most likely set:

| Need | Keys |
|---|---|
| Database | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` |
| Telegram alerts | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Futures data | `ENABLE_FUTURES=true` (default `false`; Binance futures WebSockets are geo-restricted in some countries) |
| News AI | `CRYPTOPANIC_API_KEY`, `FINNHUB_API_KEY`, and one LLM provider (`LLM_PROVIDER`, plus its key) |
| Web origins | `SIGNAL_BOT_ALLOWED_ORIGINS` (defaults to localhost:8050) |
| Paper entry timing | `PAPER_ENTRY_MAX_DELAY_SECONDS` (default 120; late/missed candle opens are skipped) |

## Documentation

| Doc | Read it for |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data model, engineering rules, API map |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | The 12 strategies, validation workflow, validated results, adding a strategy |
| [docs/ML.md](docs/ML.md) | ML signal-filter pipeline and how to run an experiment |
| [docs/NEWS_AI.md](docs/NEWS_AI.md) | News and macro-calendar AI overlay |
| [docs/WALLET_TRACKER.md](docs/WALLET_TRACKER.md) | On-chain wallet tracking: how it works, settings, limits |
| [docs/SECURITY.md](docs/SECURITY.md) | Authentication, roles, hardening notes |
| [docs/STATUS.md](docs/STATUS.md) | What is implemented, and the verified open gaps |
| [docs/PROJECT_PROGRESS.md](docs/PROJECT_PROGRESS.md) | Progress summary, completion status, and prioritized roadmap |

## Tests

```bash
python -m pytest -q        # CI runs this on Python 3.11
```

MariaDB integration tests are opt-in: `WALLET_TRACKER_MARIADB_TESTS=1` with a disposable database.

## Runtime data

`custom_strategies.json` (your saved custom strategies) and `Screen/signals.jsonl` (screener output) are runtime data, not source. They are still tracked by an older commit. On the machine that holds your real data, after merging, stop tracking them without deleting your copies:

```bash
git rm --cached custom_strategies.json Screen/signals.jsonl
printf 'custom_strategies.json\nScreen/signals.jsonl\n' >> .gitignore
git commit -m "Stop tracking runtime data"
```

Back both files up first if they matter to you.
