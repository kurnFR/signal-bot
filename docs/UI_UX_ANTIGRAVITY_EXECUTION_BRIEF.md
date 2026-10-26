# Signal Bot UI/UX Migration — Antigravity Execution Brief

**Document type:** execution handoff / source of truth for frontend migration work  
**Repository:** `kurnFR/signal-bot`  
**Prepared:** 2026-10-26  
**Current baseline:** `master` at `6f686340e2b8d0e017847f8c09a9fea027e2af38`  
**Status:** Planning decisions are sufficiently defined to begin execution. This does not mean the migration has started or is complete.

This document turns `docs/UI_UX_MODERNIZATION_PLAN.md` into an actionable sequence for Antigravity. Read both documents before making changes. Update this document's checkboxes and progress notes in every meaningful UI/UX PR.

## 1. Mission and definition of success

Incrementally modernize the Signal Bot dashboard into a professional, maintainable React + TypeScript application without losing existing features or changing backend trading behavior.

The migration is complete only when all required views have parity, accessibility and browser acceptance gates pass, the frontend build is reproducible, and CI is green on the exact final commit. A merge is not a deployment.

### Hard constraints

1. GitHub-only changes. Do not SSH to the owner's server or modify a live environment.
2. No production deployment, production database writes, worker restarts, live trading, or Telegram actions.
3. Test first: add or update regression tests before implementation when behavior is being changed.
4. Merge only when GitHub confirms the PR is mergeable and every required workflow is green on the exact PR head.
5. Preserve the Python/FastAPI backend, API contracts, authentication/authorization, database schema, workers, paper-trading lifecycle, accounting, risk controls, strategy lifecycle, and Telegram alert gates.
6. Do not remove the existing dashboard until route parity and acceptance are documented and approved.
7. Do not claim manual/browser/device verification unless it was actually performed. Clearly separate automated CI evidence from manual evidence.
8. Do not silently change product behavior, add unrelated features, or add a global state library without demonstrated need.
9. Keep each PR small, independently reviewable, and reversible. If the base branch changes, reconcile it safely; never bypass a conflict or required check.

## 2. Verified baseline

The existing UI is currently served from `web/static/index.html` (approximately 190 KB) with `web/static/js/app.js` (approximately 286 KB), `web/static/js/dashboard_enhancements.js`, and CSS under `web/static/css/`. The current page uses CDN-loaded styling/icons and existing chart libraries. These file sizes are a reason to modularize gradually, not to rewrite everything in one PR.

Already present and to be preserved:
- Light/dark theme toggle and existing dashboard enhancement styles.
- Market Screener V2 and its existing API integration.
- Existing charts, tables, forms, dialogs, polling and notification behavior.
- Identified accessibility and responsive fixes from the merged PR series #195–#233 and follow-ups.
- Existing authentication and all paper-trading / strategy / wallet / ML / RetailBot functionality.

Known gaps:
- The dashboard remains primarily vanilla JavaScript rather than React/TypeScript.
- No completed React/TypeScript migration has been verified.
- Full keyboard, screen-reader, contrast, zoom/reflow, forced-colors, reduced-motion, real-browser/mobile, and end-to-end acceptance is still outstanding.
- Runtime/manual verification and deployment are explicitly outside this execution unless separately authorized.

Before editing, inspect the current `master` again. The baseline SHA above is a reference point, not permission to work from a stale branch.

## 3. Locked architecture decisions

Use these defaults unless repository inspection exposes a concrete incompatibility. If a change is necessary, document the evidence and trade-off in the PR instead of silently substituting a stack.

| Concern | Decision |
|---|---|
| UI | React + TypeScript |
| Build tool | Vite with a committed lockfile and reproducible scripts |
| Styling | Tailwind CSS through the local build; no new CDN dependency for the migrated UI |
| Components | Repository-owned shadcn/ui components with accessible Radix primitives where appropriate |
| Icons | lucide-react |
| API state | TanStack Query behind a shared typed API/service layer |
| Tables | TanStack Table where it improves the existing data-table experience |
| Candlestick charts | Retain TradingView Lightweight Charts where appropriate |
| General analytics charts | Recharts where suitable; do not rewrite existing charts without a clear benefit |
| Forms | React Hook Form + Zod for forms that need schema validation |
| Notifications | Sonner, integrated consistently with accessible status announcements |
| Unit/component tests | Vitest + Testing Library |
| Browser tests | Playwright for critical flows |
| State | Local/component state first; no global state library by default |
| Backend | Existing Python/FastAPI and APIs remain authoritative |

Use compatible stable dependency versions selected at implementation time and commit the package lockfile. Do not use floating dependency ranges or silently upgrade unrelated dependencies. Record Node/npm versions and exact install/build/test commands in the frontend README.

## 4. Target directory structure

Create a separate frontend source tree. Do not move or overwrite the legacy dashboard during foundation work.

```text
frontend/
  package.json
  package-lock.json
  vite.config.ts
  tsconfig.json
  README.md
  index.html
  src/
    app/
      App.tsx
      routes.tsx
      providers.tsx
    components/
      ui/
      layout/
      feedback/
      data-display/
    features/
      overview/
      market-screener/
      market-detail/
      paper-trading/
      wallets/
      backtest-studio/
      strategy-library/
      retailbot2/
      settings/
    lib/
      api/
      query-client.ts
      utils.ts
    styles/
      tokens.css
      globals.css
    test/
  e2e/
```

This is a target organization, not a mandate to create empty folders. Add feature folders when the corresponding feature is migrated. Follow existing repository naming conventions where they are clearer.

Build artifacts must not overwrite `web/static/index.html` or legacy JavaScript. Keep generated output out of source control unless repository deployment conventions explicitly require committed assets. Inspect the existing static-serving and packaging configuration before choosing the final output/integration path.

## 5. Required UX and design-system rules

### Layout and interaction
- Professional, information-dense trading/research dashboard; avoid decorative gradients, unnecessary animation, and oversized cards that reduce data visibility.
- Responsive sidebar/navigation and header with a clear main-content landmark, skip link, visible focus, and meaningful page headings.
- Use consistent spacing, typography, border, radius, elevation, and semantic status tokens.
- All buttons and form controls have explicit labels and correct semantics. Icon-only actions need contextual accessible names and adequate touch targets.
- Tables must support clear sorting state, readable empty/loading/error states, keyboard access, and mobile overflow behavior without hiding critical actions.
- Dialogs must manage focus, expose a useful title/description, and return focus to the trigger when closed.
- Use toasts for transient feedback only; errors that require user action must remain visible. Announce important asynchronous status without repeated noisy live-region output.
- Provide skeleton/loading, empty, success, stale-data, permission-denied, validation-error, network-error, and retry states where applicable.

### Themes and accessibility
- Both light and dark themes are first-class. Use shared CSS/design tokens; do not maintain unrelated per-page color palettes.
- Verify text, borders, focus indicators, semantic status colors, charts, tables, menus, dialogs and dynamically inserted content in both themes.
- Respect reduced motion and forced-colors/high-contrast preferences. Never use color as the only signal.
- Target WCAG 2.2 AA for the migrated interface. Automated checks are necessary but not sufficient; document manual keyboard and screen-reader checks.
- Test reflow/zoom and narrow viewports; avoid fixed heights that clip controls or data.

### Domain safety and clarity
- Clearly label simulated/paper trades; do not imply live execution.
- Frontend state must never grant execution permission. The server remains authoritative for identity, permissions, lifecycle persistence, risk limits, paper fills, accounting and Telegram alerts.
- Keep strategy evaluation, paper tracking, Telegram alert eligibility and report visibility as distinct concepts where the existing product exposes them.
- Hiding a report row must not delete trades or alter accounting. Stopping a strategy must not be undone by a frontend refresh.
- Never make API calls directly from reusable visual components; route requests through typed service functions and shared error handling.

## 6. Execution plan and PR sequence

### UIX-00 — Reconcile planning and inventory
**Status:** Draft inventory is present on a work branch; source-level review and acceptance are still required before UI implementation.

- [ ] Re-read `docs/UI_UX_MODERNIZATION_PLAN.md`, this brief, and the current `master`.
- [ ] Inventory all user-visible dashboard views/navigation, including hidden or conditionally rendered panels.
- [ ] Map each view to its current HTML container/render function, API endpoints, forms, tables, charts, permissions and polling behavior.
- [ ] Identify the actual FastAPI static mount, dashboard entry route, authentication flow, test commands, lint conventions and packaging/deployment assumptions.
- [ ] Locate existing frontend package manifests, scripts, lockfiles and browser-test tooling before adding duplicates.
- [ ] Record desktop/tablet/mobile baseline screenshots if a browser environment is available; otherwise record this as unverified rather than inventing evidence.
- [ ] Add a view/API parity checklist to this document or a linked inventory file.
- [ ] Add a complete row-by-row endpoint register for every concrete route/method, including handler/source, request/response contract, exact auth/role and password-change guards, side effects, and tests.
- [ ] Verify endpoint count against FastAPI's actual registered routes and document counting/exclusion rules.
- [ ] Separate source-verified facts from environment-specific observations and future design requirements.

**Deliverable:** checked-in `docs/UI_UX_VIEW_INVENTORY.md` with route/view, current implementation location, APIs/data, user actions, access requirements, risk level, migration status, and parity tests. Do not infer endpoints from UI labels; verify them in source.

**Gate:** inventory covers every existing navigation item and conditionally rendered major view, and the endpoint/RBAC register is complete and source-linked. Review and merge the documentation-only acceptance PR before UIX-01. No production changes.

### UIX-01 — Frontend foundation (first implementation PR)
**Status:** Not started. This is the first code PR after UIX-00.

- [ ] Add the isolated `frontend/` Vite + React + TypeScript setup with pinned dependencies and lockfile.
- [ ] Add reproducible scripts: install, dev, typecheck, lint (if adopted), unit tests, production build, and browser tests.
- [ ] Configure strict TypeScript checks; avoid `any` unless narrowly justified.
- [ ] Add Tailwind local build, shared light/dark design tokens, global reset and typography.
- [ ] Add the minimum shared primitives: Button, IconButton, Input, Select, Checkbox/Switch, Badge/Status, Card/Panel, Dialog, Tooltip, Table shell, Skeleton, EmptyState, ErrorState, and Toast/announcements. Only create components that the pilot actually uses.
- [ ] Add app shell skeleton with navigation/main landmarks and theme switching; do not yet replace the production/default dashboard.
- [ ] Add Vitest + Testing Library tests for theme persistence/initialization and core accessibility semantics, plus Playwright smoke setup.
- [ ] Document commands and environment assumptions in `frontend/README.md`.
- [ ] Verify generated build output cannot overwrite legacy files and is not accidentally committed if ignored/generated by convention.

**Required validation:** frontend install from lockfile, typecheck, unit tests, production build, relevant existing Python tests/CI. Record exact command results in the PR.

**Gate:** old dashboard remains the default and behaves as before; no backend/API/schema changes; all required checks green.

### UIX-02 — Read-only pilot view
**Status:** Blocked until UIX-00 and UIX-01 pass.

- [ ] Select the lowest-risk, read-only view from the inventory (prefer overview/system status if genuinely read-only and self-contained).
- [ ] Add typed request/response types grounded in existing API code; do not invent new API contracts.
- [ ] Implement shared API client, authentication/session handling, timeout/error normalization and TanStack Query defaults based on observed backend behavior.
- [ ] Implement loading, empty, error, retry, stale-data and permission states.
- [ ] Expose the pilot through a controlled preview route or development-only integration. Inspect the actual FastAPI routing/static configuration first; never replace the default dashboard entry point in this phase.
- [ ] Add component tests and Playwright coverage for load, refresh, errors, permission behavior and responsive navigation.
- [ ] Compare the pilot's values and API behavior with the legacy view; document any intentional UX difference.

**Gate:** exact data parity for the pilot, keyboard operation, both themes, mobile reflow, all tests green, safe fallback to legacy UI.

### UIX-03 — Market Screener V2
**Status:** Not started.

- [ ] Map every current filter, sorting rule, signal state, table column, action, refresh interval and alert control before implementation.
- [ ] Migrate in slices while preserving current API semantics and signal interpretation.
- [ ] Add accessible filter labels, active-filter summary/reset, clear loading/empty/error states, table sorting announcements and mobile interaction.
- [ ] Test empty results, API failures, stale responses, rapid filter changes, large result sets, theme parity and permission errors.
- [ ] Keep existing charts and table behavior unless a measurable improvement is demonstrated.

**Gate:** screener values and controls match the legacy implementation; no alert/worker behavior change.

### UIX-04 — Strategy and paper-trading interfaces
**Status:** Not started.

Migrate one domain slice per PR in this order, adjusting only if the inventory shows a safer dependency order:
1. Paper orders/history and paper-account read-only reports.
2. Strategy Library and Backtest Studio read-only results.
3. Strategy lifecycle/settings forms.
4. RetailBot2 lifecycle, performance report and promotion controls.
5. Wallet tracker and remaining research/ML views.
6. Administrative/settings views.

For each slice:
- [ ] Map API behavior and permissions before implementation.
- [ ] Preserve separate state for evaluation, paper tracking, Telegram eligibility and report visibility.
- [ ] Test save/readback, duplicate submissions, errors, reloads and server-persisted state.
- [ ] Keep ledger/accounting data immutable from the UI; visibility controls only affect visibility.
- [ ] Do not enable automatic promotion or Telegram by default and do not add an alternate frontend-only permission path.
- [ ] Add focused unit and Playwright regression tests.

**Gate:** existing paper-accounting, strategy lifecycle, wallet integration and other relevant CI remain green. No database schema change or live action is bundled into a UI PR.

### UIX-05 — Consolidation and release readiness
**Status:** Not started.

- [ ] Confirm every inventory item is migrated or explicitly documented as intentionally retained.
- [ ] Verify route parity, authentication, permission handling, navigation, browser refresh/deep links, theme persistence and error recovery.
- [ ] Run Playwright critical-flow tests and visual checks at desktop, tablet and mobile sizes.
- [ ] Complete keyboard-only, screen-reader, zoom/reflow, contrast, forced-colors and reduced-motion review.
- [ ] Check supported browser behavior and large tables/charts.
- [ ] Measure build size and startup/load behavior; remove unused dependencies and duplicate code.
- [ ] Document clean checkout setup, build, test, configuration and rollback/fallback steps.
- [ ] Remove the legacy UI only in a separately reviewable change after explicit parity approval and all gates pass.
- [ ] Update both modernization docs and project progress with evidence.

**Gate:** all required CI green on exact final head; no unresolved P0/P1 regressions; residual limitations documented; runtime/deployment status stated accurately.

## 7. Inventory template

Use one row per view, and split complex views into subviews when their APIs/actions differ.

| Field | Required content |
|---|---|
| View ID / navigation label | Stable ID and exact user-visible label |
| Current source | HTML element, JS render/init function, CSS files |
| API/data dependencies | Verified endpoint, method, response fields, polling/cache behavior |
| User actions | Filters, forms, buttons, sorting, cancel/save/promote/stop operations |
| Access requirements | Anonymous/authenticated role and server-side authorization |
| Risk | Read-only / settings / lifecycle / financial reporting / alerting |
| Loading/error states | Current behavior and required target states |
| Accessibility | Labels, focus, keyboard, table/dialog/live-region needs |
| Responsive behavior | Desktop/tablet/mobile behavior and known defects |
| Tests | Existing tests and required unit/E2E acceptance |
| Migration status | Not started / in progress / parity verified / accepted legacy |
| Evidence | PR, test run, screenshot reference or manual-check record |

## 8. Definition of done for every PR

Every UI/UX PR description must include:
- Scope and explicit out-of-scope items.
- Files and views changed.
- Before/after behavior and screenshots where available.
- Accessibility and responsive considerations.
- Tests added first and exact test commands/results.
- Required workflow status on the exact head SHA.
- API/backend behavior comparison where applicable.
- Any remaining unverified/manual checks.
- Confirmation that no live deployment, production DB, worker, trading or Telegram actions occurred.

Do not merge on a stale green result if new commits were pushed. If a test fails, determine whether it is a regression or an unrelated/environmental failure and document evidence; never simply delete a meaningful test to get green.

## 9. Progress tracker

Update this table after each meaningful PR.

| Work item | Status | PR / evidence | Blocker / next action |
|---|---|---|---|
| UIX-00 Baseline inventory | In review — independent verification pending | `docs/UI_UX_VIEW_INVENTORY.md` | Reconcile the 81-row draft against current FastAPI routes/source, verify view flows/RBAC, and obtain owner acceptance |
| UIX-01 Frontend foundation | Not started | — | Wait for UIX-00 inventory and existing-tooling audit |
| UIX-02 Read-only pilot | Not started | — | Requires UIX-01 gates |
| UIX-03 Market Screener V2 | Not started | — | Requires inventory and pilot patterns |
| UIX-04 Strategy/paper-trading and remaining views | Not started | — | Migrate separately by risk and domain |
| UIX-05 Consolidation/release readiness | Not started | — | Requires route parity and full acceptance evidence |

## 10. First instruction to Antigravity

Continue with **UIX-00 only** until acceptance. Review the detailed inventory draft against current `master`, verify route count/contracts/RBAC and frontend flows, confirm actual tooling/static-routing behavior, then submit a documentation-only acceptance PR. Do not install packages, alter runtime code, or start the React migration until the inventory is reviewed and merged.

After UIX-00 is merged, implement UIX-01 in its own PR. Continue in order and update this brief after each phase. Do not ask the user to repeat decisions already specified here; raise a question only when source inspection reveals a genuine product decision that cannot be safely inferred.
