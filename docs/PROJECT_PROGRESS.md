# Project Progress, Completion Status, and Roadmap

**Repository:** [`kurnFR/signal-bot`](https://github.com/kurnFR/signal-bot)  
**Status reviewed:** 2026-10-22  
**Master commit at review:** `acc7d17bc6872eb4f821245580fa86076aabf852`  
**Overall status:** Active / not finished. PRs #239/#241 improve paper-entry timing, #243 adds the ledger, #245 integrates engine fills/recovery, #247/#248 add order APIs and dashboard controls, #250 adds MariaDB concurrency/crash-recovery tests, #252 preserves alerts after recovered fills, #253 hardens promotion metrics, and #255 aligns ML listing with deployability. Manual accessibility checks, populated-database upgrade validation, cost-model realism, runtime-data hygiene, and trading-edge validation remain incomplete.

> This document separates verified completed work from known gaps and items that still need fresh verification. A green CI run proves the covered tests passed; it does not prove production deployment, profitability, or that every possible bug is absent.

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
| Database migrations, durable jobs, and runtime-file hygiene | **Partially complete** | Versioned migrations and durable job state are merged. `custom_strategies.json` and `Screen/signals.jsonl` are still tracked and must be untracked without deleting real runtime data. |
| Strategy and ML evidence of trading edge | **Not complete** | Use leakage-safe chronological evaluation and untouched holdout data; passing software tests is not proof of profitability. |
| GCAO project documentation and status | **Needs identification / blocked** | The accessible `signal-bot` repository and retrieved project context did not identify what “GCAO” stands for, its canonical repository, or its scope. Do not assume it is the same as Signal Bot or the separate `ai_insight` / `forecast_engine` work. Add verified details after its identity and source are confirmed. |
| Production deployment and live-environment verification | **Not done by this work** | No deployment was performed as part of the PR batch summarized here. Confirm the target environment and follow its release procedure separately. |

## 3. Recent merged work

All entries below are merged pull requests in `kurnFR/signal-bot`. The listed scope describes the identified fix, not a claim that the entire category is now perfect.

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

The next gate is not another UI feature: validate the migration against a representative populated database and perform manual keyboard/screen-reader/reflow/forced-colors checks. PR #250 now exercises simultaneous fill/cancel attempts, a simulated crash between position insert and order-state update, retry after rollback, and migration/idempotency behavior against disposable MariaDB. No deployment was performed.

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

### ML model-list consistency — PR #255

[PR #255](https://github.com/kurnFR/signal-bot/pull/255) fixes a UI/API consistency gap. The model list now reports a model as eligible only when the manifest flag is enabled, the shared test-metric gate passes, and the .joblib weights path is a regular file. Deployment rejects manifest IDs that do not match the requested ID and rejects a .joblib directory instead of a file.

All PR checks passed:
- [pytest](https://github.com/kurnFR/signal-bot/actions/runs/39744708891)
- [Project tests / MariaDB integration](https://github.com/kurnFR/signal-bot/actions/runs/39744708777)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/39744708869)

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
- [ ] **Remove generated runtime state from version control.** Review tracked `custom_strategies.json` and `Screen/signals.jsonl`, preserving any intentional sample/fixture data.
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

**Last update:** 2026-10-22.  
**Next suggested engineering step:** run a migration rehearsal against a representative populated database, then perform manual keyboard/screen-reader/mobile/forced-colors checks. Separately untrack runtime data only after preserving the real files on the machine that owns them.
