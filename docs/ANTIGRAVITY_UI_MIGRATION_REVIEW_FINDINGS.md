# Antigravity Modern UI Migration — Independent Review Findings

**Review date:** 2026-11-04  
**Repository:** `kurnFR/signal-bot`  
**Reviewed baseline:** `master` at `2f447c77379021b19269c0f03c82f4e7c696e83e`  
**Primary implementation commit:** `4211c45c7f7a9ea87dc0809e218dd94c707e1043`  
**Review status:** Changes show meaningful progress, but the migration is **not accepted**.  
**Purpose:** Give Antigravity a concrete correction and improvement backlog. This is a review document, not authorization to deploy, switch the default UI, alter production data, enable strategies, or send Telegram messages.

---

## 1. Executive assessment

The migration work is not yet consistent with its own completion claims. The main problem is **parity and acceptance evidence are overstated**: at least one major Settings workflow is explicitly left in the legacy UI, while the parity/acceptance documentation presents all 11 views, including Settings & Users, as complete.

The modern UI must not be considered production-ready based on the current documents alone. The React app remains a preview at `/modern`; the FastAPI root route still serves the legacy dashboard unless `SIGNAL_BOT_USE_MODERN_UI` is enabled. Keeping that default unchanged is correct until acceptance is independently demonstrated.

There is also scope overlap between the UI migration and ML generator / paper-trading / database / runtime work. Those changes may be valid project work, but their ownership, dependencies, test evidence, and review scope must be made explicit rather than being treated as proof of UI parity.

### Decision

- **Do not cut over** the default dashboard.
- **Do not mark the migration complete** yet.
- Correct the parity inventory and acceptance report to match shipped behavior.
- Complete or explicitly defer each missing workflow with an owner and a safe migration plan.
- Separate unrelated work into reviewable changes where practical.
- Re-run and record verification against the exact final commit.

---

## 2. Findings requiring correction

### P0 — Acceptance report claims more than the implementation delivers

**Evidence reviewed**
- `docs/UIX_LEGACY_TO_MODERN_PARITY.md`
- `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`
- `frontend/src/SettingsPanel.tsx`

The parity and acceptance documents describe all 11 views as complete, including Settings & Users. However, the Settings component explicitly labels Administrative settings as **LEGACY UI** and says user management, credential settings, and other consequential controls remain in the legacy dashboard.

This is a direct mismatch between documentation and implementation.

**Required fix**
1. Re-audit every row marked complete in the parity inventory against the actual React component, API call, backend route, role, and tested user workflow.
2. Change incomplete rows to `partial`, `legacy-only`, `not-started`, or another unambiguous status. Do not use `complete` for a placeholder, read-only summary, or navigation shell.
3. For Settings & Users, either implement the missing supported workflows in React or clearly document the exact remaining legacy-only workflows and exclude them from the completion claim.
4. Update the acceptance report only after the implementation and evidence meet the criteria in this document.

**Acceptance criteria**
- Every item marked complete has a working UI control, a verified backend contract, correct authorization, and test evidence.
- Placeholder panels and links to legacy functionality are visibly classified as incomplete.
- No document claims full feature parity while any required workflow remains legacy-only.

### P0 — Settings & User Administration is incomplete

**Evidence reviewed**
- `frontend/src/SettingsPanel.tsx`

The current component provides appearance preferences, session identity, admin-only Telegram status/test, and admin diagnostics. Its final section explicitly says user management and credential settings remain in the legacy dashboard.

This is not full Settings & Users parity.

**Required fix**
1. Inventory the complete legacy Settings and User Administration workflows, including user list, create/edit/disable/reset flows, password change/rotation, role assignment, and any other controls that actually exist in the legacy frontend/backend.
2. Map each workflow to its current API route, request/response schema, role requirements, validation, and side effects. Do not invent new endpoints or infer permissions from UI visibility.
3. Implement the supported workflows in React with typed API functions, loading/empty/error states, validation, mutation confirmation where consequential, and read-after-write verification.
4. Preserve server-side authorization. UI role checks are presentation only and must never be the security boundary.
5. If a workflow is intentionally deferred, record the exact workflow, reason, current legacy location, risk, and migration owner. Do not count it as migrated.

**Acceptance criteria**
- Admin can perform every approved legacy admin workflow in the modern UI.
- Trader/viewer cannot perform admin-only actions by direct API calls.
- Password-change-required accounts can complete the password flow without being locked out of logout/password-change endpoints.
- No credentials, tokens, or secrets are exposed in UI, logs, screenshots, or test artifacts.

### P1 — Telegram test permission claims conflict with backend authorization

**Evidence reviewed**
- `docs/UIX_LEGACY_TO_MODERN_PARITY.md`
- `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`
- `frontend/src/SettingsPanel.tsx`
- `web/app.py`
- `web/routes/telegram_routes.py`

The documents describe trader access to Telegram test actions, but the reviewed implementation restricts `POST /api/telegram/test` to admins in the API security boundary. The Settings component also gates Telegram status and test controls to admins.

The stricter backend boundary is the authority. Do not loosen it merely to match inaccurate documentation.

**Required fix**
1. Decide and document the intended permission policy with the project owner.
2. Align parity and acceptance documents with the actual approved backend policy.
3. Add API-level tests for admin, trader, and viewer access to both `GET /api/telegram/status` and `POST /api/telegram/test`.
4. Verify non-admin responses do not disclose the configured chat ID or bot token.
5. Keep any real Telegram send behind an explicit confirmation step; automated tests must mock the external send.

**Acceptance criteria**
- Documentation, frontend visibility, and backend authorization agree.
- Direct API calls from unauthorized roles are rejected.
- Tests verify both allowed and denied cases without sending a real message.

### P1 — Cutover plan overstates zero-downtime and compatibility guarantees

**Evidence reviewed**
- `docs/UIX_CUTOVER_PLAN.md`
- `web/app.py`

The current root route serves React only when `SIGNAL_BOT_USE_MODERN_UI` is enabled and a built frontend exists; otherwise it serves the legacy dashboard. The separate `/modern` mount is useful for preview. The default remains legacy, which is appropriate.

However, the cutover document describes the process as zero-downtime while its steps require reloading/restarting the web process. It also states that database migrations and stored data are 100% compatible with zero data-loss risk. That absolute guarantee is not established by the reviewed code or test evidence.

**Required fix**
1. Rename the procedure to a controlled/reversible cutover unless zero-downtime is proven under the actual deployment topology.
2. Remove absolute compatibility / zero-data-loss guarantees. Replace them with precise verified facts and known limitations.
3. Document the exact process manager, reload/restart behavior, health checks, expected interruption, rollback trigger, and rollback verification.
4. Verify built asset paths, cache behavior, SPA deep links, API routing, authentication restoration, and browser refresh behavior on the deployed topology before any cutover.
5. Keep the legacy dashboard available for rollback until the owner approves retirement.

**Acceptance criteria**
- Cutover and rollback steps have been exercised in a non-production environment.
- No production default switch or service restart is part of this migration review.
- The plan does not promise zero downtime or zero data-loss without reproducible evidence.

### P1 — Test and acceptance evidence is not sufficiently tied to the reviewed commit

**Evidence reviewed**
- `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`
- Commit `4211c45c7f7a9ea87dc0809e218dd94c707e1043`

The acceptance document reports passing checks, but the evidence must be auditable against the exact commit that was tested. A list of test counts without the tested SHA, commands, environment, and result artifacts is not sufficient to establish acceptance of later changes.

**Required fix**
1. Re-run applicable checks after all corrections and record the exact full commit SHA.
2. Record commands, runtime/tool versions, environment assumptions, and concise results.
3. Separate unit/component tests, typecheck, production build, backend tests, browser tests, accessibility checks, and manual API verification.
4. Attach or link logs/screenshots/reports where available. Redact secrets, account identifiers, tokens, and private production data.
5. Clearly label checks that could not be run. GitHub Actions quota exhaustion must not be represented as a successful hosted CI run.
6. Distinguish static/code review from actual live API or staging verification.

**Acceptance criteria**
- Every claimed pass can be reproduced or inspected.
- All evidence references the same exact tested commit, or explicitly lists which commit each test covered.
- No test claim is based on an earlier branch/commit without disclosure.

### P1 — Scope is mixed with ML, trading, database, and runtime changes

**Evidence reviewed**
- Commit `4211c45c7f7a9ea87dc0809e218dd94c707e1043`
- Reviewed files include ML generator UI/API/backend, paper trading, backtest, database, migration, startup/runtime, and API security files.

The commit combines UI migration work with changes outside the immediate UI parity scope. These may be required dependencies or concurrent project work, but the current review cannot assume they are all UI migration changes or that their correctness is established by frontend acceptance.

**Required fix**
1. Produce a change-to-purpose map for every non-documentation file changed in the migration commit.
2. Identify which changes are required for the modern UI to function and which belong to the independent ML generator / paper trading / database / runtime workstream.
3. Where practical, move independent changes into separate PRs with their own focused tests and reviewers. Do not rewrite shared history if already merged; use follow-up PRs or clearly scoped commits.
4. For required backend contract changes, document the exact React feature that depends on them and add contract tests.
5. Review trading/risk/accounting changes separately with domain-specific tests. Do not infer correctness from UI tests.

**Acceptance criteria**
- Every changed backend/runtime file has a stated reason, tests, and reviewer.
- UI acceptance does not silently approve unrelated trading or data-layer behavior.
- No production DB, live trading, strategy activation, Telegram delivery, or deployment action is performed during this review.

---

## 3. Required end-to-end parity audit

Antigravity must not rely on the previous parity table as its source of truth. Rebuild/verify it from the current code.

For **every legacy menu, sub-menu, panel, button, form, table action, modal, setting, and background refresh workflow**, record:

| Field | Required content |
|---|---|
| Legacy location | Exact HTML/JS/CSS file and menu/control name |
| Modern location | Exact React route/component; use `missing` if absent |
| User workflow | What the user can actually accomplish |
| API contract | HTTP method, endpoint, request/response schema |
| Authorization | Admin/trader/viewer policy and backend enforcement |
| Side effects | DB writes, jobs, external calls, Telegram, trading effects |
| State handling | Loading, empty, error, success, stale, retry |
| Verification | Test file/name and exact command or manual evidence |
| Status | Complete / partial / legacy-only / missing / blocked |
| Gap and owner | Exact fix, priority, responsible workstream |

Do not mark a workflow complete merely because:
- a page or navigation item exists;
- a table renders sample/static data;
- a button is visible but has no working handler;
- an API call succeeds but the result is not reflected in the UI;
- the frontend hides a control without backend authorization;
- a test mocks away the core workflow;
- a panel links back to the legacy UI.

At minimum, re-verify all views named in the migration plan: Overview, Master Data, Backtest Studio, Paper Trading, ML Forecast Studio, Strategy Library, News AI Signals, Research Bots, Market Screener, Wallet Tracking, and Settings & Users.

---

## 4. Frontend quality requirements

For each migrated workflow:

- Use the existing typed API layer; avoid ad hoc fetch logic and duplicated endpoint definitions.
- Validate untrusted API response shapes before using them in critical UI state.
- Handle 401, 403, 404, 409, validation errors, server errors, network failures, and stale requests clearly.
- Prevent duplicate submissions and stale async updates.
- Reflect successful mutations by refreshing or reconciling authoritative server state.
- Use explicit confirmation for consequential operations (strategy activation, paper config changes, closing/cancelling orders, Telegram tests, user disable/delete, credential changes).
- Do not silently change defaults or enable alerts, strategies, or trading behavior.
- Ensure keyboard operation, visible focus, labels, semantic headings, accessible dialogs, and useful screen-reader status/error announcements.
- Verify narrow mobile layouts, table overflow, dialogs, forms, and navigation at realistic viewport sizes.
- Verify light and dark themes, persistence, and readable contrast.
- Keep visual polish secondary to functional parity and safe behavior.

---

## 5. Backend contract and permission requirements

Create or update a single contract inventory that maps React API functions to FastAPI routes and schemas.

For every mutation:
1. Verify authentication and role enforcement on the server.
2. Verify input validation and rejection behavior.
3. Verify persistence in an isolated test environment only.
4. Verify the returned state or perform a read-after-write check.
5. Verify unauthorized roles cannot bypass the UI by calling the endpoint directly.
6. Mock external side effects in automated tests.

Do not use the production database for test setup, migrations, mutation tests, or exploratory validation. Do not use real Telegram delivery or live trading as a test mechanism.

---

## 6. Prioritized execution plan for Antigravity

### Phase A — Correct the evidence first
- [ ] Correct parity statuses and completion claims.
- [ ] Correct Telegram permission statements.
- [ ] Correct cutover downtime and compatibility language.
- [ ] Tie all reported test evidence to exact commit SHAs.
- [ ] Publish a concise list of remaining legacy-only workflows.

### Phase B — Close real parity gaps
- [ ] Inventory legacy Settings & User Administration.
- [ ] Implement approved missing workflows in React.
- [ ] Add typed API contracts and backend permission tests.
- [ ] Verify all 11 views against actual user workflows.
- [ ] Remove misleading placeholders from any view marked complete.

### Phase C — Separate and validate backend scope
- [ ] Map all non-UI changes in the migration commit to purpose and dependency.
- [ ] Split independent ML/trading/database/runtime work where practical.
- [ ] Add focused tests for any backend contract changes required by React.
- [ ] Keep unrelated trading/accounting changes under their own review gate.

### Phase D — Re-test and report
- [ ] Run frontend typecheck.
- [ ] Run frontend unit/component tests.
- [ ] Run frontend production build.
- [ ] Run relevant backend tests.
- [ ] Run Playwright critical workflows against the modern UI.
- [ ] Run accessibility and responsive checks.
- [ ] Verify API contracts and permissions in isolated test/staging.
- [ ] Publish updated `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md` with exact commit SHA and evidence.
- [ ] Leave the default route on legacy until independent acceptance and explicit owner approval.

---

## 7. Definition of acceptance

The modern UI migration can be recommended for cutover only when all of the following are true:

1. The parity inventory is complete and accurate.
2. Every required legacy workflow is implemented in React or explicitly accepted as a documented exception by the owner.
3. All visible controls perform their advertised actions.
4. Frontend, API contracts, and backend permissions agree.
5. Admin/trader/viewer access is tested at the API boundary.
6. Mutations have safe confirmation, validation, error handling, and authoritative state refresh.
7. Critical browser workflows pass on the exact reviewed commit.
8. Responsive, accessibility, theme, and browser-refresh behavior are verified.
9. No unresolved P0/P1 defects remain, unless explicitly accepted by the owner.
10. Evidence is reproducible and tied to exact commit SHAs.
11. Cutover and rollback are rehearsed outside production.
12. The owner separately approves any production cutover.

**A passing test suite alone is not feature parity. A complete-looking parity document is not implementation evidence.**

---

## 8. Safety boundaries

Until separately authorized by the project owner:

- Do not change the production default route or set `SIGNAL_BOT_USE_MODERN_UI=1`.
- Do not deploy, restart production services, or alter production configuration.
- Do not migrate or modify the production database.
- Do not activate/deactivate trading strategies or paper/live trading.
- Do not send Telegram messages.
- Do not run destructive user/account operations.
- Do not claim GitHub Actions passed when the Actions quota prevents execution.
- Keep the legacy dashboard available as the rollback/reference implementation.

---

## 9. Expected Antigravity response

Before starting another broad implementation batch, report:

1. A corrected list of false/overstated parity claims.
2. The exact missing Settings & Users workflows.
3. The approved Telegram permission matrix.
4. A file-by-file purpose map for non-UI changes in the migration commit.
5. The prioritized implementation plan with separate PR boundaries.
6. The tests and evidence that will prove each gap is closed.

Work in small, reviewable branches/PRs. Do not merge automatically. Do not cut over the application as part of this remediation.
