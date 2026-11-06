# Post-Merge Acceptance Report — PR #699

**Repository:** `kurnFR/signal-bot`  
**Target Branch:** `master`  
**Tested HEAD SHA:** `4df758cedb507cdafccac1458082d02585084d4e`  
**Merged PR:** [#699](https://github.com/kurnFR/signal-bot/pull/699) (`feat/modern-ui-remaining-workflows` -> `master`)  
**Evaluation Date:** 2026-11-06  
**Final Status:** **PASS WITH LIMITATIONS** (All acceptance gates passed; limitations documented in Section 9)

---

## 1. Exact Tested HEAD SHA & Merge Verification

1. **Remote Reference Synchronization:**
   - Fetched latest remote references from `origin`.
   - Synchronized local `master` with `origin/master` using fast-forward only (`git merge --ff-only origin/master`).
2. **Commit Identification:**
   - **Tested HEAD SHA:** `4df758cedb507cdafccac1458082d02585084d4e`
   - Verified that HEAD matches `4df758cedb507cdafccac1458082d02585084d4e` exactly.
3. **Merge Commit Confirmation:**
   - Git commit message: `Merge pull request #699 from kurnFR/feat/modern-ui-remaining-workflows`
   - Commit parents: `2f447c7` (base `master`) and `008f883` (PR #699 feature branch HEAD).
   - Change volume: **62 files changed, 15,013 insertions(+), 617 deletions(-)**.
4. **Working Tree Cleanliness:**
   - `git status` reports working tree completely clean with zero uncommitted changes.
   - `git diff --check` executed with exit code 0 (zero whitespace errors or unresolved conflict markers).

---

## 2. Actual Independent Test Results

All test suites were executed independently on the merged `master` at commit `4df758cedb507cdafccac1458082d02585084d4e`.

| Test Category | Exact Command | Exit Code | Duration | Test Count & Outcome | Environment & Boundary Conditions |
|---|---|:---:|:---:|---|---|
| **Full Backend Regression** | `PYTHONPATH=$PWD python3 -m pytest -q` | 0 | 16.10s | **1,142 passed, 18 skipped, 6 warnings, 0 failed** | Python 3.12.14, Linux, authorized test DB `signal_bot_test`. External APIs mocked. |
| **Telegram Opt-in & Single Attempt** | `python3 -m unittest -v tests/test_deploy_telegram_opt_in.py` | 0 | 0.153s | **23 passed, 0 failed** | Unit tests verifying opt-in defaults, single-attempt on transport error, HTTP 500/502 handling, trade-close retry retention. |
| **MariaDB Concurrency** | `python3 -m unittest -v tests/test_user_management_mariadb_concurrency.py` | 0 | 4.385s | **12 passed, 0 failed** | MariaDB on `signal_bot_test`. Proves deterministic `LOCK WAIT` contention, explicit rollback, connection reuse. |
| **Frontend Unit & Component** | `cd frontend && npm test -- --run` | 0 | 8.45s | **31 test files passed, 288 tests passed, 0 failed** | Node v22.23.2, Vitest 3.1.3. Mocked component and client unit tests. |
| **Frontend Typecheck** | `cd frontend && npm run typecheck` | 0 | 8.0s | **PASS (`tsc --noEmit`, 0 errors)** | TypeScript 5.8.2 strict type checking. |
| **Frontend Production Build** | `cd frontend && npm run build` | 0 | 4.22s | **PASS (1,610 modules transformed, clean bundle)** | Vite 6.3.5, output in `frontend/dist/`. |
| **Staging Lifecycle Regressions** | `node tests/browser/test_staging_lifecycle_regressions.cjs` | 0 | 1.4s | **13 passed, 0 failed** | Node.js v22.23.2. Child process identity verification, socket error propagation, cleanup orchestration. |
| **Tracked Staging Browser Acceptance** | `node tests/browser/staging_browser_acceptance.cjs` | 0 | 15.2s | **11 views, 3 roles verified (`admin`, `trader`, `viewer`), 0 console errors, 0 network errors** | Real FastAPI server on port 8055, Chromium headless, authorized DB `signal_bot_test`. Zero production operations. |
| **Dashboard Frontend Regressions** | `python3 -m unittest -v tests/test_dashboard_frontend.py` | 0 | 0.085s | **146 passed, 0 failed** | Static dashboard structure, accessibility landmarks, focus traps, theme contrast. |
| **Specialized Route Suites** | `python3 -m unittest -v tests/test_wallet_settings_secret_redaction.py tests/test_paper_order_routes.py tests/test_ml_deploy_gate.py` | 0 | 0.316s | **51 passed, 0 failed** | Secret redaction, order route contracts, ML deployment gate eligibility. |
| **Git Hygiene Check** | `git diff --check` | 0 | 0.01s | **PASS (0 whitespace or format errors)** | Repository cleanliness verification. |

---

## 3. Database Safety Evidence

Strict database safety protocols were enforced across all test executions:

1. **Authorized Database Isolation:**
   - All tests were executed exclusively against `signal_bot_test`.
   - Production database `crypto_signals` was **never connected to, queried, migrated, or modified**.
   - Runtime assertions in test runners verify `MYSQL_DATABASE == "signal_bot_test"`; tests fail closed if connected to any other database.
2. **No Database Recreation:**
   - The test database `signal_bot_test` was not dropped, created, or wiped.
3. **Run-Specific Fixtures and Exact-ID Cleanup:**
   - Staging browser acceptance tests generated run-unique test accounts with random suffixes (e.g., `adm_b4beb3a5`, `trd_b4beb3a5`, `viw_b4beb3a5`).
   - Cleanup functions (`orchestrateStagingCleanup`) executed exact-ID deletion queries restricted to run-specific IDs (e.g., `[2080, 2081, 2082]`).
   - Pre-existing accounts and shared test data remained completely untouched.
4. **MariaDB Transaction Serialization:**
   - Concurrency tests in [`tests/test_user_management_mariadb_concurrency.py`](file:///home/BIS/signal-bot/tests/test_user_management_mariadb_concurrency.py) verified explicit row locking (`SELECT ... FOR UPDATE`) and MariaDB InnoDB lock wait timeout behavior with clean transactional rollback.

---

## 4. Ten-Workflow Acceptance Matrix

All 10 remaining workflows migrated in PR #699 were verified for endpoint correctness, payload serialization, state transitions, duplicate submission protection, and RBAC enforcement.

| ID | Domain View | User Workflow | API Method & Endpoint | Role Authorization | Verification Detail & Safety Controls | Status |
|---|---|---|---|:---:|---|:---:|
| **PT-07** | Paper Trading | Cancel simulated paper order | `POST /api/paper/orders/{order_id}/cancel` | `trader`, `admin` | Accessible native `<dialog>` modal with order ID and side readback. Focus trap and trigger restoration. Submitting disables buttons to prevent double-submit. Refreshes open orders list upon completion. | **PASS** |
| **PT-08** | Paper Trading | Manually close open position | `POST /api/paper/positions/{position_id}/close` | `trader`, `admin` | Position close modal with mark price confirmation and realized PnL preview. Prevents duplicate close requests. Refreshes positions and closed ledger on success. | **PASS** |
| **PT-09** | Paper Trading | Deploy strategy to live paper | `POST /api/paper/deploy` | `trader`, `admin` | Strategy deploy modal validating min $100 capital and 0.1%–10% risk. Telegram opt-in explicitly defaults to `false`. Enforces single-attempt (`max_retries=1`). Distinct UI handling for `status: "success"` vs `status: "partial"`. Telegram failure reported independently without rolling back deployment. | **PASS** |
| **PT-10** | Paper Trading | Circuit breaker trip & reset | `POST /api/paper/circuit-breakers/emergency-stop`, `POST /api/paper/circuit-breakers/reset` | `trader`, `admin` | Accessible emergency stop and reset confirmation modals. Immediate state readback from server. Prevents redundant clicks while request is pending. | **PASS** |
| **ML-03** | ML Forecast Studio | Launch Battle Royale tournament | `POST /api/ml/run-experiment` | `trader`, `admin` | Tournament launch modal with candidate model multi-select, evaluation metric, and validation ratio. Disabled submit during execution. Refreshes tournament experiment list upon completion. | **PASS** |
| **ML-04** | ML Forecast Studio | Deploy ML filter model to paper | `POST /api/ml/deploy-to-paper` | `trader`, `admin` | Deploy modal strictly gated to models with `eligible_for_paper=True` and verified weights. Payload accepts capital and risk parameters. Defaults `send_telegram=False` with single attempt (`max_retries=1`). Aligned response contract handles `config` and `paper_config`. Independent delivery feedback displayed via warning banner. | **PASS** |
| **RB-03** | Research Bots | Interactive strategy lifecycle updates | `POST /api/research/retailbot2/strategies` | `trader`, `admin` | In-row toggle controls for strategy active and tracking flags. Dirty-state tracking with individual save actions. Historical-only strategies are locked. Reverts local state on failure. | **PASS** |
| **RB-04** | Research Bots | Stop & Hide bot modal | `POST /api/research/retailbot2/strategies` | `trader`, `admin` | Confirmation dialog resetting active and tracking flags to false. Prevents double clicks and updates strategy table. | **PASS** |
| **WT-04** | Wallet Tracking | Track new wallet & untrack wallet | `POST /api/wallets/tracked`, `DELETE /api/wallets/tracked/{id}` | `trader`, `admin` | Add wallet modal with label, address, and network validation. Untrack modal with accessible delete confirmation. Aligned response contract correctly parses `{"status": "ok"}` without requiring `tracked_id`. Refreshes tracked list on completion. | **PASS** |
| **WT-05** | Wallet Tracking | Save scanner and alert thresholds | `PUT /api/wallets/settings` | `trader`, `admin` | Settings form validating swap thresholds and strict polling interval bounds `[15, 3600]` seconds on input, form, and client library. Birdeye API credentials masked (`●●●●●...`), preserved on blank submission, and cleared only on explicit opt-in (`clear_birdeye_api_key=true`). Validated across 8 blockchain networks. | **PASS** |

---

## 5. API Contract and RBAC Findings

1. **Server-Side Authorization Enforced:**
   - RBAC is enforced strictly at the FastAPI route boundary via `authenticate_request` and role dependency checks.
   - **Viewer Role (`viewer`):** Receives HTTP 403 Forbidden on all mutation endpoints across Paper Trading, ML Studio, Research Bots, Wallet Tracking, and Settings.
   - **Trader Role (`trader`):** Allowed operational mutations (paper trading, ML experiment launches, wallet tracking, research flags), but strictly denied HTTP 403 on User Management and Telegram test ping endpoints. Telegram chat ID is masked to `null` for non-admin callers.
   - **Unauthenticated Requests:** Strictly rejected with HTTP 401 Unauthorized across all protected API routes.
2. **Response Contract Alignment:**
   - **WT-04 Untrack:** Backend `DELETE /api/wallets/tracked/{tracked_id}` returns `{"status": "ok"}`. Frontend client [`untrackWallet()`](file:///home/BIS/signal-bot/frontend/src/lib/walletTracking.ts) accepts both `status: ok` and `tracked_id`, eliminating previous contract mismatch.
   - **ML-04 Deployment:** Backend returns `config`, `paper_config`, `telegram`, `telegramAlert`, and `sync`. Frontend [`deployMLToPaper()`](file:///home/BIS/signal-bot/frontend/src/lib/mlStudio.ts) correctly parses both `config` and `paper_config`.
   - **PT-09 Deployment:** Evaluates immediate paper status and returns `status: "success"` or `status: "partial"`. Frontend [`PaperTradingPanel.tsx`](file:///home/BIS/signal-bot/frontend/src/PaperTradingPanel.tsx) distinguishes partial deployments and renders a warning notification rather than falsely presenting success.

---

## 6. Telegram Retry and Credential-Safety Evidence

1. **Explicit Opt-in Defaults to False:**
   - Both Pydantic request models ([`DeployStrategyRequest`](file:///home/BIS/signal-bot/web/routes/paper_routes.py#L35-L48) and [`DeployMLToPaperRequest`](file:///home/BIS/signal-bot/web/routes/ml_routes.py#L88-L100)) set `send_telegram: bool = False`. Omitted fields in client payloads never trigger notifications.
2. **Deployment Notifications Enforce Single-Attempt (`max_retries=1`):**
   - Paper deployment calls [`send_deployment_alert(..., max_retries=1)`](file:///home/BIS/signal-bot/notifications/telegram_notifier.py#L299-L333).
   - ML deployment calls [`send_telegram_message(msg, max_retries=1)`](file:///home/BIS/signal-bot/notifications/telegram_notifier.py#L34-L77).
   - On transport failures (`ConnectionError`) or Telegram HTTP 5xx responses (500, 502), exactly **1 HTTP attempt** is made, and backoff sleeps are bypassed (`sleep_mock.assert_not_called()`).
3. **Automated Trading Alerts Retain Multi-Attempt Retry Behavior (`max_retries=3`):**
   - [`send_signal_alert()`](file:///home/BIS/signal-bot/notifications/telegram_notifier.py#L119-L212) and [`send_trade_close_alert()`](file:///home/BIS/signal-bot/notifications/telegram_notifier.py#L214-L270) retain default `max_retries=3` with exponential backoff sleeps (verified 3 dispatch attempts, 2 backoff sleeps).
4. **Credential & Secret Redaction:**
   - Exception handlers format diagnostics using `f"{type(e).__name__} while contacting Telegram API"`.
   - Tests assert `assertNotIn(token, response.text)` and `assertNotIn(token, captured_logs)` across all failure scenarios.
5. **No External Network Calls:**
   - All tests mocked `requests.post` and `requests.get`. Zero requests were dispatched to `api.telegram.org`.

---

## 7. Legacy and Modern Serving-Mode Verification

Direct code inspection of [`web/app.py`](file:///home/BIS/signal-bot/web/app.py#L290-L324) and execution of [`tests/test_dashboard_frontend.py`](file:///home/BIS/signal-bot/tests/test_dashboard_frontend.py) confirm:

1. **Default Legacy Serving:**
   - `GET /` serves the legacy operational dashboard from `web/static/index.html`.
   - `SIGNAL_BOT_USE_MODERN_UI` defaults to `"0"` in environment parsing (`os.getenv("SIGNAL_BOT_USE_MODERN_UI", "0")`).
   - Production systems continue serving legacy UI without disruption.
2. **Modern Preview Mode:**
   - The compiled modern React application is mounted at `GET /modern` serving `frontend/dist/index.html`.
   - Static bundles are served from `frontend/dist/assets`.
3. **Operational Discipline:**
   - No production service was restarted or redeployed. Modern UI cutover remains an explicit, owner-authorized operational event described in [`docs/UIX_CUTOVER_PLAN.md`](file:///home/BIS/signal-bot/docs/UIX_CUTOVER_PLAN.md).

---

## 8. GitHub Repository Governance Findings & Recommendation

During post-merge governance inspection:

```bash
$ gh api repos/kurnFR/signal-bot/branches/master/protection
HTTP 403: "Upgrade to GitHub Pro or make this repository public to enable this feature."
```

### Governance Analysis
1. **Current Plan Constraint:** `kurnFR/signal-bot` is a private repository on a GitHub Free organization/user tier. GitHub restricts native branch protection rules (e.g. branch protection API, required reviews, merge queues) on private repositories unless upgraded to GitHub Team/Pro or made public.
2. **Current Exposure:** Direct pushes to `master` are not blocked at the GitHub server boundary. A developer with push access could accidentally push directly to `master`.

### Recommendations for Repository Governance
1. **Policy & Workflow Enforcement (Immediate / Zero-Cost):**
   - Maintain organizational requirement that all changes must go through dedicated feature/fix branches and Pull Requests.
   - Require at least one independent review before merging.
   - Configure local git hooks (`pre-push`) on developer workstations to prevent accidental direct pushes to `master`:
     ```bash
     current_branch=$(git symbolic-ref --short HEAD)
     if [ "$current_branch" = "master" ]; then
         echo "ERROR: Direct push to master is prohibited. Use a feature branch and PR."
         exit 1
     fi
     ```
2. **Plan Upgrade Path (Recommended when budget allows):**
   - Upgrade repository to GitHub Team or GitHub Pro to unlock native branch protection:
     - Require pull request reviews before merging (minimum 1 approval).
     - Require branch to be up to date before merging.
     - Restrict who can push to `master`.
     - Enable required status checks (e.g., pytest and vitest runners) once CI capacity is active.

---

## 9. Blockers & Non-Blocking Findings

### Blockers
- **None.** All 1,142 backend tests, 288 frontend tests, 12 MariaDB concurrency tests, 13 staging regressions, and staging browser acceptance passed. Zero functional regressions or contract breaks detected.

### Non-Blocking Findings
1. **Frontend Asset Bundle Warning:**
   - Vite production build produces a warning: `dist/assets/index-Bh8V0JuG.js` is 511.26 kB minified (128.49 kB gzip), slightly exceeding Vite's default 500 kB recommendation.
   - *Impact:* Non-blocking; does not impact functionality or browser performance on broadband/desktop. Can be optimized in future maintenance via React `lazy()` dynamic imports.
2. **Pydantic V2 Deprecation Warnings:**
   - 6 warnings emitted during pytest for deprecated Pydantic methods (`.dict()` -> `.model_dump()`, `.construct()` -> `.model_construct()`) in backtest auto-promotion tests.
   - *Impact:* Non-blocking; fully compatible with current Pydantic 2.x.
3. **GitHub Free Plan Branch Protection:**
   - Documented in Section 8 above. Native protection API returned HTTP 403 due to account tier.

---

## 10. Final Status Sign-Off

### Status: **PASS WITH LIMITATIONS**

- **Workflows Verified:** All 10 remaining workflows (PT-07–PT-10, ML-03–ML-04, RB-03–RB-04, WT-04–WT-05) are complete and operational on `master`.
- **System Stability:** Full backend (1,142 tests) and frontend (288 tests) test suites pass cleanly.
- **Security & Concurrency:** Real MariaDB transaction serialization and RBAC fail-closed contracts verified.
- **Limitations:** Modern React UI remains in preview mode at `/modern` with legacy default at `/`; production cutover is pending owner staging verification per [`docs/UIX_CUTOVER_PLAN.md`](file:///home/BIS/signal-bot/docs/UIX_CUTOVER_PLAN.md); branch protection governance requires workflow convention due to GitHub Free private repository tier.
