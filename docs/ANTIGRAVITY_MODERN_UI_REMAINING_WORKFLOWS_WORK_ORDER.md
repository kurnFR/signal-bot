# Antigravity Work Order: Complete the 10 Remaining Modern UI Workflows

**Repository:** `kurnFR/signal-bot`  
**Base:** `master` after PR #697  
**Baseline merge commit:** `ecaeee4e631162a4abe2a01c1b5f6260c25ccc0f`  
**Work branch:** `docs/modern-ui-remaining-workflows-workorder`  
**Priority:** P1 — close the remaining feature-parity gap without changing production routing.

## 1. Mission

Implement the ten specifically catalogued legacy-only workflows in the modern React + TypeScript dashboard. Preserve all existing modern UI work and all legacy functionality. Do not perform a broad redesign or refactor unrelated modules.

The current acceptance report records **59 workflows: 49 COMPLETE + 10 LEGACY_ONLY**. The ten remaining workflows are:

| ID | View | Workflow | Existing legacy behavior to preserve |
|---|---|---|---|
| PT-07 | Paper Trading | Cancel paper order | Cancel a selected pending simulated order |
| PT-08 | Paper Trading | Manually close position | Close a selected simulated position with a deliberate confirmation |
| PT-09 | Paper Trading | Deploy bot modal | Configure/deploy a paper-trading bot using the existing supported configuration flow |
| PT-10 | Paper Trading | Trip/reset circuit breaker | Trip or reset the paper-trading risk circuit breaker |
| ML-03 | ML Forecast Studio | Launch tournament modal | Configure and launch the existing ML-filter tournament |
| ML-04 | ML Forecast Studio | Deploy ML filter modal | Configure/deploy an eligible ML filter using existing eligibility rules |
| RB-03 | Research Bots | Strategy lifecycle settings update | Update existing RetailBot2 strategy lifecycle settings |
| RB-04 | Research Bots | Stop/hide bot | Stop or hide a supported Research Bot using existing semantics |
| WT-04 | Wallet Tracking | Track/untrack address | Add or remove a tracked wallet address |
| WT-05 | Wallet Tracking | Save wallet alert settings | Persist the existing wallet alert configuration |

These IDs and names are taken from the merged acceptance report. Before coding, inspect the legacy handlers, backend routes, schemas, authorization dependencies, persistence behavior, and existing tests. **Do not infer endpoint names or payload shapes from this work order.** Record the actual contracts in the parity worksheet.

## 2. Non-negotiable boundaries

1. **No production activity.** Do not connect to, query, migrate, seed, or write the production `crypto_signals` database. Do not restart services or deploy.
2. **Test DB only.** Tests that require MariaDB must fail closed unless the active database is exactly the authorized `signal_bot_test`. Never fall back to production. Do not drop/recreate the test database or mutate pre-existing shared users/data. Use unique run-scoped fixtures and exact-ID cleanup.
3. **No outbound Telegram.** Do not send test pings, wallet alerts, bot alerts, or any real Telegram message. Test delivery paths with mocks/fakes and assert no network send occurs.
4. **No live trading.** Do not enable live execution, place exchange orders, or trigger real trading. Paper-trading workflows must remain clearly identified as simulation.
5. **No implicit activation.** UI preview, model training, tournament launch, filter deployment, bot configuration, wallet tracking, or alert settings must not silently activate unrelated services or notifications. Preserve existing enablement gates.
6. **Keep legacy default.** `GET /` must continue serving the legacy dashboard. Modern UI remains at `/modern`. Do not set `SIGNAL_BOT_USE_MODERN_UI=1`, alter production routing, or claim cutover readiness.
7. Preserve backend RBAC as the security boundary. Hiding a control in React is not authorization.
8. Keep existing APIs and data contracts backward compatible unless a documented defect requires a versioned/compatible change.
9. Do not weaken validation, eligibility gates, risk limits, audit logging, idempotency, or error handling to make a UI test pass.
10. GitHub Actions are not available for this work. Do not claim Actions/CI passed. Report local commands and exact tested commit SHAs.

## 3. Phase 0 — Baseline and contract discovery (required before implementation)

Create a feature branch from the current `master` head. Confirm the base SHA and record it.

For each of the ten workflows:

- Locate the legacy HTML/JavaScript handler and capture its user-visible behavior, required fields, defaults, validation, confirmation steps, and success/failure feedback.
- Locate the backend route/service/database operation it invokes.
- Record HTTP method, route, request schema, response schema, error statuses, role requirements, and whether the operation is reversible/idempotent.
- Identify whether it changes persistent configuration, creates an execution record, or changes runtime state.
- Trace all side effects, including scheduler/worker changes, Telegram dispatch, order/position mutations, and model eligibility.
- Identify existing backend tests and add missing contract/security tests before or alongside UI implementation.
- Compare the legacy implementation with the current React panels and client libraries. Reuse established API-client patterns and shared components where appropriate.

Deliver a contract table in `docs/UIX_LEGACY_TO_MODERN_PARITY.md` or the audit worksheet. If a legacy behavior is unsafe, ambiguous, or no longer supported, stop and document the discrepancy rather than silently reproducing it.

## 4. Implementation requirements by workflow

### Paper Trading

**PT-07 — Cancel paper order**
- Add an explicit row-level action only for cancellable pending/open paper orders.
- Show identifying details (order ID, symbol, side, type, quantity/price where available) in the confirmation.
- Prevent duplicate submissions and stale-row actions.
- On success, refresh/reconcile order state from the backend; do not optimistically mark cancelled before server confirmation.
- Handle already-filled, already-cancelled, missing, or non-cancellable orders with clear non-destructive feedback.
- Confirm the route cannot affect exchange/live orders.

**PT-08 — Manually close position**
- Add an explicit close action for eligible paper positions.
- Confirmation must identify symbol, direction, size, and the fact that this is a simulated/paper close.
- Prevent double-close races in the client and rely on backend transactional/idempotent safeguards.
- Refresh positions and trade history only after confirmed success.
- Handle position already closed, stale data, and backend rejection without fabricating a close result.
- Prove the backend path is paper-only.

**PT-09 — Deploy bot modal**
- Recreate the legacy configuration fields and validation only after tracing the source handler and backend contract.
- Show strategy, symbol/market, risk/execution parameters, and paper/simulation mode clearly.
- Require a deliberate confirmation before submission; prevent duplicate deployment requests.
- Show server-returned deployment/configuration identity and status.
- Do not start a live bot or enable real exchange execution. If the legacy endpoint can do either, add/retain an explicit server-side paper-only guard for this workflow; do not rely on UI labels.

**PT-10 — Trip/reset circuit breaker**
- Expose current breaker state and the supported trip/reset actions with role-aware controls.
- Reset must use a confirmation that explains the relevant risk implications and any server-enforced preconditions.
- Distinguish “request accepted” from verified resulting state; refresh the canonical status after mutation.
- Preserve server-side authorization, audit trail, and risk policy. Never bypass a tripped breaker or reset it automatically.

### ML Forecast Studio

**ML-03 — Launch tournament modal**
- Recreate the legacy tournament inputs, defaults, validation, and run summary based on the actual backend schema.
- Show that this launches a research/evaluation job, not paper/live execution.
- Prevent duplicate launches; surface job/run ID and queued/running/failed/completed state using the existing job contract.
- Preserve train/validation/test separation and all existing anti-leakage rules. Never use holdout/test results to select tournament policy.
- Handle validation rejection and worker/job failure explicitly.

**ML-04 — Deploy ML filter modal**
- Show model identity, source experiment, eligibility status, threshold/configuration, and deployment target.
- Enforce the existing backend eligibility gate; an ineligible artifact must not be deployable by changing client payloads.
- Require confirmation and explain whether the operation changes paper-trading configuration, a research-only setting, or another target. The UI must state the exact target.
- Deployment must not enable Telegram or trading automatically unless that is already an explicit, separately authorized setting in the existing product contract.
- Refresh the canonical deployed-filter state after success. Reject stale model/experiment references safely.

### Research Bots

**RB-03 — Strategy lifecycle settings update**
- Recreate supported lifecycle controls and preserve the current strategy-level configuration semantics.
- Clearly separate execution enablement from Telegram alert enablement.
- Validate values at both client and server; show persisted readback after save.
- Avoid silently enabling strategies or alerts when editing unrelated settings.
- Preserve per-strategy controls and do not introduce hardcoded strategy allowlists in the frontend.

**RB-04 — Stop/hide bot**
- Determine the exact legacy distinction between “stop” and “hide”; do not conflate a runtime stop with a presentation-only hide.
- Use separate actions/labels if the backend semantics differ.
- Confirm destructive or execution-affecting stop operations; make hide reversible if the existing behavior supports it.
- Refresh lifecycle state and relevant positions/trades after success.
- A stop action must not delete historical records or mutate unrelated strategies.

### Wallet Tracking

**WT-04 — Track/untrack address**
- Add validated address entry and network selection using the supported chain list from the backend, not a frontend-invented list.
- Validate format and chain/address compatibility using established server rules; never treat a syntactically valid address as proof of ownership.
- Show duplicate, unsupported-chain, and invalid-address errors clearly.
- Untrack must identify the exact address/network pair and require confirmation if removal has material consequences.
- Preserve historical transfers/alerts unless the existing backend contract explicitly says otherwise. Do not delete unrelated wallet data.

**WT-05 — Save wallet alert settings**
- Recreate the existing alert controls and thresholds from the legacy flow and backend schema.
- Distinguish tracking configuration from alert delivery configuration.
- Show current persisted values, validate thresholds, and save with duplicate-submit protection.
- Default to the existing safe behavior; never silently turn on outbound Telegram delivery.
- Test the dispatch gate with a mocked sender and assert zero real outbound requests.

## 5. Shared UI/UX and accessibility requirements

- Use existing React/TypeScript architecture and shared client modules; avoid duplicate API implementations.
- Every mutation needs loading, success, validation-error, server-error, and stale-data states.
- Disable only the affected action while its request is pending; prevent duplicate submissions.
- Use accessible dialogs for destructive/irreversible actions: accessible name/description, initial focus, keyboard focus management, Escape/Cancel behavior where safe, and focus restoration to the invoking control.
- Do not use blocking browser `alert()` for routine errors. Use inline feedback with appropriate live-region semantics.
- Ensure semantic labels, keyboard access, visible focus, table action names, and screen-reader status announcements.
- Preserve dark/light themes and responsive behavior at mobile (375px), tablet (768px), and desktop (1440px).
- Ensure empty, loading, partial, and failed API responses do not produce misleading success states.
- Keep UI state reconciled with server state after every successful mutation.

## 6. Role and authorization matrix

Use the actual backend policy as source of truth. For every workflow, document and test which of `viewer`, `trader`, and `admin` may read and mutate.

- Unauthorized users must receive server-side 401/403 as appropriate even when calling the API directly.
- Frontend must hide or disable controls consistently with the documented policy.
- Tests must include both allowed and denied roles.
- Do not broaden privileges to make modern UI behavior match the legacy UI.
- Confirm whether the action is available only to admin or trader/admin from the existing route dependencies; do not assume.

## 7. Required automated test coverage

Add/update tests for all ten workflows.

### Frontend component/client tests
For each workflow cover:
- initial rendering and correct role visibility;
- valid submission and exact payload contract;
- client validation and malformed response handling;
- loading state and duplicate-submit prevention;
- success state and server-state refresh/readback;
- 4xx/5xx/network failure behavior;
- stale or already-mutated entity behavior;
- confirmation cancel/Escape/focus restoration for destructive actions;
- dark/light and narrow viewport behavior where the workflow adds layout complexity.

### Backend contract/security tests
For each mutation cover:
- allowed role succeeds;
- disallowed role is rejected server-side;
- invalid/missing fields are rejected;
- entity not found / invalid lifecycle state is handled safely;
- repeated request / concurrency behavior does not create duplicate or contradictory effects;
- audit/persistence/runtime side effects match the legacy contract;
- no unintended Telegram send or live-trading side effect occurs.

### Integration/browser tests
- Keep the existing 28 mocked-API Playwright tests and extend them for the new UI interactions.
- Add staging-browser acceptance for the ten workflows against the authorized staging backend and test database only, using unique fixtures.
- Mock Telegram/external dispatch and assert no real outbound message is sent.
- Assert the paper workflows cannot reach live/exchange execution.
- Keep the staging child-process identity, PID verification, bounded termination, and fixture-preservation safeguards intact.
- Do not describe mocked API browser tests as live backend integration tests.

## 8. Documentation and parity accounting

Update all relevant documents consistently:
- `docs/UIX_LEGACY_TO_MODERN_PARITY.md`
- `docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`
- `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`
- `docs/UIX_CUTOVER_PLAN.md` only if its factual prerequisites/status need updating; do not authorize cutover.

For every workflow, record:
- legacy behavior and source location;
- modern component/action;
- API method/route and request/response contract;
- role permissions;
- tests and exact tested commit SHA;
- known limitations and safety boundaries.

Only change a workflow from `LEGACY_ONLY` to `COMPLETE` when the control, backend contract, RBAC, error paths, persistence/readback, and relevant tests are all implemented and evidenced. Do not classify based only on a rendered button or mocked API test.

Reconcile totals only after all ten have been individually accepted. If all pass, the expected arithmetic becomes 59 complete workflows and 0 legacy-only; otherwise report the exact remaining status. Keep view-level classifications consistent with workflow-level status.

## 9. Validation commands and evidence

Run focused tests first, then the complete relevant suites. At minimum report:

- Frontend: `cd frontend && npm test -- --run`
- Frontend typecheck: `cd frontend && npm run typecheck`
- Frontend build: `cd frontend && npm run build`
- Backend focused tests for each changed route/service.
- Full backend suite: `python3 -m pytest -q`
- MariaDB integration/security tests using only the authorized test DB.
- Existing staging lifecycle regressions: `node tests/browser/test_staging_lifecycle_regressions.cjs`
- Staging browser acceptance suite and its exact role/view/workflow coverage.
- `git diff --check`

For each command include:
- exact commit SHA tested;
- exact command and environment;
- pass/fail/skip counts and duration;
- whether database/external services were mocked or real staging;
- any warning or skipped test relevant to acceptance.

No test claim may be copied forward to a later commit unless that exact commit was tested or the report explicitly identifies the tested ancestor and proves the intervening diff does not affect that evidence.

## 10. Delivery and review protocol

1. Implement on a dedicated feature branch based on the current `master`.
2. Keep the change focused on these ten workflows plus necessary tests/docs.
3. Do not merge the PR.
4. Push the branch and open a PR to `master`.
5. PR description must list each workflow ID, implementation files, backend routes, role matrix, test evidence, and known limitations.
6. Include a concise risk review covering paper-only enforcement, ML eligibility/data leakage, bot lifecycle side effects, wallet identity/network validation, and Telegram dispatch gating.
7. Provide a file-by-file summary and exact HEAD SHA.
8. Wait for independent review before merge or production cutover.

## 11. Definition of done

The work is ready for independent review only when:
- all ten workflow IDs have implementation or a clearly documented blocker;
- legacy behavior and backend contracts are traced, not guessed;
- role permissions are enforced server-side and tested;
- each mutation has correct confirmation, validation, loading/error, duplicate-submit, and server readback behavior;
- no production database, Telegram, exchange, or live-trading side effect occurred;
- paper-only and research-only boundaries are demonstrated by tests;
- wallet and bot settings do not silently enable notifications or execution;
- responsive/accessibility coverage is added;
- tests and builds pass with evidence tied to exact SHAs;
- parity docs agree with code and tests;
- legacy dashboard remains the default and modern remains preview-only.

**Do not declare full modern UI parity or production cutover readiness merely because the ten controls have been added. Acceptance requires verified end-to-end behavior and safety boundaries.**
