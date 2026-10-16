# Status

What exists and works today, and the known gaps. Every item below was checked against the code (and, where noted, by running it) on `master` (after PR #24), 2026-10-15. Keep this file short: when a gap is fixed, delete the line; when you add a capability, add one row. Do not add dated reviews or history here (use pull-request descriptions).

## 1. Implemented

| Area | What exists | Where |
|---|---|---|
| Data pipeline | Historical backfill and live collectors for klines, funding, open interest, liquidations (BTC/ETH/BNB; 15m, 1h, 4h, 1d); shared feature builder | `collectors/`, `features/` |
| Backtesting | One execution engine, shared accounting with fees, slippage, funding and position sizing; 24 strategy variants; metrics, equity, PnL report | `backtest/` |
| Validation | Train/holdout split, single-use holdout check, walk-forward, optimizer, ranked "run all" screening | [STRATEGIES.md](STRATEGIES.md) |
| Paper trading | Shadow engine, shared accounting, idempotent syncs, circuit breakers that fail closed, Telegram alerts, backtest/paper parity tests | `paper/`, `notifications/` |
| ML filters | Leakage-safe pipeline, tournament, versioned artifacts, deploy-to-paper | [ML.md](ML.md) |
| News AI | News and calendar ingest, structured LLM reasoning, guarded paper execution | [NEWS_AI.md](NEWS_AI.md) |
| Wallet tracking | Swap scanner, candidates, token flow, quality-gated follow with expiry, live feed, Telegram alerts, single-instance lock | [WALLET_TRACKER.md](WALLET_TRACKER.md) |
| Research bots | `retailbot2` and the smart-money volume screener (experimental, own tables) | `paper/retailbot2.py`, `Screen/` |
| Web and security | FastAPI app, central fail-closed authentication, three roles, token revocation, audit log, login rate limiting, random first-admin password | [SECURITY.md](SECURITY.md) |
| Operations | `run_all.py` supervisor with restart backoff; CI on Python 3.11 including a MariaDB integration job | `.github/workflows/` |

Verified this session: a fresh database built from the README steps boots the app and the admin login works; all 74 API operations reject unauthenticated calls except login; the viewer role can call 0 of 29 mutating operations; the paper circuit breaker allows entries on an empty database (now covered by tests).

## 2. Known gaps (verified open)

**Trading and analytics**

1. **No execution lifecycle (P0.5).** The paper engine finds signals by re-running the strategy on closed candles and enters at the next open. It has no order state machine (pending, filled, expired), as its own module docstring says. It is a shadow engine, not live execution.
2. **ML eligibility gate is not enforced.** `ml/artifacts.py: paper_eligibility` defines the gate (at least 30 test trades, profit factor at least 1.0, drawdown at most 25%, net PnL at least 0), but `ml/run_real_experiment.py` instead marks a model eligible whenever total test R is above zero, and `POST /api/ml/deploy-to-paper` never checks `eligible_for_paper`. A model with a handful of lucky test trades can be deployed.
3. **AI insight inputs.** The Kelly suggestion has a floor (`max(0.2, …)`), so a negative-edge result can still recommend a positive risk. The regime detector reads `ema_20`, `ema_50` and `adx`, which the feature tables do not contain, so the trend/range label can only fall back to "Neutral / Sideways Range" (from code reading; not observed in the UI).
4. **Flat cost model.** Fees (0.1%) and slippage (0.05%) per side are constants; there is no spread, volume or liquidity model.
5. **No portfolio-level exposure limit.** Circuit breakers cap position count and daily loss, not notional exposure across symbols.
6. **Metric nit.** `total_trades` counts all trades while win rate uses only those with a recorded R multiple (`backtest/metrics.py`).

**Security and operations**

7. **First-login password change is not enforced** (flag returned, never checked by API or dashboard).
8. **Login rate limit is in memory** (per process; resets on restart).
9. **`run_web.py` listens on `0.0.0.0` by default** and prints a hard-coded LAN address.
10. **Master-data job state is in memory** (`JOBS` in `web/routes/master_data.py`); jobs are lost on restart.
11. **No migration runner.** Eight migrations are applied by hand in the README order; three more are upgrade-only (two of them fail with `Duplicate column` on a fresh install).
12. **Runtime data in git:** `custom_strategies.json` and `Screen/signals.jsonl` are tracked.
13. **No test sweeps every route for authentication and role rules.**
14. **Repo hygiene:** over 30 branches, many merged or abandoned (including `do-not-use`, `ignore2`, `tmp-ignore`, `x`).

**Wallet tracking** has its own list of verified limitations in [WALLET_TRACKER.md](WALLET_TRACKER.md#6-known-limitations-verified-open) (alert digest, per-pool grouping, stablecoin metadata, bot filtering, no skill scoring, Birdeye fallback unvalidated, startup ALTER privilege).

## 3. Tests

Full suite as CI runs it (`python -m pytest`, pinned `requirements.txt`): **225 passed, 0 failed, 3 skipped**; the 3 skipped are the opt-in MariaDB tests, which pass when run with `WALLET_TRACKER_MARIADB_TESTS=1`. Passing tests show the code does what the tests say; they are not evidence of trading edge. Only the two strategies in [STRATEGIES.md](STRATEGIES.md#4-results-that-passed-holdout) have passed holdout.

## 4. Documentation map

| File | Purpose |
|---|---|
| [README.md](../README.md) | What it is, quick start, configuration |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data model, engineering rules |
| [STRATEGIES.md](STRATEGIES.md) | Strategy library, validation workflow, validated results |
| [ML.md](ML.md), [NEWS_AI.md](NEWS_AI.md), [WALLET_TRACKER.md](WALLET_TRACKER.md) | One doc per signal feature |
| [SECURITY.md](SECURITY.md) | Authentication, roles, deployment notes |
| STATUS.md | This file: implemented capabilities and verified gaps |
