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
