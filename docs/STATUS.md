# Status

What exists and works today, and the known gaps. Reconciled against current `master` on 2026-10-25; historical verification claims should still be checked against the latest CI and runtime evidence. Keep this file short: when a gap is fixed, delete the line; when you add a capability, add one row. Do not add dated reviews or history here (use pull-request descriptions).

## 1. Implemented

| Area | What exists | Where |
|---|---|---|
| Data pipeline | Historical backfill and live collectors for klines, funding, open interest, liquidations (BTC/ETH/BNB; 15m, 1h, 4h, 1d); shared feature builder | `collectors/`, `features/` |
| Backtesting | One execution engine, shared accounting with configurable per-side fee/slippage assumptions, funding and position sizing; 24 strategy variants; metrics, equity, PnL report; read-only training-segment cost sensitivity report | `backtest/`, [cost sensitivity](BACKTEST_COST_SENSITIVITY.md) |
| Validation | Train/holdout split, single-use holdout check, walk-forward, optimizer, ranked "run all" screening | [STRATEGIES.md](STRATEGIES.md) |
| Paper trading | Shadow engine, shared accounting, idempotent syncs, circuit breakers that fail closed, Telegram alerts, backtest/paper parity tests | `paper/`, `notifications/` |
| ML filters | Leakage-safe pipeline, tournament, versioned artifacts, deploy-to-paper | [ML.md](ML.md) |
| News AI | News and calendar ingest, structured LLM reasoning, guarded paper execution | [NEWS_AI.md](NEWS_AI.md) |
| Wallet tracking | Swap scanner, candidates, token flow, quality-gated follow with expiry, live feed, Telegram alerts, single-instance lock | [WALLET_TRACKER.md](WALLET_TRACKER.md) |
| Research bots | `retailbot2` and the smart-money volume screener (experimental, own tables); RetailBot2 has DB-backed per-strategy evaluation/paper/Telegram/report controls, fail-closed restored exit alerts, lifecycle DB-outage fail-closed behavior, and regression coverage | `paper/retailbot2.py`, `web/routes/research_routes.py`, `Screen/`, [lifecycle spec](UI_MANAGED_STRATEGY_PROMOTION.md) |
| Main paper strategy management | Dashboard can add/update any registered executable Strategy Library strategy; deployment validates strategy, market, and timeframe before side effects. Telegram alerts are separately controlled and default off. | `web/routes/paper_routes.py`, dashboard paper view |
| Web and security | FastAPI app, central fail-closed authentication, three roles, token revocation, audit log, login rate limiting, random first-admin password, ML experiment path containment | [SECURITY.md](SECURITY.md) |
| Operations | `run_all.py` supervisor with restart backoff; CI on Python 3.11 including a MariaDB integration job | `.github/workflows/` |

Verified this session: a fresh database built from the README steps boots the app and the admin login works; all 74 API operations reject unauthenticated calls except login; the viewer role can call 0 of 29 mutating operations; the paper circuit breaker allows entries on an empty database (now covered by tests).

## 2. Known gaps

**Resolved on `master` by PRs #235–#237 (regression tests included; CI passed for the PR heads; production deployment is not implied)**

- **ML eligibility and activation are enforced fail-closed.** PR #253 rejects non-finite/malformed metrics, fractional trade counts, and negative drawdown; the registry and deploy API re-evaluate test metrics. PR #255 aligns model listing with deployability and validates regular weight files/manifest IDs. PR #259 validates the finite threshold and strategy metadata before any paper-config side effect. Thresholds remain at least 30 untouched test trades, profit factor at least 1.0, drawdown from 0% to 25%, and net PnL at least 0. PR #257 also constrains experiment-result paths to the results directory. PR #263 rejects manifest/weight symlinks escaping ml_models/.
- **AI insight.** Kelly is computed from realized win rate and profit factor with no floor; a non-positive edge gets the `NO_EDGE` verdict and 0% risk. The regime label uses EMA20/EMA50 computed from `close`.
- **Portfolio exposure limit.** `PAPER_MAX_TOTAL_NOTIONAL_USD` (default 25000) caps the summed notional of open paper positions. It is checked in `_insert_position`, so it covers both the strategy path and the news-AI path, and it fails closed when exposure cannot be verified.
- **ML filter fails closed.** If a model is attached to a paper config but its files are missing or prediction raises, the entry is skipped (it previously traded without the filter).
- **ML runtime artifact reads and writes fail closed.** PR #266 resolves artifact paths and rejects traversal/symlink escapes; PR #267 revalidates manifest ID, eligibility flag, and test metrics before paper-runtime loading; PR #269 writes weights/manifests atomically and preserves existing artifacts; PR #271 rejects external symlinks when the reusable model store reads weights or manifests.
- **Metric population.** `compute_metrics` now reports `scored_trades` (the population used for win rate, expectancy and profit factor) next to `total_trades`.
- **First-login password change is enforced** by the API (every route except `/api/auth/me`, `/logout`, `/change-password` returns 403 `password_change_required`) and by a blocking dashboard dialog. New passwords must be at least 10 characters and not trivially weak.
- **Login rate limit is durable** (`login_attempts` table, shared by all workers, survives restarts); it falls back to in-memory if the database is unavailable.
- **Bind address is explicit.** `run_web.py` / `web/app.py` default to `127.0.0.1`; set `SIGNAL_BOT_HOST=0.0.0.0` (or `--host`) deliberately, behind TLS and a firewall.
- **Master-data job state is persisted** (`background_jobs` table). Jobs a previous process left `queued`/`running` are shown as `interrupted` after a restart.
- **Migration runner.** `python -m db.migrate` (also run automatically by `run_all.py`) applies versioned, checksummed migrations under an advisory lock, distinguishes fresh installs from upgrades by checking for any existing application tables (not just `ohlcv`), and adopts hand-migrated databases. A repair migration now adds each WIB generated column in a separate statement, so a duplicate column cannot block its missing sibling. Regression coverage includes a partial database with application tables but no `ohlcv` table. Opt-in MariaDB migration tests now create per-run unique database names and only drop the database created by that test, avoiding collisions with pre-existing databases.
- **Emergency stop is now shared across processes.** It was a Python global in the web process, so the dashboard's Emergency Stop never reached the separate paper-engine process. It is now stored in `system_flags` (migration 015) and read by the engine's circuit breaker; it survives restarts. Verified live with a second process.
- **Status bar for every role.** `/api/system/status` (any signed-in user, no connection details) feeds the status bar; the detailed `/api/settings/status` stays admin-only and no longer returns the DB username. Viewers and traders previously saw a false "database unavailable".
- **Dashboard fixes found by live browser testing:** data loads only after a usable sign-in (no more 401 storms for anonymous visitors); the active tab loads right after login; Settings no longer crashes on a removed element; admin user-management errors are readable toasts instead of `[object Object]` alerts; weak-password variants such as `password1234` are rejected.
- **Route authorization sweep.** `tests/test_route_authorization_sweep.py` is generated from the live route table.

**Still open**

0. **RetailBot2 strategy lifecycle and auto-promotion are only partially complete.** PR #317 fails closed when persisted lifecycle settings cannot refresh, and preserves historical report visibility during the outage. General winner auto-promotion is not complete: it must remain opt-in, enforce validated backtest/holdout eligibility, support only strategies with an explicit paper configuration mapping, and never turn on Telegram alerts without the user's saved choice. Add restart/persistence verification for stop/remove behavior and confirm `BB_REV_INV_inverse` and `DBL_PAT_INV_inverse` against the user's persisted lifecycle settings; do not assume their runtime state from code alone. See [UI-managed strategy lifecycle](UI_MANAGED_STRATEGY_PROMOTION.md) and [project progress](PROJECT_PROGRESS.md).
1. **Execution lifecycle code is implemented; release verification remains open (P0.5).** PRs #239/#241 add timing guards, #243 adds the ledger, #245 integrates atomic fills/recovery, #247 adds authenticated history/cancellation, #248 adds dashboard state/filter/cancel controls, and #250 adds passing disposable-MariaDB tests for concurrent fills, cancel-vs-fill races, and rollback/retry after a simulated crash. An opt-in MariaDB test now adopts a populated legacy-style database and checks historical OHLCV rows remain unchanged; still validate against a representative snapshot of the actual deployed schema and perform manual browser/accessibility checks. Paper/shadow only. Track evidence and hard stop conditions in [RELEASE_VERIFICATION_CHECKLIST.md](RELEASE_VERIFICATION_CHECKLIST.md); see also [PAPER_ORDER_LIFECYCLE.md](PAPER_ORDER_LIFECYCLE.md).
2. **Cost realism remains open.** Fees and slippage are now environment-configurable per side (`BACKTEST_FEE_PCT`, `BACKTEST_SLIPPAGE_PCT`) with backward-compatible defaults of 0.1% and 0.05% respectively (PR #289). PR #290 adds a read-only training-segment sensitivity report across explicit fee × slippage scenarios. These are still constant-rate assumptions: there is no historical bid/ask spread, order-book depth, volume participation, market-impact, latency, or venue-specific fee-tier model. Sensitivity is not evidence of profitability; validate assumptions against trustworthy venue/instrument data before treating results as realistic.
3. **Artifact provenance remains partial:** paper model manifests record a SHA-256 weight digest and the runtime verifies it before deserialization. PR #296 documents the threat model, signed-artifact contract, key-management options, and rollout gates. Signing and signature enforcement are **not implemented**; a matching adjacent digest still cannot authenticate an artifact if both files can be replaced. Implementation is blocked on owner decisions for signer infrastructure, authorized approvers, public-key trust/distribution, rotation/revocation, and approval workflow. See [ML artifact provenance design](ML_ARTIFACT_PROVENANCE_DESIGN.md).
4. **Runtime data in git:** ignore rules are now prepared for `custom_strategies.json` and `Screen/signals.jsonl`, but the files remain tracked until a separate preservation-first untracking change. Back up the real files before pulling any commit that removes them; follow README, "Runtime data".
5. **Repo hygiene:** over 30 branches, many merged or abandoned (including `do-not-use`, `ignore2`, `tmp-ignore`, `x`).

**Wallet tracking** has its own list of verified limitations in [WALLET_TRACKER.md](WALLET_TRACKER.md#6-known-limitations-verified-open) (alert digest, per-pool grouping, stablecoin metadata, bot filtering, no skill scoring, Birdeye fallback unvalidated, startup ALTER privilege).

## 3. Tests

CI runs the pytest suite, project tests, RetailBot accounting regression, and an opt-in MariaDB integration job. Consult the latest workflow results for the current counts and conclusions; test success is not evidence of trading edge. Only the strategies documented in [STRATEGIES.md](STRATEGIES.md#4-results-that-passed-holdout) have passed the stated holdout process.

## 4. Documentation map

| File | Purpose |
|---|---|
| [README.md](../README.md) | What it is, quick start, configuration |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data model, engineering rules |
| [STRATEGIES.md](STRATEGIES.md) | Strategy library, validation workflow, validated results |
| [ML.md](ML.md), [NEWS_AI.md](NEWS_AI.md), [WALLET_TRACKER.md](WALLET_TRACKER.md) | One doc per signal feature |
| [SECURITY.md](SECURITY.md) | Authentication, roles, deployment notes |
| [RELEASE_VERIFICATION_CHECKLIST.md](RELEASE_VERIFICATION_CHECKLIST.md) | Evidence-based CI, migration, paper lifecycle, and manual accessibility release gates |
| [ML_ARTIFACT_PROVENANCE_DESIGN.md](ML_ARTIFACT_PROVENANCE_DESIGN.md) | Threat model, signed-artifact contract, key-management decisions, and provenance rollout gates |
| STATUS.md | This file: implemented capabilities and verified gaps |
