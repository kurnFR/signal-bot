# Signal Bot — Architecture, Security & UI/UX Review

**Review date:** 2026-10-31  
**Repository:** `kurnFR/signal-bot`  
**Review context:** GitHub review of the UI/UX migration and final acceptance work, including PR #604  
**Status:** Findings and recommendations; remediation not yet completed.

> This document records the review conclusions and proposed work. It is not a production approval, security certification, or evidence that every finding has already been reproduced in a live environment. Re-verify each finding against the current source and tests before implementation or closure.

## 1. Executive assessment

Signal Bot has made substantial progress toward a more maintainable quantitative trading platform. The React/TypeScript/Vite frontend preview, API contract reconciliation, responsive layouts, themes, and automated test coverage are positive improvements.

**Recommendation: continue the migration and reliability work, but do not treat the React migration as production-ready yet.** The next milestone should prioritize security, predictable API side effects, operational observability, and end-to-end acceptance rather than adding more visual features.

The current migration model remains:

- Legacy production frontend: `web/static/`
- React frontend: preview / migration candidate
- Production cutover: not approved by this review
- Repository workflow: GitHub-only changes; no live deployment unless explicitly authorized
- Merge policy: merge only after required checks are green and acceptance gates are satisfied

## 2. Review snapshot and evidence boundaries

The previous review recorded the following project evidence:

| Area | Recorded state | Interpretation |
|---|---|---|
| Frontend | React + TypeScript + Vite preview; 11 navigation views; light/dark themes | Positive migration progress |
| Frontend tests | 30 test files, 191 tests, TypeScript checks, production build, 28 Chromium Playwright tests | Recorded as passing in project evidence; rerun on the current head |
| Backend tests | 745 passed, 12 skipped in a full run; skipped MariaDB tests reportedly passed separately | Recorded evidence; verify current CI and integration environment |
| API inventory | 81 static API method/path identities matched 81 runtime route entries | Static parity is useful but does not prove live response compatibility |
| GitHub Actions | Four workflow runs at the reviewed PR head were failed | Root causes were not established; inspect logs before attributing to code |
| Production acceptance | Live API parity, manual accessibility, isolated mutation/RBAC verification, and owner sign-off remain outstanding | Do not cut over until completed |

### Reviewed pull request

- PR: [#604 — Final migration acceptance and authentication contracts](https://github.com/kurnFR/signal-bot/pull/604)
- Branch: `feat/uix-final-acceptance-pass`
- Reviewed head: `b7ed4ba87b8d6d35ccbc09778476ef82c1feabec`
- Base: `master`
- State at review: open, draft, unmerged

The PR head and workflow state can change. Always re-check the live PR before relying on this snapshot.

## 3. Highest-priority findings

### P0 — Security and reliability

These findings require source-level confirmation and focused regression tests before closure.

#### 3.1 Token revocation must fail closed

The prior review identified a potential fail-open path where a revocation-table lookup failure could be interpreted as “not revoked.”

**Risk:** a revoked token may remain accepted when revocation storage is unavailable or partially failing.

**Recommended remediation:**
- Fail closed when token revocation status cannot be established.
- Treat revocation storage as a required authentication dependency.
- Add tests for database errors during revocation checks.
- Verify logout and revocation behavior across multiple application workers.
- Ensure the client is not told that logout/revocation succeeded when server-side revocation failed.

#### 3.2 Audit logging must be durable and observable

The prior review identified audit logging that may swallow database errors.

**Risk:** sensitive actions may succeed without a durable audit record, weakening incident investigation and accountability.

**Recommended remediation:**
- Define which security-sensitive actions require audit records.
- Make audit persistence failures observable and actionable.
- Use a transaction or durable outbox where appropriate.
- Add retry/reconciliation behavior for transient persistence failures.
- Add tests proving that critical operations cannot silently lose required audit events.

#### 3.3 Login rate limiting must be shared across workers

The prior review identified a process-local fallback when durable rate limiting is unavailable.

**Risk:** each worker may maintain independent counters, weakening brute-force protection in a multi-worker deployment.

**Recommended remediation:**
- Use a shared rate-limit backend.
- Define and expose degraded-protection behavior when that backend is unavailable.
- Test concurrent attempts across multiple workers.
- Ensure the fallback cannot silently provide a materially weaker security guarantee.

### P1 — API architecture and data integrity

| Finding to verify | Risk / impact | Recommended action |
|---|---|---|
| Research GET route may initialize database schema | Read requests can require DDL permissions and cause writes | Move schema setup into migrations or explicit startup initialization |
| ML GET route may create directories | GET is not fully read-only and may mutate filesystem state | Initialize required paths during controlled startup |
| User deletion may lack a corresponding audit event | Incomplete administrative history | Add and test deletion audit events |
| Admin password creation/reset may use a weaker policy than user password change | Inconsistent account security | Centralize password policy and validation |
| User API response may omit a documented field | Frontend/backend contract drift | Reconcile schemas, serializers, and API tests |
| Master Data inputs may lack backend allowlists | Direct API clients can bypass UI validation | Enforce server-side allowlists and constraints |
| Job errors may expose raw exception details | Internal information disclosure | Return sanitized error codes; retain details in protected logs |
| Strategy AI requests may lack explicit size limits | Unbounded prompt/history and provider usage | Enforce request-size, history-length, and timeout limits |

**Architecture rule:** GET endpoints should not perform consequential database or filesystem initialization. Use migrations, controlled startup routines, or explicit write operations instead.

## 4. UI/UX and product improvements

The React migration is a sound direction. Continue improving the product without changing existing trading behavior unintentionally.

### 4.1 Executive dashboard

Recommended:
- Configurable dashboard widgets and saved layouts.
- Clear separation of market activity, portfolio metrics, strategy performance, and system health.
- Consistent loading, empty, stale-data, and error states.
- Visible timestamps and data freshness indicators.

### 4.2 Market Screener V2

Recommended:
- Saved filters and reusable screener presets.
- Column visibility and ordering.
- Predictable sorting and pagination.
- Explainable alert/signal reasons.
- Clear distinction between no results, loading, stale data, and API failure.

### 4.3 Paper Trading and Wallet Tracking

Recommended:
- Unified exposure and risk overview across strategies and monitored wallets.
- Position and trade history with clear source attribution.
- Data freshness and worker status.
- Explicit separation between simulated/paper activity and live execution.
- No new live-trading or Telegram side effects without explicit user authorization.

### 4.4 ML Studio and Backtest Studio

Recommended:
- Consistent experiment lifecycle: dataset → training → validation → approval → deployment → monitoring → rollback.
- Preserve artifact provenance, configuration, metrics, and dataset identity.
- Make promotion and deployment explicit, permission-controlled actions.
- Prevent unvalidated or stale artifacts from being activated.

These are proposed design directions, not claims that the features already exist.

## 5. Recommended feature roadmap

Prioritize improvements that strengthen trading decisions and operational reliability.

| Priority | Feature | Expected value |
|---|---|---|
| P1 | Centralized Risk Dashboard | One place to understand exposure and risk |
| P1 | Strategy Performance Comparison | Compare strategies using consistent metrics |
| P1 | Alert Quality Analytics | Measure signal effectiveness and outcomes |
| P1 | System Health Center | Detect disconnected workers, failures, and stale data |
| P2 | Strategy Lifecycle Management | Control research, paper trading, promotion, and retirement |
| P2 | Unified Event Timeline | Trace signals, trades, wallet activity, and system events |
| P2 | Dashboard Customization | Personalize information without code changes |
| P2 | API/Worker Observability | Diagnose failures, latency, and bottlenecks |

### 5.1 Strategy Lifecycle Management Center

A high-value product improvement is a unified lifecycle for strategies:

1. **Research / Strategy Library** — definitions, versions, and experiments.
2. **Backtesting and Validation** — historical performance and out-of-sample validation.
3. **Paper Trading** — forward performance and risk monitoring.
4. **Approved Strategy** — explicit, permission-controlled promotion and alert activation.
5. **Monitoring / Retirement** — deterioration detection, stop, rollback, and audit history.

This should connect existing Backtest, Strategy Library, RetailBot, ML, and Paper Trading capabilities without creating separate or inconsistent promotion mechanisms.

Promotion must not be based on a single win-rate or backtest metric. Require out-of-sample evidence, trade-count thresholds, fees/slippage assumptions, drawdown limits, and forward paper-trading criteria. Keep alert activation separate from strategy evaluation.

## 6. PR #604 acceptance improvements

The previous review highlighted these specific areas:

1. **Token normalization:** frontend validation checks `token.trim()` but may store the original token. Reject surrounding whitespace or normalize consistently before storage and use.
2. **Authentication failure handling:** distinguish invalid sessions from temporary service/network failures. Preserve recoverable session state while providing a clear retry path; only clear credentials when invalidity is established.
3. **Responsive testing:** page-level horizontal-overflow checks do not prove every table, dialog, chart, and interactive control is usable.
4. **Browser coverage:** Chromium-only Playwright coverage should be supplemented with Firefox and WebKit where supported.
5. **Accessibility:** automated tests do not replace keyboard-only, screen-reader, contrast, focus-management, and zoom acceptance.
6. **Documentation consistency:** consolidate historical checkpoints into one authoritative current acceptance status. Clearly label historical test results versus current-head results.

## 7. Execution roadmap

### Phase 1 — Security and backend reliability

- [ ] Verify and fix token revocation failure behavior.
- [ ] Make critical audit events durable and observable.
- [ ] Replace weak process-local rate-limit fallback with shared protection.
- [ ] Add regression tests for database/backend failures.
- [ ] Verify logout and revocation across multiple workers.

### Phase 2 — API contract and integration acceptance

- [ ] Verify all React views against actual FastAPI responses.
- [ ] Validate response schemas and error contracts.
- [ ] Remove consequential side effects from GET endpoints.
- [ ] Verify role boundaries and authorization server-side.
- [ ] Test mutations against an isolated test database.
- [ ] Verify worker/job behavior without live trading or Telegram side effects.

### Phase 3 — Frontend quality

- [ ] Run frontend tests, type checks, and production build on the current PR head.
- [ ] Add component-level responsive tests for tables, dialogs, charts, and controls.
- [ ] Add Firefox and WebKit coverage where practical.
- [ ] Complete keyboard, focus, screen-reader, contrast, and zoom checks.
- [ ] Verify loading, empty, stale, and error states.

### Phase 4 — Trading intelligence improvements

- [ ] Design Strategy Lifecycle Management.
- [ ] Design Centralized Risk Dashboard.
- [ ] Add Alert Quality Analytics.
- [ ] Add Unified Event Timeline.
- [ ] Add worker/API observability.

### Phase 5 — Migration acceptance

- [ ] Resolve all high-priority findings.
- [ ] Re-run required CI and integration suites.
- [ ] Confirm live API parity and role-based access.
- [ ] Complete manual accessibility and responsive acceptance.
- [ ] Obtain explicit owner sign-off.
- [ ] Prepare a separate production migration and rollback plan.

## 8. Required engineering guardrails

- GitHub-only changes unless live deployment is explicitly requested.
- Do not modify production data or production secrets during tests.
- Use isolated test databases, such as `signal_bot_test`, for integration work.
- Preserve the existing `crypto_signals` production database.
- Do not trigger live trades, live workers, or Telegram notifications during acceptance tests.
- Do not remove or silently alter existing features while modernizing the UI.
- Add regression tests before or alongside bug fixes.
- Re-run relevant tests after every meaningful change.
- Merge only when required checks are green and the acceptance criteria are satisfied.
- Keep production cutover separate from code merge and require explicit authorization.

## 9. Immediate next action

Start a dedicated **P0/P1 remediation pass**:

1. Inspect the current source and tests for token revocation, audit persistence, and login rate limiting.
2. Reproduce each confirmed issue with focused tests.
3. Implement the smallest safe fixes and regression tests.
4. Address GET side effects and API contract gaps.
5. Re-run the relevant backend, frontend, MariaDB integration, and RetailBot regression suites.
6. Inspect all failing GitHub Actions logs and classify failures as code, environment, or workflow problems.
7. Update this document with evidence, commit/PR references, and explicit status for every finding.

**Final recommendation:** continue from PR #604 as an acceptance and reliability milestone. Do not declare production readiness or perform a production frontend cutover until the security, API, integration, accessibility, and owner-approval gates are closed.


---

## 10. Re-review addendum — 2026-10-31 (current master vs PR #604)

**This section supersedes the original “remediation not yet completed” status above.** The original review described the PR branch as it existed before the latest master commits. The current source was re-read on 2026-10-31.

### 10.1 Repository and PR state

- Current `master`: `aa837012cecfc6d10e0d25e049f64439ec278fbd`
- PR #604 head: `c85b49940fc7b3d971488d986efc86d5ce6a6891`
- PR #604 remains open and draft.
- Current master contains six commits beyond the PR's recorded base `eeaf8843d140701f88623a22aa925353e7e2e2d2`.
- A compare of current master against the PR head reports the histories have diverged (master is six commits ahead; the PR branch has 46 commits not present in current master). **Do not merge PR #604 as-is.** First reconcile its unique commits and current-master changes; check for duplicate, obsolete, or conflicting work.
- The latest master commit is `aa837012`, titled “fix(security): resolve P0/P1 review findings for token revocation, durable audit logging, and GET purity”. Its diff includes meaningful fixes, but the commit title overstates closure in at least two areas noted below.

### 10.2 Finding disposition on current master

| Previous finding | Current disposition | Evidence / remaining gap |
|---|---|---|
| Token revocation lookup failed open | **Fixed in source; focused tests added; hosted CI not green** | `is_token_revoked()` now returns revoked on storage errors. `revoke_token()` raises if persistence fails and logout returns HTTP 503 rather than reporting success. Multi-worker integration verification is still required. |
| Revocation persistence failure could be reported as logout success | **Fixed in source; focused test added** | Logout catches revocation persistence errors and returns 503. Verify client retry/session behavior and real multi-worker behavior. |
| Audit writes silently swallowed | **Partially fixed; still open** | `log_audit()` now returns success/failure and supports `raise_on_error`; user deletion writes the audit row in the same transaction as deletion. However, no production call site was found using `raise_on_error=True`. Login, logout, password change, user create/reset/update and other callers still use the default best-effort behavior or write after the main transaction. A failed audit write can therefore still be silently non-fatal for important actions. |
| Login throttling falls back to process-local memory | **Still open — P0/P1 security gap** | Current master still falls back to `_login_attempts` when durable DB rate-limit operations fail. The new health fields are not updated by those fallback paths, and the added test checks only that health keys exist; it does not prove cross-worker throttling or degraded-state accuracy. |
| Research GET initializes lifecycle schema | **Fixed in source; test added** | DDL/commit removed from read helpers; table initialization moved to explicit startup initialization. Verify startup failure is surfaced and migration ordering is deterministic. |
| ML list GET creates artifact directories | **Fixed in source; tests added** | Experiment/model/RL list handlers now return empty collections when directories do not exist instead of creating them. |
| User deletion lacks audit history | **Fixed in source; test added** | Delete and audit insert share one DB transaction. |
| Admin create/reset password policy weaker than self-service | **Fixed in source; tests added** | Admin create and reset now call the common password validator against the target username. |
| GET /api/users omits `must_change_password` | **Fixed in source; test added** | Query and response now include the boolean field. |
| Master Data market/timeframe validation | **Partially fixed; tests added** | Market and timeframe are validated server-side and symbol length is bounded. Symbol syntax/allowed exchange symbol validation should be strengthened using trusted exchange metadata or a strict symbol contract. |
| Job exception details exposed to readers | **Partially fixed** | Backfill/feature job paths in the latest diff replace raw exception text with generic messages. Audit every job type and persisted historical job message before calling this closed. |
| Strategy AI prompt/history unbounded | **Partially fixed** | Prompt length is capped and history count is capped. Individual history message sizes and `currentConfig` size/depth are not yet bounded; add total request-byte and per-field limits. |
| Frontend token whitespace normalization | **Fixed in source** | API token read trims whitespace and login stores the trimmed token. Add/retain regression tests for persisted legacy tokens with whitespace. |

**Important distinction:** “Fixed in source” means the inspected current-master implementation contains the intended change. It does not mean the full acceptance suite passed, production behavior was verified, or the risk is certified closed.

### 10.3 Current CI and release gate

The latest four GitHub Actions runs on current master commit `aa837012` are all reported as failed:

- [Test](https://github.com/kurnFR/signal-bot/actions/runs/41186444819)
- [RetailBot accounting regression](https://github.com/kurnFR/signal-bot/actions/runs/41186444831)
- [tests](https://github.com/kurnFR/signal-bot/actions/runs/41186444805)
- [Frontend foundation](https://github.com/kurnFR/signal-bot/actions/runs/41186444811)

The available job metadata exposes job-level failure but no usable step-level diagnostics. The cause is **unknown**; do not label it an application regression, quota issue, or environment issue without logs. Current master is therefore **not green and not merge/release-ready**.

The new focused security and API-purity tests are useful, but they are not a substitute for green full CI, MariaDB integration, multi-worker tests, and real-server acceptance.

### 10.4 Remaining P0/P1 remediation

1. **Replace rate-limit fallback with a deliberate shared-backend failure policy.** Do not silently downgrade to per-process memory. If the shared store is unavailable, return a controlled service-unavailable response for login or use a separately designed shared backend. Expose accurate degraded health state and test two independent app workers against the same store.
2. **Make required audit events part of the protected transaction.** Use `log_audit_cursor()` with the business transaction for DB-backed actions; for cross-system actions, use a durable outbox. Explicitly define which events may be best-effort. Add failure-injection tests proving critical actions do not succeed without their required audit record.
3. **Complete request bounds.** Bound total AI request size, each history message, history content, and `currentConfig`; constrain Master Data symbols and ML experiment identifiers/parameters at the API boundary.
4. **Finish operational job safety.** Ensure every public job status/message is sanitized, internal exception detail stays in protected logs, jobs survive process restarts, duplicate submissions are controlled, and job ownership/authorization is enforced.
5. **Validate security behavior under multiple workers.** Test login throttling, logout/revocation, role changes, disabled users, and session invalidation with separate worker processes—not only mocks or one TestClient process.

### 10.5 Professional / commercial-readiness roadmap

A polished dashboard is not yet a sellable SaaS product. Before charging customers, Signal Bot needs clear product boundaries, safe operations, customer isolation, and supportable releases.

#### P0 — Trust and capital protection
- Central risk policy: max exposure, max open positions, per-strategy allocation, max daily loss, drawdown stop, correlation/concentration checks, and an emergency pause.
- Separate research, paper trading, and live execution with unmistakable labels and permission boundaries.
- Market-data freshness checks, stale-price blocking, exchange/API outage handling, idempotent order processing, duplicate-alert prevention, and recovery after restart.
- Backtest integrity: out-of-sample and walk-forward validation, realistic fees/slippage, no look-ahead leakage, survivorship/data-quality checks, and reproducible datasets/configuration.
- Explicit user approval for any future live-trading capability. No profitability guarantees or misleading “AI accuracy” claims.

#### P1 — SaaS foundations
- Decide deployment model: single-tenant self-hosted first, or multi-tenant hosted SaaS. Do not claim multi-tenancy until database, cache, files, jobs, wallets, secrets, and API authorization are tenant-isolated.
- Customer onboarding wizard: connect read-only exchange keys first, configure markets/timeframes, choose screener presets, test Telegram, and explain paper-vs-live modes.
- Secure credential vault/encryption, key rotation, least privilege, secret redaction, optional MFA, session/device management, and a documented recovery flow.
- Backup/restore and migration tooling with tested rollback; health checks, structured logs, metrics, alerting, and operational runbooks.
- Versioned API contracts, release notes, upgrade checks, support diagnostics, and clear retention/export/delete controls.

#### P2 — Paid-user experience
- Role-aware workspace, saved screener presets, custom columns/layouts, watchlists, and exportable performance reports.
- Strategy comparison with trade count, expectancy, profit factor, drawdown, exposure, fees/slippage, and out-of-sample/forward-paper results—not win rate alone.
- Alert-quality analytics that connect each alert to subsequent price movement and strategy outcome, with deduplication and configurable delivery policies.
- Unified event timeline for signals, wallet activity, paper trades, strategy changes, jobs, and admin actions.
- Guided empty states, searchable help, onboarding checklist, in-app diagnostics, clear timestamps/data freshness, and accessible responsive components.

#### P3 — Commercial operations
- Define buyer persona and pricing metric (per user, workspace, monitored wallet, or feature tier); avoid pricing until infrastructure cost and data-provider constraints are understood.
- Terms, privacy policy, risk disclosures, support/SLA boundaries, incident response, data retention, and jurisdiction-specific legal review.
- Market-data/exchange API terms and redistribution rights must be reviewed before offering customer-facing market data.
- Customer acceptance tests, staging environment, release/rollback process, and a documented support escalation path.

### 10.6 Recommended next sequence

1. Fix shared rate-limit degradation behavior and add multi-worker tests.
2. Make critical audit events transactionally durable; enumerate every audit call site and classify its required guarantee.
3. Finish AI/ML/Master Data request bounds and job-status redaction.
4. Diagnose the failed master GitHub Actions runs from actual step logs; rerun all required checks until green.
5. Reconcile PR #604 with current master; do not merge the stale/diverged branch wholesale.
6. Complete staging API/RBAC/mutation acceptance, accessibility and browser matrix, backup/restore rehearsal, and owner sign-off.
7. Only then prioritize paid-product onboarding, workspace configuration, analytics, packaging, and commercial/legal readiness.

**Current decision:** several previous P1 findings have been corrected in current master, but shared login rate limiting and complete audit durability remain open. All current-master hosted workflows are failing, the PR branch has diverged, and production acceptance is incomplete. Keep React migration preview-only; no deployment or live-trading authorization is implied.

---

## 11. Master execution tracker — living checklist

**Tracking rule:** this is the operational checklist for the next engineering passes. Update the status and evidence in this section after each PR/commit. Do not mark an item complete from code inspection alone if it requires a test, CI run, staging verification, or owner decision.

### Status legend

- [ ] Not started
- [~] In progress / implemented but evidence incomplete
- [x] Complete with evidence recorded
- [!] Blocked
- [—] Deferred by product decision

### 11.1 Immediate release blockers

- [ ] **CI-01 — Diagnose current master workflow failures.**
  - [ ] Obtain failing step logs for Test.
  - [ ] Obtain failing step logs for RetailBot accounting regression.
  - [ ] Obtain failing step logs for tests.
  - [ ] Obtain failing step logs for Frontend foundation.
  - [ ] Classify each root cause as code, dependency, environment, workflow configuration, or external service.
  - [ ] Fix the root cause on a dedicated branch and rerun the exact failed jobs.
  - [ ] Run the complete required suite; record run URLs and commit SHA.
  - Evidence baseline: master aa837012; run IDs listed in §10.3.
- [ ] **PR-01 — Reconcile PR #604 with current master.**
  - [ ] Review its 46 unique commits and classify each as required, already superseded, obsolete, or conflicting.
  - [ ] Rebase/cherry-pick only the required work onto a fresh branch from current master.
  - [ ] Resolve conflicts and inspect the final diff for unintended changes.
  - [ ] Run frontend, backend, API contract, and integration acceptance on the reconciled branch.
  - [ ] Close or supersede the stale draft only after its useful work is accounted for.
  - Guardrail: do not merge PR #604 as-is.

### 11.2 P0 security and reliability

- [~] **SEC-01 — Token revocation fails closed.**
  - [x] Source behavior changed to reject tokens when revocation storage cannot be checked.
  - [x] Logout reports failure when persistent revocation cannot be written.
  - [x] Focused failure-injection tests added.
  - [ ] Add multi-process/multi-worker integration test against shared MariaDB.
  - [ ] Verify token revocation after restart and from a different worker.
  - [ ] Verify frontend behavior for logout HTTP 503 and safe retry.
  - Closure evidence required: focused tests + multi-worker integration + green CI.
- [ ] **SEC-02 — Shared login rate limiting (highest-priority open security item).**
  - [ ] Trace every durable-store error path and every process-local fallback.
  - [ ] Decide and document fail-closed behavior when shared storage is unavailable (recommended: controlled 503; do not silently weaken protection).
  - [ ] Ensure all application workers use the same shared counters and lockout window.
  - [ ] Ensure health state accurately reports normal/degraded/unavailable mode.
  - [ ] Add tests for store timeout, connection failure, malformed response, recovery, and concurrent requests.
  - [ ] Add two-worker integration test proving a threshold reached on worker A is enforced on worker B.
  - [ ] Verify no secrets, passwords, or raw credentials enter logs/metrics.
  - Closure evidence required: multi-worker test + operational health signal + green CI.
- [~] **SEC-03 — Durable audit events.**
  - [x] Audit helper now exposes failure and supports raising on error.
  - [x] User deletion writes its audit record in the same DB transaction.
  - [ ] Inventory all audit call sites and label each event mandatory or best-effort.
  - [ ] Make login/security-sensitive/admin mutations fail safely if a mandatory audit record cannot be persisted.
  - [ ] Use the same transaction via cursor helper for DB-backed operations.
  - [ ] Use a durable outbox for events that cross transaction boundaries.
  - [ ] Add failure-injection tests for user create/update/delete, password reset/change, role/activation changes, login failures, logout, and token revocation.
  - [ ] Confirm audit records contain actor, action, target, timestamp, outcome, and safe metadata.
  - Closure evidence required: call-site matrix + atomicity/failure tests + green CI.
- [ ] **SEC-04 — Multi-worker authorization acceptance.**
  - [ ] Verify disabled-user access is rejected immediately.
  - [ ] Verify role changes invalidate or update existing authorization as designed.
  - [ ] Verify revoked sessions are rejected across workers.
  - [ ] Verify admin-only APIs reject trader/viewer roles server-side.
  - [ ] Verify session expiry and clock-skew behavior.
  - [ ] Record test results using isolated test data only.

### 11.3 P1 API, validation, and operational safety

- [~] **API-01 — GET endpoint purity.**
  - [x] Research read helpers no longer create lifecycle schema.
  - [x] ML list endpoints no longer create missing directories.
  - [ ] Confirm lifecycle schema is installed through a versioned migration or controlled startup migration.
  - [ ] Make startup schema failure visible and prevent a misleading healthy state.
  - [ ] Scan all GET handlers for DDL, mkdir, writes, commits, worker starts, or hidden network mutations.
  - [ ] Add route-level regression coverage for any remaining side effects.
- [~] **API-02 — Request validation and resource limits.**
  - [x] Master Data market/timeframe allowlists added; symbol length bounded.
  - [x] Strategy AI prompt length and history count bounded.
  - [ ] Validate symbols against a strict supported-symbol contract or trusted exchange metadata.
  - [ ] Bound each history message length and total serialized request bytes.
  - [ ] Bound currentConfig size, nesting depth, and allowed keys.
  - [ ] Validate ML experiment IDs, artifact paths, and training parameters.
  - [ ] Add request timeout and concurrency limits for expensive AI/ML operations.
  - [ ] Test oversized, malformed, unknown, and adversarial payloads.
- [~] **OPS-01 — Job status and lifecycle safety.**
  - [x] Some backfill/feature job errors now return sanitized messages.
  - [ ] Audit every job type for raw exception leakage.
  - [ ] Ensure protected logs retain stack traces while public APIs expose safe error codes.
  - [ ] Verify job ownership and role authorization on create/read/cancel.
  - [ ] Prevent duplicate submissions and enforce per-user/system concurrency limits.
  - [ ] Persist job state and verify restart recovery.
  - [ ] Add timeout, cancellation, retry, and terminal-state tests.
- [ ] **OPS-02 — Production operational baseline.**
  - [ ] Add readiness/liveness checks that test real dependencies without mutating state.
  - [ ] Add structured logs with request/job correlation IDs and secret redaction.
  - [ ] Add metrics for API latency/errors, DB pool saturation, queue depth, worker heartbeat, alert delivery, and stale market data.
  - [ ] Define actionable alerts and escalation/runbook steps.
  - [ ] Test backup and restore using a non-production environment.

### 11.4 Frontend and product quality

- [ ] **UX-01 — Frontend acceptance.**
  - [ ] Run current frontend unit/component tests, TypeScript checks, and production build.
  - [ ] Test each major view at desktop, tablet, and mobile widths.
  - [ ] Test table overflow, dialogs, charts, forms, menus, and keyboard navigation individually.
  - [ ] Add Firefox and WebKit coverage where supported.
  - [ ] Complete keyboard-only, focus, screen-reader, contrast, and zoom checks.
  - [ ] Verify loading, empty, stale, offline, permission-denied, and server-error states.
  - [ ] Verify login expiry, logout failure, and session recovery behavior.
- [ ] **UX-02 — Professional product consistency.**
  - [ ] Establish a documented design system: typography, spacing, colors, surfaces, borders, focus, status, and responsive breakpoints.
  - [ ] Standardize page headers, filters, data tables, charts, empty states, confirmations, and notifications.
  - [ ] Provide data freshness timestamps and source attribution for market/portfolio data.
  - [ ] Add accessible labels, predictable focus management, and consistent error language.
  - [ ] Preserve existing workflows and verify feature parity before any production UI cutover.

### 11.5 Trading product capabilities

- [ ] **TRD-01 — Central Risk Management Center.**
  - [ ] Define exposure, position, leverage, daily-loss, drawdown, and concentration limits.
  - [ ] Define strategy-level and account-level risk budgets.
  - [ ] Add emergency pause/kill switch with role restrictions and audit trail.
  - [ ] Block actions on stale prices, missing market data, or breached limits.
  - [ ] Test boundary conditions and restart/recovery behavior in paper mode.
- [ ] **TRD-02 — Strategy Lifecycle Management.**
  - [ ] Define Research → Backtest → Out-of-sample validation → Paper trading → Approved → Monitoring → Retired.
  - [ ] Require minimum trade count and out-of-sample/walk-forward evidence.
  - [ ] Include fees, slippage, drawdown, exposure, and regime sensitivity.
  - [ ] Separate strategy evaluation from Telegram alert activation and live execution permission.
  - [ ] Require explicit authorized promotion and preserve versioned approval history.
- [ ] **TRD-03 — Alert Quality Analytics.**
  - [ ] Track alert generation, delivery, deduplication, acknowledgement, and subsequent outcome.
  - [ ] Measure precision/false positives, expectancy, adverse excursion, and missed/duplicate alerts.
  - [ ] Separate paper outcomes from live outcomes.
  - [ ] Make alert thresholds configurable and auditable.
- [ ] **TRD-04 — Unified Event Timeline and System Health.**
  - [ ] Correlate signals, strategy changes, wallet events, paper trades, jobs, API errors, and admin actions.
  - [ ] Display worker heartbeat, queue status, data freshness, and exchange connectivity.
  - [ ] Provide filters, time ranges, export, and safe diagnostics.

### 11.6 Commercial readiness

- [ ] **BIZ-01 — Product and customer definition.**
  - [ ] Choose initial customer segment and primary use case.
  - [ ] Decide self-hosted single-tenant versus hosted multi-tenant delivery.
  - [ ] Define what is research-only, paper-only, and potentially live; default new customers to safe paper/read-only modes.
  - [ ] Validate customer demand before building broad feature tiers.
- [ ] **BIZ-02 — Tenant/security foundations (required before hosted multi-tenant SaaS).**
  - [ ] Enforce tenant scope in every database query, cache key, file/object path, job, wallet, and API authorization check.
  - [ ] Add tenant-isolation tests proving one customer cannot read or mutate another customer's data.
  - [ ] Encrypt customer credentials and sensitive data at rest; support rotation and revocation.
  - [ ] Implement least-privilege roles, optional MFA, session/device management, and account recovery.
- [ ] **BIZ-03 — Onboarding and paid-user experience.**
  - [ ] Build guided setup for markets, timeframes, watchlists, screener presets, and notification test.
  - [ ] Provide saved workspaces, custom columns, reports, and clear usage limits.
  - [ ] Add in-app help, diagnostics, export, retention, and account deletion workflows.
  - [ ] Make pricing/plan entitlements server-enforced, not frontend-only.
- [ ] **BIZ-04 — Commercial operations and governance.**
  - [ ] Define pricing against actual infrastructure and data-provider costs.
  - [ ] Review exchange and market-data licensing/redistribution rights.
  - [ ] Prepare terms, privacy, risk disclosures, retention policy, support boundaries, and incident response.
  - [ ] Define release cadence, staging, migrations, rollback, backup/restore, and customer support escalation.
  - [ ] Obtain jurisdiction-specific legal review before commercial launch.

### 11.7 Acceptance gates — do not skip

- [ ] **GATE-01:** all P0 security findings closed with regression and multi-worker evidence.
- [ ] **GATE-02:** all required GitHub Actions checks green on the exact candidate commit.
- [ ] **GATE-03:** isolated MariaDB integration and RetailBot accounting regression green.
- [ ] **GATE-04:** API contract, RBAC, and mutation tests pass against a running staging instance.
- [ ] **GATE-05:** frontend responsive, cross-browser, and accessibility acceptance complete.
- [ ] **GATE-06:** backup/restore and operational runbooks tested.
- [ ] **GATE-07:** product scope, data rights, support, and legal/commercial review complete.
- [ ] **GATE-08:** owner explicitly approves release and any production cutover separately.

### 11.8 Progress log

| Date | Commit / PR | Work completed | Verification | Remaining |
|---|---|---|---|---|
| 2026-10-31 | master aa837012 | Token revocation failure handling; audit helper improvements; user deletion audit; GET purity; several validation and response fixes | Focused tests added; latest four master workflows failed | Shared rate limiting, full audit durability, multi-worker verification, CI diagnosis |
| 2026-10-31 | [PR #605](https://github.com/kurnFR/signal-bot/pull/605) | Re-review disposition and commercial roadmap recorded; this living checklist added | Markdown fetched back from GitHub and key sections verified | Execute SEC-02 and CI-01 first |

**Next execution task:** SEC-02 — shared login rate limiting. Before editing, inspect the current implementation and tests on the latest master, reproduce the fallback behavior, then implement the smallest fail-safe change with cross-worker regression coverage. Do not mix unrelated UI or commercial features into this security PR.
