# UI/UX Migration Execution Log

**Updated:** 2026-10-28  
**Status:** In progress — React preview only; legacy dashboard remains the production default.  
**Deployment:** None. Do not mount the preview in FastAPI or change production serving without explicit owner approval.

This log records verified execution checkpoints and keeps the next actions actionable. It supplements, rather than replaces, the detailed phase plan and view inventory.

## Latest completed checkpoint

### PR #552 — preview mutation RBAC matrix

- **Status:** Merged.
- **Merge commit:** `45658d43b8d7da604ed6ab37b5e7f02b238550a8`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/552
- **Coverage added:** server-side role policy tests for screener controls, Master Data backfill/feature-build actions, Strategy Library mutations, and backtest runs. Tests also protect fail-closed behavior for unknown mutations and authenticated read access to Telegram status.
- **Validation:** pytest, unit tests, Wallet Tracker MariaDB integration, and RetailBot accounting regression all passed on exact PR head `dfc0e294c07049890e9019b4ce8b9345ae0288d5`.
- **Safety:** Calls only the pure authorization helper; no API mutations, DB writes, worker actions, live trades, Telegram sends, or deployment.

### PR #551 — mobile navigation close-focus regression

- **Status:** Merged.
- **Merge commit:** `ada5f5864a047685a375de66fa405243fdac5b7d`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/551
- **Coverage added:** browser regression verifies explicit close control resets `aria-expanded`, restores focus to the menu opener, and removes the mobile sidebar and scrim.
- **Validation:** frontend type-check, unit tests, production build, Chromium browser smoke, pytest, Wallet Tracker MariaDB integration, and RetailBot accounting regression all passed on exact PR head `cf05085d6c47bfdd4b11e151875aba38884a6acc`.
- **Safety:** Mocked browser API only; no production deployment.

### PR #549 — primary navigation accessibility semantics

- **Status:** Merged.
- **Merge commit:** `1aaa99b4835b9a4fc8e77a675928df74a043b9b4`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/549
- **Coverage added:** Playwright verifies that the primary navigation exposes `aria-current="page"` for the active view, updates it after navigation to Market Screener, and clears it from Overview.
- **Exact PR-head validation:** Frontend type-check, unit tests, production build, Chromium browser smoke, pytest, Screener unit tests, Wallet Tracker MariaDB integration, and RetailBot accounting regression all passed on `c72b6c85345d97346f2dfdabaed43becb1009864`.
- **Safety:** Browser test uses the mocked API harness. No production deployment or live mutation occurred.

### Recent merged regression slices

- **PR #543** — Settings diagnostics redaction/access controls, Telegram-test cancellation, and stale-session reset. Merge: `d68ff01ebc878e850ac6d56324eba0e764592797`.
- **PR #544** — migration execution log and acceptance gates.
- **PR #545** — backtest promotion API boundary tests. Merge: `7d574c2aaad50c1cbcbc88cd81c8e6f4321fdcd7`.
- **PR #546** — Master Data confirmation and viewer job-launch restrictions. Merge: `a4140b3`.
- **PR #547** — Market Screener explicit-save mutation boundary. Merge: `a48f5013c72425625e74bb8980affa2814ac5b8b`.
- **PR #548** — keyboard skip-link focus and activation. Merge: `c4dfc8477690e49e2e407f7513bfebf64c43a63a`.

## Current migration boundary

- React/TypeScript/Vite remains an isolated preview, not mounted by FastAPI.
- `web/static/` remains the production dashboard.
- Read-only preview slices and safe Settings are not proof of full route, role, mutation, responsive, or accessibility parity.
- CI passing is not a substitute for owner acceptance or real-device/runtime verification.

## Next execution queue

1. **Reconcile inventory against runtime routes and roles.** Compare each row in `docs/UI_UX_VIEW_INVENTORY.md` with effective FastAPI routes, HTTP methods, auth/RBAC, polling, error/empty states, and side effects. Record discrepancies and evidence; do not rely on a static decorator count alone.
2. **Close the browser-flow gaps.** Expand Playwright beyond smoke coverage to verify migrated-view navigation and role-gated requests, loading/error/empty behavior, refresh/session recovery, and narrow viewport behavior. Keep all mutation endpoints mocked unless a separately reviewed test explicitly requires otherwise.
3. **Accessibility acceptance.** Track keyboard/focus, screen-reader semantics, zoom/reflow, contrast, reduced motion, and forced-colors checks for each migrated view and both themes.
4. **Feature parity review.** Review Market Screener V2, Master Data, and Settings view-by-view; keep all unverified controls clearly non-functional or absent rather than implying they work.
5. **Release gate.** Only after the above evidence is complete, document residual risks and request explicit owner acceptance. Production cutover/deployment remains a separate decision.

## Merge policy

- Add or update regression coverage before behavior changes.
- Use one independently reviewable slice per PR.
- Merge only when every required check is green on the exact PR head.
- Never deploy, switch FastAPI routes, change production data, restart workers, execute trades, or send live Telegram messages as part of frontend migration without explicit approval.

### PR #554 — viewer Strategy Library mutation boundary

- **Status:** Merged.
- **Merge commit:** `a9fd2c3e42edeb0bb10e722c22ecc2fc6fbbbafc`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/554
- **Coverage added:** Playwright renders a mocked saved strategy for a viewer and verifies that create, quick-test, edit, delete, and AI-draft controls are absent. It also records custom-strategy requests and asserts that opening the view causes no non-GET request.
- **Exact PR-head validation:** Frontend foundation workflow, pytest, unit tests, and RetailBot accounting regression all passed on `e0e055d6a5f8c9bfd2a054b1be4accb6a86f8ac1`.
- **Safety:** All API responses are mocked. No saved strategy files, database, workers, live trades, Telegram sends, or production deployment were touched.

### PR #555 — viewer Strategy Library checkpoint documentation

- **Status:** Merged.
- **Merge commit:** `cd6714c3d5850c2121c66f910c65a45e6900233f`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/555
- **Change:** Records PR #554's mocked viewer browser test and exact-head validation.
- **Validation:** All five reported checks on documentation PR head `e6177fbbb12acd5aee361eafa698189e0991e025` completed successfully: unit tests, Wallet Tracker MariaDB integration, RetailBot accounting regression, and two pytest workflow runs.
- **Safety:** Documentation only; no deployment or live side effects.




### PR #559 — Backtest Studio API recovery and mobile scrim correction

- **Status:** Merged.
- **Merge commit:** `73e5be44995c1dd9d74f320c26cd0373c4fca51b`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/559
- **Coverage/fix:** Mocked Backtest Studio history failure → Retry recovery; adjusted the mobile scrim to leave the 252px navigation sidebar interactive.
- **Exact-head validation:** Frontend type-check, unit tests, production build, Chromium browser smoke, pytest, unit tests, paper-accounting, and Wallet Tracker MariaDB integration passed on `679eb16a6bab4e651f2656ae3be04921553f7fab`.
- **Safety:** Backtest recovery uses GET-only mocked API responses; no real simulation or production action was triggered.

### PR #560 — Master Data empty and recovery states

- **Status:** Merged.
- **Merge commit:** `5133958824c020de40872f461fa6387b617a874c`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/560
- **Coverage:** Successful empty coverage/jobs/markets responses render explicit empty states without false API errors; temporary coverage failure recovers after Retry; requests remain GET-only.
- **Exact-head validation:** Frontend type-check, unit tests, production build, Chromium browser smoke, pytest, unit tests, paper-accounting, and Wallet Tracker MariaDB integration passed on `70aec5df24ace2039395b84c1b2afd47c2344cd5`.
- **Safety:** Mocked responses only; no backfill or feature-build job, database write, worker action, or production deployment.
