# Signal Bot — Complete Modern UI Migration Plan

**Repository:** `kurnFR/signal-bot`  
**Target:** Complete the React + TypeScript frontend so it can replace the legacy dashboard.  
**Implementation owner:** Antigravity  
**Independent review and acceptance:** ChatGPT / repository owner  
**Deployment status:** Not authorized by this plan.

## 1. Mission and target architecture

Finish the modern React frontend and make it functionally equivalent to the existing legacy dashboard.

The objective is not merely to improve the appearance of the current React preview. The objective is to migrate the entire application experience so users can operate Signal Bot through one modern interface without needing the old frontend for any feature or configuration.

The intended architecture is:

- React + TypeScript is the single dashboard user experience.
- FastAPI and the existing backend APIs remain authoritative for business logic, authentication, authorization, trading, accounting, risk controls, strategy execution and Telegram delivery.
- Configuration and administration are part of the modern UI, protected by backend-enforced roles.
- The legacy frontend remains available during development and acceptance as a comparison and rollback option.
- Production cutover is a separate, explicitly approved activity after acceptance.

**Do not create a split architecture where ordinary users use the modern UI but administrators must return to the legacy UI for configuration.**

## 2. Current state — implemented foundation

The repository already contains a meaningful React/Vite application foundation. The following areas have implementations in the modern preview; their completeness and live API behavior still need to be checked against the latest source:

| Area | Existing implementation |
|---|---|
| Frontend foundation | React, TypeScript, Vite, frontend package/lock files |
| Dashboard shell | Sidebar, header, page layout and responsive navigation foundation |
| Authentication | Login, session restoration, logout and forced-password-change flow |
| Permissions | Role-aware presentation for admin, trader and viewer; backend remains authoritative |
| Themes | Dark/light theme support and persisted preference |
| Overview | System status and operational health panels |
| Market Screener | Screener signals, status and configuration panels |
| Paper Trading | Positions, orders, trades, metrics and risk information panels |
| Strategy Library | Strategy catalog, strategy testing and custom strategy workflows |
| Wallet Tracking | Wallet and token-flow read panels |
| Backtest Studio | Saved backtest results and experiment panels |
| ML Studio | ML experiments, models and artifact panels |
| Research | RetailBot2 positions, trades, statistics and strategy information |
| News AI | News signals, events and performance information |
| Master Data | Data coverage and job panels |
| Settings | Settings and diagnostics panels |

Historical owner-run test evidence exists in the repository acceptance documentation. It includes frontend type-check/build, component tests and Chromium Playwright runs on earlier, explicitly identified commits. These historical results are not proof that the latest branch is complete or that every workflow is live and correct. Re-run the checks against the exact final commit.

## 3. Known migration gaps and acceptance status

The migration is **not accepted**. FastAPI currently serves the legacy dashboard at `/`; the React application has been an isolated preview. Verify the current serving switch and route behavior before changing anything.

Important gaps to investigate and close:

| Priority | Workstream | Required outcome |
|---|---|---|
| P0 | Full legacy feature parity | Every existing useful menu, sub-menu, tab, control and workflow has a modern equivalent |
| P0 | API integration | Pages use real backend contracts; errors, nulls, empty states and session expiry are handled correctly |
| P0 | Operational mutations | Every supported configuration and action is implemented with validation, confirmation, persistence and readback |
| P0 | Strategy lifecycle | Promotion, enable/disable, removal, paper configuration and Telegram settings work independently where supported |
| P0 | Paper Trading | Supported configuration, deployment, stop/cancel, position/order and risk workflows are represented safely |
| P0 | Wallet Tracking | Chain, wallet candidate, token, threshold, tracking and alert controls are complete where supported |
| P0 | ML workflows | Existing ML filter and independent ML signal-generator workflows remain distinct; model and Telegram controls are explicit |
| P0 | Research Bots | Strategy lifecycle, paper/report settings and independent alert controls are complete where supported |
| P1 | Backtest workflows | Execution, comparison, trade evidence and promotion workflows are covered with safeguards |
| P1 | Settings and Users | User management, roles, password flows, application settings and authorized diagnostics are covered |
| P1 | Accessibility | Keyboard, focus, screen reader, contrast, zoom and forced-colors checks are completed |
| P1 | Browser coverage | Chromium, Firefox and WebKit coverage is run where supported |
| P1 | Responsive behavior | Phone, tablet, desktop, zoom and overflow behavior are checked |
| P1 | Live parity | Modern UI values are compared to actual backend responses in a safe test/staging environment |
| P1 | Acceptance report | Evidence, residual defects and exact tested commit are recorded |

The top-level menu labels differ between the two applications. For example, legacy labels include “Home & Overview”, “ML Forecast Studio”, “News AI Signals”, “Research Bots” and “Settings & Users”. React labels include “Overview”, “ML Studio”, “News AI”, “Research” and “Settings”. Compare actual page contents and workflows, not just the number or wording of menu entries.

## 4. Phase 1 — create the legacy-to-modern parity inventory

**Do this before further page redesign or implementation.**

Inspect at minimum:

- `web/static/index.html`
- `web/static/js/app.js`
- `web/static/js/`
- `web/static/css/`
- `frontend/src/`
- `frontend/e2e/`
- `web/routes/`
- `web/app.py`

Create `docs/UIX_LEGACY_TO_MODERN_PARITY.md`.

For every legacy menu and page, record:

1. Menu and sub-menu label.
2. Existing page functionality.
3. Tables, charts, forms, filters and dialogs.
4. Every button/control and its intended action.
5. API method and path, including payload/response contract where applicable.
6. Required roles and backend authorization behavior.
7. Confirmation, safety and error behavior.
8. Corresponding React component.
9. Status: `COMPLETE`, `PARTIAL`, `MISSING` or `INTENTIONALLY_REMOVED`.
10. Test coverage and evidence.
11. Any reason a legacy feature is intentionally not being migrated; this requires owner review.

Do not mark a feature complete because a placeholder panel or disabled button exists. Verify that the workflow works.

## 5. Phase 2 — complete the shared application shell

Complete and standardize:

- Responsive desktop sidebar and mobile navigation.
- Header, breadcrumbs and consistent page titles.
- Dark and light themes.
- Shared design tokens and reusable components.
- Buttons, inputs, selectors, tables, dialogs and confirmation dialogs.
- Toast notifications and accessible status announcements.
- Loading skeletons, empty states, API errors and retry behavior.
- Keyboard navigation, visible focus and focus restoration.
- Responsive charts and tables.
- Consistent date, time, number, currency and percentage formatting.
- Appropriate persistence of user preferences.
- No document-level horizontal overflow or duplicate horizontal scrollbars.

Keep the design professional, information-dense and appropriate for a quantitative trading platform. Avoid unnecessary redesign of working components when the actual gap is missing functionality.

## 6. Phase 3 — complete every menu and workflow

Use the parity inventory as the authoritative checklist. At minimum, inspect and complete the following areas.

### Overview
- System status and health.
- Database and worker status.
- Market-data and collector status.
- Operational summaries and refresh behavior.

### Master Data
- Historical data coverage.
- Backfill configuration and submission.
- Feature generation/build.
- Job progress, history, errors and refresh.
- Validation and confirmation for write operations.

### Backtest Studio
- Strategy, market and timeframe selection.
- Backtest execution and progress.
- Results, trade history and evidence.
- Performance, drawdown and equity charts.
- Strategy comparison.
- Existing promotion/tournament workflow, with explicit confirmation and server-side eligibility enforcement.
- Clearly distinguish research results from paper deployment.

### Paper Trading
- Paper configurations.
- Strategy and market selection.
- Deployment and stop controls.
- Open positions and orders.
- Trade history and supported cancellation/order actions.
- Performance metrics and risk information.
- Circuit-breaker and emergency-stop state.
- Clear distinction between paper execution and live trading.

### ML Forecast Studio
- Existing ML filter workflow.
- Independent ML signal-generator workflow.
- Training and experiment history.
- Model artifacts, evaluation and eligibility.
- Model selection and configuration.
- Paper activation/deactivation where supported.
- Independent Telegram settings.
- Clear research-only versus paper-eligible status.

Do not merge the existing ML filter and the independent ML generator into one ambiguous workflow. Preserve both.

### Strategy Library
- Built-in and custom strategies.
- Strategy details and parameters.
- Create, edit, test and delete.
- AI-generated drafts with review before saving.
- Backtest integration.
- Promotion and lifecycle controls where supported.
- No implicit paper activation or Telegram activation.

### News AI Signals
- Signal history and details.
- News and event feed.
- Performance and statistics.
- Existing supported operational settings and controls.

### Research Bots
- RetailBot2 positions, trades and performance.
- Strategy-level lifecycle settings.
- Start/stop and enable/disable controls.
- Paper and report settings.
- Strategy promotion where implemented.
- Independent Telegram settings.
- Separation of visibility, evaluation, paper execution and alert delivery.

### Market Screener
- Screener V2 signals and details.
- Filters and configuration.
- Status and statistics.
- Supported start/stop and control settings.
- Telegram alert settings.
- Responsive signal tables.

### Wallet Tracking
- Tracked wallets and wallet candidates.
- Supported chains and networks.
- Token filters and minimum transaction thresholds.
- Wallet and token-flow history.
- Tracking configuration and lifecycle.
- Telegram alerts and alert preferences.
- Status and activity feed.

### Settings and Users
- Existing application settings and user preferences.
- Theme settings.
- User management and role assignment.
- Password reset and forced-password-change workflows.
- Telegram configuration and test actions where authorized.
- System diagnostics and audit history where supported.

If the actual legacy dashboard contains additional functionality, add it to the inventory. The repository's current legacy implementation is the source for feature discovery; do not rely only on this summary.

## 7. Phase 4 — API and operational correctness

Create or complete a typed API service layer.

Requirements:

- One consistent authentication mechanism and token/session handling.
- Session-expiry and unauthorized-response handling.
- Runtime validation for important API response contracts.
- Correct nullable, empty and partial response handling.
- Request cancellation when navigating away where appropriate.
- Correct polling and refresh intervals.
- Avoid unnecessary repeated requests.
- Clear error messages and retry behavior.
- Read-after-save verification for mutations.
- Correct refresh/invalidation of affected page data.
- No fabricated success state when the server rejects or fails a request.

Do not duplicate backend business logic in React. The backend remains authoritative.

For every mutation:

1. Validate input.
2. Apply role-aware UI presentation without treating it as the security boundary.
3. Show a confirmation dialog for consequential actions.
4. Send the actual backend request.
5. Handle success and failure distinctly.
6. Refresh affected data.
7. Verify persisted state where practical.
8. Prevent duplicate submissions.
9. Preserve an audit trail when the backend supports or requires one.

## 8. Phase 5 — permissions and safety

Test at least these session states:

- Admin.
- Trader.
- Viewer.
- Unauthenticated user.
- User required to change password.

Verify both UI visibility and backend authorization. Hiding a button is not authorization.

Explicitly protect against:

- Viewer users executing protected mutations.
- Training implicitly activating paper trading.
- Paper activation implicitly enabling Telegram.
- Strategy creation implicitly promoting or deploying a strategy.
- Report visibility changing trading accounting.
- UI defaults overriding persisted backend settings.
- Failed requests being presented as successful saves.
- Duplicate submissions causing duplicate jobs/actions.
- Destructive actions occurring without appropriate confirmation.

Preserve existing trading, risk, accounting and Telegram safeguards. Do not send Telegram messages or activate strategies as part of development testing.

## 9. Phase 6 — automated and browser tests

Run from `frontend/`:

```bash
npm ci --no-audit --no-fund
npm run typecheck
npm test
npm run build
```

Run backend regression tests from repository root:

```bash
PYTHONPATH=$PWD python -m pytest -q
```

Add and execute Playwright tests covering:

- Login, logout and session expiry.
- Forced password change.
- Every navigation entry and page.
- Major forms, dialogs and filters.
- Every supported mutation workflow.
- Save and readback.
- Admin/trader/viewer permissions.
- API errors, retries and loading states.
- Theme persistence.
- Mobile navigation.
- Responsive tables and charts.
- Refresh and direct navigation.
- No horizontal document overflow.
- No duplicate horizontal scrollbars.

Use realistic API fixtures for deterministic tests, but do not mistake mocked API tests for live backend validation.

Run Chromium, Firefox and WebKit where supported. Record browser/device coverage that could not be completed and why.

GitHub Actions quota has been exhausted according to the owner. Do not claim hosted CI passed if no workflow executed. Record local/manual test commands, results, environment and exact commit SHA.

## 10. Phase 7 — live API and mutation acceptance

After mocked tests pass, compare the React UI against the actual FastAPI API in a safe test/staging environment.

For read-only parity, record:
- Role used.
- Capture timestamp.
- Endpoint and HTTP status.
- Redacted response sample or field summary.
- UI value and API value comparison.
- Row counts, nullable values and empty states.

For mutations, use an isolated test environment and non-production credentials. Verify:
- Authorized positive path.
- Unauthorized/forbidden negative path.
- Validation errors.
- Confirm and cancel behavior.
- Persistence and readback.
- Audit record where applicable.
- Retry and duplicate-submit behavior.
- Safe failure behavior.

Do not run backfills, feature builds, saved backtests, strategy create/delete, paper deployment, wallet activation or Telegram tests against production as part of this work. Do not use the production database for test mutations.

## 11. Phase 8 — acceptance report

Create or update:

`docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`

Include:

- Legacy-to-modern feature parity matrix.
- Completed, partial, missing and intentionally removed features.
- API endpoint and response-contract coverage.
- Role and permission results.
- Mutation workflow results.
- Unit/component test results.
- Type-check and build results.
- Playwright results by browser.
- Accessibility and responsive checks.
- Live API parity evidence.
- Known defects and residual risks.
- Exact tested commit SHA.
- Tests that could not run and why.

Do not mark the migration accepted while any required legacy feature is missing or a critical workflow remains unverified.

## 12. Phase 9 — prepare a separate, reversible FastAPI cutover

Inspect the existing modern UI serving mechanism in `web/app.py` and the frontend build configuration.

Keep the modern UI serving switch disabled during development and acceptance.

Prepare a separate cutover plan covering:

- Building `frontend/dist`.
- Static asset serving and cache headers.
- SPA route fallback.
- API route precedence.
- Authentication and session behavior.
- Asset cache invalidation.
- Health checks and smoke tests.
- Rollback to the legacy dashboard.
- Verification after cutover.

Do not change the production default, deploy, or restart services as part of this task.

## 13. Deliverables

1. `docs/UIX_LEGACY_TO_MODERN_PARITY.md`
2. Completed React frontend features and workflows.
3. Typed API integration and error handling.
4. Unit/component and Playwright regression tests.
5. Updated `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`.
6. Separate reversible cutover plan.
7. GitHub PRs with clear scope, screenshots where useful, test evidence and exact tested commit SHA.

Use feature branches and small reviewable PRs. Avoid one large, difficult-to-review migration PR. Do not merge automatically.

## 14. Definition of done

The migration is complete only when:

- Every required legacy feature is present in the modern UI.
- Every menu and sub-menu works.
- Every visible action performs its advertised operation.
- Supported settings can be viewed and changed by authorized users.
- Admin, trader and viewer permissions are correct and backend-enforced.
- Trading, risk, accounting and Telegram safeguards are preserved.
- Frontend type-check, tests and production build pass.
- Browser tests cover critical workflows.
- Accessibility and responsive acceptance is recorded.
- Live API parity and isolated mutation verification are recorded.
- No unresolved P0/P1 defects remain, or the owner explicitly accepts documented exceptions.
- Acceptance evidence references the exact tested commit.
- Production cutover remains a separate owner-approved action.

## 15. First task for Antigravity

Start with **Phase 1 only**.

Produce `docs/UIX_LEGACY_TO_MODERN_PARITY.md` by inspecting the actual legacy and React source. Identify missing features with exact file paths, API endpoints, roles and workflows. Report findings before implementing the next batch.

Do not spend time redesigning pages that already exist while major legacy workflows remain unidentified. Once the inventory is reviewed, implement the highest-priority gaps in small, independently testable PRs.
