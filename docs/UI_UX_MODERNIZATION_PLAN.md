# Signal Bot UI/UX Modernization Plan

**Status as of 2026-10-27: the isolated React/TypeScript preview migration is actively underway; it is not production-mounted and is not a replacement for the legacy dashboard.** The frontend lockfile is now committed; CI is being switched to reproducible `npm ci` installs.

This plan separates the improvements already present in the current dashboard from the future frontend architecture. It is intentionally incremental: preserve the Python/FastAPI backend, existing APIs, database schema, worker behavior, strategy/risk controls, and Telegram gates unless a separately reviewed change requires otherwise. No deployment is implied by a merge.

## 1. Current implementation (verified from `master`)

The current dashboard is served from `web/static/index.html` with a large vanilla JavaScript application in `web/static/js/app.js`, CSS files under `web/static/css/`, and external CDN assets. The page currently loads Tailwind CSS through its CDN, Lucide icons, TradingView Lightweight Charts, and Chart.js. Theme enhancement logic is in `dashboard_enhancements.js` and `dashboard_enhancements.css`.

The repository already has a light/dark theme toggle and a substantial set of targeted accessibility and responsive fixes. The dashboard also contains a Market Screener V2 view. These are real improvements, but they do not constitute a migration to the recommended component-based frontend stack.

### Status summary

| Area | Status | Evidence / remaining work |
|---|---|---|
| Existing dashboard operation and backend | **Keep / preserve** | FastAPI, Python workers, APIs, database and paper-trading controls remain the source of truth. |
| Targeted accessibility improvements | **Implemented for identified issues** | PRs #195–#233 and follow-ups are merged. Full keyboard, screen-reader, zoom/reflow, forced-colors and browser checks remain open. |
| Responsive table and mobile polish | **Implemented for identified issues** | Mobile viewport-aware scrolling, sticky headers and sorting improvements are present. Real-device verification remains open. |
| Light/dark theme | **Present; full design-system consistency not verified** | Existing theme toggle and enhancement CSS exist. Audit every dashboard view, modal, chart, table, and dynamically inserted element for parity. |
| Market Screener V2 | **Existing view; modernization completion unverified** | View and API integration exist. Complete product-level UX, empty/error/loading states, accessibility and mobile acceptance review remains open. |
| React + TypeScript preview migration | **In progress; preview-only** | Vite/React/TypeScript foundation and read-only views are merged; frontend remains isolated from FastAPI static serving. Full parity, browser verification, and rollout acceptance remain open. |
| Browser-level visual and interaction regression suite | **Incomplete** | Unit and static frontend checks exist; end-to-end browser checks and visual regression coverage are still needed. |
| Runtime/manual verification | **Not done in this work** | No browser session against the owner's deployed environment was performed and no deployment was made. |

## 2. Recommended target stack

Use the following stack for new frontend work and for the eventual incremental migration:

- **React + TypeScript** for explicit component boundaries and safer UI state.
- **Tailwind CSS** as the styling foundation, built through a pinned local build pipeline rather than relying on the CDN for the migrated application.
- **shadcn/ui** components, generated and owned by the repository, with accessible Radix-based primitives where appropriate.
- **lucide-react** for consistent icon components.
- **TradingView Lightweight Charts** for candlesticks and market-price charts; retain it where it meets the use case.
- **Recharts** for ordinary analytics and portfolio/performance charts where suitable.
- **TanStack Table** for data-heavy tables and sorting/filtering/pagination.
- **TanStack Query** for API request caching, refetching, loading/error states and invalidation.
- **React Hook Form + Zod** for validated settings and configuration forms.
- **Sonner** for consistent non-blocking notifications.
- **Vitest + Testing Library** for component/unit tests and **Playwright** for browser-level flows.
- **class-variance-authority**, **clsx**, **tailwind-merge**, and **tw-animate-css** as small supporting utilities.
- Do not add a global state library by default. Introduce one only if real cross-page state complexity justifies it.

This is the target recommendation, not a claim that these dependencies have already been installed.

## 3. Target application structure

Organize the frontend around a stable dashboard shell and domain-oriented feature modules:

- **Dashboard shell:** sidebar/navigation, responsive header, theme settings, global notifications, connection/worker status and accessible main-content region.
- **Home / Overview:** system health and concise market/paper-account summaries.
- **Market Screener:** smart-money/universe health, filters, signal quality, status and alert controls.
- **Market Detail:** selected symbol, market chart, timeframe, signal/event context and related strategy evidence.
- **Trading / Wallets:** paper positions, trade history and wallet-tracking activity, with clear separation between simulated trades and on-chain observations.
- **Backtest Studio / Strategy Library:** backtest runs, comparison/equity charts, configuration, validation and evidence.
- **Research Bots / RetailBot2:** lifecycle controls, per-strategy/mode paper accounting, Telegram eligibility and report visibility.
- **Settings / Users:** permissions, preferences and administrative controls.
- **API service layer:** typed request/response contracts; components should not directly duplicate fetch/error/auth logic.

All trading decisions, risk limits, paper execution, lifecycle persistence, Telegram gates and accounting remain server-side. Frontend state must not become an alternate source of truth for execution permissions.

## 4. Migration phases and gates

### Phase 0 — Baseline and inventory
**Status: partially complete; keep open.**

- [x] Record current dashboard implementation and distinguish existing fixes from a full framework migration.
- [x] Keep a project progress checklist and test-first merge policy.
- [ ] Inventory all dashboard routes/views, API calls, forms, charts, tables, dialogs and dynamic render paths.
- [ ] Capture baseline screenshots and key user-flow behavior at desktop, tablet and mobile widths.
- [ ] Record current performance, accessibility and bundle/loading baseline.
- [ ] Identify CDN dependencies and a safe build/deployment strategy before introducing a new toolchain.

### Phase 1 — Design system and build foundation
**Status: foundation merged; reproducible install hardening is complete and full design-system adoption remains in progress.**

- [x] Add an isolated React + TypeScript + Vite package/build setup and frontend CI; keep the existing dashboard entry point as the default.
- [x] Generate and commit `frontend/package-lock.json` through PR #456, based on the registry-resolved install.
- [x] Verify `npm ci` in CI on the exact PR head and merge the workflow change that uses it (PR #457).
- [ ] Define design tokens for dark/light surfaces, text, borders, semantic status, focus, spacing, radius and typography.
- [ ] Add the shared app shell, navigation, buttons, inputs, dialogs, tables, badges, skeletons, empty/error states and toast conventions.
- [ ] Verify keyboard navigation, focus management, reduced motion, forced colors and contrast in both themes.
- [ ] Add Playwright smoke coverage before migrating additional features; component tests alone are not browser acceptance.

**Gate:** existing dashboard remains available and all required backend/CI tests pass.

### Phase 2 — Migrate one low-risk vertical slice
**Status: not started.**

- [ ] Choose one read-heavy dashboard view as a pilot.
- [ ] Connect it through the typed API service layer and TanStack Query.
- [ ] Match existing permissions, refresh semantics, loading/error/empty states and responsive behavior.
- [ ] Add component and Playwright regression tests; compare against the baseline.
- [ ] Use a controlled route/feature boundary so the existing UI can remain available during validation.

**Gate:** no change to backend contracts or trading behavior; pilot passes accessibility, browser and CI checks.

### Phase 3 — Screener and data-heavy views
**Status: not started.**

- [ ] Migrate Market Screener V2 and its filters, signal table, status, alert controls and responsive table behavior.
- [ ] Adopt TanStack Table only where it improves sorting, filtering, row rendering or accessibility over the current implementation.
- [ ] Keep charts on Lightweight Charts/Recharts as appropriate; avoid rewriting chart logic without evidence.
- [ ] Verify data freshness, loading/errors, empty results, large datasets and mobile interactions.

**Gate:** compare API calls and displayed results against the existing implementation; preserve screener alerts and worker controls.

### Phase 4 — Strategy, paper-trading and settings views
**Status: not started.**

- [ ] Migrate Backtest Studio, strategy settings, RetailBot2 lifecycle/report panels and paper-trade views in separately testable slices.
- [ ] Add Zod validation and React Hook Form where forms benefit from explicit schemas.
- [ ] Verify disabled/stopped strategies stay disabled; only explicitly enabled strategy/mode combinations can alert.
- [ ] Verify hidden report rows do not change ledger/equity accounting and that the UI clearly distinguishes visibility from execution.
- [ ] Test permission errors, save/readback failures, duplicate actions, refresh and restart-related API state.

**Gate:** existing backend safety tests and accounting regression checks remain green; no frontend-only bypass of server authorization.

### Phase 5 — Consolidation and release readiness
**Status: not started.**

- [ ] Complete all remaining views and remove duplicated legacy UI only after route parity and acceptance checks.
- [ ] Run Playwright critical-flow, accessibility, responsive and visual-regression checks.
- [ ] Test current supported browsers and mobile viewport sizes.
- [ ] Measure bundle size and loading behavior; pin dependencies and document reproducible build steps.
- [ ] Update architecture, setup, security and project progress docs.
- [ ] Perform staging/runtime verification only with explicit approval; production deployment remains a separate decision.

**Gate:** all required CI green, no unresolved P0/P1 UI regressions, documented residual risks, and explicit approval for any deployment.

## 5. Acceptance criteria for calling the migration complete

The migration is complete only when all of the following are true:

- Every required dashboard view is running in the approved component architecture, or any intentionally retained legacy view is explicitly documented and accepted.
- Light and dark themes are consistent across pages, charts, tables, dialogs, dynamic states and mobile layouts.
- Keyboard, screen-reader, contrast, zoom/reflow, reduced-motion and forced-colors checks are completed.
- Core user flows are covered by browser-level tests, including authentication/permissions, screener filters, strategy settings, paper-trade reports and error recovery.
- API contracts, accounting, worker lifecycle, risk controls and Telegram gating remain server-authoritative and unchanged unless separately reviewed.
- The local build is reproducible with the committed npm lockfile and `npm ci`; CDN dependency and supply-chain decisions are documented.
- CI is green on the exact final head, documentation is updated, and runtime/deployment verification is separately recorded.

## 6. Working constraints

1. Do not attempt a big-bang rewrite of the existing dashboard.
2. Make one independently reviewable slice per PR and add regression tests before implementation.
3. Preserve existing functionality and API behavior; separate necessary backend changes into their own reviewed scope.
4. Merge only after all required checks pass on the exact PR head.
5. Never deploy or switch production serving without explicit approval.
6. Record incomplete checks honestly; a successful unit build does not prove route parity or runtime safety.


## 7. Execution update — 2026-10-27

This execution record supplements the phase checklist and preserves the previously merged preview-slice history. It does **not** imply that the frontend migration is complete or authorize production rollout.

### Merged preview slices

| PR | Preview capability | Boundary |
|---|---|---|
| [#417](https://github.com/kurnFR/signal-bot/pull/417) | React/TypeScript/Vite foundation and responsive shell | Isolated build; not mounted by FastAPI |
| [#421](https://github.com/kurnFR/signal-bot/pull/421) | Market Screener V2 read view | No screener control mutations |
| [#423](https://github.com/kurnFR/signal-bot/pull/423) | Existing authentication gate | Browser-level parity and role-aware control audit remain open |
| [#427](https://github.com/kurnFR/signal-bot/pull/427) | Paper Trading ledger/risk monitor | Read-only; no deploy/cancel/config actions |
| [#430](https://github.com/kurnFR/signal-bot/pull/430) | Wallet Tracking | Read-only; no wallet/worker mutations |
| [#431](https://github.com/kurnFR/signal-bot/pull/431) | Backtest Studio saved results | No backtest execution or promotion |
| [#432](https://github.com/kurnFR/signal-bot/pull/432) | ML Studio saved experiments and model eligibility | No ML runs or model deployment |
| [#433](https://github.com/kurnFR/signal-bot/pull/433) | Research positions, closed trades, lifecycle readback | No lifecycle, paper, or Telegram mutations |
| [#434](https://github.com/kurnFR/signal-bot/pull/434) | News AI sentiment and event feed | No polling, inference, strategy execution, or alerts |
| [#435](https://github.com/kurnFR/signal-bot/pull/435) | Master Data coverage and job history | No backfills, feature builds, or worker launches |
| [#436](https://github.com/kurnFR/signal-bot/pull/436) | Safe Settings preview | Local theme preference and basic session identity only; no admin settings |

### Verified CI and remaining gates

- PR #456 committed `frontend/package-lock.json`; Frontend foundation, pytest, general tests/MariaDB integration, and RetailBot accounting regression passed on its exact PR head before merge.
- PR #457 changed CI to `npm ci`, used the lockfile for cache keys, removed temporary bootstrap automation, and updated the migration docs. All required checks passed on its exact head and the first `master` run passed.
- Repository CI is not evidence of browser visual parity, production deployment, real-user authorization behavior in the owner's environment, or live data correctness.
- PR #473 handles a session expiring after initial sign-in: protected API GET responses with HTTP 401 clear the stale token and return the authenticated shell to the sign-in screen. Transport/auth-gate regression tests and the frontend typecheck, unit tests, and build passed on its exact head. This is component-level coverage, not a replacement for the outstanding Playwright browser-flow gate.
- **Mobile navigation accessibility follow-ups merged:** #441 adds Escape dismissal; #443 adds `aria-expanded`/`aria-controls`; #444 manages focus entry and restoration. Their frontend tests/build and backend regression workflows passed on each exact PR head.
- [ ] Reconcile every route and API call in `docs/UI_UX_VIEW_INVENTORY.md` against current `master`, including roles, forced-password behavior, side effects, polling, and empty/error states.
- [ ] Add Playwright smoke coverage for sign-in, forced password change, navigation, theme persistence, session expiry, and narrow viewport behavior.
- [ ] Run keyboard/focus, screen-reader, zoom/reflow, reduced-motion, contrast, and forced-colors checks across every migrated view.
- [ ] Complete missing read-only Master Data/Settings parity and identify any remaining unimplemented views before exposing the preview to users.
- [ ] Decide and test a controlled route/feature-flag rollout only after parity acceptance; do not switch FastAPI's default dashboard until explicit owner acceptance.
- [ ] Keep mutation work (strategy promotion, Telegram eligibility, paper controls, backfills, model deployment, wallet/worker control) in separate API-backed PRs with role and side-effect tests.

### Current safe operating rule

The React preview is still **not mounted by FastAPI**. Do not claim a complete frontend migration. Do not remove or redirect the legacy dashboard until every view's parity, permissions, side effects, responsive behavior, accessibility, and regression tests are accepted. No live deployment, production DB, worker, trading, or Telegram changes were made by these UI preview slices.


## 8. API error-redaction audit — 2026-10-27

The follow-up API audit found several failure paths that returned raw exception text to authenticated clients or logged exception strings that could contain token-bearing URLs, database metadata, or internal implementation details. The following independently reviewed fixes were merged only after their required workflows passed on the exact PR head:

| PR | Scope | Merged commit |
|---|---|---|
| [#467](https://github.com/kurnFR/signal-bot/pull/467) | Telegram notifier: redact request exceptions from send results, connectivity checks, and logs | \`a4b3a2c2905572be1e187f2fe3155369ba33adab\` |
| [#468](https://github.com/kurnFR/signal-bot/pull/468) | Paper-trading sync and deployment response errors | \`c1e008d7afa3ff9f3d8fb78e4baa30f0e9698b48\` |
| [#469](https://github.com/kurnFR/signal-bot/pull/469) | ML experiment and model-deployment error responses | \`aee8b1beff6ea2673a4cf80a5ca8b132bffbd86e\` |
| [#470](https://github.com/kurnFR/signal-bot/pull/470) | Backtest policy, audit, and per-strategy simulation errors | \`90e4064d3ef9330ba734a75925cdfa0410790b0f\` |
| [#471](https://github.com/kurnFR/signal-bot/pull/471) | Research and News AI route errors | \`a26a4ea0ddcfdda75bd836530559aafb568cfc18\` |

Regression tests inject synthetic secret-bearing exception strings and assert that API responses/log messages do not return those strings. Unexpected failures now use stable generic client messages; server diagnostics record exception types for these reviewed paths. This does not mean every exception/logging path in the repository has been audited.

### Remaining security and migration work

- [ ] Continue auditing API routes and background services for raw exception details, credential leakage, unsafe logging, authorization mismatches, and unintended side effects.
- [ ] Review all authenticated role/method combinations against route inventory, especially endpoints added after the centralized fail-closed authorization boundary.
- [ ] Run the documented Playwright route/critical-flow checks and accessibility acceptance suite.
- [ ] Complete view/API parity and document remaining limitations before any production route switch.
- [ ] Keep production deployment, live trading, workers, database changes, and Telegram operations outside this work unless explicitly approved.

The frontend migration remains incomplete. These backend error-redaction fixes do not change the production dashboard mount or authorize deployment.
