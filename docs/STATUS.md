# Status

What exists and works today, and the known caveats. Reconciled against `master` at **Release `v1.0.1`** (`a498c0b`, PRs #754–#782) on **2026-11-24**.

## 1. Implemented (Production-Ready `v1.0.1`)

| Area | What exists | Where |
|---|---|---|
| Data pipeline & funding | Historical backfill, supervised live collectors (`run_collectors.py`), advisory-locked Binance Futures 8h funding rate poller (`funding_poller.py`) with HTTP 418/429 cooldown defense; shared feature builder | `collectors/`, `features/` |
| Backtesting & math | Single execution engine (`simulate.py`), shared accounting (`accounting.py`), bankruptcy ruin floor (`equity.py`), fold-deduplicated walk-forward (`walk_forward.py`), accurate funding exit boundaries, holdout validation, cost sensitivity | `backtest/`, [cost sensitivity](BACKTEST_COST_SENSITIVITY.md) |
| Paper trading | Shadow engine with persisted `strategy_params` overrides (migration 033), settled futures funding accrual, entry + position-close Telegram alerts, fail-closed circuit breakers, and backtest/paper parity tests | `paper/`, `notifications/` |
| Pro Trader Modern UI | React 19 + TypeScript SPA served by default at `/` & `/modern/*` across 12 views (including `/modern/trading-desk`), `lightweight-charts` v5, WebSocket live streams (`/ws/market`, `/ws/paper`, `/ws/system`), Command Palette (`Cmd+K`), Onboarding Wizard, Audit Logs viewer, and Commercial License card | `frontend/`, [frontend/README.md](../frontend/README.md) |
| ML filters | Purged chronological and walk-forward evaluation, execution-aware tournament, versioned artifacts, `GET /api/ml/models/{model_id}`, verified paper inference, `ml_signal_generator_v1` paper deploy | [ML.md](ML.md) |
| News AI & Wallet tracking | News/macro LLM overlay with guarded paper execution; on-chain swap scanner, rankings, `POST /api/wallets/poll`, and per-wallet Telegram alerts | [NEWS_AI.md](NEWS_AI.md), [WALLET_TRACKER.md](WALLET_TRACKER.md) |
| Smart Money Screener | Volume/momentum detector with independent `telegram_enabled` override and pause confirmation | `Screen/`, `web/routes/research_routes.py` |
| Security & compliance | Fail-closed RBAC, HTTP security headers (CSP/HSTS/`X-Frame-Options`), CORS `PATCH`, 1h prod token TTL + silent refresh, MariaDB-backed single-use WS tickets & compute rate limits (migration 035), Fernet-encrypted DB Telegram settings (migration 034), Terms acceptance, and immutable Operator Audit Logs | [SECURITY.md](SECURITY.md) |
| Commercial packaging & CI/CD | Hardened multi-stage non-root `Dockerfile` + `.dockerignore`, `docker-compose.yml`, Caddy HTTPS/TLS overlay (`docker-compose.prod.yml`), signed `SB1` license keys + `scripts/generate_license.py`, white-label branding, and automated GHCR + GitHub Release pipeline (`release.yml`) | `deploy/PRODUCTION_RUNBOOK.md`, `web/licensing.py`, `.github/workflows/` |

**Release Evidence:** Release [`v1.0.1`](https://github.com/kurnFR/signal-bot/releases/tag/v1.0.1) (`a498c0b`) passed all backend (`Test`, `tests`, `RetailBot accounting regression`), frontend (`Frontend foundation`), and `Release & Container Publish` workflows and published the GHCR Docker image with deployment assets.

## 2. Known gaps

**Resolved on `master` by PRs #235–#237 (regression tests included; CI passed for the PR heads; production deployment is not implied)**

- **ML eligibility and activation are enforced fail-closed.** PR #253 rejects non-finite/malformed metrics, fractional trade counts, and negative drawdown; the registry and deploy API re-evaluate test metrics. PR #255 aligns model listing with deployability and validates regular weight files/manifest IDs. PR #259 validates the finite threshold and strategy metadata before any paper-config side effect. Thresholds remain at least 30 untouched test trades, profit factor at least 1.0, drawdown from 0% to 25%, and net PnL at least 0. PR #257 also constrains experiment-result paths to the results directory. PR #263 rejects manifest/weight symlinks escaping ml_models/.
- **AI insight.** Kelly is computed from realized win rate and profit factor with no floor; a non-positive edge gets the `NO_EDGE` verdict and 0% risk. The regime label uses EMA20/EMA50 computed from `close`.
- **Portfolio exposure limit.** `PAPER_MAX_TOTAL_NOTIONAL_USD` (default 25000) caps the summed notional of open paper positions. It is checked in `_insert_position`, so it covers both the strategy path and the news-AI path, and it fails closed when exposure cannot be verified.
- **ML filter fails closed.** If a model is attached to a paper config but its files are missing or prediction raises, the entry is skipped (it previously traded without the filter).
- **ML runtime artifact reads and writes fail closed.** PR #266 resolves artifact paths and rejects traversal/symlink escapes; PR #267 revalidates manifest ID, eligibility flag, and test metrics before paper-runtime loading; PR #269 writes weights/manifests atomically and preserves existing artifacts; PR #271 rejects external symlinks when the reusable model store reads weights or manifests.
- **Metric population.** `compute_metrics` now reports `scored_trades` (the population used for win rate, expectancy and profit factor) next to `total_trades`.
- **First-login password change is enforced** by the API (every route except `/api/auth/me`, `/logout`, `/change-password` returns 403 `password_change_required`) and by a blocking dashboard dialog. New passwords must be at least 10 characters and not trivially weak.
- **Session invalidation is per-user and fail-closed.** Admin password reset, admin role/active-state changes, and self-service password changes increment `users.session_version`; older signed tokens are rejected. Password changes return a replacement token, which both React and legacy dashboard clients store (PRs #612 and #614).
- **Logout failure is visible and retryable.** If the server cannot confirm token revocation, the frontend keeps the session and offers a retry on both the dashboard and forced-password-change screen (PRs #613 and #615).
- **API authentication is bearer-only.** The unused `auth_token` cookie fallback was removed after repository-wide search found no cookie issuer or cookie-only client; cookie-only requests receive HTTP 401 (PR #617).
- **Revocation storage is expiry-bounded.** Local revoked-token cache entries expire with their tokens (PR #611); expired MariaDB blacklist rows are pruned in batches of 1,000 under a non-blocking advisory lock, on a separate best-effort transaction after revocation/audit commit. Cleanup failure cannot roll back revocation.
- **Login rate limit is durable and fail-closed** (`login_attempts` table, shared by all workers, survives restarts); unavailable shared storage blocks login with HTTP 503 rather than falling back to process-local counters (PR #606).
- **Mandatory audit records are durable.** Login/security events fail with HTTP 503 if their required audit write fails; DB-backed logout, password changes, and admin user mutations commit the business change and audit row atomically (PR #607).
- **Bind address is explicit.** `run_web.py` / `web/app.py` default to `127.0.0.1`; set `SIGNAL_BOT_HOST=0.0.0.0` (or `--host`) deliberately, behind TLS and a firewall.
- **Master-data job state is persisted** (`background_jobs` table). Jobs a previous process left `queued`/`running` are shown as `interrupted` after a restart.
- **Migration runner.** `python -m db.migrate` applies versioned, checksummed migrations under an advisory lock, distinguishes fresh installs from upgrades by checking for any existing application tables (not just `ohlcv`), and adopts hand-migrated databases. `run_all.py` does not invoke it automatically by default; startup migration execution requires `SIGNAL_BOT_AUTO_MIGRATE=1`, unless overridden by `SKIP_DB_MIGRATE=1`. A repair migration now adds each WIB generated column in a separate statement, so a duplicate column cannot block its missing sibling. Regression coverage includes a partial database with application tables but no `ohlcv` table. Opt-in MariaDB migration tests now create per-run unique database names and only drop the database created by that test, avoiding collisions with pre-existing databases.
- **Emergency stop is now shared across processes.** It was a Python global in the web process, so the dashboard's Emergency Stop never reached the separate paper-engine process. It is now stored in `system_flags` (migration 015) and read by the engine's circuit breaker; it survives restarts. Verified live with a second process.
- **Status bar for every role.** `/api/system/status` (any signed-in user, no connection details) feeds the status bar; the detailed `/api/settings/status` stays admin-only and no longer returns the DB username. Viewers and traders previously saw a false "database unavailable".
- **Dashboard fixes found by live browser testing:** data loads only after a usable sign-in (no more 401 storms for anonymous visitors); the active tab loads right after login; Settings no longer crashes on a removed element; admin user-management errors are readable toasts instead of `[object Object]` alerts; weak-password variants such as `password1234` are rejected.
- **Route authorization is explicit and fail-closed.** Viewer-readable GET routes are enumerated in `AUTHENTICATED_READ_PATHS`; new registered GET routes remain denied until reviewed. The route sweep checks every registered GET route's viewer decision and denies unknown GET/mutation routes (PR #610).
- **ML artifact listing paths are contained.** Experiment, saved-model, and RL-model listings resolve JSON files before sorting or reading and skip external symlink targets; the RL artifact directory itself cannot be a symlink (PR #639).
- **ML evaluation labels are purged at time boundaries.** Supervised datasets retain trade exit timestamps; train and validation labels whose outcomes cross into the next partition are removed, including absolute-timestamp and walk-forward splits. Tournament entry points reject datasets without exit timestamps (PR #661).
- **Paper ML inference uses the verified artifact path end to end.** The paper engine validates one manifest snapshot, hashes and deserializes weights through the same file descriptor, and constructs its filter through the artifact factory rather than unsupported constructor arguments (PRs #659, #660, #664).
- **Online retraining checkpoints only persisted artifacts.** The retrainer normalizes the current experiment-result schema and verifies the matching manifest/weight pair and SHA-256 before advancing the schedule; missing or mismatched artifacts fail the cycle (PRs #662, #666). Automatic paper promotion remains unimplemented and is not implied by a retraining success.
- **ML experiment dashboard contracts are aligned.** Result JSON exposes the experiment ID, market, baseline test metrics, validation diagnostics, and Brier score; list/detail APIs normalize older result files (PRs #663, #665).
- **ML and Backtest error responses redact internal exception details.** The ML retraining status API returns only an allowlisted audit schema and a generic failure message (PR #634); ML walk-forward and RL training endpoints return generic errors (PR #635); rolling Backtest walk-forward does the same while preserving its existing HTTP status (PR #636). Detailed diagnostics remain in server logs. PR-head and post-merge CI passed for all three.
- **ML request schemas validate before training/data access.** Symbols are normalized and bounded; market/timeframe values, registered strategies, model/target types, RL agents/reward functions, and numeric training bounds are checked (PR #638).
- **Online ML retraining audit reports truthful promotion state.** Successful cycles are labeled `retrained`, not `retrained_and_promoted`; the audit distinguishes requested promotion from applied promotion and reports `not_implemented` until a real paper-activation path exists (PR #641). This does not implement automatic paper promotion.

**Still open**

0. **RetailBot2 strategy lifecycle and auto-promotion are only partially complete.** PR #317 fails closed when persisted lifecycle settings cannot refresh, and preserves historical report visibility during the outage. General winner auto-promotion is not complete: it must remain opt-in, enforce validated backtest/holdout eligibility, support only strategies with an explicit paper configuration mapping, and never turn on Telegram alerts without the user's saved choice. PR #320 adds deterministic API read-after-save and fresh-worker reload regression tests for stop/remove settings. Still verify the actual owner-environment database/worker restart behavior and confirm `BB_REV_INV` with mode `inverse` and `DBL_PAT_INV` with mode `inverse` against the persisted lifecycle settings; do not assume their runtime state from code alone. PR #322 adds route regression coverage that both requested inverse identities are registered, default Telegram/auto-promotion off, and that the RetailBot2 lifecycle API refuses to enable its not-yet-implemented auto-promotion flag without saving. All PR-head checks passed; actual saved state and worker behavior remain unverified. See [UI-managed strategy lifecycle](UI_MANAGED_STRATEGY_PROMOTION.md) and [project progress](PROJECT_PROGRESS.md).
   The 2026-10-25 seven-strategy Backtest Studio compatibility crosswalk (PR #344) found **zero approved mappings**. Keep unsupported mappings absent; the main paper engine's auto-promotion is separate from RetailBot2 and does not activate its lifecycle. See [UI-managed strategy lifecycle](UI_MANAGED_STRATEGY_PROMOTION.md).
1. **Execution lifecycle code is implemented; release verification remains open (P0.5).** PRs #239/#241 add timing guards, #243 adds the ledger, #245 integrates atomic fills/recovery, #247 adds authenticated history/cancellation, #248 adds dashboard state/filter/cancel controls, and #250 adds passing disposable-MariaDB tests for concurrent fills, cancel-vs-fill races, and rollback/retry after a simulated crash. An opt-in MariaDB test now adopts a populated legacy-style database and checks historical OHLCV rows remain unchanged; still validate against a representative snapshot of the actual deployed schema and perform manual browser/accessibility checks. Paper/shadow only. Track evidence and hard stop conditions in [RELEASE_VERIFICATION_CHECKLIST.md](RELEASE_VERIFICATION_CHECKLIST.md); see also [PAPER_ORDER_LIFECYCLE.md](PAPER_ORDER_LIFECYCLE.md).
2. **Cost realism remains open.** Fees and slippage are now environment-configurable per side (`BACKTEST_FEE_PCT`, `BACKTEST_SLIPPAGE_PCT`) with backward-compatible defaults of 0.1% and 0.05% respectively (PR #289). PR #290 adds a read-only training-segment sensitivity report across explicit fee × slippage scenarios. These are still constant-rate assumptions: there is no historical bid/ask spread, order-book depth, volume participation, market-impact, latency, or venue-specific fee-tier model. Sensitivity is not evidence of profitability; validate assumptions against trustworthy venue/instrument data before treating results as realistic.
3. **Artifact provenance remains partial:** paper model manifests record a SHA-256 weight digest and the runtime verifies it before deserialization. PR #296 documents the threat model, signed-artifact contract, key-management options, and rollout gates. Signing and signature enforcement are **not implemented**; a matching adjacent digest still cannot authenticate an artifact if both files can be replaced. Implementation is blocked on owner decisions for signer infrastructure, authorized approvers, public-key trust/distribution, rotation/revocation, and approval workflow. See [ML artifact provenance design](ML_ARTIFACT_PROVENANCE_DESIGN.md).
4. **Runtime data in git:** ignore rules are now prepared for `custom_strategies.json` and `Screen/signals.jsonl`, but the files remain tracked until a separate preservation-first untracking change. Back up the real files before pulling any commit that removes them; follow README, "Runtime data".
5. **Repo hygiene:** over 30 branches, many merged or abandoned (including `do-not-use`, `ignore2`, `tmp-ignore`, `x`).

**Wallet tracking** has its own list of verified limitations in [WALLET_TRACKER.md](WALLET_TRACKER.md#6-known-limitations-verified-open) (alert digest, per-pool grouping, stablecoin metadata, bot filtering, no skill scoring, Birdeye fallback unvalidated, startup ALTER privilege).


- Backtest Studio promotion regression coverage now explicitly checks that an unmapped strategy, unavailable policy storage, or unavailable audit-claim persistence cannot activate a paper config (PR #328, merged as `1f8b2ed`). This does not complete generalized RetailBot2 auto-promotion; its API remains fail-closed until an explicit executable mapping and lifecycle-safe validation path exists.
- Merged in PR #330 (`8fca11d`): an identical promotion decision with a prior audit status of `processing` is surfaced as `in_progress` and instructs operators to reconcile config/Telegram side effects before retrying. It does not auto-retry or claim crash recovery is solved; all five required CI checks passed on the final head.

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

- Merged in PR #331 (`ac7be2d`): the Research API forces historical-only composite rows to remain non-executable after merging persisted settings; report visibility remains separately controlled. All five required CI checks passed on the final head.
- Merged in PR #332 (`c9caa1c`): the RetailBot2 worker independently forces `COMPOSITE` and `COMPOSITE_INV` to remain non-executable even if legacy/manual lifecycle rows enable them. Report visibility remains separately controlled. All five required CI checks passed on final head; live worker/DB behavior is not verified.

- Merged in PR #333 (`0553bbe`): progress/status docs now reflect PR #332's worker-side composite guard and retain the live-verification caveat.
- Merged in PR #334 (`436ab38`): RetailBot2 now always attempts the initial lifecycle DB read regardless of OS monotonic uptime. All five required CI checks passed on the final head. This closes the startup code-path gap in tests; live worker/DB behavior remains unverified.


- Merged in PR #336 (`f65abab`): lifecycle refresh outage regression coverage simulates a failed initial DB read, verifies fail-closed evaluation/new paper entries/Telegram/auto-promotion, preserves historical report visibility, and verifies recovery on the 15-second retry. All five required CI checks passed on final head `4e4c8084ce427fdc8b57a319cd3af316ae799fa4`. Live worker/DB behavior remains unverified; no production DB or deployment changes.

- Merged in PR #338 (`02ada39`): added a fail-closed, test-only Backtest Studio to RetailBot2 identity-mapping contract. It is not connected to activation and does not define real mappings; auto-promotion remains rejected pending audited mappings and end-to-end validation.
- Merged in PR #339 (`01712d6`): malformed mapping modes and unavailable executable registries now fail closed, with regression coverage. The helper is still disconnected from activation; no actual mappings or automatic promotion are enabled. No deployment or production DB changes.

- Merged in PR #341 (`9893d72`): mapping resolution now uses a separate server-owned reviewed registry rather than trusting candidate-supplied mapping metadata. All five required CI checks passed on head `09db6a81cf17613001e8e69eb668fe0fdac720af`. Code review also confirms Backtest Studio auto-promotion writes to the main `paper_configs` engine, not RetailBot2 lifecycle state; the two registries and activation paths must remain distinct. No real mappings, RetailBot2 auto-promotion, production DB changes, or deployment were introduced.


### RetailBot2 strategy compatibility audit (2026-10-25)

- Reviewed RetailBot2 Bollinger-reversion and double-pattern entry logic against Backtest Studio's registered strategies. `confluence_reversal_v1` is not semantically equivalent to Bollinger reversion; `double_pattern_v1` requires confirmed pivots plus a neckline break, unlike RetailBot2's earlier extrema-based entry.
- No source-to-RetailBot2 mappings are approved. Do not guess mappings for `BB_REV_INV` / `DBL_PAT_INV` based on names. The mapping resolver is server-registry-based but not wired to activation; RetailBot2 auto-promotion remains fail-closed pending reviewed mappings and end-to-end lifecycle, audit/recovery, accounting, Telegram and stop/hide tests.
- PR #342 merged as `e17713a` after all five required CI checks passed. This was documentation-only; no production DB changes or deployment.



### RetailBot2 seven-strategy crosswalk (2026-10-25)

- Reviewed all seven RetailBot2 strategy identities (RSI_CTR, MA_WHIP, SR_TRAP, MACD_DIV, BB_REV, DBL_PAT, SPIKE_FADE) against the current Backtest Studio strategy registry.
- Result: **zero approved mappings**. Nearest candidates (such as EMA trend, supply/demand, or double-pattern strategies) have material trigger/data/entry-timing differences; they are investigation candidates only, not safe mappings. The detailed matrix and approval gate are in [UI-managed strategy lifecycle](UI_MANAGED_STRATEGY_PROMOTION.md).
- The server-owned mapping resolver remains disconnected from activation. RetailBot2 auto-promotion must remain fail-closed; no code activation, production DB change, or deployment was performed.


### RetailBot2 promotion mapping guard — PR #346 (2026-10-25)

- Merged [PR #346](https://github.com/kurnFR/signal-bot/pull/346) as [`2f7ead7`](https://github.com/kurnFR/signal-bot/commit/2f7ead76e170b8a1a6cb9e32e1b7fb6896bbc9d8). The five required PR-head checks passed before merge.
- Regression coverage now iterates over every Backtest Studio strategy registry entry and verifies that the empty reviewed mapping registry rejects it. This guards against accidental implicit mapping when new candidates are registered.
- No approved mappings were added. The RetailBot2 promotion API remains fail-closed; Backtest Studio's existing main-paper-engine auto-promotion is separate. Post-merge pytest, unit tests, Wallet Tracker MariaDB integration, and paper-accounting checks subsequently completed successfully on the documentation merge commit `cf74e872078a8e92a9f136f5dcbbdb13bc577643`. This is CI evidence only; no deployment or production DB changes.


### Post-merge CI status (2026-10-25)

- PR #346's merge commit passed both pytest runs, unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression.
- PR #347's documentation merge commit passed post-merge pytest, unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression.
- No runtime, production database, Telegram, or deployment verification is implied.


### RetailBot2 report visibility mode isolation — PR #349 (2026-10-25)

- Merged [PR #349](https://github.com/kurnFR/signal-bot/pull/349) as [`86a26a9`](https://github.com/kurnFR/signal-bot/commit/86a26a92970dd69cdc802b2bc9a7af7802491ef5).
- Tests now assert that both report query paths join lifecycle settings by exact strategy ID and mode. A hidden inverse identity must not suppress shadow results for the same base strategy.
- All five required PR-head checks and all four post-merge checks passed. CI does not establish live DB/worker behavior or deployment.


### RetailBot2 lifecycle API validation — PR #351 (2026-10-25)

- [PR #351](https://github.com/kurnFR/signal-bot/pull/351) merged as [`f999f4c`](https://github.com/kurnFR/signal-bot/commit/f999f4cbbdd4c1df59858de87d534121c4818b34). The five required pre-merge checks and four post-merge checks passed.
- Lifecycle API switches now use strict boolean validation. Numeric `0`/`1` and string values such as `"true"`/`"false"` are rejected with HTTP 422 rather than silently coerced into decisions. Valid JSON boolean clients are unchanged.
- PR #349 also strengthened tests for strategy-and-mode-specific report visibility; PR #350 documented that guard. No runtime settings, production DB, or deployment changed.


### RetailBot2 lifecycle readback hardening — PR #353 (2026-10-25)

- [PR #353](https://github.com/kurnFR/signal-bot/pull/353) merged as [`4424db9`](https://github.com/kurnFR/signal-bot/commit/4424db91def562d7156a5c2ca852f9b89167fb43) after all five required pre-merge checks passed.
- Lifecycle GET no longer uses Python `bool(value)` on persisted values; text `"0"`/`"false"` is correctly false and unknown values fail closed. The endpoint also never returns auto-promotion enabled while the feature is unavailable.
- Post-merge CI was pending at documentation time. No DB/runtime/deployment changes.


### Worker-side lifecycle fail-closed guard — PR #355 (2026-10-25)

- [PR #355](https://github.com/kurnFR/signal-bot/pull/355) merged as [`80b1c12`](https://github.com/kurnFR/signal-bot/commit/80b1c125c0ee80c4fcb9e58f15b11e404669a43b); all five required PR-head checks passed.
- RetailBot2 worker lifecycle reload now explicitly parses stored flags; text values `"0"` and `"false"` are false, unknown values fail closed, and auto-promotion is forced off in the worker cache until implemented.
- PR #353 and #354 post-merge checks passed. PR #355 post-merge CI subsequently completed successfully (all five checks). No DB/runtime/deployment changes.


### RetailBot2 lifecycle DB-boundary parsing — PR #357 (2026-10-25)

- [PR #357](https://github.com/kurnFR/signal-bot/pull/357) merged as [`fb8be81`](https://github.com/kurnFR/signal-bot/commit/fb8be81c9446cb4c16dd307db04075c1a6a82c3f).
- The DB helper now parses known boolean representations explicitly; textual `"0"`/`"false"` and malformed values fail closed instead of relying on Python truthiness.
- All five pre-merge checks and all four post-merge checks passed. Auto-promotion remains disabled. No production DB, runtime, Telegram, or deployment changes.
