# Project Progress, Completion Status, and Roadmap

**Repository:** [`kurnFR/signal-bot`](https://github.com/kurnFR/signal-bot)  
**Status reviewed:** 2026-10-26  
**Master commit at review:** `30271138d4a75479ef415250d63853ba57d2d48e`  
**Overall status:** Active / not finished. PR #282 makes migration status inspection read-only, PR #283, #285, and #287 refresh this progress document, PR #284 makes migration dry-runs read-only, and PR #286 adds a safety-gated populated-database migration rehearsal runbook; PRs #239/#241 improve paper-entry timing, #243 adds the ledger, #245 integrates engine fills/recovery, #247/#248 add order APIs and dashboard controls, #250 adds MariaDB concurrency/crash-recovery tests, #252 preserves alerts after recovered fills, #253/#255 harden ML promotion/listing, #257 contains ML experiment paths, #259 validates deployment manifests before activation, and #263 contains model artifact paths; #265 hardens custom-strategy persistence, #266 validates runtime ML artifact paths, #267 rechecks ML eligibility immediately before model loading, and #269 makes model-weight/manifest writes atomic and prevents duplicate experiment runs from overwriting existing artifacts; #271 prevents the reusable model store from following external symlinks when reading weights or manifests. Manual accessibility checks, populated-database upgrade validation, cost-model realism, runtime-data hygiene, and trading-edge validation remain incomplete. Recent RetailBot2 lifecycle safety fixes are also merged: PR #351 enforces strict API booleans, PR #353 hardens lifecycle readback, PR #355 hardens worker flag parsing, and PR #357 parses persisted DB flags explicitly; PR #358 records the latest hardening and CI evidence. Auto-promotion remains fail-closed because the compatibility crosswalk found zero approved RetailBot2 mappings. PR #360 records the latest lifecycle/report audit, and PR #361 fixes missing paper-config IDs in the Backtest Studio promotion audit; PR #363 prevents reporting successful activation without a confirmed persisted config ID; PR #364 preserves activation and Telegram outcomes when final audit finalization fails; and PR #365 requires reconciliation when both config identity and the compensating audit write are unconfirmed; PR #367 fails closed when Telegram promotion alert delivery is not explicitly confirmed.

> This document separates verified completed work from known gaps and items that still need fresh verification. A green CI run proves the covered tests passed; it does not prove production deployment, profitability, or that every possible bug is absent.



### Verified RetailBot2 lifecycle and paper-strategy controls — PRs #312–#317 (2026-10-25)

- [PR #312](https://github.com/kurnFR/signal-bot/pull/312) makes restored legacy RetailBot2 trades fail closed for Telegram exit alerts when no explicit per-trade alert decision was persisted, and adds regression tests for legacy and explicitly enabled trades.
- [PR #313](https://github.com/kurnFR/signal-bot/pull/313) adds regression coverage for lifecycle semantics: disabling paper tracking prevents new background trades, unpromoted strategies may continue paper evaluation without Telegram alerts, and explicitly promoted strategies may send entry alerts.
- [PR #314](https://github.com/kurnFR/signal-bot/pull/314) adds a UI form to add/update any registered executable Strategy Library strategy for paper tracking, with separate controls for evaluation, Telegram alerts, and report visibility; Telegram defaults off.
- [PR #315](https://github.com/kurnFR/signal-bot/pull/315) validates strategy identity, market, and timeframe before paper deployment side effects.
- GitHub Actions for current `master` commit `0fa6fe4` were verified successful for `pytest`, `unit-tests`, `wallet-tracker-mariadb-integration`, and `paper-accounting`.
- Follow-up validation remains necessary: verify runtime DB state and worker behavior in the owner environment, confirm stopped/hidden strategy behavior across reloads and reports, and manually inspect the dashboard at mobile/desktop sizes. No live deployment was performed.

### RetailBot2 lifecycle and Backtest Studio boundary audit — 2026-10-25

A follow-up source review traced lifecycle settings through the dashboard API, persisted database rows, worker refresh, trade entry/exit, and reporting.

- **Worker refresh and restart:** the worker attempts an initial lifecycle read even shortly after OS boot, reloads settings periodically, and fails closed for evaluation, paper entries, Telegram, and auto-promotion when the lifecycle DB refresh is unavailable. A fresh worker reads the persisted settings again. Unit tests cover early startup, refresh failure/recovery, and restart behavior. These tests do not verify the owner environment's actual DB or worker process.
- **Mode identity:** inverse signals receive the `_INV` suffix and are controlled independently from their shadow counterparts.
- **Report visibility:** both the Telegram performance-stat query and dashboard per-strategy stats query filter by the exact strategy/mode pair using `report_enabled`. Overall shadow/inverse realized-P&L totals intentionally continue to include all closed trades, including hidden strategies, so hiding a row does not rewrite account-level accounting.
- **Backtest Studio boundary:** `web/routes/backtest.py` only activates candidates registered in the main Backtest Studio `STRATEGIES` registry and creates a main paper-engine configuration. It does not activate RetailBot2 strategies. The RetailBot2 compatibility resolver still has no production-approved mappings and no activation caller; do not connect it or enable auto-promotion until semantic compatibility and lifecycle integration are reviewed and tested.
- **Remaining release verification:** verify the saved settings against the owner's runtime database, check worker logs after a real process restart, manually confirm dashboard behavior at desktop/mobile sizes, and reconcile any stale `processing` promotion audit entries before retrying. No live DB, worker, Telegram, or deployment changes were performed during this audit.

## 1. Project purpose and boundaries

Signal Bot is a research and paper-trading platform for cryptocurrency strategies using Binance market data. It collects data, builds features, backtests strategies, runs selected strategies in a simulated paper engine, and offers optional ML signal filters, a news/macro AI overlay, wallet tracking, research bots, and a web dashboard.

**Important boundary:** it is not a live-trading system. The documented execution path simulates trades and does not place real orders.

Primary project references:

- [README](../README.md) — setup and dashboard overview
- [Architecture](ARCHITECTURE.md) — components, data flow, shared accounting invariants
- [Status / known gaps](STATUS.md) — feature inventory and risks identified in the earlier code review
- [Strategies](STRATEGIES.md) — strategy validation and holdout discipline
- [ML](ML.md) — model training and promotion
- [News AI](NEWS_AI.md) — news and macro overlay
- [Wallet Tracker](WALLET_TRACKER.md) — on-chain tracking and known limitations
- [Populated database migration rehearsal](POPULATED_DATABASE_MIGRATION_REHEARSAL.md) — safety-gated procedure for validating upgrades against an isolated populated-schema copy
- [Security](SECURITY.md) — authentication and roles

## 2. Completion dashboard

| Workstream | Status | Evidence / next action |
|---|---|---|
| Dashboard accessibility fixes from PRs #195–#233 | **Completed for this batch** | Merged to `master`; the final PR and post-merge CI checks passed for #233. Continue auditing rather than assuming all accessibility issues are gone. |
| Keyboard focus, skip-link focus, and focus styling | **Completed for identified issues** | PRs #231 and #232 merged; add browser/manual checks for keyboard navigation and forced-colors mode. |
| Sortable table announcements and header semantics | **Completed for identified issues** | PRs #191–#200 and #223–#224 merged; perform a screen-reader pass on dynamically rendered tables. |
| Accessible names, labels, dialog semantics, and button behavior | **Completed for identified issues** | PRs #195, #205–#221, #225–#227 merged; continue a whole-dashboard audit for remaining controls. |
| Mobile dashboard and theme-toggle usability | **Completed for identified issues** | PRs #187 and #193, #228 merged; verify on real mobile browsers and small viewports. |
| Database-status announcements and toast behavior | **Completed for identified issues** | PRs #222, #225, #229–#230 merged; broader live-region behavior still merits manual verification. |
| Theme-specific sortable-indicator contrast | **Completed** | PR #233 merged and CI passed. |
| Full project correctness and bug-free guarantee | **Not complete / cannot be guaranteed** | Continue risk-based review, regression tests, integration tests, and manual verification. |
| ML paper-promotion safety | **Implemented with strengthened validation** | PR #253 rejects non-finite/malformed metrics and fractional trade counts, validates drawdown bounds, and makes the registry recheck stored metrics. PR #255 aligns model-list eligibility with deploy requirements and checks manifest ID/regular weight files. Keep negative tests and artifact provenance under review. |
| Authentication and first-login hardening | **Implemented for identified controls** | First-login password change, stronger password policy, durable login throttling, and route authorization sweep are merged; continue periodic security review. |
| Paper execution lifecycle and portfolio-level risk | **Code implemented; release validation open** | PRs #245, #247, and #248 integrate durable pending orders, atomic fills/recovery, authenticated history/cancellation, and dashboard state/filter controls. PR #250 adds passing MariaDB tests for fill concurrency, cancel-vs-fill, and crash rollback/retry. Populated-database upgrades and manual browser/accessibility checks remain open. Keep the system paper-only. |
| Database migrations, durable jobs, and runtime-file hygiene | **Partially complete** | Versioned migrations and durable job state are merged. PR #282 makes status inspection read-only; PR #284 makes dry-run read-only. A safety-gated populated-database rehearsal runbook is available in [POPULATED_DATABASE_MIGRATION_REHEARSAL.md](POPULATED_DATABASE_MIGRATION_REHEARSAL.md), but the actual rehearsal remains **not performed**. `custom_strategies.json` and `Screen/signals.jsonl` remain tracked; ignore rules are prepared, but final untracking must be performed with a verified backup on the machine that owns the real data. |
| Strategy and ML evidence of trading edge | **Not complete** | Use leakage-safe chronological evaluation and untouched holdout data; passing software tests is not proof of profitability. |
| GCAO project documentation and status | **Needs identification / blocked** | The accessible `signal-bot` repository and retrieved project context did not identify what “GCAO” stands for, its canonical repository, or its scope. Do not assume it is the same as Signal Bot or the separate `ai_insight` / `forecast_engine` work. Add verified details after its identity and source are confirmed. |
| Production deployment and live-environment verification | **Not done by this work** | No deployment was performed as part of the PR batch summarized here. Confirm the target environment and follow its release procedure separately. |

## 3. Recent merged work

### Database migration and accounting hardening — PRs #274–#282

- [PR #274](https://github.com/kurnFR/signal-bot/pull/274) adds regression coverage that migration adoption preserves existing OHLCV rows.
- [PR #275](https://github.com/kurnFR/signal-bot/pull/275) rejects non-finite financial-accounting inputs and derived results.
- [PR #276](https://github.com/kurnFR/signal-bot/pull/276) documents migration and accounting hardening.
- [PR #277](https://github.com/kurnFR/signal-bot/pull/277) repairs partially applied WIB generated-column migrations by adding columns independently.
- [PR #278](https://github.com/kurnFR/signal-bot/pull/278) refreshes documentation for the WIB migration repair.
- [PR #279](https://github.com/kurnFR/signal-bot/pull/279) detects fresh installs by checking for existing application tables, including partial databases without `ohlcv`.
- [PR #280](https://github.com/kurnFR/signal-bot/pull/280) isolates opt-in MariaDB migration tests with unique per-run database names and cleanup limited to the database created by each test.

The latest migration tests improve regression coverage and reduce the risk of integration tests damaging a developer/shared database. PR #282 also removes the write/commit side effect from `python -m db.migrate --status`; its tests cover both missing and existing migration-tracking tables. The PR #282 pre-merge and post-merge workflow types all passed on merge commit `333dcce9529e815d8f3fb5fb8fd01540fa47f4ac`. These tests do **not** replace a rehearsal against a representative populated deployed-schema snapshot.

All entries below are merged pull requests in `kurnFR/signal-bot`. The listed scope describes the identified fix, not a claim that the entire category is now perfect.

### Read-only migration status — PR #282

- [PR #282](https://github.com/kurnFR/signal-bot/pull/282) makes `python -m db.migrate --status` inspection-only: it no longer creates `schema_migrations` or commits changes. Regression tests verify that both missing and existing tracking-table cases perform no writes or commits.
- Pre-merge and post-merge CI passed for all three workflow types on merge commit [`333dcce`](https://github.com/kurnFR/signal-bot/commit/333dcce9529e815d8f3fb5fb8fd01540fa47f4ac).
- No production database was modified and no deployment was performed.

### Migration dry-run safety — PR #284

- [PR #284](https://github.com/kurnFR/signal-bot/pull/284) prevents `python -m db.migrate --dry-run` from creating the target database or creating the `schema_migrations` tracking table as a side effect.
- Regression tests verify that dry-run does not create the tracking table, execute migration statements, or commit changes.
- All five observed checks passed on PR head `0120ec411139c57cbd05cb8540787a36db7a22bc`; merged as [`56c1093`](https://github.com/kurnFR/signal-bot/commit/56c1093a1c2f2619ded303c1f35dd02297f2c872). Post-merge checks were subsequently verified successful.
- This is a read-only guarantee for persistent schema/data effects; it does not replace a populated deployed-schema rehearsal. No production database was modified and no deployment was performed.

### Progress documentation refresh — PR #283

- [PR #283](https://github.com/kurnFR/signal-bot/pull/283) refreshed this roadmap through PR #280. This follow-up records the subsequent migration safety changes and updates the reviewed master commit.
- PR #283 merged as [`4fbe112`](https://github.com/kurnFR/signal-bot/commit/4fbe1127337d2e201f38fe4c73217e762c89cedf); its primary post-merge workflow types passed. A later duplicate pytest run was still in progress at the last review and is not treated as required release evidence.

### Dashboard accessibility and behavior — PRs #195–#233

- **#195** — Accessible names for workflow dialogs.
- **#196** — Pause dashboard polling while the page is hidden.
- **#197–#198** — Improve table-sort announcements with row counts and correct pluralization.
- **#199–#200, #223–#224** — Declare table-header column scope, including dynamically rendered headers.
- **#201–#202** — Align theme-toggle semantics and prevent repeated keyboard events from sorting tables.
- **#203–#211** — Add contextual accessible names to dashboard refresh, strategy, simulation, paper-market, and Telegram controls.
- **#212–#215** — Prevent cancel/helper buttons from submitting forms and remove nested dialog semantics.
- **#216** — Give dashboard buttons explicit types.
- **#217–#221** — Improve input and select labels across market search, backtesting, custom strategies, and workflow forms.
- **#222** — Contextual names for toast-dismiss controls.
- **#225** — Restore keyboard focus after manual toast dismissal.
- **#226–#227** — Accessible names for dynamic strategy-rule and strategy-library actions.
- **#228** — Preserve a 44px theme-toggle target on mobile.
- **#229–#230** — Announce database connection status accessibly without repeating unchanged state.
- **#231–#232** — Keep keyboard focus visible after using the skip link and ensure the main-content target's intended focus outline is not overridden by the global rule.
- **#233** — Improve light-theme sort-indicator contrast.

Relevant merged PRs:

- [PR #195](https://github.com/kurnFR/signal-bot/pull/195)
- [PR #196](https://github.com/kurnFR/signal-bot/pull/196)
- [PR #222](https://github.com/kurnFR/signal-bot/pull/222)
- [PR #228](https://github.com/kurnFR/signal-bot/pull/228)
- [PR #231](https://github.com/kurnFR/signal-bot/pull/231)
- [PR #232](https://github.com/kurnFR/signal-bot/pull/232)
- [PR #233](https://github.com/kurnFR/signal-bot/pull/233)

### Paper-execution timing review — PRs #239 and #241

The fresh audit found that the paper engine could only discover an entry after its intended candle had closed, yet record the historical open as the fill. That could make simulated results misleading.

- [PR #239](https://github.com/kurnFR/signal-bot/pull/239) — signal only on closed candles; use the immediately following in-progress candle for next-open execution; skip missing, stale, future-dated, and gapped candles.
- [PR #241](https://github.com/kurnFR/signal-bot/pull/241) — add `PAPER_ENTRY_MAX_DELAY_SECONDS` (default 120) so an engine that observes the candle late cannot silently backfill the opening price.

PR #243 adds the durable `paper_orders` schema and guarded transition service. PR #245 wires order creation, expiry/recovery, and atomic fills into the engine; PR #247 adds authenticated list/cancel routes; PR #248 adds the dashboard order ledger, status filter, and pending-only cancellation action. MariaDB crash/concurrency and manual accessibility validation remain open. See [PAPER_ORDER_LIFECYCLE.md](PAPER_ORDER_LIFECYCLE.md).

### Paper-order engine integration — PR #245

[PR #245](https://github.com/kurnFR/signal-bot/pull/245) merged on 2026-10-22. The engine now writes eligible signals to `paper_orders`, processes pending orders after restart, and atomically creates a paper position with the `FILLED` transition. It rechecks the configured entry window and shared emergency stop before fill, rejects blocked/duplicate position inserts, and uses persisted order values on idempotent retries. The ML-filter path also no longer relies on a branch-local pandas import.

All PR checks passed on head `6971d3d92490ff545c8b72d6ff6c9b1ba019c8a5`:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39741047779)
- [Project tests / MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39741047801)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39741047718)

Remaining: authenticated order list/cancel API, dashboard order history, and direct MariaDB validation of concurrent workers and crash windows. No production deployment was performed.

### Paper-order API and dashboard — PRs #247 and #248

- [PR #247](https://github.com/kurnFR/signal-bot/pull/247) adds `GET /api/paper/orders` with status filtering and `POST /api/paper/orders/{order_id}/cancel`. Cancellation is trader/admin-only, attributes the actor, and returns conflict for a non-pending order. The central authorization sweep covers these new routes.
- [PR #248](https://github.com/kurnFR/signal-bot/pull/248) adds a separate dashboard order ledger with status filter and cancellation action. Dynamic values are escaped, header scopes are explicit, and app.js cache-busting tests now tolerate version bumps.
- Latest PR-head checks passed: [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39742628179), [project tests/MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39742628146), and [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39742628249).
- The post-merge master checks for PR #247 also passed: [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39741807178), [project tests](https://github.com/kurnFR/signal-bot/actions/runs/39741807160), and [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39741807146).

The next gate is not another UI feature: validate the migration against a representative snapshot of the actual deployed schema and perform manual keyboard/screen-reader/reflow/forced-colors checks. An opt-in MariaDB integration test now covers legacy-style adoption and historical OHLCV preservation. PR #250 now exercises simultaneous fill/cancel attempts, a simulated crash between position insert and order-state update, retry after rollback, and migration/idempotency behavior against disposable MariaDB. No deployment was performed.

### MariaDB order-lifecycle integration — PR #250

[PR #250](https://github.com/kurnFR/signal-bot/pull/250) adds real InnoDB tests and wires them into the disposable MariaDB CI job. The latest PR checks passed:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39743379753)
- [Project tests including MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39743385846)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39743385996)

Coverage includes migration reapplication and idempotent signal creation, two concurrent fills yielding exactly one position, rollback/retry after an exception between position insert and order transition, and a race between cancellation and fill with one terminal winner. These tests use a dedicated test-position table and a disposable CI database; they do not prove a production deployment or replace populated-database upgrade and browser/accessibility validation.

### Follow-up reliability fixes — PRs #252 and #253

- [PR #252](https://github.com/kurnFR/signal-bot/pull/252) fixes a recovery-path alert gap: when a pending order fills after an engine restart, the same Telegram entry alert is now sent after the fill transaction commits. The alert uses the persisted order values and remains non-fatal if Telegram delivery fails.
- [PR #253](https://github.com/kurnFR/signal-bot/pull/253) closes an ML promotion-gate edge case: NaN was rejected, but infinity, fractional trade counts, negative drawdown, and malformed metric containers were not all rejected. The gate now checks finiteness and ranges, and the in-memory registry re-evaluates metrics instead of trusting only the eligibility flag.

PR #253 checks passed:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39744257095)
- [Project tests / MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39744257159)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39744257086)

### ML model artifact containment — PR #263

[PR #263](https://github.com/kurnFR/signal-bot/pull/263) closes the remaining symlink-containment gap in ML model handling. Model listing skips manifests that resolve outside `ml_models/`, external weight symlinks are not considered deployable, and deployment rejects both cases. All three PR checks passed; the post-merge master workflows are still running at the time of this review.

### ML experiment path and deployment-manifest hardening — PRs #257 and #259

- [PR #257](https://github.com/kurnFR/signal-bot/pull/257) validates experiment IDs and resolves the requested result path, rejecting symlinks that escape the ml_results directory. Its three PR workflows passed.
- [PR #259](https://github.com/kurnFR/signal-bot/pull/259) validates threshold, base strategy, symbol, market, timeframe, and strategy-parameter shape before changing paper configuration. This prevents malformed manifest metadata from causing an error after the activation side effect. Its pytest, project tests, and RetailBot accounting regression workflows passed.

These changes improve API correctness and filesystem safety. They do not prove model profitability or complete the remaining manual and release validation.

### ML model-list consistency — PR #255

[PR #255](https://github.com/kurnFR/signal-bot/pull/255) fixes a UI/API consistency gap. The model list now reports a model as eligible only when the manifest flag is enabled, the shared test-metric gate passes, and the .joblib weights path is a regular file. Deployment rejects manifest IDs that do not match the requested ID and rejects a .joblib directory instead of a file.

All PR checks passed:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39744708891)
- [Project tests / MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39744708777)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39744708869)

### ML experiment path containment — PR #257

[PR #257](https://github.com/kurnFR/signal-bot/pull/257) hardens `GET /api/ml/experiments/{experiment_id}`. IDs are restricted to filename-safe characters, `..` sequences are rejected, resolved paths must remain directly inside the results directory, and the target must be a regular file. Regression tests cover valid IDs, malformed IDs, and a symlink that points outside the results root.

All PR checks passed:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39745244099)
- [Project tests / MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39745244146)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39745244131)

### ML activation manifest validation — PR #259

[PR #259](https://github.com/kurnFR/signal-bot/pull/259) validates all manifest values used before the deploy-to-paper side effect. A threshold must be finite and strictly between 0 and 1; strategy parameters must be an object; the base strategy must be registered; and symbol, market, and timeframe must be supported. This prevents malformed editable JSON from activating a paper config and then failing while formatting the Telegram notification.

All PR checks passed:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39745729753)
- [Project tests / MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39745729829)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39745729700)

### Runtime persistence and ML safety — PRs #265–#267

- [PR #265](https://github.com/kurnFR/signal-bot/pull/265) prevents custom-strategy data loss by rejecting corrupt persisted JSON rather than treating it as an empty list, atomically replacing the file, and changing in-memory registries only after persistence succeeds. Regression tests cover malformed data, successful atomic writes, and simulated replace failure.
- [PR #266](https://github.com/kurnFR/signal-bot/pull/266) revalidates the persisted ML model ID and resolves manifest/weight paths inside `ml_models/` before runtime loading. It rejects traversal IDs and symlink escapes and requires both files to be regular files.
- [PR #267](https://github.com/kurnFR/signal-bot/pull/267) rechecks the manifest model ID, explicit `eligible_for_paper` flag, and untouched-test metrics at paper-runtime load time. This closes the gap where a previously deployed model's manifest could be edited while its paper config remained active.
- [PR #269](https://github.com/kurnFR/signal-bot/pull/269) makes model weights and manifests atomic writes, rejects existing symlink paths, and removes a duplicate-experiment fallback that could overwrite the stored artifact. All three PR-head workflows passed for #269.
- [PR #271](https://github.com/kurnFR/signal-bot/pull/271) closes the complementary read boundary: `ModelStore.load()` and `load_manifest()` now resolve paths and reject external symlink escapes before reading. Its pytest, project/MariaDB tests, and RetailBot accounting workflows passed.
- All three PR-head workflows passed for #266 and #267: pytest, project tests (including MariaDB integration), and RetailBot accounting regression. #265's recorded PR-head workflows also passed.
- These changes harden persistence and ML runtime checks; they do not authenticate artifact provenance against a writer who can edit both manifest and weights. Immutable artifact provenance / content-hash verification remains a possible future control. No deployment was performed.

### PR #233 final verification

PR #233, “Improve light-theme sort indicator contrast,” merged on 2026-10-21 as commit `c72896a501717e4b69492f292429499f496ad809`. Its final-head checks passed:

- [Test / pytest](https://github.com/kurnFR/signal-bot/actions/runs/39531202566)
- [Project tests](https://github.com/kurnFR/signal-bot/actions/runs/39531202669)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39531202675)

Post-merge `master` checks also passed:

- [Master Test / pytest](https://github.com/kurnFR/signal-bot/actions/runs/39531305752)
- [Master project tests](https://github.com/kurnFR/signal-bot/actions/runs/39531305725)
- [Master RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39531305784)

PR #237 added a fail-closed guard for an unreadable shared emergency-stop state and merged on 2026-10-22. Master CI is being checked separately; no production deployment is implied.

## 4. Existing product capabilities

The repository already documents these substantial components. They are **existing capabilities**, not tasks newly implemented by the recent accessibility PRs:

- Market-data collection and shared feature building.
- Backtesting, strategy variants, metrics, and holdout / walk-forward validation.
- Paper trading with shared accounting, Telegram alerts, and circuit breakers.
- ML signal filters and model artifacts.
- News and macro-calendar AI signals with guarded paper execution.
- Wallet tracking, candidate aggregation, monitored-wallet feed, and optional Telegram alerts.
- Experimental `retailbot2` and smart-money volume screener.
- FastAPI dashboard, authentication/roles, and operational process supervisor.

Use the linked domain documents above for implementation details. Recheck them against the current code before relying on dated verification claims.

## 5. Prioritized roadmap — unfinished work

The issues below were recorded in `docs/STATUS.md` during a review dated 2026-10-15. They are high-value follow-up candidates, **but each must be rechecked against current `master` before being treated as definitely still open**. When one is fixed, update that status document and add a regression test.

### P0 — Safety and correctness

- [x] **Enforce ML eligibility consistently.** Ensure experiment evaluation and the deploy-to-paper API use the same gate: at least 30 untouched test trades, profit factor ≥ 1.0, maximum drawdown ≤ 25%, and net PnL ≥ 0, as documented in [ML.md](ML.md). Add negative tests proving ineligible models cannot be deployed.
- [ ] **Close the paper-execution lifecycle gap.** Document and test pending, filled, expired, and cancelled signal/order states where appropriate. Keep the system explicitly paper-only unless a separate, reviewed scope authorizes anything else.
- [x] **Add portfolio-level exposure controls.** Existing per-position-count and daily-loss circuit breakers do not replace a notional exposure limit across symbols. Fail closed if the exposure state cannot be verified.
- [x] **Correct AI insight risk suggestions.** Ensure a negative or unproven edge cannot produce a positive Kelly-based risk recommendation; ensure regime detection uses features that actually exist, with tests for fallback behavior.
- [ ] **Improve cost and metric consistency.** Review flat fee/slippage assumptions, consider spread/liquidity effects where data supports them, and reconcile total-trade counts with the population used for win-rate calculations.

### P1 — Security and reliability

- [x] **Enforce first-login password change.** Check the `must_change_password` state in the API and UI, and test bypass attempts.
- [x] **Strengthen login rate limiting.** Consider shared/durable limits if the app runs across multiple workers or restarts.
- [x] **Make bind-address configuration explicit.** Review the default `0.0.0.0` listener and startup logging so exposure is intentional.
- [x] **Persist master-data job state.** Avoid losing job status on process restart; test restart/recovery behavior.
- [x] **Introduce a migration runner.** Replace manual migration ordering with a versioned, repeatable migration process; distinguish fresh installs from upgrades.
- [ ] **Remove generated runtime state from version control.** Ignore rules are prepared. These files remain tracked; before a separate removal commit is pulled to a data-bearing checkout, back up and verify both files, then untrack locally with `git rm --cached` and verify they remain on disk.
- [x] **Expand route authorization coverage.** Test authentication and role rules across all API routes, including read/write boundaries.

### P1 — Wallet Tracker limitations

Revisit the known limitations in [WALLET_TRACKER.md](WALLET_TRACKER.md), including alert digests, per-pool grouping, stablecoin metadata, bot filtering, skill/quality scoring, validation of the Birdeye fallback, and required startup database privileges. Prioritize based on user impact and testability rather than implementing every item at once.

### P2 — Accessibility and user experience

- [ ] Run a complete keyboard-only pass through every dashboard tab, dialog, table, notification, and dynamic workflow.
- [ ] Run manual screen-reader checks for live regions, sort announcements, dialog labels, focus restoration, and changing database status.
- [ ] Verify contrast, zoom/reflow, reduced-motion, forced-colors, and mobile touch targets in real browsers.
- [ ] Check that all dynamic controls retain correct accessible names after rerendering.
- [ ] Keep CSS cache-busting tokens and regression tests in sync for future static-asset changes.

### P2 — Engineering and release readiness

- [ ] Re-run the full test suite and opt-in MariaDB integration tests against a disposable database.
- [ ] Add focused tests for each resolved status gap; do not treat static source assertions as a substitute for behavior tests where browser-level tests are feasible.
- [ ] Review stale/abandoned Git branches and establish a cleanup policy after confirming they are not needed.
- [ ] Reconcile documentation with current implementation after each meaningful change.
- [ ] Perform a separate, explicitly approved staging/production verification when a deployment is requested. No deployment is implied by a merge.

## 6. Definition of “project finished”

The project should not be marked finished just because the latest PRs are merged. A reasonable completion gate is:

- [ ] All P0 safety/correctness issues are resolved or explicitly accepted with documented rationale.
- [ ] P1 security/reliability issues are resolved or have owners, mitigations, and target dates.
- [ ] Full unit tests and relevant integration tests pass on the final commit.
- [ ] Key user flows are verified in a browser, including accessibility and responsive behavior.
- [ ] Docs and migration/setup instructions match the code.
- [ ] Deployment and runtime health are verified separately when deployment is in scope.
- [ ] Remaining risks and intentionally unsupported behavior are documented.

## 7. Working rules for the next iterations

1. Start from current `master` and inspect the actual code before proposing a change.
2. Fix one concrete, reproducible issue per small PR where practical.
3. Add a regression test that fails before the fix and passes after it.
4. Run and inspect all required CI workflows on the final PR head.
5. Merge only after checks pass; verify the workflows on the resulting `master` commit.
6. Do not claim “bug-free,” “production verified,” or “profitable” based on a green test suite.
7. Do not deploy unless explicitly requested.
8. Update this roadmap and [STATUS.md](STATUS.md) when a task is completed, and keep the remaining checklist honest.

---

**Last update:** 2026-10-23.  
**Next suggested engineering step:** run a migration rehearsal against a representative populated database, then perform manual keyboard/screen-reader/mobile/forced-colors checks. Separately untrack runtime data only after preserving the real files on the machine that owns them.

### Migration rehearsal documentation and post-merge verification — PR #287

[PR #287](https://github.com/kurnFR/signal-bot/pull/287) updates this progress document to record the populated-database migration rehearsal runbook from PR #286, corrects the stale PR #284 verification wording, and explicitly preserves the status that the actual populated-schema rehearsal has **not been performed**.

PR #287 merged as [`c834837`](https://github.com/kurnFR/signal-bot/commit/c834837f4c190bf7719c8d9f6e203e3ec4424ac8). All four observed post-merge workflow types passed on the merge commit: pytest, unit tests, MariaDB integration, and paper accounting. No database was modified and no deployment was performed.

### Populated-database migration rehearsal runbook — PR #286

[PR #286](https://github.com/kurnFR/signal-bot/pull/286) adds [POPULATED_DATABASE_MIGRATION_REHEARSAL.md](POPULATED_DATABASE_MIGRATION_REHEARSAL.md), a safety-gated procedure for rehearsing schema upgrades against an isolated copy of a representative populated database. It specifies backup and checksum evidence, restore validation, schema and data baselines, read-only status/dry-run checks, post-migration comparisons, recovery/abort criteria, and sign-off evidence. The progress dashboard and this runbook explicitly state that the actual populated-schema rehearsal has **not been performed**; it requires an authorized representative snapshot and an isolated target.

All pre-merge checks passed on head `080ecf6ce13c7e52e0a2d64ee0b913e90c15059a`. PR #286 merged as [`2a06062`](https://github.com/kurnFR/signal-bot/commit/2a06062845051e07abcc80072fdabed5e9ec1092), and all four observed post-merge workflow types passed: pytest, unit tests, MariaDB integration, and paper accounting. No production database was changed and no deployment was performed.

### MariaDB migration-test isolation — PR #280

[PR #280](https://github.com/kurnFR/signal-bot/pull/280) replaces fixed MariaDB integration-test database names with unique per-run names. Setup no longer drops a database before creating it; cleanup drops only the unique database created for that test. This reduces the risk of destructive collisions when opt-in integration tests are run against a shared or developer-managed MariaDB server. CI passing does not replace a rehearsal against a representative deployed schema.


### Follow-up reliability and accessibility — PRs #317–#318 (2026-10-25)

- [PR #317](https://github.com/kurnFR/signal-bot/pull/317) makes RetailBot2 lifecycle refresh failures fail closed: strategy evaluation, new paper entries, Telegram alerts, and auto-promotion are disabled until persisted settings can be read again. Historical report visibility is preserved and refresh retries continue.
- [PR #318](https://github.com/kurnFR/signal-bot/pull/318) moves the forced-password dialog's Tab focus trap to a document-level key handler, so keyboard focus is contained even if focus escapes the dialog. The handler ignores hidden dialogs and regression tests check outside-focus recovery and the static asset cache token.
- PR #318 merged as [`293212b`](https://github.com/kurnFR/signal-bot/commit/293212b7f884f1567c99df1a2babc26c3465409a). All five observed PR-head check runs passed: pytest (including a duplicate run), unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression.
- No live deployment or production database change was performed.
- Remaining lifecycle work is not complete: auto-promotion is not generally enabled; winner eligibility and supported paper-config mapping still need a safe, explicit workflow. Verify that stopped strategies stay stopped across worker restart, hidden strategies remain out of reports, and the requested `BB_REV_INV_inverse` / `DBL_PAT_INV_inverse` settings match persisted user choices. See [UI-managed strategy lifecycle](UI_MANAGED_STRATEGY_PROMOTION.md).


### Strategy lifecycle stop/remove regression coverage — PR #320 (2026-10-25)

- [PR #320](https://github.com/kurnFR/signal-bot/pull/320) adds API coverage that saves a strategy with evaluation, paper trading, Telegram, and report visibility disabled, then confirms a fresh dashboard read returns those disabled settings.
- It also adds a worker reinitialization test that loads the same persisted-row fixture into a new RetailBot2 worker and verifies the stop/remove settings do not revert to defaults.
- All five observed PR-head check runs passed: pytest (including duplicate run), unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression. Merged as [`729c182`](https://github.com/kurnFR/signal-bot/commit/729c18227cc120653f3862543bc71bbd97e424d3).
- These deterministic tests validate the settings boundary but do not replace a real owner-environment DB/worker restart check. General auto-promotion and verification of the user's actual saved `BB_REV_INV_inverse` / `DBL_PAT_INV_inverse` settings remain open.


### Strategy lifecycle fail-closed API regression tests — PR #322 (2026-10-25)

- [PR #322](https://github.com/kurnFR/signal-bot/pull/322) verifies that the requested inverse identities `BB_REV_INV` and `DBL_PAT_INV` are registered and default to evaluation/paper/report enabled while Telegram and auto-promotion remain disabled.
- It also verifies that the RetailBot2 lifecycle API rejects an attempt to enable `auto_promote_enabled` and does not persist the request while the dedicated validated workflow is unavailable.
- All five observed PR-head checks passed: pytest (including the duplicate run), unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression. Merged as [`4bcdece`](https://github.com/kurnFR/signal-bot/commit/4bcdece6114eedd7af802cf5358a564ffa39235b).
- These tests verify registration/default/API guardrails only. They do **not** confirm the user's actual saved DB choices, Telegram delivery, or worker behavior in the owner environment. No live deployment or production DB access was performed.


### Lifecycle UI clarity and promotion guardrails — PRs #323–#324 (2026-10-25)

- [PR #323](https://github.com/kurnFR/signal-bot/pull/323) records the inverse-strategy registration/default tests and the fail-closed API guard from PR #322. It merged as [`2f9c0ed`](https://github.com/kurnFR/signal-bot/commit/2f9c0ed83bee6b97ddc129244491d9e35a8dd199); all five observed checks passed.
- [PR #324](https://github.com/kurnFR/signal-bot/pull/324) corrects the RetailBot2 lifecycle UI copy: automatic promotion is not available for this lifecycle workflow yet, and backtest results do not activate strategies automatically. A regression test protects the wording. It merged as [`9672134`](https://github.com/kurnFR/signal-bot/commit/96721349a26901f0b0a9659e3f7121a49fb7883e); all five observed PR-head checks passed.
- This is a UI/documentation clarity fix, not implementation of generalized RetailBot2 auto-promotion. The supported Battle Royale winner flow remains a separate feature and must not be presented as activating RetailBot2 strategies.
- Still open: implement a validated, opt-in promotion workflow with supported strategy-to-paper mappings, holdout eligibility gates, auditable decisions, and explicit user-controlled Telegram permission. Separately verify actual saved lifecycle settings and worker restart behavior in the owner environment. No production DB access or deployment was performed.


### One-click RetailBot2 Stop & Hide — PR #326 (2026-10-25)

- [PR #326](https://github.com/kurnFR/signal-bot/pull/326) adds a confirmed per-strategy/mode **Stop & Hide** action for executable lifecycle rows. It disables evaluation, new paper entries, Telegram alerts, and report visibility through the existing authenticated lifecycle API. Historical trades are retained; existing open positions are not force-closed and remain subject to normal exit management. Historical-only composite rows are excluded.
- A failed save triggers a research-data refresh so the UI can restore persisted settings instead of leaving a misleading local success state. The dashboard script cache token is bumped when the JavaScript changes.
- CI initially exposed a stale cache-token assertion in an existing dashboard test. The test was updated to validate the token format rather than pinning a specific version. On the final head, all five observed checks passed: both pytest runs, unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression.
- Merged as [`0d207ff`](https://github.com/kurnFR/signal-bot/commit/0d207ff2a1f68ed25da5319c120330eaf4ab7072). This is GitHub-only verification: it does not establish that the user's production database or worker has applied the settings.
- Still open: general RetailBot2 auto-promotion, actual owner-environment DB/worker restart verification, and confirming the user's saved lifecycle choices for `BB_REV_INV_inverse` / `DBL_PAT_INV_inverse`. No deployment or production DB mutation was performed.

- [PR #328](https://github.com/kurnFR/signal-bot/pull/328) adds regression coverage for the existing Backtest Studio promotion workflow's fail-closed boundaries: candidates outside the executable paper registry, unavailable policy storage, and unavailable audit-claim persistence must not activate a paper config. All five observed checks passed on head `a2ab29cfd38e0a36a18fa29475c1e6e400b3d765`; merged as [`1f8b2ed`](https://github.com/kurnFR/signal-bot/commit/1f8b2ed537672bb423aabd120ef6d49f330247b7). Tests only; no runtime settings, production DB, or deployment changed.
- Next: design and implement a separately validated, explicit mapping for RetailBot2 strategy/mode identities before allowing any generalized RetailBot2 auto-promotion. Do not reuse Backtest Studio's strategy registry blindly: the systems have different executable strategy identities and lifecycle controls. Keep the existing API rejection in place until mapping, holdout eligibility, audit/retry semantics, explicit user opt-in, Telegram separation, and stop/hide interactions are all covered.

- Follow-up reliability fix merged in [PR #330](https://github.com/kurnFR/signal-bot/pull/330), commit [`8fca11d`](https://github.com/kurnFR/signal-bot/commit/8fca11d1a8a8484a2199682dfdbfe34ba832d6db): identical Backtest Studio promotion decisions whose prior audit row is still `processing` return `in_progress` with an explicit reconciliation warning rather than `already_processed`. All five required CI checks passed on final head `f1c8b9ba298ed6458e665dfd524e196a92a03f09`. Automatic retries remain intentionally disabled because activation and Telegram delivery may have partially succeeded. No deployment or production DB changes.

- [PR #331](https://github.com/kurnFR/signal-bot/pull/331) merged as [`ac7be2d`](https://github.com/kurnFR/signal-bot/commit/ac7be2dea1ee1ee53e5dc84aae88a52f3414d37d): Research API now forces historical-only composite rows to remain non-executable after merging persisted settings. All five required checks passed on final head `7c23e12538191a299c98b061b826b3e43bf8ad64`. Report visibility remains user-controlled. No production DB changes or deployment.

- [PR #332](https://github.com/kurnFR/signal-bot/pull/332) merged as [`c9caa1c`](https://github.com/kurnFR/signal-bot/commit/c9caa1c08b4a965fbeb7d89aaeab76e23150a420): RetailBot2 worker now independently forces `COMPOSITE` and `COMPOSITE_INV` to remain non-executable even when legacy/manual lifecycle rows contain enabled flags, while preserving report visibility. All five required CI checks passed on final head `dca43623d0b8567282bfc56697e551634b1b155e`. This is test-backed source behavior; live worker/DB behavior remains unverified.

- [PR #333](https://github.com/kurnFR/signal-bot/pull/333) merged the documentation update for PR #332 as `0553bbe`; all five required CI checks passed.
- [PR #334](https://github.com/kurnFR/signal-bot/pull/334) merged as [`436ab38`](https://github.com/kurnFR/signal-bot/commit/436ab3874f7d9b319d3af5c429b3a6f6cdc2733a). It ensures the RetailBot2 worker always attempts its first lifecycle DB read, even within 15 seconds of OS boot. The regression test and all five required CI checks passed on final head `535b88a1bed586c2d06a7a890b8313dec994f359`. Live worker/DB behavior remains unverified; no deployment or production DB changes.


### RetailBot2 lifecycle refresh outage regression — PR #336 (merged 2026-10-25)

- [PR #336](https://github.com/kurnFR/signal-bot/pull/336) merged as [`f65abab`](https://github.com/kurnFR/signal-bot/commit/f65abab8a28de3167356f054ad8686a4e7d22f50) after all five required CI checks passed on final head `4e4c8084ce427fdc8b57a319cd3af316ae799fa4`.
- Regression coverage simulates a failed first lifecycle-settings DB read, verifies fail-closed behavior while settings are unknown, preserves historical report visibility, and verifies recovery after the 15-second retry interval.
- This is automated test coverage only. Live worker/DB behavior remains unverified; no deployment or production DB changes were performed.

### RetailBot2 promotion identity mapping — initial contract in progress

- [PR #338](https://github.com/kurnFR/signal-bot/pull/338) merged as [`02ada39`](https://github.com/kurnFR/signal-bot/commit/02ada39ed94910d4199eea9addd8ca3428847fe2) after all five required CI checks passed on final head `bac30fa38a5528162dedd878f787d0196edef988`.
- Added a pure, fail-closed resolver contract requiring explicit compatibility metadata, a registered executable strategy/mode, and a non-empty mapping version. It rejects missing/unapproved mappings, unknown identities, invalid modes, and historical-only composites.
- The helper is not wired into activation, defines no actual mappings, and cannot enable paper trading or Telegram by itself. RetailBot2 auto-promotion remains rejected pending a sourced/audited mapping registry and end-to-end safety gates.
- [PR #339](https://github.com/kurnFR/signal-bot/pull/339) merged as [`01712d6`](https://github.com/kurnFR/signal-bot/commit/01712d62d5985489c8b48b1aa0b56f0307a32448) after all five required pre-merge checks passed on head `f223112e72b8a364e347a409171d3ae6817c693d`.
- Added regression coverage ensuring malformed mode values and an unavailable/non-iterable executable registry are rejected without raising. The helper remains test-only, defines no real mappings, and is not connected to activation.
- Next: source and audit a real mapping registry before connecting it to any promotion path. Keep automatic promotion rejected until eligibility, lifecycle, audit/recovery, explicit user controls, and Telegram separation are tested end to end. No deployment or production DB changes.


### RetailBot2 mapping trust boundary — PR #341 (merged 2026-10-25)

- [PR #341](https://github.com/kurnFR/signal-bot/pull/341) merged as [`9893d72`](https://github.com/kurnFR/signal-bot/commit/9893d7261003992c29fb0befaf009cfeae07d8e1) after all five required CI checks passed on head `09db6a81cf17613001e8e69eb668fe0fdac720af`.
- The mapping resolver now receives a separate server-owned registry keyed by exact Backtest Studio source strategy ID; candidate-supplied mapping metadata cannot self-attest compatibility or override the target. Regression tests cover forged metadata, unknown source IDs, malformed registries, invalid targets/modes, and fail-closed behavior.
- Code-path review confirms Backtest Studio's existing `_auto_promote_top_candidate` promotes into the main `paper.engine` / `paper_configs` workflow, whose `STRATEGIES` registry is separate from RetailBot2's `StrategyID` and lifecycle registry. Do not reuse that workflow as if it activates RetailBot2.
- No production mapping records were added. The resolver remains disconnected from activation; generalized RetailBot2 auto-promotion stays rejected until a real mapping source is reviewed and the distinct lifecycle, user opt-in, audit/recovery, paper accounting, Telegram and stop/hide controls are integrated and tested end to end. No deployment or production DB changes.


### RetailBot2 strategy compatibility audit — reviewed 2026-10-25

- PR #342 merged as [`e17713a`](https://github.com/kurnFR/signal-bot/commit/e17713ac6c25c3e43d3b1f70d7479128170466d7) after all five required CI checks passed on head `66b15635daca059cb0a1f9d1b2b6295f17e22aa0`. It documents the trust boundary: Backtest Studio auto-promotion targets the main `paper_configs` engine, not RetailBot2 lifecycle state.
- Reviewed source implementations: RetailBot2 `strat_bband_revert` enters near/crossing Bollinger bands; Backtest Studio `confluence_reversal_v1` combines RSI oversold, rolling support, elevated volume, volatility and MACD-turn conditions. These are not equivalent strategies.
- RetailBot2 `strat_double_pattern` uses recent extrema and can enter after price moves away from an extreme; Backtest Studio `double_pattern_v1` waits for confirmed pivots and neckline-break confirmation. Entry semantics differ, so the names alone do not justify mapping them.
- **No valid source-to-RetailBot2 mappings are approved yet.** Do not map `BB_REV_INV` or `DBL_PAT_INV` to superficially similar Backtest Studio strategies. Review signal definitions, parameters, market/timeframe assumptions, risk/exit behavior and accounting before approving any mapping.
- The server-owned mapping resolver remains a fail-closed helper, disconnected from activation. Generalized RetailBot2 auto-promotion must remain rejected until a reviewed registry and end-to-end lifecycle integration exist, including opt-in policy, persistent lifecycle settings, idempotent audit/recovery, independent paper accounting, Telegram separation, and stop/hide/report behavior. CI does not verify owner-environment worker or DB state. No deployment or production DB changes.



### Seven-strategy compatibility crosswalk — documentation follow-up

The source review now covers every RetailBot2 strategy ID and records the nearest Backtest Studio candidate only as a future investigation lead. No mapping is approved: matching labels, broad trading intent, or inverse direction do not prove equivalent triggers, data requirements, risk exits or accounting. The mapping registry must remain empty for unsupported pairs; promotion must reject them visibly. See [UI-managed strategy lifecycle](UI_MANAGED_STRATEGY_PROMOTION.md) for the full matrix and the evidence required to approve a mapping. No runtime or production DB changes.


### RetailBot2 compatibility audit — PR #344 (2026-10-25)

- [PR #344](https://github.com/kurnFR/signal-bot/pull/344) merged the seven-strategy Backtest Studio-to-RetailBot2 source-compatibility crosswalk. All five required pre-merge checks and the observed post-merge pytest, unit, Wallet Tracker MariaDB integration, and paper-accounting checks passed.
- The audit approved **zero of seven mappings**. The server-owned mapping resolver remains disconnected from activation; do not create guessed mappings or enable RetailBot2 auto-promotion. Backtest Studio's existing winner flow targets the main paper engine and does not activate RetailBot2.
- No production DB changes or deployment were performed. CI does not establish owner-environment worker/database state or strategy profitability.


### Empty RetailBot2 mapping registry guard — PR #346 (2026-10-25)

- [PR #346](https://github.com/kurnFR/signal-bot/pull/346) merged as [`2f7ead7`](https://github.com/kurnFR/signal-bot/commit/2f7ead76e170b8a1a6cb9e32e1b7fb6896bbc9d8). All five required PR-head checks passed before merge: both pytest runs, unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression.
- Added a parameterized regression test for every currently registered Backtest Studio strategy, including inverse variants, asserting that an empty server-owned RetailBot2 mapping registry rejects every candidate.
- This prevents accidental name-based or implicit mapping as the Backtest Studio registry grows. It does not add mappings or activate paper trading/Telegram. RetailBot2 auto-promotion remains unavailable until exact mappings are reviewed and the separate lifecycle workflow is implemented.
- Post-merge verification subsequently completed successfully on merge commit `2f7ead76e170b8a1a6cb9e32e1b7fb6896bbc9d8`: both pytest runs, unit tests, paper-accounting regression, and Wallet Tracker MariaDB integration all passed. No live deployment or production DB changes were performed.


### Post-merge CI verification — mapping guard and documentation (2026-10-25)

- PR #346 merge commit `2f7ead76e170b8a1a6cb9e32e1b7fb6896bbc9d8`: both pytest runs, unit tests, paper-accounting regression, and Wallet Tracker MariaDB integration all passed.
- PR #347 documentation merge commit `cf74e872078a8e92a9f136f5dcbbdb13bc577643`: post-merge pytest, unit tests, paper-accounting regression, and Wallet Tracker MariaDB integration all passed.
- This confirms CI only; it does not verify the owner environment's worker, DB state, Telegram delivery, profitability, or deployment.


### RetailBot2 report visibility mode-isolation guard — PR #349 (2026-10-25)

- [PR #349](https://github.com/kurnFR/signal-bot/pull/349) merged as [`86a26a9`](https://github.com/kurnFR/signal-bot/commit/86a26a92970dd69cdc802b2bc9a7af7802491ef5).
- Added regression assertions to both RetailBot2 performance-report query tests requiring lifecycle settings to join on both strategy ID and mode. This prevents hiding one mode (for example, inverse) from accidentally hiding the same strategy's other mode (shadow).
- All five required PR-head checks passed; all four post-merge checks (pytest, unit tests, Wallet Tracker MariaDB integration, paper-accounting regression) also passed. This is CI evidence only; no live runtime or deployment verification.


### RetailBot2 report-mode isolation and strict lifecycle input validation — PRs #349–#351 (2026-10-25)

- [PR #349](https://github.com/kurnFR/signal-bot/pull/349) adds assertions to both report query tests that lifecycle settings join on both strategy ID and mode. All five PR-head and four post-merge checks passed.
- [PR #350](https://github.com/kurnFR/signal-bot/pull/350) records the report-mode guard and verified CI in project documentation. All five PR-head and five post-merge checks passed.
- [PR #351](https://github.com/kurnFR/signal-bot/pull/351) uses Pydantic `StrictBool` for the five RetailBot2 lifecycle flags and adds tests rejecting numeric/string boolean coercion with HTTP 422. All five PR-head checks and all four post-merge checks passed on merge commit [`f999f4c`](https://github.com/kurnFR/signal-bot/commit/f999f4cbbdd4c1df59858de87d534121c4818b34).
- These changes are test/API hardening only. RetailBot2 auto-promotion remains rejected; no live DB, worker settings, or deployment were changed.


### RetailBot2 lifecycle readback fail-closed fix — PR #353 (2026-10-25)

- [PR #353](https://github.com/kurnFR/signal-bot/pull/353) merged as [`4424db9`](https://github.com/kurnFR/signal-bot/commit/4424db91def562d7156a5c2ca852f9b89167fb43); all five required pre-merge checks passed.
- Fixed a truthiness hazard in lifecycle GET: legacy text `"0"` no longer becomes enabled merely because non-empty strings are truthy in Python. Recognized booleans and explicit `0`/`1` values are parsed; unknown values fail closed.
- GET now always reports `auto_promote_enabled: false` until the promotion workflow is implemented, even if an old persisted row contains a true value. A regression test covers legacy text flags and unavailable auto-promotion readback.
- Post-merge CI was still running when this note was written. No production DB, worker runtime, strategy activation, or deployment changes were made.


### RetailBot2 worker lifecycle readback hardening — PR #355 (2026-10-25)

- [PR #355](https://github.com/kurnFR/signal-bot/pull/355) merged as [`80b1c12`](https://github.com/kurnFR/signal-bot/commit/80b1c125c0ee80c4fcb9e58f15b11e404669a43b) after all five required PR-head checks passed.
- The worker now parses persisted lifecycle flags explicitly rather than using `bool(value)`, which misinterprets non-empty strings such as `"0"`. Unknown representations fail closed.
- The worker cache now forces `auto_promote_enabled` false while promotion is not implemented, even if a legacy row contains true. Regression coverage exercises the worker execution boundary.
- PR #353's four post-merge checks and PR #354's five post-merge checks passed. PR #355 post-merge checks subsequently completed successfully (all five checks). No deployment or production DB changes.


### RetailBot2 lifecycle DB-boundary parsing — PR #357 (2026-10-25)

- [PR #357](https://github.com/kurnFR/signal-bot/pull/357) merged as [`fb8be81`](https://github.com/kurnFR/signal-bot/commit/fb8be81c9446cb4c16dd307db04075c1a6a82c3f).
- The shared database helper now parses lifecycle flags explicitly instead of applying Python `bool(value)`; textual `"0"`/`"false"` and unknown legacy representations no longer become enabled by truthiness.
- A regression test exercises string false values and malformed values through the database helper itself.
- All five PR-head checks passed; all four post-merge checks passed. This is automated regression evidence only. No live DB, worker runtime, Telegram, or deployment changes.


### Backtest Studio promotion audit linkage — PR #361 (2026-10-25)

- [PR #361](https://github.com/kurnFR/signal-bot/pull/361) fixes a traceability gap: `set_paper_config()` performed its upsert but did not return the persisted `paper_configs.id`, so the promotion audit's `paper_config_id` was always null even on successful activation.
- The paper-config helper now reads the persisted row by its unique strategy configuration identity and returns its ID. Regression tests cover the lookup and verify the promotion audit receives the ID.
- All four observed post-merge checks passed: `pytest`, `unit-tests`, `paper-accounting`, and `wallet-tracker-mariadb-integration`. Merge commit: [`1936a76`](https://github.com/kurnFR/signal-bot/commit/1936a76749fecce3937a07779063d6ef2f7c5920).
- This improves audit traceability only. It does not enable RetailBot2 auto-promotion, add strategy mappings, or change production DB/worker/Telegram state. Owner-environment verification remains outstanding.


### Backtest Studio promotion failure-path hardening — PRs #363–#365 (2026-10-26)

- [PR #363](https://github.com/kurnFR/signal-bot/pull/363) prevents reporting promotion success or attempting Telegram delivery when the paper-config helper does not return a valid persisted configuration ID. The promotion audit is marked failed with reconciliation guidance.
- [PR #364](https://github.com/kurnFR/signal-bot/pull/364) handles a final audit-write failure after configuration activation and Telegram handling. It reports `promoted_audit_pending`, preserves the known Telegram outcome, and directs operators to reconcile rather than misreporting the activation as failed.
- [PR #365](https://github.com/kurnFR/signal-bot/pull/365) handles the compounded failure where the persisted config ID is unconfirmed and the audit update also throws. It returns `reconciliation_required`, states that the config and audit need reconciliation, and does not attempt Telegram delivery.
- All four required PR-head checks passed for #365 at head `cc05213c86c057761aa3655740e085b3a57740cd`: `pytest`, `unit-tests`, `paper-accounting`, and `wallet-tracker-mariadb-integration`. PR #365 merged as [`b762dca`](https://github.com/kurnFR/signal-bot/commit/b762dcae5f0ea48807616368a73a33c66a1da737). Post-merge workflows were queued at the time this note was prepared and are not recorded as passed.
- Scope is the **Backtest Studio main paper engine** only. This does not enable RetailBot2 auto-promotion, create RetailBot2 strategy mappings, or modify production DB, worker, Telegram, or deployment state.
- Remaining owner-environment checks: reconcile any stale `processing` decisions before retry; verify persisted config/audit rows and worker behavior in the intended runtime; and separately confirm that the UI accurately presents `promoted_audit_pending` and `reconciliation_required` statuses. These are not proven by CI alone.


### Telegram promotion delivery result validation — PR #367 (2026-10-26)

- [PR #367](https://github.com/kurnFR/signal-bot/pull/367) changes Backtest Studio promotion handling to count Telegram delivery as successful only when the notifier returns a dictionary with `success is True`. Unexpected values and missing/false success flags are recorded as failed alert delivery rather than a false-positive success.
- A regression test verifies that an unconfirmed notifier response produces `promoted_alert_failed`, preserves the paper activation, and persists `telegram_result="failed"`.
- All four required PR-head checks passed: `pytest`, `unit-tests`, `paper-accounting`, and `wallet-tracker-mariadb-integration`. Merged as [`3d6e29b`](https://github.com/kurnFR/signal-bot/commit/3d6e29bb175cd24249b94413367b2d06ff52cdbf). Post-merge workflows were queued or in progress at the time this note was prepared and are not recorded as passed.
- This is reporting hardening for the Backtest Studio main paper engine only. No live Telegram messages, production DB, worker, or deployment were used.


### Backtest Studio promotion status feedback — PR #369 (2026-10-26)

- [PR #369](https://github.com/kurnFR/signal-bot/pull/369) fixes the UI result-toast fallback that could display a generic successful Battle Royale message for an unrecognized promotion status, including statuses that require operator reconciliation.
- The UI now explicitly warns/errors for `promoted_audit_pending`, `reconciliation_required`, and `in_progress`; handles `disabled` and `no_candidate` separately; and warns on unknown future statuses rather than implying success.
- Regression test covers the known reconciliation states and asserts the unsafe generic-success fallback is absent. An overly broad initial test assertion failed; it was narrowed to target the exact unsafe fallback, after which all PR-head CI checks passed.
- Required PR-head checks passed at `0eafe93464c850cc0e468558d010af63a579ab2e`: `pytest`, `unit-tests`, `paper-accounting`, and `wallet-tracker-mariadb-integration`. PR #369 merged as [`df6ec3a`](https://github.com/kurnFR/signal-bot/commit/df6ec3a3d7325c0adf4b71f94e1bd30d5edf8da0). Post-merge workflows were queued when this note was prepared; they are not marked passed here.
- This is UI status messaging only. It does not reconcile DB records automatically or change production DB, worker, Telegram, or deployment state.


### Backtest Studio uncertain activation reconciliation — PR #371 (2026-10-26)

- [PR #371](https://github.com/kurnFR/signal-bot/pull/371) hardens the promotion path when `set_paper_config()` raises after a write may have started. Instead of returning an ordinary failure that could encourage an unsafe retry, the flow returns `reconciliation_required` and instructs operators to reconcile `paper_configs` and the promotion audit before retrying.
- The regression test verifies the uncertain activation status, persisted audit status, `telegram_result="unknown"`, and that Telegram is not attempted.
- All PR-head checks passed on `6f1e542d37f8c1f042b675f82e3c5eeb8c85bb9d`: `tests`, both `Test` runs, `RetailBot accounting regression`, and `wallet-tracker-mariadb-integration`. Merged as [`0abea95`](https://github.com/kurnFR/signal-bot/commit/0abea954cfdc13f6d3dd8426c66fed3d23a0f21f).
- Expected failure paths after a confirmed activation remain separately handled: Telegram exceptions become `promoted_alert_failed`, and final audit-write exceptions become `promoted_audit_pending` while preserving the known Telegram result. CI validates these paths; live runtime reconciliation remains an owner-environment task.
- Scope is Backtest Studio's main paper engine. No production DB, worker, live Telegram, or deployment changes were made.

### Backtest Studio unconfirmed paper-config identity — PR #373 (2026-10-26)

- [PR #373](https://github.com/kurnFR/signal-bot/pull/373) treats a paper-configuration activation result without a confirmed persisted config ID as `reconciliation_required`, even when the audit update itself succeeds. This avoids classifying an uncertain side effect as an ordinary failure and discourages an unsafe automatic retry.
- The regression test verifies the returned and persisted reconciliation status, the missing `paper_config_id`, `telegram_result="not_attempted"`, and that Telegram is not called.
- All observed PR-head checks passed on `17d9f3378b6dbb289752ef520793ef5a9d3303ba`: `tests`, both `Test` runs, and `RetailBot accounting regression`. The Wallet Tracker MariaDB integration workflow was not present in the observed PR-head runs, so it is not claimed as a PR-head check for this change.
- Merged as [`3027113`](https://github.com/kurnFR/signal-bot/commit/30271138d4a75479ef415250d63853ba57d2d48e). This is code/automated-test evidence only; any uncertain runtime activation still requires owner-environment reconciliation. No production DB, worker, live Telegram, or deployment changes were made.

### Backtest Studio promotion audit finalization — PR #375 (2026-10-26)

- [PR #375](https://github.com/kurnFR/signal-bot/pull/375) makes `update_strategy_promotion_decision` return whether exactly one audit row was updated. After paper activation, a zero-row finalization is treated as `promoted_audit_pending` rather than reporting a completed promotion without confirmation of the final audit state.
- Regression coverage verifies that a zero-row finalization preserves the known Telegram result, reports the pending audit/reconciliation state, and does not repeat or hide the alert side effect.
- Required observed PR-head checks passed on `1f6930615aef1d2217da4d9f0645f260a8c62b32`: `tests`, both `Test` runs, and `RetailBot accounting regression`.
- Merged as [`8ab73b9`](https://github.com/kurnFR/signal-bot/commit/8ab73b9dcf9800e9abd6a23718c2428ff20b55be). This is code and CI evidence only; no production database, worker, live Telegram, or deployment changes were made.

### Backtest Studio invalid persisted promotion policy guard — PR #377 (2026-10-26)

- [PR #377](https://github.com/kurnFR/signal-bot/pull/377) revalidates persisted auto-promotion thresholds before decision identity creation or audit claiming. Non-finite thresholds, out-of-range values, and invalid minimum-trade counts return an explicit policy error before paper-config activation or Telegram delivery.
- Test-first regression coverage checks non-finite and out-of-range thresholds and asserts that no audit claim, paper-config activation, or Telegram alert is attempted.
- All observed checks passed on exact PR head `69d9982b9159a76a897efdbf8f683b4b2852ff09`: `tests`, both `Test` runs, and `RetailBot accounting regression`. The `wallet-tracker-mariadb-integration` job within the `tests` workflow also completed successfully on that head.
- Merged as [`64c4ede`](https://github.com/kurnFR/signal-bot/commit/64c4ede147daa264989b03f34388c0741f394912). This validates the code path in CI; it does not mutate or verify production DB policy values, worker state, Telegram, or deployment.
