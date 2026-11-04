# Antigravity Work Order — Modern UI Migration Remediation

**Repository:** `kurnFR/signal-bot`  
**Implementation owner:** Antigravity  
**Independent reviewer:** ChatGPT / repository owner  
**Date prepared:** 2026-11-04  
**Status:** Remediation required — do not cut over  
**Related review:** [ANTIGRAVITY_UI_MIGRATION_REVIEW_FINDINGS.md](ANTIGRAVITY_UI_MIGRATION_REVIEW_FINDINGS.md)  
**Parity worksheet:** [ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md)

## 1. Objective

Finish the modern React + TypeScript frontend as a real replacement for the legacy dashboard, not just a visual shell. Every required legacy workflow must either work end-to-end in React or be explicitly documented as an owner-approved exception.

This work order is implementation guidance. It does not authorize production deployment, a default-route switch, production database changes, service restarts, live/paper trading changes, strategy activation, or real Telegram delivery.

## 2. Verified baseline and blockers

The following points were verified against the repository's current `master` documentation and source references during the 2026-11-04 review:

1. `docs/UIX_LEGACY_TO_MODERN_PARITY.md` labels the parity audit `COMPLETE`, reports 11/11 views complete, 81 API routes, 202 frontend tests, 28 Playwright tests, and 1,087 backend tests.
2. `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md` also claims full functional equivalence and marks Settings & Users complete.
3. `frontend/src/SettingsPanel.tsx` contains an explicit LEGACY UI / incomplete administrative-settings area. User management and credential settings are described as remaining in the legacy dashboard. This contradicts the complete Settings & Users claim.
4. The acceptance RBAC table says traders may test Telegram, while the reviewed frontend and backend enforce admin-only Telegram test access. The backend is the security authority; do not weaken it to make the documentation appear consistent.
5. `docs/UIX_CUTOVER_PLAN.md` calls the cutover zero-downtime but requires a process reload/restart. Its claim of 100% database compatibility and zero data-loss risk is not established by the reviewed evidence.
6. The acceptance report's historical test counts are not proof that the current remediation commit passes. Record new evidence against the exact commit actually tested.
7. The migration implementation includes changes beyond frontend presentation (ML generator, paper/backtest, database, runtime and security-related areas). These need a file-by-file purpose/dependency map and independent review where appropriate.

**Decision:** The modern UI is not accepted for cutover. Keep the legacy dashboard as the default and rollback/reference implementation.

## 3. Required work — priority order

### P0-A: Rebuild the parity evidence

- [ ] Audit the legacy dashboard directly: `web/static/index.html`, `web/static/js/`, `web/static/css/`.
- [ ] Audit the modern app directly: `frontend/src/`, `frontend/e2e/`, frontend tests and API client modules.
- [ ] Complete [the parity audit worksheet](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md) for every menu, tab, panel, table action, form, modal, setting and refresh workflow.
- [ ] Change unsupported `COMPLETE` statuses to `PARTIAL`, `LEGACY_ONLY`, `MISSING`, or `BLOCKED`.
- [ ] Do not count a page shell, placeholder, static table, disabled control, legacy link, mocked-only workflow, or unverified endpoint as complete.
- [ ] Reconcile the parity document and acceptance report with the actual implementation and evidence.

### P0-B: Complete Settings & Users

Inspect the actual legacy implementation and enumerate every supported workflow before coding. At minimum, investigate:

- [ ] User listing and detail.
- [ ] Create user.
- [ ] Edit user attributes.
- [ ] Enable/disable user, if supported.
- [ ] Password reset/change/rotation flows.
- [ ] Role assignment and validation.
- [ ] Application and credential settings.
- [ ] Authorized diagnostics and audit information.
- [ ] Session/logout behavior and forced-password-change behavior.

For every workflow, identify the existing backend route, schema, role enforcement, side effects and tests. Reuse supported contracts; do not invent endpoints or broaden permissions as a shortcut.

Implement the missing approved workflows in React with:
- typed API functions and validated response handling;
- loading, empty, success and actionable error states;
- form validation and duplicate-submit protection;
- explicit confirmation for consequential account changes;
- read-after-write refresh from authoritative server state;
- server-side authorization tests for admin, trader and viewer;
- secret-safe logs and screenshots.

If a workflow is not supported by the backend or is intentionally deferred, document that exact gap and do not mark Settings & Users complete.

### P0-C: Resolve the Telegram permission contract

- [ ] Inspect the current FastAPI route and dependencies for Telegram status/test endpoints.
- [ ] Produce an approved permission matrix for admin, trader and viewer.
- [ ] Treat backend authorization as authoritative.
- [ ] Align React control visibility and all migration documents with the approved policy.
- [ ] Add API tests for allowed and denied roles, including checks that unauthorized responses do not reveal chat IDs, tokens or other secrets.
- [ ] Mock the actual Telegram send in automated tests. Never send a real message as part of test execution.

Do not change admin-only behavior to trader access without explicit owner approval and a separate security review.

### P1-A: Correct the cutover and rollback plan

- [ ] Replace unsupported “zero-downtime” language with “controlled, reversible cutover” unless the actual deployment topology proves zero downtime.
- [ ] Remove absolute “100% compatible” and “zero data-loss risk” statements.
- [ ] Document actual process manager behavior, restart/reload requirement, expected interruption, health checks and rollback trigger.
- [ ] Verify SPA deep links, browser refresh, asset paths, cache headers, API route precedence, authentication restoration and session expiry in a non-production environment.
- [ ] Keep the legacy frontend available until owner-approved retirement.

No production cutover or service restart is part of this work order.

### P1-B: Map mixed implementation scope

Create a file-by-file map for non-UI files changed by the modern UI migration implementation. For each file record:
- purpose;
- modern UI feature depending on it;
- API/schema contract affected;
- whether it is necessary for UI parity;
- tests and exact command;
- whether it should be reviewed in a separate PR.

Separate independent ML, paper-trading, backtest, database, migration, runtime and security changes where practical. Do not rewrite merged history; use focused follow-up PRs.

### P1-C: Close frontend functional and quality gaps

For every workflow marked complete:
- [ ] Verify real backend integration; no production-only or hardcoded sample data.
- [ ] Handle 401, 403, 404, 409, validation, server and network errors.
- [ ] Prevent stale asynchronous responses and duplicate submissions.
- [ ] Refresh authoritative state after mutations.
- [ ] Confirm consequential actions before submission.
- [ ] Verify keyboard access, focus visibility/restoration, labels, semantic headings and accessible status/error announcements.
- [ ] Verify responsive layout at phone, tablet and desktop widths.
- [ ] Verify dark/light themes, contrast and persisted preferences.
- [ ] Verify browser refresh and direct navigation to nested routes.
- [ ] Ensure tables have a single usable horizontal scroll region and no document-level horizontal overflow.

## 4. Required test and evidence process

Run checks locally or in an approved isolated test environment. Do not depend on GitHub Actions while the repository's Actions quota is exhausted.

Record a test evidence table with:

| Field | Required value |
|---|---|
| Tested commit | Full 40-character SHA |
| Environment | OS, Python, Node, browser and database type/version |
| Command | Exact command, including working directory |
| Result | Pass/fail and exact count |
| Artifact | Log/report path or CI URL, if available |
| Limitations | Anything mocked, skipped or not executed |

Required checks, where supported by the project:
1. Frontend typecheck.
2. Frontend unit/component tests.
3. Frontend production build.
4. Relevant backend tests.
5. Playwright critical workflows.
6. API authorization tests for admin/trader/viewer.
7. Accessibility and responsive checks.
8. Safe staging/read-only live API verification, if available and explicitly approved.

Use an isolated test database only. Never use the production database for exploratory or mutation tests. Mock Telegram and trading/external side effects. Clearly distinguish code-level tests from staging/live verification.

A previous test count must not be copied forward as a new pass. If a check was not run on the final SHA, say so.

## 5. Suggested PR boundaries

Use small reviewable PRs. Suggested order:

1. **PR A — Evidence correction:** parity inventory, Telegram policy documentation, acceptance report and cutover language.
2. **PR B — Settings & Users parity:** React workflows, API contracts and authorization tests.
3. **PR C — Remaining functional parity:** prioritize P0 workflows from the completed audit; avoid mixing unrelated backend changes.
4. **PR D — Accessibility/responsive/browser fixes:** focused frontend quality changes.
5. **PR E — Final acceptance evidence:** exact-SHA test report and remaining risk register.

If the implementation requires backend changes, keep each PR narrow and explain the contract dependency.

## 6. Definition of done

- [ ] All legacy workflows are inventoried in the worksheet.
- [ ] Every status is supported by code-level evidence and a test or documented manual verification.
- [ ] Settings & Users is genuinely implemented or explicitly listed as a not-yet-accepted exception.
- [ ] Telegram permission policy is consistent across backend, frontend, tests and documentation.
- [ ] Cutover plan makes no unsupported uptime or data-loss guarantees.
- [ ] Non-UI changes have a purpose/dependency/test map.
- [ ] Critical workflows pass on the exact final SHA.
- [ ] Accessibility, responsive behavior, themes and browser refresh are checked.
- [ ] No unresolved P0/P1 issues remain, except exceptions explicitly accepted by the owner.
- [ ] Default route remains legacy until independent review and explicit owner authorization.

## 7. Non-negotiable safety boundaries

- Do not set `SIGNAL_BOT_USE_MODERN_UI=1` in production.
- Do not deploy or restart production services.
- Do not alter production configuration or production database.
- Do not activate/deactivate strategies or change paper/live trading.
- Do not send real Telegram messages.
- Do not perform destructive user/account actions outside isolated tests.
- Do not claim GitHub Actions passed if no run occurred.
- Do not merge your own remediation PRs automatically.

## 8. Required Antigravity handoff

In each PR description, include:
1. What changed and why.
2. Which parity worksheet rows are closed.
3. Which rows remain open and why.
4. Backend endpoints and role policy affected.
5. Exact tests/commands and tested SHA.
6. Known risks and intentionally deferred work.
7. Confirmation that production and external side effects were not touched.

Start with the evidence correction and parity audit. Do not begin another broad visual redesign before the workflow gaps are known.
