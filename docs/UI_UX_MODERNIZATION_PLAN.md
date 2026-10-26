# Signal Bot UI/UX Modernization Plan

**Status as of 2026-10-26: incremental UI/UX improvements are merged; the recommended React/TypeScript frontend migration is not complete and has not started as a production code migration.**

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
| React + TypeScript + component library migration | **Not started / not complete** | No evidence of an implemented React/TypeScript build pipeline or migration of the existing dashboard. |
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
**Status: not started.**

- [ ] Add a pinned local frontend package/build setup on a dedicated branch; do not remove the existing dashboard entry point yet.
- [ ] Define design tokens for dark/light surfaces, text, borders, semantic status, focus, spacing, radius and typography.
- [ ] Add the shared app shell, navigation, buttons, inputs, dialogs, tables, badges, skeletons, empty/error states and toast conventions.
- [ ] Verify keyboard navigation, focus management, reduced motion, forced colors and contrast in both themes.
- [ ] Add Vitest/Testing Library and Playwright smoke coverage before migrating a feature.

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
- The local build is reproducible with pinned dependencies; CDN dependency and supply-chain decisions are documented.
- CI is green on the exact final head, documentation is updated, and runtime/deployment verification is separately recorded.

## 6. Working constraints

1. Do not attempt a big-bang rewrite of the existing dashboard.
2. Make one independently reviewable slice per PR and add regression tests before implementation.
3. Preserve existing functionality and API behavior; separate necessary backend changes into their own reviewed scope.
4. Merge only after all required checks pass on the exact PR head.
5. Update this plan and `docs/PROJECT_PROGRESS.md` after each meaningful phase.
6. No live deployment, production database mutation, worker restart, live trading, or Telegram changes without explicit authorization.
