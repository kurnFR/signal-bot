# Signal Bot UI/UX Comprehensive View & API Inventory (UIX-00)

**Document Version:** 1.0.0  
**Status:** Draft inventory — pending source-level verification and acceptance; UIX-00 is not complete  
**Target Architecture:** React + TypeScript + Vite + Tailwind CSS + shadcn/ui + TanStack Query/Table  
**Audited Master Commit:** `1a6ead5`  
**Execution Brief Reference:** [`docs/UI_UX_ANTIGRAVITY_EXECUTION_BRIEF.md`](UI_UX_ANTIGRAVITY_EXECUTION_BRIEF.md)  
**Modernization Plan Reference:** [`docs/UI_UX_MODERNIZATION_PLAN.md`](UI_UX_MODERNIZATION_PLAN.md)  

---

## 1. Executive Summary & Verification Context

This is the first-pass UIX-00 inventory draft. It is useful as a navigation and review map, but it does **not yet satisfy the UIX-00 acceptance gate**. Claims about endpoint totals, endpoint-specific authorization, request/response contracts, conditional UI states, and some chart parity requirements must be checked against the current source before this document is treated as authoritative.

### Key Audit Findings
1. **Views & Dialogs:** The dashboard consists of **11 primary navigation views** (`#view-home`, `#view-master-data`, `#view-backtest`, `#view-paper`, `#view-ml-studio`, `#view-strategies`, `#view-news-ai`, `#view-research`, `#view-market-screener`, `#view-wallet-tracking`, `#view-settings`) and **7 modal dialogs** (`#login-modal`, `#force-password-modal`, `#backfill-modal`, `#deploy-modal`, `#modal-run-ml`, `#modal-deploy-ml`, `#modal-inspect-ml`).
2. **Backend API Endpoints:** The initial draft estimates 81 HTTP endpoints across 14 route modules in `web/routes/`. This number and complete endpoint coverage are **not yet independently auditable from this document** because a row-by-row register of every path, method, source handler, request/response contract, authorization rule, and side effect is missing. Do not treat the count as verified until that register is added.
3. **Frontend Implementation:** The current UI is implemented as a single 2,406-line HTML template (`web/static/index.html`), a ~5,200-line monolithic JavaScript controller (`web/static/js/app.js`), an accessibility enhancement bundle (`web/static/js/dashboard_enhancements.js`), and companion stylesheets.
4. **Third-Party CDN Dependencies:** External runtime scripts are pulled from CDNs: Tailwind CSS (`cdn.tailwindcss.com`), Lucide Icons (`unpkg.com/lucide`), TradingView Lightweight Charts v4.1.1 (`unpkg.com/lightweight-charts@4.1.1`), and Chart.js (`cdn.jsdelivr.net/npm/chart.js`).
5. **Frontend Tooling:** No `package.json`, `package-lock.json`, or `tsconfig.json` was found in the inspected repository paths for this first pass. Host-specific Node.js/npm availability was not independently verified and must be checked in the actual UIX-01 execution environment.

---

## 2. Frontend Tooling & Serving Configuration Audit

### Current Serving Configuration
- **Static Asset Mount:** In `web/app.py`, static files are mounted at `/static`:
  ```python
  STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
  app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
  ```
- **Entrypoint Route (`GET /`):** Served via `serve_index()` in `web/app.py`. Reads `web/static/index.html` and injects:
  ```html
  <script src="/static/js/dashboard_enhancements.js?v=20261016-2"></script>
  ```
  before `</body>`.
- **CORS Configuration:** Controlled via `SIGNAL_BOT_ALLOWED_ORIGINS` environment variable (default: `http://localhost:8050,http://127.0.0.1:8050`). `allow_credentials=True`, methods `["GET", "POST", "PUT", "DELETE", "OPTIONS"]`, headers `["Authorization", "Content-Type"]`.

### Target Architecture (Phase UIX-01 onward)
- **Directory Isolation:** The modernized application will live entirely in a top-level `frontend/` directory.
- **Vite Build Target:** Production build will output to an isolated distribution directory (e.g., `frontend/dist/`).
- **Coexistence Guarantee:** Legacy static assets in `web/static/` remain untouched during migration phases UIX-01 through UIX-04. The legacy UI remains the default entrypoint until Phase UIX-05 release verification.
- **Required Node.js Environment:** Node.js (>= 20.x LTS) and npm will need to be provisioned on developer/CI environments before executing UIX-01.

---

## 3. Provisional Authentication & RBAC Pattern Matrix

The patterns below are a navigation aid, **not an endpoint-level permission audit**. Verify every concrete route against the current middleware, router dependencies, route-specific guards, password-change restrictions, and side effects. Record exceptions explicitly before UIX-00 can pass. Do not infer permission solely from an endpoint prefix.

| Endpoint Pattern | Allowed HTTP Methods | Role Access Level | Password Change Restriction | Notes |
|---|---|---|---|---|
| `/api/auth/login` | POST | Anonymous (Public) | Bypass | Returns JWT/session token |
| `/api/auth/me` | GET | Authenticated (All roles) | Allowed | Returns current user profile & role |
| `/api/auth/logout` | POST | Authenticated (All roles) | Allowed | Invalidates session |
| `/api/auth/change-password` | POST | Authenticated (All roles) | Allowed | Clears `must_change_password` flag |
| `/api/users/*` | GET, POST, PUT, DELETE | `admin` only | Blocked | User administration & credential resets |
| `/api/settings/*` | GET, POST | `admin` only | Blocked | System & runtime configuration |
| `/api/telegram/test` | POST | `admin` only | Blocked | Direct Telegram alert test |
| `/api/telegram/status` | GET | Authenticated (All roles) | Blocked | Read-only connection status |
| `/api/master-data/backfill` | POST | `trader`, `admin` | Blocked | Async kline backfill dispatch |
| `/api/master-data/build-features`| POST | `trader`, `admin` | Blocked | Async feature computation dispatch |
| `/api/custom-strategy/*` | POST, PUT, DELETE | `trader`, `admin` | Blocked | Strategy creation & modification |
| `/api/custom-strategy/*` | GET | Authenticated (All roles) | Blocked | Read-only strategy definitions |
| `/api/paper/*` (mutations) | POST, PUT, DELETE | `trader`, `admin` | Blocked | Order cancellation, position closing, config edits |
| `/api/paper/*` (reads) | GET | Authenticated (All roles) | Blocked | Ledger, positions, trades, metrics read-only |
| `/api/backtest/*` (mutations) | POST, PUT | `trader`, `admin` | Blocked | Run simulation, update promotion policy |
| `/api/backtest/*` (reads) | GET | Authenticated (All roles) | Blocked | History, strategies, audit logs |
| `/api/ml/*` (mutations) | POST, DELETE | `trader`, `admin` | Blocked | Run tournament, deploy model |
| `/api/ml/*` (reads) | GET | Authenticated (All roles) | Blocked | Models, experiments, manifests |
| `/api/wallets/*` (mutations) | POST, PUT, DELETE | `trader`, `admin` | Blocked | Track/untrack wallet, tracker settings |
| `/api/wallets/*` (reads) | GET | Authenticated (All roles) | Blocked | On-chain flow, tracked list, positions |
| `/api/research/*` (mutations) | POST | `trader`, `admin` | Blocked | Bot worker control, lifecycle toggles |
| `/api/research/*` (reads) | GET | Authenticated (All roles) | Blocked | Live stats, positions, closed trades |
| `/api/crypto/*` | GET | Authenticated (All roles) | Blocked | Binance symbol search & popular pairs |
| `/api/system/*` | GET | Authenticated (All roles) | Blocked | System health, collectors heartbeats |

---

## 4. Background Polling & Real-Time Data Flow

The legacy application manages background polling inside `App.init()` in `web/static/js/app.js`:

```javascript
// Active interval: 15,000ms (15s)
setInterval(async () => {
    if (!this.authToken || this.autoRefreshInProgress || document.hidden) return;
    this.autoRefreshInProgress = true;
    try {
        const refreshTasks = [this.fetchSystemStatus()];
        if (this.currentTab === "paper") refreshTasks.push(this.fetchPaperData());
        if (this.currentTab === "market-screener") refreshTasks.push(this.fetchScreenerData());
        if (this.currentTab === "wallet-tracking") refreshTasks.push(this.fetchWalletData({ silent: true }));
        await Promise.allSettled(refreshTasks);
    } finally {
        this.autoRefreshInProgress = false;
    }
}, 15000);
```

### Invariants for React Modernization:
1. **Document Visibility Pause:** Polling must halt when tab/window is hidden (`document.hidden` or `visibilitychange`).
2. **Concurrency Guard:** Stale or slow responses must not spawn overlapping requests (`autoRefreshInProgress` behavior managed by TanStack Query `staleTime`/`refetchInterval`).
3. **Tab Isolation:** Active polling must only execute for the currently visible view (except universal system status).
4. **Isolated Job Poller:** Asynchronous jobs (backfill, build-features) poll `/api/master-data/jobs/{job_id}` every 2 seconds until `status in ("completed", "failed")`.

---

## 5. Comprehensive View Inventory Table

| View ID / Navigation Label | Current Source | API/Data Dependencies | User Actions | Access Requirements | Risk Level | Loading & Error States | Accessibility Needs | Responsive Behavior | Required Tests & Acceptance | Migration Status |
|---|---|---|---|---|---|---|---|---|---|---|
| `view-home`<br>**Home & Overview** | `index.html` (`#view-home`), `app.js` (`renderHome`, `fetchSystemStatus`) | `GET /api/system/status`<br>`GET /api/master-data/coverage`<br>`GET /api/backtest/history` | Navigation tabs, "New Backfill" header action, "Sign Out" button | Authenticated (`viewer`, `trader`, `admin`) | Low (Read-only) | Skeletons for heartbeats and leaderboard; error toast on status failure | `aria-labelledby`, `aria-live="polite"` header, live clock, keyboard focusable skip-link | 4-col KPI collapses to 1-col on mobile; 3-col main grid collapses to 1-col | `tests/test_system_status.py`, pilot view Vitest + Playwright smoke test | Planned for UIX-02 Pilot |
| `view-master-data`<br>**Master Data** | `index.html` (`#view-master-data`), `app.js` (`renderMasterData`, `submitBackfill`, `triggerComputeFeatures`, `pollJobs`) | `GET /api/master-data/coverage`<br>`GET /api/crypto/search`<br>`GET /api/crypto/popular`<br>`POST /api/master-data/backfill`<br>`POST /api/master-data/build-features`<br>`GET /api/master-data/jobs` | Search symbol, trigger backfill modal, compute features per dataset, filter jobs | Read: All roles.<br>Mutations: `trader`, `admin` | High (Long async jobs, Binance rate limits) | Table skeletons; active job progress bars (0-100%); error alert banner on job failure | Form field associations (`for`/`id`), table header `scope="col"`, sort announcements, progress bar `aria-valuenow` | Table horizontal scroll on mobile; filter inputs wrap | `tests/test_master_data.py`, `tests/test_search.py`, form validation tests | Not started (UIX-04) |
| `view-backtest`<br>**Backtest Studio** | `index.html` (`#view-backtest`), `app.js` (`renderBacktestStudio`, `runBacktest`, `runBattleRoyale`, `renderBacktestChart`) | `GET /api/backtest/strategies`<br>`POST /api/backtest/run`<br>`POST /api/backtest/run-all`<br>`GET /api/backtest/history`<br>`GET /api/backtest/runs/{id}`<br>`GET/PUT /api/backtest/promotion-policy`<br>`GET /api/backtest/promotion-audit` | Select strategy & pair, set date range/capital, run backtest, run Battle Royale, inspect candlestick & equity chart, deploy to paper, update promotion policy | Read: All roles.<br>Run/Deploy: `trader`, `admin`.<br>Policy edit: `admin` | High (CPU compute, promotion gating) | Progress indicators; run button disabled during simulation; chart resize handlers; error dialogs | Candlestick canvas fallback description, form labels, focus trap on deploy dialog | Charts flex/resize responsively; parameter controls stack vertically on tablet/mobile | `tests/test_backtest.py`, `tests/test_accounting.py`, `tests/test_paper_backtest_parity.py` | Not started (UIX-04) |
| `view-paper`<br>**Paper Trading** | `index.html` (`#view-paper`), `app.js` (`fetchPaperData`, `addPaperStrategyConfig`, `togglePaperStrategy`, `togglePaperTelegram`, `togglePaperReport`, `manualClosePaperPosition`, `cancelPaperOrder`) | `GET /api/paper/configs`<br>`POST /api/paper/configs`<br>`GET /api/paper/metrics`<br>`GET /api/paper/positions`<br>`POST /api/paper/positions/{id}/close`<br>`GET /api/paper/orders`<br>`POST /api/paper/orders/{id}/cancel`<br>`GET /api/paper/trades`<br>`POST /api/paper/sync`<br>`GET /api/paper/circuit-breakers`<br>`POST /api/paper/circuit-breakers/*` | Add strategy config, toggle evaluation/telegram/report, filter order status, cancel pending order, close position, sync ticks, trip/reset circuit breaker | Read: All roles.<br>Mutations: `trader`, `admin` | Critical (Simulation ledger, crash recovery, Telegram gating) | Live badge updates; table reloading state; confirmation alerts; toast on trade events | ARIA switch roles for toggles, table sort announcements, confirm modals before close/cancel | Horizontal scroll wrappers for order ledger and positions tables; responsive KPI cards | `tests/test_paper_routes.py`, `tests/test_paper_orders_mariadb.py`, `tests/test_paper_metrics_visibility.py`, `tests/test_retailbot_accounting.py` | Not started (UIX-04) |
| `view-ml-studio`<br>**ML Forecast Studio** | `index.html` (`#view-ml-studio`), `app.js` (`fetchMLData`, `runMlTraining`, `deployMlModel`, `inspectMLExperiment`) | `GET /api/ml/models`<br>`GET /api/ml/experiments`<br>`GET /api/ml/experiments/{id}`<br>`POST /api/ml/run-experiment`<br>`POST /api/ml/deploy-to-paper`<br>`GET/POST /api/paper/circuit-breakers/*` | Run tournament experiment modal, inspect experiment (ROC/calibration/features), deploy model filter to paper, toggle circuit breaker | Read: All roles.<br>Train/Deploy: `trader`, `admin` | High (Heavy ML compute, manifest integrity) | Training spinner; evaluation metric charts; empty states for untrained datasets | Focus management on modals; chart accessible descriptions; progress bar semantics | 2-col tournament board collapses to 1-col; metrics tables scroll horizontally | `tests/test_ml_routes.py`, `tests/test_ml_promotion.py` | Not started (UIX-04) |
| `view-strategies`<br>**Strategy Library** | `index.html` (`#view-strategies`), `app.js` (`renderStrategyLibrary`, `initStrategyArchitect`, `fetchStrategies`, `saveCustomStrategy`, `deleteCustomStrategy`) | `GET /api/backtest/strategies`<br>`GET /api/custom-strategy/list`<br>`GET /api/custom-strategy/indicators`<br>`POST /api/custom-strategy/create`<br>`POST /api/custom-strategy/generate-ai`<br>`POST /api/custom-strategy/quick-test`<br>`DELETE /api/custom-strategy/{name}` | Browse strategy cards, build visual rule tree, prompt AI strategy generator, quick-test strategy, save/delete custom strategy, send to backtest | Read: All roles.<br>Mutations: `trader`, `admin` | Medium (Custom code/rule safety, JSON schema validation) | AI prompt spinner; syntax validation errors; rule builder block drag/drop state | Accessible names on dynamic rule builder inputs; delete confirmation alerts | Builder grid wraps from 2-col to 1-col on mobile; card grid auto-reflows | `tests/test_custom_strategy.py` | Not started (UIX-04) |
| `view-news-ai`<br>**News AI Signals** | `index.html` (`#view-news-ai`), `app.js` (`fetchNewsData`, `renderNewsSignals`, `renderNewsFeed`) | `GET /api/news/signals`<br>`GET /api/news/events`<br>`GET /api/news/stats` | Filter news feed by symbol/keyword, filter sentiment (Bullish/Bearish), manual refresh | Authenticated (`viewer`, `trader`, `admin`) | Low (Read-only overlay display) | Skeletons for sentiment gauges and news cards; empty state on no keyword match | Sentiment badge contrast compliance; screen-reader labels for confidence gauges | News feed and metrics panel stack vertically on tablets and mobile devices | `tests/test_news_routes.py` | Not started (UIX-04) |
| `view-research`<br>**Research Bots** | `index.html` (`#view-research`), `app.js` (`fetchResearchData`, `_fetchBotControl`, `toggleBotLifecycleSetting`) | `GET /api/research/retailbot2/stats`<br>`GET /api/research/retailbot2/positions`<br>`GET /api/research/retailbot2/trades`<br>`GET/POST /api/research/retailbot2/strategies`<br>`GET/POST /api/research/retailbot2/control` | Toggle lifecycle (paper, telegram, report) per strategy/mode (shadow/inverse), start/stop/restart bot daemon, filter closed trades | Read: All roles.<br>Mutations: `trader`, `admin` | Critical (Daemon process control, strict boolean flags, SQL column aliases) | Worker status pill (green/red/pulsing); toggle transition locks; error toast | ARIA switch roles, distinct shadow vs inverse labels, sortable trade headers | Dual shadow/inverse comparison grid stacks into single column on mobile | `tests/test_retailbot2_lifecycle.py`, `tests/test_retailbot_accounting.py` | Not started (UIX-04) |
| `view-market-screener`<br>**Market Screener** | `index.html` (`#view-market-screener`), `app.js` (`fetchScreenerData`, `renderScreenerTable`, `filterScreener`) | `GET /api/research/screener/status`<br>`GET /api/research/screener/signals`<br>`GET /api/research/screener/stats`<br>`GET/POST /api/research/screener/control` | Filter signals by volume/quality/symbol, sort columns, screener daemon control, "Send to Backtest" shortcut | Read: All roles.<br>Control: `trader`, `admin` | Medium (Real-time screening, worker control) | Table skeleton; zero results message; background polling every 15s when active | Table sort announcements with counts; filter form labels; keyboard navigable table | Table horizontal scroll wrapper; responsive filter toolbar | `tests/test_screener.py` | Planned for UIX-03 |
| `view-wallet-tracking`<br>**Wallet Tracking** | `index.html` (`#view-wallet-tracking`), `app.js` (`fetchWalletData`, `_wtLoadSettings`, `saveWalletSettings`, `walletTrack`, `walletUntrack`, `walletToggleTrack`) | `GET /api/wallets/status`<br>`GET /api/wallets/tracked`<br>`POST/DELETE /api/wallets/tracked`<br>`POST /api/wallets/tracked/toggle`<br>`GET /api/wallets/events`<br>`GET /api/wallets/positions`<br>`GET /api/wallets/token-flow`<br>`GET/PUT /api/wallets/settings` | Add tracked address, remove wallet, toggle tracking active, filter events, change token-flow period (6h/24h/7d), update alert thresholds | Read: All roles.<br>Track/Untrack: `trader`, `admin`.<br>Settings: `admin` | Medium (External RPC/scan parsing) | Skeletons for event stream and token flow cards; address validation error notices | Form input labels, monospace wallet address styling with copy feedback, table headers | Flow charts and transaction tables stack on smaller screens | `tests/test_wallet_tracker.py`, `tests/test_wallet_tracker_mariadb.py` | Not started (UIX-04) |
| `view-settings`<br>**Settings & Users** | `index.html` (`#view-settings`), `app.js` (`renderSettings`, `fetchUsers`, `handleCreateUser`, `toggleUserStatus`, `submitResetPassword`, `testTelegram`) | `GET /api/settings/status`<br>`GET/POST /api/users`<br>`PUT/DELETE /api/users/{id}`<br>`POST /api/users/{id}/reset-password`<br>`GET /api/telegram/status`<br>`POST /api/telegram/test` | Create user, toggle user active status, admin password reset, delete user, test Telegram bot connection, inspect DB metrics | Admin-only for users & telegram test (`admin`).<br>Status read: All roles | Critical (Security, RBAC credentials, session revocation) | User creation button spinner; error alert for weak passwords; confirmation dialogs | Confirmation modals for deactivation/deletion; explicit role select labels; focus trapping | User table scrolls horizontally; configuration cards wrap | `tests/test_auth_routes.py`, `tests/test_users.py` | Not started (UIX-04) |

---

## 6. Modal Dialogs Inventory Table

| Dialog ID & Title | Trigger Source | Verified API Endpoints | Form Fields & Actions | Access Requirement | Accessibility Semantics | Target Modern Component |
|---|---|---|---|---|---|---|
| `login-modal`<br>"Sign In to Signal Bot Pro" | Automatic on unauthenticated visit or 401 session expiry | `POST /api/auth/login` | Username, Password, "Authenticate & Enter Terminal" button | Public (Anonymous) | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="login-modal-title"`, `aria-live="assertive"` error box, focus trapped | Radix / shadcn `Dialog` with React Hook Form + Zod |
| `force-password-modal`<br>"Set a new password" | Automatic when `currentUser.must_change_password === true` | `POST /api/auth/change-password` | Current password, New password (min 10 chars), Confirm new password, "Save & continue", "Sign out instead" | Authenticated with temporary password | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="force-password-title"`, `aria-describedby="force-password-help"` | Radix / shadcn `Dialog` with strict validation |
| `backfill-modal`<br>"Start Historical Backfill" | Header action button ("New Backfill") | `POST /api/master-data/backfill`<br>`GET /api/crypto/search` | Symbol search, Market (Spot/Futures), Timeframe, Years slider, Auto-features checkbox, Cancel, Start Backfill | `trader`, `admin` | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="backfill-modal-title"`, Escape dismiss | Radix / shadcn `Dialog` with Combobox |
| `deploy-modal`<br>"Deploy Strategy to Paper Trading" | Backtest Studio leaderboard row action | `POST /api/paper/deploy` | Pre-filled strategy/symbol, Capital allocation input, Risk % dropdown, Telegram alerts toggle, Cancel, Confirm Deploy | `trader`, `admin` | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="deploy-modal-title"`, form field associations | Radix / shadcn `Dialog` with confirmation guard |
| `modal-run-ml`<br>"Train ML Forecast Model" | ML Studio view action ("Run Tournament") | `POST /api/ml/run-experiment` | Dataset dropdown, Algorithm checkboxes (LightGBM, XGBoost, CatBoost, LogisticRegression), Horizon, Calibration toggle, Run button | `trader`, `admin` | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="modal-run-ml-title"` | Radix / shadcn `Dialog` with multi-step validation |
| `modal-deploy-ml`<br>"Deploy ML Model Filter" | ML Studio tournament leaderboard row action | `POST /api/ml/deploy-to-paper` | Target paper strategy select, Probability threshold slider, Filter mode select, Cancel, Deploy button | `trader`, `admin` | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="modal-deploy-ml-title"` | Radix / shadcn `Dialog` with slider control |
| `modal-inspect-ml`<br>"Model Tournament Evaluation" | ML Studio tournament leaderboard row action | `GET /api/ml/experiments/{id}` | Inspect ROC curve, Calibration curve, Top feature importance chart, Close button | Authenticated (All roles) | `role="dialog"`, `aria-modal="true"`, focus returns to triggering row on close | Radix / shadcn `Dialog` or `Sheet` |

---

## 7. Dynamic & Shared UI Components

1. **Top Application Bar:**
   - Elements: Page title (`#view-header-title`), description (`#view-header-desc`), "New Backfill" trigger button, user badge (`#top-user-badge`), role badge (`#top-user-role`), and logout button (`#top-user-container`).
   - Behaviors: Dynamically updates header text upon tab switch; reflects active username and role; hides when unauthenticated.
2. **Left Navigation Sidebar:**
   - Elements: Brand logo (`⚡ Signal Bot Pro`), 11 navigation buttons (`.nav-btn`), WIB Real-Time Clock (`#wib-clock`), Database Status Badge (`#status-db-badge`), version footer.
   - Behaviors: Sets `aria-current="page"` and `.active` class on current tab; WIB clock updates every second via `setInterval`; database status badge polls `/api/settings/status` and `/api/system/status`.
3. **Toast Notification System:**
   - Implementation: Injected into `#toast-container` with type palette (`success`, `error`, `warning`, `info`), accessible `role="status"` or `role="alert"`, `aria-atomic="true"`, respect for `prefers-reduced-motion`, and focus restoration to invoking trigger upon dismissal.
4. **Sortable Table Enhancement Bundle (`dashboard_enhancements.js`):**
   - Injected dynamically into `GET /` response.
   - Provides table header `scope="col"`, `aria-sort="ascending|descending|none"`, sort indicators with theme contrast support, row count announcements, and prevents repeated keyboard sorting events.

---

## 8. Chart & Visualization Inventory

| View Location | Chart Container ID | Chart Technology | Purpose & Data Rendered | Parity Invariant for Modernization |
|---|---|---|---|---|
| Backtest Studio | `#candlestick-chart-box` | TradingView Lightweight Charts v4.1.1 | High-performance interactive candlestick chart with entry/exit trade markers and volume histogram | Must support dark/light theme, crosshair sync, window resize reflow, and tooltip inspection |
| Backtest Studio | `#equity-chart-canvas` | Chart.js (v4+) | Historical portfolio equity curve vs. buy-and-hold benchmark | Must plot drawdown underwater chart and high-water mark curve |
| Market Screener | Dynamic container | Chart.js / SVG meters | Smart-money volume spike distribution and quality score gauges | Accessible tabular data fallback required |
| News AI Signals | Dynamic sentiment cards | Tailwind CSS meters & SVG gauges | Bullish/Bearish sentiment ratios and confidence intervals | Screen-reader accessible percentage values |
| ML Forecast Studio | `#modal-inspect-ml` canvas | Chart.js | ROC curves, calibration curves, and feature importance bar rankings | Must support crisp rendering on high-DPI displays |

---

## 9. Baseline Visual & Accessibility Verification Status

- **Browser Screenshot Verification:** Marked **Unverified** in current host environment. The execution environment lacks a configured headless browser or display server for automated visual snapshot capture. Visual snapshots will be recorded during Phase UIX-01 / UIX-02 when Playwright browser binaries are provisioned.
- **Accessibility Baseline:** Previous PRs #195–#233 established accessible skip-links, table column scope, theme contrast, dialog ARIA attributes, and live regions in the legacy UI. All these semantics are cataloged in this inventory and must be preserved or improved in the React implementation.

---

## 10. Parity & Acceptance Gate Checklist for Future Phases

- [ ] **UIX-00 (Baseline Inventory):** Draft maps 11 views and 7 dialogs; acceptance remains blocked pending a complete source-linked endpoint register, endpoint-level RBAC/side-effect verification, and reconciliation of UI states against source.
- [ ] **UIX-01 (Frontend Foundation):** `frontend/` Vite + React + TypeScript setup with Tailwind and primitives; zero replacement of legacy dashboard.
- [ ] **UIX-02 (Read-Only Pilot):** Executive Trading Overview (`view-home`) pilot view previewed at safe route with 100% data parity and accessibility compliance.
- [ ] **UIX-03 (Market Screener V2):** Complete migration of Market Screener V2 slice with verified signal display, filtering, and worker controls.
- [ ] **UIX-04 (Strategy & Paper Trading):** Phased migration of paper-trading ledger, backtest studio, custom strategy builder, ML studio, research bots, and settings.
- [ ] **UIX-05 (Consolidation & Release Readiness):** Full parity sign-off, responsive verification, and legacy dashboard deprecation.


## 11. Review findings and required closure work

The current document is a draft, not an approved source of truth. Before UIX-00 is accepted, the owner/reviewer must verify and update all of the following against the current `master` source:

- [ ] Add a complete endpoint register with one row per concrete route and HTTP method: path, router/module, handler, request parameters/body, response model or shape, auth dependency, role requirement, forced-password-change behavior, mutation/side effects, and relevant tests.
- [ ] Reconcile the estimated endpoint count with the actual FastAPI route registry; document excluded framework/static/health routes and counting rules.
- [ ] Check every navigation view, conditional panel, modal, form action, polling loop, and API call against the actual HTML/JavaScript, not only a view-level summary.
- [ ] Verify all RBAC exceptions and consequential actions, especially user administration, settings, backfill/build jobs, backtest execution/promotion policy, paper close/cancel/circuit-breakers, ML experiment/deploy, wallet settings, worker controls, and Telegram test actions.
- [ ] Label every statement as source-verified, repository search result, environment-specific/unverified, or future acceptance requirement.
- [ ] Review chart “parity invariants” and distinguish existing behavior from proposed requirements; do not state a chart or behavior is current unless source confirms it.
- [ ] Re-run the review against the current base commit before accepting UIX-00.

**Gate decision:** UIX-00 remains **pending** until the above evidence is recorded and reviewed. Do not begin UIX-01 implementation before the inventory acceptance PR is merged.
