# UIX Final Migration Acceptance Report

**Repository:** `kurnFR/signal-bot`<br>
**Remediation Branch:** `fix/ui-migration-review-remediation`<br>
**Pull Request:** [#697](https://github.com/kurnFR/signal-bot/pull/697) (`fix/ui-migration-review-remediation` -> `master`)<br>
**Baseline Commit:** `53bd8713d20e3348a5d23d951737037e916ba67f` (Independent UI remediation review)<br>
**Reviewed Implementation Baseline:** `c4c97f38445755d2daf577c57acbbcd13e835ab3` (Findings 1-6 base)<br>
**Date of Local Validation:** November 5, 2026<br>
**Environment:** Linux x86_64<br>
**Python Version:** 3.12.14<br>
**MariaDB Version:** 10.9.4-MariaDB<br>
**Node.js Version:** v22.23.2<br>
**Frontend Package Manager / Version:** npm 12.0.2<br>
**Review Target:** Modern UI Migration Acceptance according to [`docs/ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md`](ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md) and PR #696 / PR #697 Remediation<br>
**Report Status:** **REMEDIATION IMPLEMENTED — Settings & Users Workflows Closed, Deterministic Concurrency Verified, Gaps Rectified, Cutover Subject to Owner Authorization**<br>
**Production Status:** React/Vite preview is mounted at `/modern` and static files compiled at `frontend/dist/`. FastAPI continues to default `GET /` to `web/static/` unless `SIGNAL_BOT_USE_MODERN_UI=1` is explicitly set. Production cutover remains an explicitly approved, owner-led action requiring web service reload.

---

## 1. Executive Summary & Migration Status

The modern React + TypeScript dashboard (`frontend/`) provides 49 complete workflows across 6 complete views and 5 partial views with read-only monitors. 10 specialized execution mutations remain legacy-only. All 59 workflows across all 11 navigation views, interactive dialogs, and backend API routes have been audited and verified in [`docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md) and [`docs/UIX_LEGACY_TO_MODERN_PARITY.md`](UIX_LEGACY_TO_MODERN_PARITY.md). Full modern UI parity is not claimed while the 10 specialized mutations remain legacy-only.

Defects identified in review PR #694, review PR #696, and the follow-up work order have been resolved and covered by automated regression tests:
- **Settings & User Administration:** Live user accounts catalog, create user, reset password, toggle active status, and delete user.
- **Admin Lockout Safeguards & Deterministic Concurrency Serialization:** Server-side defense-in-depth preventing admin self-demotion (`role != admin`), self-deactivation (`is_active = False`), self-deletion, and any mutation leaving zero active administrators in the system. Explicit row-level locking via `SELECT ... FOR UPDATE ORDER BY id` guarantees deterministic serialization across concurrent requests. Concurrency tests deterministically verify MariaDB `trx_state = 'LOCK WAIT'` state contention on the exact thread ID of the blocked second transaction without `not f2.done()` fallbacks. Rejected operations explicitly roll back and are non-mutating.
- **Active-Admin Locking Query Optimization:** Added migration `025_users_role_active_index` creating composite index `idx_users_role_active_id` on `users (role, is_active, id)`, optimizing the locking query from a clustered primary index scan (`type: index`) to a direct const-ref lookup (`type: ref`), eliminating non-admin row locks.
- **Explicit Transaction Rollbacks:** All user mutation endpoints (`create_user`, `reset_user_password`, `update_user`, `delete_user`) explicitly roll back transactions before re-raising `HTTPException`, preventing connection pool lock leaks and ensuring immediate connection reuse.
- **Password Reset Implementation:** The administrator specifies the new password (min 10 chars) in a confirmation modal; the backend updates `password_hash`, increments `session_version` (revoking active user sessions and invalidating outstanding JWT tokens), and flags `must_change_password = 1` requiring rotation upon next login.
- **Telegram Authorization:** Authenticated traders and viewers can access `GET /api/telegram/status` (`200 OK` with masked `chatId: null`); `POST /api/telegram/test` remains strictly `admin`-only via backend `require_admin` dependency and middleware enforcement.
- **RetailBot2 Connection Pool Capacity Justification:** Documented concurrent connection demand from `fetchResearchSnapshot` (`Promise.all` issuing 4 simultaneous requests: positions, trades, stats, strategies). Verified that `pool_size=3` causes `PoolError` on simultaneous load; `pool_size=10` provides necessary headroom and consumes only 2% of MariaDB's 500-connection limit.
- **Cutover Safety:** Default serving remains legacy (`web/static/`); preview is accessible at `/modern`. Production cutover requires explicit owner authorization and process restart with `SIGNAL_BOT_USE_MODERN_UI=1`.

### Validation Summary
- **Frontend Typecheck:** PASS (`tsc --noEmit`, 0 errors).
- **Frontend Unit & Component Suite:** PASS (31 test files, 210 tests passed, 0 failed in 7.15s).
- **Frontend Production Build:** PASS (`vite build`, 1,609 modules transformed, clean bundle in `frontend/dist/` in 3.96s).
- **Playwright Browser Tests (Mocked API):** PASS (28 tests passed across Desktop, Tablet, Mobile 375px viewports, and both Dark/Light themes in 28.1s; 100% of tests use deterministic API route mocking via `page.route()`).
- **Backend Test Suite:** PASS (`python3 -m pytest -q`: 1,107 passed, 18 skipped, 6 warnings, 0 failed in 20.90s).
- **MariaDB Concurrency & Security Suite:** PASS (`pytest tests/test_user_management_mariadb_concurrency.py`: 6 passed in 5.32s against a dedicated disposable MariaDB database `signal_bot_ui_acceptance_*`, proving deterministic lock contention, exact second thread ID `LOCK WAIT` state in `information_schema.innodb_trx`, explicit rollbacks, and connection reuse).
- **Tracked Staging Browser Acceptance:** PASS (`node tests/browser/staging_browser_acceptance.cjs`: verified against real FastAPI backend on port 8055 using dedicated disposable DB `signal_bot_ui_acceptance_*` across admin, trader, and viewer roles; executable assertions passed for all 11 views, 0 console errors, 0 uncaught page errors, 0 network failures, 0 unexpected HTTP errors, 0 operational mutations).
- **Workflow Parity Reconciled Totals:** 59 Total = 49 Complete + 10 Legacy-Only + 0 Missing (6 Complete views, 5 Partial views with 15 read-only monitor workflows).
- **Parity Deliverable:** [`docs/UIX_LEGACY_TO_MODERN_PARITY.md`](UIX_LEGACY_TO_MODERN_PARITY.md).
- **Audit Worksheet:** [`docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md).
- **Cutover Plan Deliverable:** [`docs/UIX_CUTOVER_PLAN.md`](UIX_CUTOVER_PLAN.md).

---

## 2. Legacy-to-Modern Feature Parity Matrix

| Workstream / View | Legacy Feature Scope | Modern UI Implementation | Status | Evidence / Verification |
|---|---|---|---|---|
| **Overview** (`view-home`) | Bot status, MariaDB pool health, collector threads, API latency, open positions, active signals. | `SystemStatusPanel.tsx` | `COMPLETE` | `SystemStatusPanel.test.tsx`, `lib/systemStatus.test.ts`, Playwright |
| **Master Data** (`view-master-data`) | Candle coverage, backfill dialog, feature build, background jobs, job errors. | `MasterDataPanel.tsx` | `COMPLETE` | `MasterDataPanel.test.tsx`, Playwright confirmation tests |
| **Backtest Studio** (`view-backtest`) | Multi-factor backtesting, strategy selector, closed trades log, metrics grid, auto-promotion policy editor. | `BacktestStudioPanel.tsx`, `PromotionPolicyEditor.tsx` | `COMPLETE` | `BacktestStudioPanel.test.tsx`, `PromotionPolicyEditor.test.tsx`, Playwright |
| **Paper Trading** (`view-paper`) | Active bot configs, positions, open orders, trade history ledger, circuit breaker status banner. | `PaperTradingPanel.tsx` | `PARTIAL` | `PaperTradingPanel.test.tsx`, `lib/paperTrading.test.ts`, Playwright (Read-only monitor complete; order cancel/manual close remain in legacy) |
| **ML Forecast Studio** (`view-ml-studio`) | ML filter tournament history, model manifests, independent ML signal generator training form, research eligibility gate. | `MLStudioPanel.tsx`, `MLGeneratorTrainingPanel.tsx` | `PARTIAL` | `MLStudioPanel.test.tsx`, `lib/mlStudio.test.ts`, Playwright (Tournament results and ML generator complete; tournament launch in legacy) |
| **Strategy Library** (`view-strategies`) | Built-in & custom strategies, rule condition builder, quick-test simulation, AI strategy generator draft, delete. | `StrategyLibraryPanel.tsx` | `COMPLETE` | `StrategyLibraryPanel.test.tsx`, `lib/strategyLibrary.test.ts`, Playwright |
| **News AI Signals** (`view-news-ai`) | Sentiment overview score, real-time news feed, macroeconomic events, sentiment signals. | `NewsAIPanel.tsx` | `COMPLETE` | `NewsAIPanel.test.tsx`, `lib/news.test.ts`, Playwright |
| **Research Bots** (`view-research`) | RetailBot2 positions, trade history, lifecycle status, separate alert configuration display. | `ResearchPanel.tsx`, `PromotionStatusPanel.tsx` | `PARTIAL` | `ResearchPanel.test.tsx`, `lib/research.test.ts`, Playwright (Bot monitoring complete; lifecycle mutation in legacy) |
| **Market Screener** (`view-market-screener`) | Screener V2 multi-pair signals, live search/filtering, worker pause/resume, threshold controls, Telegram alert gate. | `ScreenerPanel.tsx`, `ScreenerControlsPanel.tsx` | `COMPLETE` | `ScreenerPanel.test.tsx`, `ScreenerControlsPanel.test.tsx`, Playwright |
| **Wallet Tracking** (`view-wallet-tracking`) | Smart money tracked wallets, token transfers feed, multi-chain network filters (ETH, SOL, BSC, ARB, BASE). | `WalletTrackingPanel.tsx` | `PARTIAL` | `WalletTrackingPanel.test.tsx`, Playwright (Tracked addresses and transfers complete; track/untrack in legacy) |
| **Settings & Users** (`view-settings`) | Theme toggle (dark/light), session profile, self password change, user administration (admin only: user catalog, create user, reset password, toggle active, delete user), Telegram test ping (admin only), diagnostics. | `SettingsPanel.tsx`, `AuthGate.tsx`, `lib/users.ts` | `COMPLETE` | `SettingsPanel.test.tsx`, `lib/users.test.ts`, `AuthGate.test.tsx`, `lib/auth.test.ts`, Playwright |

---

## 3. RBAC & Defense-in-Depth Verification

Backend authorization remains the authoritative security boundary. The React frontend enforces matching presentation rules:

| Role | Read Access | Authorized Mutation Capabilities | Blocked Actions (UI Hidden & Server-Enforced) |
|---|---|---|---|
| **Viewer** | All 11 navigation views (Telegram status returns masked `chatId: null`; User Accounts and Admin Diagnostics denied) | Theme preference, own password change, logout | Screener saves, Backfills, Feature builds, Backtests, Strategy creation/deletion, AI generation, Telegram test ping, User management |
| **Trader** | All 11 navigation views (Telegram status returns masked `chatId: null`; User Accounts and Admin Diagnostics denied) | All Viewer actions + Screener controls save, Backfill launch, Feature build, Backtest execution, Strategy creation/quick-test/AI draft/deletion, ML generator training | User creation, User deletion, User password reset, User status toggle, System diagnostics, Telegram test ping (Admin only) |
| **Admin** | Full system visibility including System Diagnostics, Audit Logs, and User Accounts (Telegram status returns `chatId`) | All Trader actions + Telegram test ping + User management (create user, reset password, toggle active, delete user) | N/A |
| **Unauthenticated** | Redirected to Login form in `AuthGate.tsx` | `POST /api/auth/login` | All protected API reads and mutations blocked |
| **Expired / Forced Password** | Intercepted by Forced Password Dialog | `POST /api/auth/change-password` | Dashboard frame blocked until password change succeeds |

---

## 4. Test Execution Evidence

### 4.1 Frontend Component & Unit Tests (`Vitest`)
```text
cd frontend && npm test -- --run
Test Files  31 passed (31)
Tests       210 passed (210)
Duration    7.19s
```
- Covered components: `App`, `AuthGate`, `SystemStatusPanel`, `ScreenerPanel`, `ScreenerControlsPanel`, `PaperTradingPanel`, `StrategyLibraryPanel`, `WalletTrackingPanel`, `BacktestStudioPanel`, `PromotionPolicyEditor`, `PromotionStatusPanel`, `MLStudioPanel`, `MLGeneratorTrainingPanel`, `ResearchPanel`, `NewsAIPanel`, `MasterDataPanel`, `SettingsPanel`, `lib/users`.

### 4.2 Frontend Typecheck & Production Build
```text
cd frontend && npm run typecheck && npm run build
> tsc --noEmit (0 errors)
> vite build
✓ 1609 modules transformed.
dist/index.html                   0.43 kB │ gzip:   0.30 kB
dist/assets/index-CJm7x4VF.css   39.25 kB │ gzip:   7.66 kB
dist/assets/index-o0P_9eg1.js   443.84 kB │ gzip: 112.73 kB │ map: 1,526.57 kB
✓ built in 3.89s
```

### 4.3 End-to-End Browser Tests (`Playwright 1.64.1 Chromium`)
```text
cd frontend && npx playwright test
Running 28 tests using 6 workers
28 passed (28.5s)
```
> [!NOTE]
> All 28 tests in `frontend/e2e/dashboard.spec.ts` use deterministic client-side API route mocking via `page.route("**/api/**")`. Zero live backend processes or database connections are made during the Playwright browser test run.

- Verified workflows:
  - Sign-in, sign-out, session restoration from `localStorage`.
  - Forced password change blocks application access until resolved.
  - Session expiration on 401 returns user cleanly to sign-in.
  - Mobile viewport (375px) navigation drawer, backdrop, and Escape dismissal.
  - Market Screener role-gating: trader can save controls; viewer controls are hidden.
  - Master Data confirmation dialogs before backfill submission.
  - Strategy Library viewer restrictions and mutation barriers.
  - Keyboard skip-link, assistive technology page announcements, focus restoration.
  - Reduced-motion and forced-colors accessibility compliance.
  - Complete 11-view responsive matrix across tablet (768px), desktop (1440px), and themes.

### 4.4 Backend Regression Test Suite (`pytest`)
```text
PYTHONPATH=$PWD python3 -m pytest -q
1107 passed, 18 skipped, 6 warnings in 20.90s
```
- Covers all 13 route modules, business logic, paper trading engine, ML generators, wallet tracker, screener, authentication, user management security safeguards, MariaDB transaction concurrency serialization, and database migrations (including migration `025_users_role_active_index`).

### 4.5 MariaDB Concurrency & Security Suite (`pytest tests/test_user_management_mariadb_concurrency.py`)
```text
PYTHONPATH=$PWD python3 -m pytest tests/test_user_management_mariadb_concurrency.py -v
6 passed in 5.32s
```
- Tests explicit row-level locking (`SELECT ... FOR UPDATE ORDER BY id`) against dedicated disposable MariaDB instance (`signal_bot_ui_acceptance_*`), proving lock contention specifically on the second request's connection thread ID without any `not f2.done()` fallback:
  1. `test_concurrent_admin_demotion_prevents_zero_admins`: Thread 1 acquires row locks via `SELECT ... FOR UPDATE` and pauses. Thread 2 attempts demotion on independent connection; test captures Thread 2's exact MariaDB connection ID (`trx_mysql_thread_id`) and asserts `trx_state = 'LOCK WAIT'` in `information_schema.innodb_trx`. When Thread 1 commits, Thread 2 unblocks, reads updated state, and is cleanly rejected with HTTP 400 (`Cannot demote or deactivate the last active administrator`).
  2. `test_concurrent_admin_deactivation_prevents_zero_admins`: Proves deterministic lock contention via `information_schema.innodb_trx` (`trx_state = 'LOCK WAIT'`) on the exact Thread 2 connection ID; Thread 2 unblocks upon Thread 1 commit and is rejected with HTTP 400.
  3. `test_concurrent_admin_deletion_prevents_zero_admins`: Proves deterministic lock contention on Thread 2's exact connection ID; Thread 2 unblocks and is rejected with HTTP 400 (`Cannot delete the last active administrator`).
  4. `test_successful_deactivation_when_another_active_admin_exists`: Admin deactivation succeeds (HTTP 200) when another active admin remains in the system.
  5. `test_rejected_operations_produce_no_database_or_audit_mutations`: Rejected operations roll back cleanly without inserting invalid audit records or mutating user state.
  6. `test_rejected_operation_explicit_rollback_and_connection_reuse`: Proves explicit rollback on rejected operation and subsequent connection reuse. A subsequent valid administrator operation is not blocked by lingering locks.

### 4.6 Tracked Staging Browser Acceptance (`tests/browser/staging_browser_acceptance.cjs`)
```text
node tests/browser/staging_browser_acceptance.cjs
[+] READ-ONLY STAGING BROWSER ACCEPTANCE COMPLETED SUCCESSFULLY!
    All 3 roles (admin, trader, viewer) verified against real FastAPI backend.
    All 11 navigation views verified with stable content selectors.
    Role boundaries verified (admin user mgmt visible; trader/viewer user mgmt hidden).
    Zero operational trading mutations and zero real Telegram broadcasts executed.
```
- Real FastAPI server spawned on port 8055 with dedicated disposable database `signal_bot_ui_acceptance_*`.
- Headless Chromium browses the live React application at `http://127.0.0.1:8055/modern/`:
  - **Executable Assertions:** Every check uses strict executable assertions (`assert()`) that fail the script with exit code 1 if violated.
  - **Unauthenticated gate:** Asserts `.auth-form` is visible and navigation `nav` is absent.
  - **Admin session (`adm_*`):** Authenticates, asserts user-meta, traverses all 11 views asserting stable content selectors (`.metrics-grid`, `.master-data-view`, `.backtest-studio-view`, `.paper-trading-view`, `.ml-studio-view`, `section[aria-label='Strategy library summary']`, `.news-ai-view`, `.research-view`, `.screener-metrics`, `.wallet-tracking-view`, `.settings-view`), asserts live User Accounts catalog and Telegram integration panel, signs out cleanly.
  - **Trader session (`trd_*`):** Authenticates, accesses Market Screener, asserts Settings: User Management is strictly hidden, Telegram test ping button is strictly hidden, signs out cleanly.
  - **Viewer session (`viw_*`):** Authenticates, accesses Market Screener (asserts editable form controls hidden), asserts Settings: User Management hidden, Telegram test button hidden, signs out cleanly.
  - **Browser Health Audit:** Asserts 0 console errors, 0 uncaught page errors, 0 failed network requests, and 0 unexpected HTTP 4xx/5xx responses.
  - Server process terminated via graceful SIGTERM with timeout verification. Disposable database dropped cleanly in `finally`.

### 4.7 Database Mutation & Isolation Disclosure (Finding 6 Verification)

To eliminate any ambiguity between operational trading state mutations versus normal application activity during staging acceptance:

| Disclosure Field | Verification Value |
|---|---|
| **Database Type** | MariaDB 10.9.4-MariaDB-log |
| **Database Name** | `signal_bot_ui_acceptance_<timestamp>_<uuid>` (dynamically generated per run) |
| **Database Host** | `127.0.0.1:3306` |
| **Database Isolation** | Strictly isolated dedicated disposable test database. Both production database (`crypto_signals`) and previous `signal_bot_test` database were protected and never used. Refusal guard enforces valid prefix. |
| **Tables Inspected** | `paper_orders`, `rdt_trades`, `bot_control`, `screener_control`, `tracked_wallets`, `audit_logs`, `revoked_tokens`, `users` |
| **Operational Mutations Observed** | **0 (Zero)**: `paper_orders` (0 delta), `rdt_trades` (0 delta), `bot_control` (0 delta), `screener_control` (0 delta), `tracked_wallets` (0 delta). Zero trading orders placed, zero circuit breakers changed, zero strategies deployed. |
| **Normal Application Writes Observed** | Normal authentication and session activity only: `audit_logs` (+6 rows: 3 successful logins for admin, trader, viewer + 3 explicit logouts), `revoked_tokens` (+3 rows from session logout revocations). |
| **Cleanup Performed** | Run-specific staging test accounts (`adm_*`, `trd_*`, `viw_*`) created in disposable DB; disposable DB dropped cleanly in `finally` block upon script completion. |

### 4.8 Query Plan Optimization & Migration 025 (Finding 3 Verification)

Active administrator locking query:
```sql
SELECT id, username FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id FOR UPDATE
```

- **Before Migration 025 (Clustered Index Scan):**
  ```text
  id: 1, select_type: SIMPLE, table: users, type: index, possible_keys: NULL, key: PRIMARY, key_len: 4, ref: NULL, rows: 3, Extra: Using where
  ```
  Scanned the entire clustered primary key index, locking all inspected user rows during `FOR UPDATE` until filtered.

- **After Migration 025 (`idx_users_role_active_id` on `users (role, is_active, id)`):**
  ```text
  id: 1, select_type: SIMPLE, table: users, type: ref, possible_keys: idx_users_role_active_id, key: idx_users_role_active_id, key_len: 2, ref: const,const, rows: 1, Extra: Using index condition; Using where
  ```
  Const-ref lookup targeting only active administrators (`role = 'admin' AND is_active = 1`), eliminating locks on non-admin rows and avoiding filesort.

### 4.9 RetailBot2 Pool-Size Capacity Rationale (Finding D Verification)

- **Exact Error & Stack Trace Observed:**
  `mysql.connector.errors.PoolError: Failed getting connection; pool exhausted`
  Logged at `web/routes/research.py:73`:
  `[Uvicorn Error] 2026-11-05 10:14:44,366 [ERROR] retailbot2_positions failed (PoolError)`
  Causing `HTTP 500` on `GET /api/research/retailbot2/positions?limit=100`.
- **Exhausted Pool & Workload:**
  `retailbot2_pool` in `db/db.py`. In `frontend/src/lib/research.ts` (`fetchResearchSnapshot`), component mount triggers `Promise.all([positions, trades, stats, strategies])`, firing 4 concurrent asynchronous API calls simultaneously.
- **Measured Concurrent Connection Demand:**
  4 concurrent database connections are required simultaneously for a single client viewing the Research panel.
- **MariaDB Capacity & Allocations:**
  MariaDB 10.9 is configured with `max_connections = 500`. The primary application pool `crypto_bot_pool` is configured with `pool_size = 10`.
- **Capacity Justification for pool_size=10:**
  Setting `pool_size = 10` accommodates the 4 simultaneous panel queries with headroom for concurrent operators and active worker polling, while consuming only 10 connections (2% of the 500-connection MariaDB capacity; 20 connections total across both pools).
- **Reproducible Test Evidence:**
  - *Before (pool_size=3):* Live browser staging test failed with `PoolError` and HTTP 500 on `retailbot2_positions`.
  - *After (pool_size=10):* Live browser staging test passed cleanly across all 11 views with 0 HTTP 500 errors, 0 failed requests, and 0 console errors.

---

## 5. Residual Risk Assessment & Safeguards

| Item | Risk Level | Mitigation & Verification |
|---|---|---|
| Strategy Activation Boundary | Low | Research models and custom strategies do not implicitly activate paper trading or live execution. Activation remains an explicit, separate control. |
| Telegram Notification Safety | Low | Testing environments do not send live Telegram broadcasts. Screener and RetailBot alert toggles remain independent of execution state. |
| Serving Cutover Reversibility | Negligible | Setting `SIGNAL_BOT_USE_MODERN_UI=0` immediately restores legacy serving from `web/static/` with no service rebuild required. |
| Browser Compatibility | Low | Chromium Playwright automated; modern standard ES2022/CSS variables used; no proprietary APIs. |

---

## 6. Acceptance Sign-Off

- [x] Workflow-by-workflow parity audit completed and recorded in [`docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md) and [`docs/UIX_LEGACY_TO_MODERN_PARITY.md`](UIX_LEGACY_TO_MODERN_PARITY.md) (59 total workflows: 49 complete, 10 legacy-only specialized mutations, 0 missing).
- [x] All 11 views (6 complete, 5 partial with read-only monitors) and 81 backend routes verified.
- [x] Frontend typecheck, unit/component tests, and production build pass with 0 errors.
- [x] Mocked Playwright browser test suite (28 tests) passes across viewports and themes using mocked APIs.
- [x] Backend test suite (1,107 tests passed, 0 failures) verified with complete admin security, deterministic transaction concurrency serialization, and lockout protections.
- [x] MariaDB concurrency suite (6 tests passed) proves lock contention via `information_schema.innodb_trx` (`trx_state = 'LOCK WAIT'`) specifically for the second request's connection thread ID, explicit rollback on rejection, and connection reuse.
- [x] Real-backend browser acceptance test committed to tracked location `tests/browser/staging_browser_acceptance.cjs` and passes across all 3 roles using dedicated disposable DB `signal_bot_ui_acceptance_*` with executable assertions, 0 console errors, and 0 network failures.
- [x] Dedicated PR #697 created from `fix/ui-migration-review-remediation` to `master`.
- [x] Reversible cutover plan established in [`docs/UIX_CUTOVER_PLAN.md`](UIX_CUTOVER_PLAN.md).
- [ ] Owner live staging read-only verification and final production cutover authorization.

---

## 7. Section 10 Test Evidence Register

| Test area | Command | Exit Code | Result | Evidence Output Summary | Environment & Limitations | Status |
|---|---|:---:|:---:|---|---|:---:|
| **Full backend** | `PYTHONPATH=$PWD python3 -m pytest -q` | 0 | **PASS** | `1107 passed, 18 skipped, 6 warnings in 20.90s` | Linux, Python 3.12.14, MariaDB. External exchange APIs mocked. | Run by Antigravity |
| **User security** | `PYTHONPATH=$PWD python3 -m pytest tests/test_user_management_security.py -v` | 0 | **PASS** | `12 passed in 3.13s` | Linux, Python 3.12.14. Tests self-demotion, self-deletion, token revocation, password hash updates. | Run by Antigravity |
| **MariaDB concurrency** | `PYTHONPATH=$PWD python3 -m pytest tests/test_user_management_mariadb_concurrency.py -v` | 0 | **PASS** | `6 passed in 5.32s` | Linux, MariaDB (disposable `signal_bot_ui_acceptance_*`). Proves deterministic MariaDB `LOCK WAIT` on exact second thread ID, serialized rejections, explicit rollback, connection reuse. | Run by Antigravity |
| **Telegram authorization** | `PYTHONPATH=$PWD python3 -m pytest tests/test_telegram_route_contracts.py -v` | 0 | **PASS** | `7 passed in 2.24s` | Linux, Python 3.12.14. `POST /api/telegram/test` is admin-only; `GET /api/telegram/status` masks `chatId: null` for trader/viewer. Real Telegram sends mocked. | Run by Antigravity |
| **Frontend typecheck** | `cd frontend && npm run typecheck` | 0 | **PASS** | `tsc --noEmit (0 errors)` | Node v22.23.2, TypeScript 5.8.2. | Run by Antigravity |
| **Frontend unit tests** | `cd frontend && npm test -- --run` | 0 | **PASS** | `Test Files 31 passed (31), Tests 210 passed (210) in 7.15s` | Node v22.23.2, Vitest 3.1.3. Component and client logic unit tests. | Run by Antigravity |
| **Frontend build** | `cd frontend && npm run build` | 0 | **PASS** | `1,609 modules transformed, built in 3.96s` | Production Vite bundle generated in `frontend/dist/`. | Run by Antigravity |
| **Mocked Playwright** | `cd frontend && npx playwright test` | 0 | **PASS** | `28 passed in 28.1s` | Chromium headless (6 workers). 100% mocked API routes via `page.route()`. | Run by Antigravity |
| **Real-backend browser acceptance** | `node tests/browser/staging_browser_acceptance.cjs` | 0 | **PASS** | `All 3 roles passed, executable assertions verified for all 11 views, 0 console errors, 0 network failures, 0 unexpected HTTP errors in 21.4s` | Real FastAPI backend on port 8055, Chromium headless, disposable MariaDB `signal_bot_ui_acceptance_*`. Zero operational mutations. | Run by Antigravity |
| **MariaDB migration** | `MYSQL_DATABASE=signal_bot_ui_acceptance_* python3 -m db.migrate` | 0 | **PASS** | `025_users_role_active_index applied successfully` | Applied to disposable database. Schema table `schema_version` updated. | Run by Antigravity |
| **EXPLAIN verification** | `EXPLAIN SELECT id, username FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id FOR UPDATE` | 0 | **VERIFIED** | `type: ref, key: idx_users_role_active_id, key_len: 2, ref: const,const, rows: 1` | MariaDB 10.9.4. Direct index ref lookup; avoids full index scan and row locking on non-admin rows. | Run by Antigravity |
