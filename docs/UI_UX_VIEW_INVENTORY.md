# Signal Bot UI/UX Comprehensive View & API Inventory (UIX-00)

**Document Version:** 1.2.0  
**Status:** In review — Market Screener control editor is now reachable in the React preview; full route-by-route source verification and owner acceptance pending  
**Target Architecture:** React 18+ + TypeScript + Vite + Tailwind CSS + shadcn/ui + TanStack Query v5 + TanStack Table v8  
**Source baseline for reconciliation:** `57d3564a5b7211511f65a4fab8d70552cb12f729` (master after PR #528, 2026-10-28; runtime route-registry and frontend endpoint-parity tests exist, but complete row-by-row inventory verification and owner acceptance remain pending)  
**Execution Brief Reference:** [`docs/UI_UX_ANTIGRAVITY_EXECUTION_BRIEF.md`](UI_UX_ANTIGRAVITY_EXECUTION_BRIEF.md)  
**Modernization Plan Reference:** [`docs/UI_UX_MODERNIZATION_PLAN.md`](UI_UX_MODERNIZATION_PLAN.md)  

---

## 1. Executive Summary & Audit Scope

This document is Antigravity's detailed baseline inventory draft for **Phase UIX-00** of the Signal Bot UI/UX modernization program. It contains a row-by-row register intended to cover dashboard views, modal dialogs, polling, FastAPI routes, RBAC, request/response schemas, and side effects. The register has not yet been independently verified row-by-row against the current `master` source; do not treat its entries or totals as authoritative until that review is complete.

### Audit Summary Metrics
- **Navigation Views Audited:** 11 primary views (`view-home`, `view-master-data`, `view-backtest`, `view-paper`, `view-ml-studio`, `view-strategies`, `view-news-ai`, `view-research`, `view-market-screener`, `view-wallet-tracking`, `view-settings`).
- **Modal Dialogs Audited:** 7 interactive dialogs (`login-modal`, `force-password-modal`, `backfill-modal`, `deploy-modal`, `modal-run-ml`, `modal-deploy-ml`, `modal-inspect-ml`).
- **Backend API Endpoint Rows Listed:** Source inspection counted 81 `@router` HTTP endpoint decorators across the 13 modules explicitly included by `web/app.py` (`auth`, `paper`, `search`, `master_data`, `backtest`, `settings`, `system_status`, `custom_strategy`, `telegram`, `ml`, `news`, `research`, and `wallet`). `web/app.py` also declares `GET /` outside those routers. This verifies the static decorator count, not runtime registry completeness or one-to-one parity with all 81 rows in the draft register.
- **Frontend Tooling State:** The isolated React preview exists under `frontend/` with `package.json`, TypeScript, Vite, and Vitest. The npm lockfile is committed in PR #456 and CI uses `npm ci` after PR #457. Playwright browser smoke coverage has passed in CI, but it does not replace broader browser/device acceptance or owner sign-off.
- **Serving Configuration:** FastAPI mounts `web/static/` at `/static` and dynamically injects `dashboard_enhancements.js` into `GET /`.
- **Gate Invariant:** UIX-00 remains in "In Review / Pending Acceptance" status until route-by-route source verification and owner acceptance are recorded. The isolated React preview has already been implemented in reviewed slices, but must remain disconnected from FastAPI production serving until release gates pass.

---

## 2. Frontend Tooling & Serving Configuration Audit

### 2.1 Current Production Serving Architecture
- **Static Asset Mount:** Mounted in `web/app.py`:
  ```python
  STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
  app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
  ```
- **Entry Route (`GET /`):** Served via `serve_index()` in `web/app.py`. Reads `web/static/index.html` and injects:
  ```html
  <script src="/static/js/dashboard_enhancements.js?v=20261016-2"></script>
  ```
  immediately before `</body>`.
- **CORS Configuration:** Controlled via `SIGNAL_BOT_ALLOWED_ORIGINS` (default: `http://localhost:8050,http://127.0.0.1:8050`). `allow_credentials=True`, methods `["GET", "POST", "PUT", "DELETE", "OPTIONS"]`, headers `["Authorization", "Content-Type"]`.

### 2.2 Repository Tooling & Serving State (rechecked 2026-10-28)
- **Frontend manifests:** `frontend/package.json`, `frontend/tsconfig.json`, and `frontend/vite.config.ts` exist. The preview uses React, TypeScript, Vite, and Vitest; `npm run build` runs the TypeScript check before the production build.
- **Lockfile:** `frontend/package-lock.json` is committed on `master` after PR #456, and the frontend workflow uses `npm ci`. Frontend workflow run #132 completed successfully on 2026-10-27, including type-check, unit tests, production build, Playwright setup, and browser smoke tests. Reproducible frontend installation is verified for that run; broader migration acceptance remains open.
- **Host tooling:** The older note that Node.js was unavailable came from a prior host audit and is not evidence of current CI or execution-environment availability. Verify Node/npm versions in the actual environment used for any build.
- **Serving boundary:** Modern frontend code remains isolated under `frontend/`, with build output to `frontend/dist/`. FastAPI still serves the legacy `web/static/` dashboard; do not switch routes or mount the preview before browser, parity, RBAC, accessibility, and owner-acceptance gates pass.

### 2.3 React Preview Mutation Boundary (rechecked 2026-10-28)

The preview is mostly read-only, with three intentional, role-gated mutation surfaces and one non-persisted compute action.

- `ScreenerControlsPanel.tsx` calls `POST /api/research/screener/control` only when an admin or trader explicitly selects **Save changes**. Viewer sessions do not fetch the editor configuration and cannot submit changes. The request changes persisted screener settings, including the Telegram alert gate and detection thresholds; it does not itself send a Telegram message or restart the worker.
- `MasterDataPanel.tsx` allows admin/trader users to review and confirm a backfill request (`POST /api/master-data/backfill`) or feature-build request (`POST /api/master-data/build-features`). These actions can write OHLCV/features and launch background workers. Viewer sessions cannot access the controls.
- `BacktestStudioPanel.tsx` allows admin/trader users to submit `POST /api/backtest/run` only after reviewing and confirming a run configuration. The response is shown with an explicit saved-run ID when persistence is confirmed; a null `runId` is presented as persistence not confirmed. This simulates historical trades only and does not execute live orders.
- `StrategyLibraryPanel.tsx` offers an explicit admin/trader `POST /api/custom-strategy/quick-test` action. It simulates against up to 1,000 stored candles and returns metrics without saving or activating the strategy.
- Backend authorization remains authoritative. UI hiding is defense in depth, not a replacement for route-level role enforcement.
- Other React preview panels remain read-only. Strategy creation/deletion/AI generation, promotion, deployment, wallet-tracking control, and Telegram-send actions are not exposed.
- **Safety note:** Do not point the preview at a production-connected API while experimenting with these controls unless changing the live screener configuration, starting data-processing jobs, running a persisted backtest, or spending compute on a quick test is intended. This does not authorize production serving of the React preview.

Browser smoke tests that navigate between views assert no incidental mutation requests; focused component tests cover each explicit action flow and viewer restrictions.

---

## 3. Global Security, RBAC & Authentication Architecture

API requests to `/api/*` are guarded by the `api_security_boundary` middleware in `web/app.py`.

### 3.1 Authentication Tokens & Session
- **Bearer Token Authentication:** Header format `Authorization: Bearer <token>`.
- **Token Verification:** Decoded via `authenticate_request(request)` using `SECRET_KEY` in `web/auth.py`.
- **Rate Limiting:** Durable rate limiter (`enable_durable_rate_limit()`) protects against credential brute-forcing.

### 3.2 Role Hierarchy & Enforcement Matrix
- `admin`: Full system access, user administration, password resets, system settings, Telegram test.
- `trader`: Can execute backtests, trigger backfills/feature builds, deploy to paper, update paper configs, trip circuit breakers, control research bots/screener. Cannot manage users or system settings.
- `viewer`: Read-only access to dashboards, charts, metrics, positions, orders, and trade history. Cannot trigger mutations or dispatch jobs.

```
       [ Anonymous (Unauthenticated) ]
                      │
            POST /api/auth/login
                      ▼
            [ Authenticated User ]
             ┌────────┴────────┐
             ▼                 ▼
   must_change_password=True  must_change_password=False
   (BLOCKED from all APIs    ┌─────────┼─────────┐
    except /api/auth/me,     ▼         ▼         ▼
    /api/auth/logout,     Viewer    Trader     Admin
    /api/auth/change-pwd) (Read-only) (Trading) (Full Control)
```

### 3.3 Forced-Password-Change Guard
If `user.get("must_change_password") == True`, requests to endpoints outside `PASSWORD_CHANGE_ALLOWED_PATHS` are immediately rejected:
```json
HTTP 403 Forbidden
{
  "detail": "Password change required before continuing",
  "code": "password_change_required"
}
```

---

## 4. Draft Endpoint-by-Endpoint Register (81 Listed Route Rows)

The following draft register lists 81 API endpoint rows across the 13 router modules included in `web/app.py`. Static source inspection found 81 `@router` HTTP endpoint decorators across those modules, plus the app-level `GET /` route. The remaining audit is to compare every row and effective prefix/method/path with FastAPI's runtime route registry, account for mounted/static and framework routes, and verify handler contracts, RBAC, forced-password behavior, polling, and side effects. Do not treat the inventory as accepted until that mapping and owner review are recorded.

### 4.1 Authentication & User Administration (`auth_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `POST /api/auth/login` | `login`<br>`auth_routes.py` | Anonymous (Public) | Bypassed (Public) | **Body:** `LoginRequest`<br>`{username: str, password: str}` | **200 OK:**<br>`{"token": str, "user": {"id": int, "username": str, "email": str, "role": str, "must_change_password": bool}}`<br>**401:** Invalid credentials | Authenticates bcrypt hash; writes `LOGIN_SUCCESS` or `LOGIN_FAILED` to `audit_logs`; resets failure count | `#login-modal`<br>`App.handleLogin()` |
| `POST /api/auth/logout` | `logout`<br>`auth_routes.py` | Authenticated (All roles) | Allowed during change | **Headers:** `Authorization: Bearer <token>` | **200 OK:**<br>`{"status": "success", "message": "Successfully logged out"}` | Writes `LOGOUT` to `audit_logs`; client clears `localStorage` token | `#top-user-container`<br>`App.handleLogout()` |
| `GET /api/auth/me` | `get_current_user_profile`<br>`auth_routes.py` | Authenticated (All roles) | Allowed during change | **Headers:** `Authorization: Bearer <token>` | **200 OK:**<br>`{"id": int, "username": str, "email": str, "role": str, "must_change_password": bool}` | Read-only session verification; refreshes client user state | Header badge & `App.checkAuth()` |
| `POST /api/auth/change-password` | `change_password`<br>`auth_routes.py` | Authenticated (All roles) | Allowed during change | **Body:** `ChangePasswordRequest`<br>`{old_password: str, new_password: str}` (min 10 chars, complexity enforced) | **200 OK:**<br>`{"status": "success", "message": "Password changed successfully"}`<br>**400:** Password policy error | Updates bcrypt hash in `users`; clears `must_change_password` flag; writes `USER_PASSWORD_CHANGE` to `audit_logs` | `#force-password-modal`<br>`App.handleForcedPasswordChange()` |
| `GET /api/auth/audit-logs` | `get_audit_logs`<br>`auth_routes.py` | `admin` only (`Depends(require_admin)`) | Blocked (403 required) | **Query:** `limit: int = 50` | **200 OK:**<br>`{"items": List[{"id": int, "action": str, "username": str, "user_id": int, "details": dict, "ip_address": str, "created_at": str}]}` | Read-only query on MariaDB `audit_logs` table | Settings View (`#view-settings`) audit table |
| `GET /api/users` | `list_users`<br>`auth_routes.py` | `admin` only (`Depends(require_admin)`) | Blocked (403 required) | None | **200 OK:**<br>`{"users": List[{"id": int, "username": str, "email": str, "role": str, "is_active": bool, "must_change_password": bool, "created_at": str, "updated_at": str}]}` | Read-only query on `users` table | User Administration panel (`#view-settings`) |
| `POST /api/users` | `create_user`<br>`auth_routes.py` | `admin` only (`Depends(require_admin)`) | Blocked (403 required) | **Body:** `CreateUserRequest`<br>`{username: str, email: Optional[str], password: str, role: str}` | **201 Created:**<br>`{"status": "success", "user": {"id": int, "username": str, "email": str, "role": str, "is_active": bool, "must_change_password": bool}}`<br>**400:** Duplicate/Weak | Inserts row into `users` with `must_change_password=1`; writes `USER_CREATE` to `audit_logs` | Add User Modal / Form (`#view-settings`) |
| `POST /api/users/{user_id}/reset-password` | `reset_user_password`<br>`auth_routes.py` | `admin` only (`Depends(require_admin)`) | Blocked (403 required) | **Path:** `user_id: int`<br>**Body:** `ResetPasswordRequest`<br>`{new_password: str}` | **200 OK:**<br>`{"status": "success", "message": str}`<br>**404:** User not found | Updates password hash in `users`; sets `must_change_password=1`; writes `USER_PASSWORD_RESET` to `audit_logs` | Reset Password dialog (`#view-settings`) |
| `PUT /api/users/{user_id}` | `update_user`<br>`auth_routes.py` | `admin` only (`Depends(require_admin)`) | Blocked (403 required) | **Path:** `user_id: int`<br>**Body:** `UpdateUserRequest`<br>`{role: Optional[str], is_active: Optional[bool]}` | **200 OK:**<br>`{"status": "success", "message": str}`<br>**400:** Cannot deactivate last admin | Modifies user permissions/status in `users`; writes `USER_UPDATE` to `audit_logs` | User row toggle / edit (`#view-settings`) |
| `DELETE /api/users/{user_id}` | `delete_user`<br>`auth_routes.py` | `admin` only (`Depends(require_admin)`) | Blocked (403 required) | **Path:** `user_id: int` | **200 OK:**<br>`{"status": "success", "message": str}`<br>**400:** Self-deletion forbidden | Deletes user record from `users`; writes `USER_DELETE` to `audit_logs` | User row delete action (`#view-settings`) |

---

### 4.2 Master Data & Historical Backfill (`master_data.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/master-data/coverage` | `get_data_coverage`<br>`master_data.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"summary": {"totalCandles": int, "distinctSymbols": int, "coverageEntries": int}, "coverage": List[{"symbol": str, "market": str, "timeframe": str, "candleCount": int, "featureCount": int, "hasFeatures": bool, "minOpenTime": datetime|null, "maxCloseTime": datetime|null, "startDate": str|null, "endDate": str|null}]}` | Aggregates OHLCV metadata from MariaDB `ohlcv` and `features` tables | Overview KPI cards & Master Data dataset table |
| `POST /api/master-data/backfill` | `trigger_backfill`<br>`master_data.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `BackfillRequest`<br>`{symbol: str, market: str, timeframe: str, years: float, auto_compute_features: bool}` | **200 OK:**<br>Job object: `{"id": str, "type": "backfill", "symbol": str, "market": str, "timeframe": str, "years": float, "status": "queued", "progress": 0, "candles_fetched": 0, "message": str, "created_at": epoch_seconds, "updated_at": epoch_seconds}` | Creates job in `job_store`; spawns asynchronous background kline backfill task; queries Binance REST API | `#backfill-modal`<br>`App.submitBackfill()` |
| `POST /api/master-data/build-features` | `trigger_build_features`<br>`master_data.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `FeatureBuildRequest`<br>`{symbol: str, market: str, timeframe: str}` | **200 OK:**<br>Job object: `{"id": str, "type": "build_features", "symbol": str, "market": str, "timeframe": str, "status": "queued", "progress": 0, "message": str, "created_at": epoch_seconds, "updated_at": epoch_seconds}` | Spawns background worker to calculate TA-Lib/pandas technical features; writes to `features` table | Dataset table action (`#view-master-data`) |
| `GET /api/master-data/jobs` | `list_jobs`<br>`master_data.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"jobs": List[{"id": str, "type": str, "symbol": str, "market": str, "timeframe": str, "status": str, "progress": number, "message": str, "created_at": epoch_seconds, "updated_at": epoch_seconds, ...}]}` | Reads active and recent job state from memory and persistent `job_store` | Active Jobs table (`#view-master-data`) |
| `GET /api/master-data/jobs/{job_id}` | `get_job`<br>`master_data.py` | Authenticated (All roles) | Blocked (403 required) | **Path:** `job_id: str` | **200 OK:**<br>Job object returned by `get_job`, matching stored/live job fields (`id`, `status`, `progress`, `message`, timestamps, and type-specific fields); **404:** Job not found<br>**404:** Job not found | Reads discrete job progress; used by polling worker | Polling cycle (`App.pollJobs`) |

---

### 4.3 Market Asset Search (`search.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/crypto/search` | `search_crypto`<br>`search.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `q: str = ""`, `market: str = "all"`, `limit: int = 25` (1–100) | **200 OK:**<br>`{"query": str, "market": str, "count": int, "results": List[{"symbol": str, "baseAsset": str, "quoteAsset": "USDT", "market": "spot"|"futures", "lastPrice": number, "priceChangePercent": number, "highPrice": number, "lowPrice": number, "volume": number, "quoteVolume": number}]}` | Reads cached 24-hour ticker snapshots from Binance spot and/or futures endpoints; cache TTL 60s, stale cached data may be returned after provider errors | Legacy symbol-search input in backfill flow (`#modal-backfill-symbol`) |
| `GET /api/crypto/popular` | `popular_crypto`<br>`search.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `market: str = "spot"` | **200 OK:**<br>`{"market": str, "items": List[ticker]}`; each ticker has the same fields as `/api/crypto/search` | Returns the top 15 USDT ticker rows by quote volume for futures only when `market=futures`; otherwise returns spot tickers. Uses Binance-backed 60s cache and may fall back to stale cache on provider errors | Legacy quick-select list (`#popular-coins-list`) |

---

### 4.4 Backtest Studio & Visual Simulator (`backtest.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/backtest/strategies` | `list_strategies`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"strategies": List[{"id": str, "baseName": str, "isInverse": bool, "displayName": str, "category": str, "validated": bool, "description": str, "params": List[dict], "sharedParams": List[dict]}]}` | Introspects executable `STRATEGIES` registry and attaches display metadata plus per-strategy/shared parameter definitions | Strategy dropdowns across Backtest and Paper views |
| `POST /api/backtest/run` | `execute_backtest`<br>`backtest.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `BacktestRunRequest`<br>`symbol: str` required; `market: str = "spot"`; `timeframe: str = "1d"`; `strategy: str = "confluence_ensemble_v1"`; `data_segment: str = "full"`; `initial_capital: float = 5000 (min 100)`; `risk_per_trade_pct: float = 1.0 (0.1–10)`; `save_to_db: bool = true`; `params: dict = {}` | **200 OK:**<br>`{"runId": int|null, "symbol": str, "market": str, "timeframe": str, "strategy": str, "dataSegment": str, "totalCandles": int, "metrics": dict, "equity": dict, "trades": List[dict], "candles": List[dict]}` | Runs event-driven simulation; if `save_to_db=true`, attempts persistence in `backtest_runs` and `backtest_trades`; response uses `equity`, not `equityCurve` | Legacy Run Backtest form (`#view-backtest`) |
| `POST /api/backtest/run-all` | `run_all_strategies`<br>`backtest.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `RunAllStrategiesRequest`<br>`{symbol: str, market: str, timeframe: str, data_segment: str, initial_capital: float, risk_per_trade_pct: float}` | **200 OK:**<br>`{"symbol": str, "market": str, "timeframe": str, "leaderboard": List[dict], "aiInsight": Optional[str]}` | Executes Battle Royale tournament across all registered strategies; ranks by profit factor / expectancy | "Battle Royale" button (`#view-backtest`) |
| `POST /api/backtest/ai-insight` | `get_standalone_ai_insight`<br>`backtest.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `{symbol: str, market: str, timeframe: str, strategy: str}` | **200 OK:**<br>`{"symbol": str, "strategy": str, "metrics": dict, "insight": str}` | Generates quantitative performance diagnosis and trade risk critique | "AI Quantitative Insight" panel (`#view-backtest`) |
| `GET /api/backtest/history` | `get_backtest_history`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `symbol: Optional[str]`, `timeframe: Optional[str]`, `strategy: Optional[str]`, `limit: int = 30` (1–100) | **200 OK:**<br>`{"runs": List[{"id": int, "symbol": str, "market": str, "timeframe": str, "strategy": str, "dataSegment": str, "totalTrades": int, "winRatePct": number|null, "expectancyR": number|null, "profitFactor": number|null, "maxDrawdownR": number|null, "createdAt": str}]}` | Reads persisted simulation history from `backtest_runs`, newest IDs first; React preview explicitly requests `limit=100` | Legacy leaderboard/history (`#view-backtest`, `#view-home`); React read-only preview |
| `GET /api/backtest/runs/{run_id}` | `get_run_details`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | **Path:** `run_id: int` | **200 OK:**<br>`{"run": {"id": int, "symbol": str, "market": str, "timeframe": str, "strategy": str, "dataSegment": str, "params": dict, "totalTrades": int, "winRatePct": number|null, "expectancyR": number|null, "profitFactor": number|null, "maxDrawdownR": number|null, "createdAt": str}, "trades": List[{"direction": str, "entryTime": datetime, "entryTimeWib": str, "entryPrice": number, "exitTime": datetime, "exitTimeWib": str, "exitPrice": number, "exitReason": str, "stopLoss": number, "takeProfit": number, "netReturnPct": number, "rMultiple": number|null, "holdingBars": int}]}`<br>**404:** Run not found | Fetches persisted run metadata and closed trades only; this handler does not return `equityCurve` or `candles` | Legacy history drill-down and React read-only preview |
| `GET /api/backtest/promotion-policy` | `get_promotion_policy`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"policy": {"enabled": bool, "min_test_trades": int, "min_profit_factor": float, "min_expectancy_r": float, "max_drawdown_r": float, "min_rank_score": float, "require_holdout": bool, "send_telegram": bool}}` | Reads auto-promotion qualification criteria | Promotion Policy drawer (`#view-backtest`) |
| `PUT /api/backtest/promotion-policy` | `update_promotion_policy`<br>`backtest.py` | `admin` only (`_role_allowed` enforcement) | Blocked (403 required) | **Body:** `StrategyAutoPromotionPolicyRequest`<br>`enabled: bool = false`; `min_test_trades: int = 30 (30–10000)`; `min_profit_factor: float = 1.2 (1–10)`; `min_expectancy_r: float = 0.05 (0–10)`; `max_drawdown_r: float = 8.0 (0–100, >0)`; `min_rank_score: float = 0.1 (0.01–1)`; `require_holdout: bool = true`; `send_telegram: bool = true` | **200 OK:**<br>`{"status": "ok", "policy": dict}` when persisted readback succeeds; `{"status": "saved_readback_unavailable", "policy": submitted_values, "warning": str}` when write succeeds but readback fails<br>**400:** Threshold validation error; **500:** load/write failure | Persists auto-promotion gates; successful write with failed readback is explicitly partial confirmation, not a failed save or verified readback | "Save Policy" button (`#btn-save-promotion-policy`) |
| `GET /api/backtest/promotion-audit` | `list_promotion_audit`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 25` (1–100) | **200 OK:**<br>`{"events": List[dict]}` | Reads strategy auto-promotion decision audit from persistence; UI requests `limit=5` for the compact home panel | Promotion Audit modal/table and home panel (`#view-backtest`, `#bt-promotion-audit`) |

---

### 4.5 Paper Trading Execution & Ledger (`paper_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/paper/configs` | `list_configs`<br>`paper_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"configs": List[{"id": int, "symbol": str, "market": str, "timeframe": str, "strategy_name": str, "is_active": bool, "allocated_capital": float, "risk_per_trade_pct": float, "telegram_enabled": bool, "report_enabled": bool}]}` | Reads active paper strategy deployments from `paper_configs` | Strategy Configs Table (`#view-paper`) |
| `POST /api/paper/configs` | `update_config`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `PaperConfigRequest`<br>`{symbol: str, market: str, timeframe: str, strategy_name: str, is_active: bool, allocated_capital: float, risk_per_trade_pct: float, telegram_enabled: Optional[bool], report_enabled: Optional[bool]}` | **200 OK:**<br>`{"status": "success", "config_id": int, "message": str}` | Inserts or updates paper engine config in `paper_configs`; triggers worker configuration reload | Add/Edit Paper Config form (`#view-paper`) |
| `GET /api/paper/positions` | `list_positions`<br>`paper_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"positions": List[{"id": int, "symbol": str, "side": str, "entry_price": float, "current_price": float, "quantity": float, "unrealized_pnl": float, "sl_price": float, "tp_price": float, "opened_at": str}]}` | Reads live open positions from `paper_positions` joined with latest market ticks | Active Positions Table (`#view-paper`) |
| `POST /api/paper/positions/{position_id}/close` | `close_position`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | **Path:** `position_id: int` | **200 OK:**<br>`{"status": "success", "closed_price": float, "realized_pnl": float}`<br>**404:** Position not open | Forces market exit; updates `paper_positions` to closed; inserts trade record into `paper_trades`; dispatches Telegram exit notification | "Close Position" button (`#view-paper`) |
| `GET /api/paper/orders` | `list_paper_orders`<br>`paper_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `status: Optional[str] = "all"`, `limit: int = 50` | **200 OK:**<br>`{"orders": List[{"id": int, "client_order_id": str, "symbol": str, "side": str, "order_type": str, "price": float, "status": str, "created_at": str, "filled_at": Optional[str]}]}` | Reads durable order ledger from `paper_orders` with status filtering (`all`, `pending`, `filled`, `cancelled`, `expired`) | Order Ledger Table (`#paper-orders-status-filter`) |
| `POST /api/paper/orders/{order_id}/cancel` | `cancel_paper_order`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | **Path:** `order_id: int` | **200 OK:**<br>`{"status": "cancelled", "order_id": int}`<br>**400:** Order not cancellable | Atomically transitions order status to `CANCELLED` in `paper_orders`; prevents execution engine fill | "Cancel Order" button (`#view-paper`) |
| `GET /api/paper/trades` | `list_trades`<br>`paper_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 50` | **200 OK:**<br>`{"trades": List[{"id": int, "symbol": str, "side": str, "entry_price": float, "exit_price": float, "realized_pnl": float, "pnl_pct": float, "duration_minutes": int, "closed_at": str}]}` | Reads closed trades from `paper_trades` filtered by `COALESCE(c.report_enabled, TRUE) = TRUE` | Closed Trades Table (`#view-paper`) |
| `GET /api/paper/metrics` | `get_metrics`<br>`paper_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"metrics": {"total_trades": int, "win_rate": float, "profit_factor": float, "cumulative_r": float, "expectancy_r": float, "net_pnl": float, "strategy_breakdown": List[dict]}}` | Calculates aggregate portfolio statistics respecting `report_enabled` visibility flags | Portfolio Headline Cards (`#view-paper`) |
| `POST /api/paper/sync` | `sync_market_tick`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | None | **200 OK:**<br>`{"status": "success", "results": List[dict]}` | Triggers forward candle evaluation pass; evaluates SL/TP triggers against latest candle; executes pending orders | "Sync Market Ticks" button (`#view-paper`) |
| `POST /api/paper/deploy` | `deploy_strategy`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `DeployStrategyRequest`<br>`{symbol: str, market: str, timeframe: str, strategy_name: str, allocated_capital: float, risk_per_trade_pct: float, send_telegram: bool}` | **200 OK:**<br>`{"status": "activated", "message": str, "config_id": int}`<br>**400:** Invalid strategy/pair | Creates paper config in `paper_configs`; writes to `strategy_promotions` audit log; dispatches Telegram promotion notification | `#deploy-modal`<br>`App.confirmDeploy()` |
| `GET /api/paper/circuit-breakers` | `get_circuit_breakers`<br>`paper_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"status": "NORMAL" \| "TRIPPED", "daily_loss_usd": float, "daily_loss_limit": float, "current_drawdown_pct": float, "max_drawdown_limit": float, "tripped_at": Optional[str], "reason": Optional[str]}` | Reads risk management safety monitor | Circuit Breaker status badge (`#view-paper`, `#view-ml-studio`) |
| `POST /api/paper/circuit-breakers/emergency-stop` | `trip_circuit_breaker`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `{reason: str}` | **200 OK:**<br>`{"status": "success", "message": str, "breaker": dict}` | Forces circuit breaker state to `TRIPPED`; halts new order placement; dispatches emergency Telegram broadcast | "Emergency Halt" button (`#view-paper`) |
| `POST /api/paper/circuit-breakers/reset` | `reset_circuit_breaker`<br>`paper_routes.py` | `trader`, `admin` | Blocked (403 required) | None | **200 OK:**<br>`{"status": "success", "message": "Circuit breaker reset", "breaker": dict}` | Resets circuit breaker to `NORMAL`; allows execution engine to resume evaluations | "Reset Breaker" button (`#view-paper`) |

---

### 4.6 Machine Learning Forecast Studio (`ml_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/ml/experiments` | `list_experiments`<br>`ml_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"experiments": List[{"experiment_id": str, "dataset": str, "target_horizon": str, "winning_model": str, "val_auc": float, "created_at": str}]}` | Reads completed tournament manifests from `ml/experiments/` | ML Tournament Leaderboard (`#view-ml-studio`) |
| `GET /api/ml/experiments/{experiment_id}` | `get_experiment_details`<br>`ml_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Path:** `experiment_id: str` | **200 OK:**<br>`{"experiment_id": str, "models": List[dict], "feature_importances": List[dict], "roc_curves": dict, "calibration_curves": dict}`<br>**404:** Not found | Reads experiment JSON artifact; verifies path contains no symlinks outside store | `#modal-inspect-ml`<br>`App.inspectMLExperiment()` |
| `GET /api/ml/models` | `list_saved_models`<br>`ml_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"models": List[{"model_id": str, "model_type": str, "dataset": str, "val_auc": float, "accuracy": float, "created_at": str}]}` | Lists validated model weight packages eligible for live paper deployment | Trained Models Table (`#view-ml-studio`) |
| `POST /api/ml/run-experiment` | `trigger_experiment`<br>`ml_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `RunExperimentRequest`<br>`{symbol: str, market: str, timeframe: str, strategy_name: str, model_type: str, target_type: str, min_validation_trades: int}` | **200 OK:**<br>`{"status": "success", "experiment_id": str, "result": dict}`<br>**400:** Insufficient validation trades | Executes cross-validated model tournament; trains LightGBM, CatBoost, and LogReg; validates weights atomically | `#modal-run-ml`<br>`App.submitMlRun()` |
| `POST /api/ml/deploy-to-paper` | `deploy_ml_model_to_paper`<br>`ml_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `DeployMLToPaperRequest`<br>`{model_id: str, allocated_capital: float, risk_per_trade_pct: float, send_telegram: bool}` | **200 OK:**<br>`{"status": "success", "message": str}`<br>**400:** Model manifest invalid | Deploys model as real-time inference filter on paper trading signals; updates `paper_configs.ml_model_id` | `#modal-deploy-ml`<br>`App.submitDeployMl()` |

---

### 4.7 Strategy Library & Custom Rules (`custom_strategy.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/custom-strategy/indicators` | `get_indicators`<br>`custom_strategy.py` | `trader`, `admin` (prefix-wide role gate) | **403 for viewer**; 401 if unauthenticated; forced-password-change gate applies | None | **200 OK:**<br>`{"indicators": List[{"name": str, "label": str, "category": str, "description": str, "default": number, "type": str}], "operators": List[str]}` | Returns indicator definitions and supported operators; current viewer restriction can prevent the Strategy Library from loading this data | Visual Rule Builder dropdowns (`#view-strategies`) |
| `GET /api/custom-strategy/list` | `list_custom_strategies`<br>`custom_strategy.py` | `trader`, `admin` (prefix-wide role gate) | **403 for viewer**; 401 if unauthenticated; forced-password-change gate applies | None | **200 OK:**<br>`{"strategies": List[dict]}` (saved strategy definitions from `custom_strategies.json`) | Reads saved strategy definitions; read errors are logged and currently fall back to an empty list in this handler | Custom Strategy Cards (`#view-strategies`) |
| `POST /api/custom-strategy/generate-ai` | `generate_ai_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `GenerateAIStrategyRequest`<br>`{prompt: str, history: Optional[List[dict]], currentConfig: Optional[dict]}` | **200 OK:**<br>`{"status": "success", "provider": str, "strategy": dict, "chatResponse": str}` | Queries configured LLM (OpenAI/Anthropic/heuristic fallback) to generate valid rule JSON | AI Strategy Prompt input (`#view-strategies`) |
| `POST /api/custom-strategy/create` | `create_custom_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `CustomStrategyRequest`<br>`{name: str, displayName: str, description: str, longConditions: List[dict], shortConditions: List[dict], slAtrMult: float, riskRewardRatio: float}` | **200 OK:**<br>`{"status": "success", "name": str, "displayName": str, "message": str}`<br>**400:** Invalid syntax | Validates conditions; writes strategy definition into `custom_strategies.json` | "Save Strategy" button (`#view-strategies`) |
| `DELETE /api/custom-strategy/{name}` | `delete_custom_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Path:** `name: str` | **200 OK:**<br>`{"status": "success", "message": str}`<br>**404:** Not found | Removes strategy from `custom_strategies.json` | Delete button on strategy card (`#view-strategies`) |
| `POST /api/custom-strategy/quick-test` | `quick_test_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `QuickTestRequest`<br>`{strategy: CustomStrategyRequest, symbol: str, market: str, timeframe: str}` | **200 OK:**<br>`{"status": "success", "symbol": str, "market": str, "timeframe": str, "totalCandles": int, "totalTrades": int, "winRate": number, "winRatePct": number, "netR": number, "profitFactor": number|null, "maxDrawdownR": number|null, "expectancyR": number|null}`<br>**400:** Fewer than 50 source candles | Runs an in-memory simulation on the latest up to 1,000 candles; returns percentage and R-based metrics, with non-finite optional metrics sanitized to null | Legacy "Quick Test" button (`#view-strategies`); React `StrategyLibraryPanel` Quick test action for admin/trader roles, explicit click, non-persisted |

---

### 4.8 News AI Overlay Signals (`news_routes.py`)

**Contract verification (2026-10-28):** The route handlers in `web/routes/news_routes.py`, database read helpers in `db/db.py`, and React preview consumer `frontend/src/lib/news.ts` were reconciled after PR #521. The previous sentiment-bucket / overlay-multiplier contract below was stale and did not match the API.

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/news/stats` | `news_stats` → `get_news_overlay_stats`<br>`news_routes.py`, `db/db.py` | Authenticated (all roles) | Blocked (403 required) | None | **200 OK:** `{total_events_collected: int, events_evaluated_by_ai: int, high_confidence_signals: int, avg_confidence: number|null, trades_opened: int, pending_signals: int, performance: {closed_trades: int, wins: int, losses: int, win_rate: number|null, expectancy_r: number|null, profit_factor: number|null, total_r: number, net_pnl: number, max_drawdown_r: number}, heartbeats: [{collector_name: str, status: str, detail: str|null, last_beat_time: str|null}]}` | Read-only aggregation of `news_events`, `news_ai_signals`, closed `paper_trades` for `news_ai_overlay`, and `collector_heartbeat`. No collector, AI inference, strategy execution, or Telegram action is triggered. | React preview: `NewsAIPanel` operational summary, closed-trade performance, and collector health; legacy News AI view remains production-served |
| `GET /api/news/signals` | `news_signals` → `get_recent_news_signals`<br>`news_routes.py`, `db/db.py` | Authenticated (all roles) | Blocked (403 required) | **Query:** `limit: int = 50` (database helper clamps to 1–200) | **200 OK:** `{signals: [{id: int, symbol: str, market: str|null, timeframe: str|null, bias: str|null, confidence: int|null, reasoning: str|null, invalidation_condition: str|null, time_horizon: str|null, trade_timing: str|null, acted_on: bool, paper_position_id: int|null, skip_reason: str|null, created_at: str|null, headline: str|null, url: str|null, source: str|null, category: str|null, country: str|null, impact: str|null, scheduled_at: str|null, actual_value: string|number|null, forecast_value: string|number|null, previous_value: string|number|null}]}` | Read-only joined query of persisted `news_ai_signals` and triggering `news_events`, newest first. | React preview: `NewsAIPanel` signals table and text filter; legacy News AI signals table |
| `GET /api/news/events` | `news_events` → `get_recent_news_events`<br>`news_routes.py`, `db/db.py` | Authenticated (all roles) | Blocked (403 required) | **Query:** `limit: int = 30` (database helper clamps to 1–200) | **200 OK:** `{events: [{id: int, source: str|null, category: str|null, headline: str|null, url: str|null, symbols: str|null, country: str|null, impact: str|null, published_at: str|null, scheduled_at: str|null, actual_value: string|number|null, forecast_value: string|number|null, previous_value: string|number|null, processed_at: str|null, fetched_at: str|null}]}` | Read-only query of collected `news_events`, ordered by published/scheduled/fetched time. Does not fetch new articles. | React preview: `NewsAIPanel` macro & crypto events table; legacy News AI event feed |

**Nullability and presentation rules:** Empty-history metrics are nullable where no denominator exists (`avg_confidence`, `win_rate`, `expectancy_r`, `profit_factor`); UI renders unavailable numeric values as an em dash rather than coercing null to zero. `high_confidence_signals` currently reflects the count of persisted signal rows in `get_news_overlay_stats`, not a separately applied confidence threshold. The React preview uses GET requests only and remains isolated from production serving pending the global migration gates.

---

### 4.9 Research Bots & Screener Engine (`research_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/research/retailbot2/stats` | `retailbot2_stats`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"shadow": {"win_rate": float, "trades": int, "net_pnl": float}, "inverse": {"win_rate": float, "trades": int, "net_pnl": float}}` | Aggregates shadow vs. inverse performance respecting `report_enabled` flags | RetailBot2 Comparison Cards (`#view-research`) |
| `GET /api/research/retailbot2/positions` | `retailbot2_positions`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 100` | **200 OK:**<br>`{"positions": List[{"id": int, "symbol": str, "mode": "SHADOW" \| "INVERSE", "entry_price": float, "current_price": float, "unrealized_pnl": float, "opened_at": str}]}` | Reads active research trades from `rdt_trades` where `closed_at IS NULL` | Research Active Positions Table (`#view-research`) |
| `GET /api/research/retailbot2/trades` | `retailbot2_trades`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 50` | **200 OK:**<br>`{"trades": List[{"id": int, "symbol": str, "side": str, "mode": str, "entry_price": float, "exit_price": float, "realized_pnl": float, "closed_at": str}]}` | Selects qualified `t.id, t.symbol, t.side, t.mode` from `rdt_trades` joining `rdt_strategy_lifecycle` with `report_enabled = TRUE` | Recent Research Trades Table (`#view-research`) |
| `GET /api/research/retailbot2/strategies` | `retailbot2_strategies_get`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"strategies": List[{"strategy_id": str, "mode": str, "evaluation_enabled": bool, "paper_enabled": bool, "telegram_enabled": bool, "report_enabled": bool}]}` | Reads strategy lifecycle registry from `rdt_strategy_lifecycle` | Lifecycle Settings Grid (`#view-research`) |
| `POST /api/research/retailbot2/strategies` | `retailbot2_strategies_set`<br>`research_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `StrategyLifecycleRequest`<br>`{strategy_id: str, mode: str, evaluation_enabled: StrictBool, paper_enabled: StrictBool, telegram_enabled: StrictBool, report_enabled: StrictBool, auto_promote_enabled: StrictBool}` | **200 OK:**<br>`{"status": "ok", "strategy_id": str, "mode": str, "settings": dict}`<br>**400:** Non-boolean value | Inserts or updates row in `rdt_strategy_lifecycle`; strictly enforces boolean flags; affects worker evaluation | Strategy Lifecycle Toggles (`#view-research`) |
| `GET /api/research/retailbot2/control` | `retailbot2_control_get`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"enabled": bool, "overrides": dict, "last_heartbeat": Optional[str]}` | Reads daemon control state from `rdt_control` | Bot Status Badge (`#view-research`) |
| `POST /api/research/retailbot2/control` | `retailbot2_control_set`<br>`research_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `BotControlRequest`<br>`{enabled: bool, overrides: dict}` | **200 OK:**<br>`{"status": "ok", "note": str}` | Updates `rdt_control` table; worker daemon picks up state change on next loop (~30s) | Start / Stop / Restart buttons (`#view-research`) |
| `GET /api/research/screener/signals` | `screener_signals`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 100` | **200 OK:**<br>`{"signals": List[{"id": int, "symbol": str, "timeframe": str, "volume_surge_mult": float, "score": float, "signal_type": str, "created_at": str}]}` | Reads smart-money signals from `smart_money_signals` | Screener Signals Table (`#view-market-screener`) |
| `GET /api/research/screener/stats` | `screener_stats`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"total_signals": int, "average_quality": float, "universe_count": int, "active_surges": int}` | Calculates screening quality aggregates | Screener Summary Cards (`#view-market-screener`) |
| `GET /api/research/screener/status` | `screener_status`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"last_scan": str, "scanned_symbols": int, "healthy": bool}` | Returns health status of background market scanner | Health indicator pill (`#view-market-screener`) |
| `GET /api/research/screener/control` | `screener_control_get`<br>`research_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"enabled": bool, "overrides": dict}` | Reads screener control state from `screener_control` | Screener Daemon Badge (`#view-market-screener`) |
| `POST /api/research/screener/control` | `screener_control_set`<br>`research_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `BotControlRequest`<br>`{enabled: bool, overrides: dict}` | **200 OK:**<br>`{"status": "ok", "note": str}` | Updates `screener_control` table; background scanner respects toggle | Screener Start/Stop controls (`#view-market-screener`) |

---

### 4.10 Wallet Tracking & On-Chain Flow (`wallet_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/wallets/meta` | `wallets_meta`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"periods_hours": List[int], "chains": dict}` | Reads supported blockchain configurations (Solana, Base, Ethereum) | Chain & Timeframe pickers (`#view-wallet-tracking`) |
| `GET /api/wallets/status` | `wallets_status`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"service_healthy": bool, "last_scan": str, "tracked_count": int}` | Checks wallet tracking engine health | Status Badge (`#view-wallet-tracking`) |
| `GET /api/wallets/tracked` | `wallets_tracked`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"tracked": List[{"id": int, "chain": str, "wallet": str, "label": str, "status": str, "created_at": str}]}` | Reads active tracked addresses from `tracked_wallets` | Tracked Wallets Table (`#view-wallet-tracking`) |
| `POST /api/wallets/tracked` | `wallets_track`<br>`wallet_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `TrackRequest`<br>`{chain: str, wallet: str, label: Optional[str]}` | **200 OK:**<br>`{"status": "ok", "chain": str, "wallet": str}`<br>**400:** Invalid address | Inserts wallet address into `tracked_wallets`; adds address to scan schedule | Add Tracked Wallet Form (`#view-wallet-tracking`) |
| `POST /api/wallets/tracked/toggle` | `wallets_toggle_track`<br>`wallet_routes.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `TrackRequest`<br>`{chain: str, wallet: str}` | **200 OK:**<br>`{"status": "ok", "tracked": bool, "chain": str, "wallet": str}` | Updates `status` between `ACTIVE` and `PAUSED` in `tracked_wallets` | Active status toggle (`#view-wallet-tracking`) |
| `DELETE /api/wallets/tracked` | `wallets_untrack_by_query`<br>`wallet_routes.py` | `trader`, `admin` | Blocked (403 required) | **Query:** `chain: str`, `wallet: str` | **200 OK:**<br>`{"status": "ok", "chain": str, "wallet": str}` | Removes address from `tracked_wallets` via query parameters | Untrack action (`#view-wallet-tracking`) |
| `DELETE /api/wallets/tracked/{tracked_id}` | `wallets_untrack`<br>`wallet_routes.py` | `trader`, `admin` | Blocked (403 required) | **Path:** `tracked_id: int` | **200 OK:**<br>`{"status": "ok"}`<br>**404:** Not found | Removes wallet entry by primary key ID | Delete button in tracked table (`#view-wallet-tracking`) |
| `GET /api/wallets/positions` | `wallets_positions`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `period_hours: int = 24`, `side: Optional[str]`, `chain: Optional[str]`, `min_net_usd: float = 0`, `limit: int = 50`, `only_tracked: bool = False` | **200 OK:**<br>`{"positions": List[{"token_address": str, "token_symbol": str, "chain": str, "net_usd": float, "trade_count": int}]}` | Reads on-chain DEX accumulated token positions | Token Accumulation Table (`#view-wallet-tracking`) |
| `GET /api/wallets/token-flow` | `wallets_token_flow`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `period_hours: int = 24`, `limit: int = 15` | **200 OK:**<br>`{"flows": List[{"token_symbol": str, "chain": str, "inflow_usd": float, "outflow_usd": float, "net_flow_usd": float}]}` | Aggregates volume inflow vs outflow per token across scanned DEX pairs | Token Net Flow Chart / Cards (`#view-wallet-tracking`) |
| `GET /api/wallets/events` | `wallets_events`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 50`, `since_id: Optional[int]`, `chain: Optional[str]`, `wallet: Optional[str]`, `side: Optional[str]` | **200 OK:**<br>`{"events": List[{"id": int, "tx_hash": str, "chain": str, "wallet": str, "token_in": str, "token_out": str, "amount_usd": float, "timestamp": str}]}` | Reads live transaction log from DEX pool swaps | Live On-Chain Event Stream (`#view-wallet-tracking`) |
| `GET /api/wallets/settings` | `wallets_get_settings`<br>`wallet_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"enabled": bool, "networks": List[str], "min_trade_usd": float, "min_net_usd": float, "poll_interval_seconds": int, ...}` | Reads tracker configuration thresholds | Tracker Settings Panel (`#view-wallet-tracking`) |
| `PUT /api/wallets/settings` | `wallets_save_settings`<br>`wallet_routes.py` | `admin` only (`_role_allowed` enforcement) | Blocked (403 required) | **Body:** `WalletSettingsRequest`<br>`{enabled: bool, networks: list[str], min_trade_usd: float, ...}` | **200 OK:**<br>`{"status": "ok", "settings": dict}`<br>**400:** Invalid config | Persists tracker configuration; notifies scanner worker process | Save Settings button (`#view-wallet-tracking`) |

---

### 4.11 System Diagnostics & Settings (`settings.py` & `system_status.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/settings/status` | `get_system_status`<br>`settings.py` | `admin` only (`ADMIN_ONLY_PREFIXES`) | Blocked (403 required) | None | **200 OK:**<br>`{"database": {"status": str, "host": str, "port": int, "user": str, "pool_size": int}, "system": {"version": str, "uptime_seconds": int, "memory_mb": float}, "features": dict}` | Introspects sensitive connection configuration, active pool size, and host metrics | Database Connection Status (`#view-settings`) |
| `GET /api/system/status` | `get_public_system_status`<br>`system_status.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"database": {"status": "ok" \| "error", "tables": dict}, "collectors": List[{"name": str, "status": str, "last_heartbeat": str, "healthy": bool}]}` | Safe, non-sensitive health check; returns database connectivity and real-time collector heartbeats | `#status-db-badge`, `#home-heartbeats-list` |

---

### 4.12 Telegram Alert Notifications (`telegram_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/telegram/status` | `get_status`<br>`telegram_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"configured": bool, "chatId": Optional[str], "hasToken": bool}` | Checks whether Telegram bot token and chat ID are configured in environment | No direct consumer located in `web/static/js/*.js` or `web/static/index.html` during this source pass; endpoint/UI mapping remains unresolved |
| `POST /api/telegram/test` | `run_test`<br>`telegram_routes.py` | `admin` only (`_role_allowed` enforcement) | Blocked (403 required) | **Body:** `TelegramTestRequest`<br>`{custom_message: Optional[str]}` | **200 OK:**<br>`{"success": bool, "message": str}`<br>**400:** Not configured or send failure | Sends outbound test alert via Telegram Bot API (`sendMessage`) to configured chat | "Test Telegram Connection" button (`#view-settings`) |

---

## 5. Reconciled Dashboard Views Inventory (11 Views)

| View ID / Label | Current Source | Consumed Endpoints | User Actions & Controls | Risk Level | Accessibility Requirements | Responsive Reflow Behavior | Required Test Coverage |
|---|---|---|---|---|---|---|---|
| `view-home`<br>**Home & Overview** | `index.html` (`#view-home`), `app.js` (`renderHome`, `fetchSystemStatus`) | `GET /api/system/status` for non-admin roles; `GET /api/settings/status` for admins (legacy status bar role switch)<br>`GET /api/master-data/coverage`<br>`GET /api/backtest/history` | Navigation tabs, "New Backfill" header action, "Sign Out" button | Low (Read-only) | `aria-labelledby`, `aria-live="polite"` header, live clock, keyboard focusable skip-link | 4-col KPI collapses to 1-col on mobile; 3-col main grid collapses to 1-col | `tests/test_system_status.py`, pilot view Vitest + Playwright smoke test |
| `view-master-data`<br>**Master Data** | `index.html` (`#view-master-data`), `app.js` (`renderMasterData`, `submitBackfill`, `triggerComputeFeatures`, `pollJobs`) | `GET /api/master-data/coverage`<br>`GET /api/crypto/search`<br>`GET /api/crypto/popular`<br>`POST /api/master-data/backfill`<br>`POST /api/master-data/build-features`<br>`GET /api/master-data/jobs` | Search symbol, trigger backfill modal, compute features per dataset, filter jobs | High (Long async jobs, Binance rate limits) | Form field associations (`for`/`id`), table header `scope="col"`, sort announcements, progress bar `aria-valuenow` | Table horizontal scroll on mobile; filter inputs wrap | `tests/test_master_data.py`, `tests/test_search.py`, form validation tests |
| `view-backtest`<br>**Backtest Studio** | `index.html` (`#view-backtest`), `app.js` (`renderBacktestStudio`, `runBacktest`, `runBattleRoyale`, `renderBacktestChart`) | `GET /api/backtest/strategies`<br>`POST /api/backtest/run`<br>`POST /api/backtest/run-all`<br>`GET /api/backtest/history`<br>`GET /api/backtest/runs/{id}`<br>`GET/PUT /api/backtest/promotion-policy`<br>`GET /api/backtest/promotion-audit` | Select strategy & pair, set date range/capital, run backtest, run Battle Royale, inspect candlestick & equity chart, deploy to paper, update promotion policy | High (CPU compute, promotion gating) | Candlestick canvas fallback description, form labels, focus trap on deploy dialog | Charts flex/resize responsively; parameter controls stack vertically on tablet/mobile | `tests/test_backtest.py`, `tests/test_accounting.py`, `tests/test_paper_backtest_parity.py` |
| `view-paper`<br>**Paper Trading** | `index.html` (`#view-paper`), `app.js` (`fetchPaperData`, `addPaperStrategyConfig`, `togglePaperStrategy`, `togglePaperTelegram`, `togglePaperReport`, `manualClosePaperPosition`, `cancelPaperOrder`) | `GET /api/paper/configs`<br>`POST /api/paper/configs`<br>`GET /api/paper/metrics`<br>`GET /api/paper/positions`<br>`POST /api/paper/positions/{id}/close`<br>`GET /api/paper/orders`<br>`POST /api/paper/orders/{id}/cancel`<br>`GET /api/paper/trades`<br>`POST /api/paper/sync`<br>`GET /api/paper/circuit-breakers`<br>`POST /api/paper/circuit-breakers/*` | Add strategy config, toggle evaluation/telegram/report, filter order status, cancel pending order, close position, sync ticks, trip/reset circuit breaker | Critical (Simulation ledger, crash recovery, Telegram gating) | ARIA switch roles for toggles, table sort announcements, confirm modals before close/cancel | Horizontal scroll wrappers for order ledger and positions tables; responsive KPI cards | `tests/test_paper_routes.py`, `tests/test_paper_orders_mariadb.py`, `tests/test_paper_metrics_visibility.py`, `tests/test_retailbot_accounting.py` |
| `view-ml-studio`<br>**ML Forecast Studio** | `index.html` (`#view-ml-studio`), `app.js` (`fetchMLData`, `runMlTraining`, `deployMlModel`, `inspectMLExperiment`) | `GET /api/ml/models`<br>`GET /api/ml/experiments`<br>`GET /api/ml/experiments/{id}`<br>`POST /api/ml/run-experiment`<br>`POST /api/ml/deploy-to-paper`<br>`GET/POST /api/paper/circuit-breakers/*` | Run tournament experiment modal, inspect experiment (ROC/calibration/features), deploy model filter to paper, toggle circuit breaker | High (Heavy ML compute, manifest integrity) | Focus management on modals; chart accessible descriptions; progress bar semantics | 2-col tournament board collapses to 1-col; metrics tables scroll horizontally | `tests/test_ml_routes.py`, `tests/test_ml_promotion.py` |
| `view-strategies`<br>**Strategy Library** | `index.html` (`#view-strategies`), `app.js` (`renderStrategyLibrary`, `initStrategyArchitect`, `fetchStrategies`, `saveCustomStrategy`, `deleteCustomStrategy`) | `GET /api/backtest/strategies`<br>`GET /api/custom-strategy/list`<br>`GET /api/custom-strategy/indicators`<br>`POST /api/custom-strategy/create`<br>`POST /api/custom-strategy/generate-ai`<br>`POST /api/custom-strategy/quick-test`<br>`DELETE /api/custom-strategy/{name}` | Browse strategy cards, build visual rule tree, prompt AI strategy generator, quick-test strategy, save/delete custom strategy, send to backtest | Medium (Custom code/rule safety, JSON schema validation) | Accessible names on dynamic rule builder inputs; delete confirmation alerts | Builder grid wraps from 2-col to 1-col on mobile; card grid auto-reflows | `tests/test_custom_strategy.py` |
| `view-news-ai`<br>**News AI Signals** | `index.html` (`#view-news-ai`), `app.js` (`fetchNewsData`, `renderNewsSignals`, `renderNewsFeed`) | `GET /api/news/signals`<br>`GET /api/news/events`<br>`GET /api/news/stats` | Filter news feed by symbol/keyword, filter sentiment (Bullish/Bearish), manual refresh | Low (Read-only overlay display) | Sentiment badge contrast compliance; screen-reader labels for confidence gauges | News feed and metrics panel stack vertically on tablets and mobile devices | `tests/test_news_routes.py` |
| `view-research`<br>**Research Bots** | `index.html` (`#view-research`), `app.js` (`fetchResearchData`, `_fetchBotControl`, `toggleBotLifecycleSetting`) | `GET /api/research/retailbot2/stats`<br>`GET /api/research/retailbot2/positions`<br>`GET /api/research/retailbot2/trades`<br>`GET/POST /api/research/retailbot2/strategies`<br>`GET/POST /api/research/retailbot2/control` | Toggle lifecycle (paper, telegram, report) per strategy/mode (shadow/inverse), start/stop/restart bot daemon, filter closed trades | Critical (Daemon process control, strict boolean flags, SQL column aliases) | ARIA switch roles, distinct shadow vs inverse labels, sortable trade headers | Dual shadow/inverse comparison grid stacks into single column on mobile | `tests/test_retailbot2_lifecycle.py`, `tests/test_retailbot_accounting.py` |
| `view-market-screener`<br>**Market Screener** | `index.html` (`#view-market-screener`), `app.js` (`fetchScreenerData`, `renderScreenerTable`, `filterScreener`) | `GET /api/research/screener/status`<br>`GET /api/research/screener/signals`<br>`GET /api/research/screener/stats`<br>`GET/POST /api/research/screener/control` | Filter signals by volume/quality/symbol, sort columns, screener daemon control, "Send to Backtest" shortcut | Medium (Real-time screening, worker control) | Table sort announcements with counts; filter form labels; keyboard navigable table | Table horizontal scroll wrapper; responsive filter toolbar | `tests/test_screener.py` |
| `view-wallet-tracking`<br>**Wallet Tracking** | `index.html` (`#view-wallet-tracking`), `app.js` (`fetchWalletData`, `_wtLoadSettings`, `saveWalletSettings`, `walletTrack`, `walletUntrack`, `walletToggleTrack`) | `GET /api/wallets/status`<br>`GET /api/wallets/tracked`<br>`POST/DELETE /api/wallets/tracked`<br>`POST /api/wallets/tracked/toggle`<br>`GET /api/wallets/events`<br>`GET /api/wallets/positions`<br>`GET /api/wallets/token-flow`<br>`GET/PUT /api/wallets/settings` | Add tracked address, remove wallet, toggle tracking active, filter events, change token-flow period (6h/24h/7d), update alert thresholds | Medium (External RPC/scan parsing) | Form input labels, monospace wallet address styling with copy feedback, table headers | Flow charts and transaction tables stack on smaller screens | `tests/test_wallet_tracker.py`, `tests/test_wallet_tracker_mariadb.py` |
| `view-settings`<br>**Settings & Users** | `index.html` (`#view-settings`), `app.js` (`renderSettings`, `fetchUsers`, `handleCreateUser`, `toggleUserStatus`, `submitResetPassword`, `testTelegram`) | `GET /api/settings/status`<br>`GET/POST /api/users`<br>`PUT/DELETE /api/users/{id}`<br>`POST /api/users/{id}/reset-password`<br>`GET /api/telegram/status` (consumer unverified)<br>`POST /api/telegram/test` (legacy `btn-tg-test` enhancement handler) | Create user, toggle user active status, admin password reset, delete user, test Telegram bot connection, inspect DB metrics | Critical (Security, RBAC credentials, session revocation) | Confirmation modals for deactivation/deletion; explicit role select labels; focus trapping | User table scrolls horizontally; configuration cards wrap | `tests/test_auth_routes.py`, `tests/test_users.py` |

---

## 6. Reconciled Modal Dialogs & Workflows (7 Dialogs)

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

## 7. Conditional Flows & Edge Cases Reconciled with Source

1. **Authentication & Session Interception Flow:**
   - On load, `App.init()` calls `App.checkAuth()` (`GET /api/auth/me`).
   - If token is missing or returns 401: `#login-modal` is displayed; all dashboard polling and data fetches are halted.
   - If `must_change_password === true`: `#force-password-modal` is displayed immediately with backdrop-blur. The user cannot access any view or close the modal without either saving a valid new password (`POST /api/auth/change-password`) or clicking "Sign out instead" (`App.cancelForcedPasswordChange()`).
2. **Background Polling Concurrency & Visibility Guards:**
   - Interval runs every 15,000ms (15s).
   - If `document.hidden === true` (tab switched or minimized), polling is completely suppressed.
   - If `this.autoRefreshInProgress === true`, new polling cycles are skipped to avoid cascading queue jams.
   - Polling calls are dispatched with `Promise.allSettled()`, ensuring an error in one panel does not crash other visible panels.
3. **Paper Execution Visibility & Ledger Parity:**
   - Recent paper-trade history (`GET /api/paper/trades`), headline metrics (`GET /api/paper/metrics`), and strategy metrics join `paper_configs` on `(symbol, market, timeframe, strategy_name)` using `COALESCE(c.report_enabled, TRUE) = TRUE`.
   - Hidden strategies are excluded from report views, but closed positions and ledger rows remain immutable in `paper_trades` to guarantee financial ledger integrity.
4. **Research Bots Mode Separation:**
   - RetailBot2 operates two strictly separated strategy identities: `SHADOW` (raw signal) and `INVERSE` (counter-trend execution with `_INV` suffix).
   - Controls (`POST /api/research/retailbot2/strategies`) enforce `StrictBool` on all 5 flags: `evaluation_enabled`, `paper_enabled`, `telegram_enabled`, `report_enabled`, `auto_promote_enabled`.
5. **Circuit Breaker Halt & Recovery:**
   - When daily loss or maximum drawdown thresholds are breached, the circuit breaker transitions to `TRIPPED`.
   - In `TRIPPED` state, new order entries in the paper engine are blocked, and an emergency stop banner is displayed across the Paper Trading and ML Studio headers.

---

## 8. Chart & Visualization Technical Specifications

1. **TradingView Lightweight Charts v4.1.1 (`#candlestick-chart-box`):**
   - Renders Candlestick Series (`createCandlestickSeries`) with volume histogram sub-pane (`createHistogramSeries`).
   - Supports historical trade markers (`series.setMarkers`) showing BUY (green arrow below bar) and SELL (red arrow above bar) executions with price tooltips.
   - Theme sync: Responds to light/dark mode by dynamically updating background (`#0b1020` vs `#ffffff`), grid lines (`rgba(255,255,255,0.05)`), and crosshair styles.
   - Window resize handler with `ResizeObserver` prevents chart canvas distortion.
2. **Portfolio Equity Curve (`#equity-chart-canvas`):**
   - Implemented with Chart.js (v4+).
   - Dual-line chart plotting cumulative strategy equity vs. buy-and-hold benchmark equity.
   - Drawdown underwater fill area with semi-transparent red accent (`rgba(244, 63, 94, 0.15)`).
3. **ML Tournament Evaluation Charts (`#modal-inspect-ml`):**
   - Multi-ROC curve plotting true-positive vs false-positive rates for competing models.
   - Reliability diagram (calibration curve) showing predicted vs observed probabilities.
   - Horizontal bar chart of top 15 Gini/permutation feature importances.
4. **On-Chain Token Flow Bar Chart (`#view-wallet-tracking`):**
   - Bi-directional bar chart depicting net USD volume flow (green for net accumulation, rose for net distribution) across 6h, 24h, and 7d horizons.

---

## 9. UIX-00 Acceptance Gate & Sign-off State

- [ ] **Repository Inspection:** Recheck static serving, entrypoint, CORS, manifests, and Node/npm availability on the current source baseline.
- [ ] **View Inventory:** 11 navigation views and 7 dialogs are mapped in the draft; reconcile conditional panels, forms, modal triggers, and error states against current source.
- [x] **Static API route-count baseline:** Source inspection of all 13 routers included by `web/app.py` found 81 `@router` HTTP endpoint decorators, plus app-level `GET /`. This is a decorator count, not a verified one-to-one count of runtime operations or inventory rows.
- [x] **Available CI evidence:** Recent CI runs for PRs #511–#513 passed their reported required checks. This does not certify complete route or caller parity.
- [ ] **Parity-test scope:** Independently inspect whether the current test suite compares every runtime operation against the inventory and detects legacy caller drift; existing tests must not be described as full inventory parity unless they actually do so.
- [ ] **Acceptance Review Gate:** **In Review / Pending Acceptance**. UIX-00 will be marked complete only after the user/owner has reviewed and approved this baseline inventory.
- [x] **UIX-01 foundation status:** The React/Vite preview, committed lockfile, and frontend CI are already present. PR #499 is merged, and its `Frontend foundation` workflow run #132 completed successfully, including Playwright browser smoke tests. This does not complete UIX-00 or authorize production cutover.
- [ ] **Remaining audit scope:** Verify route methods, request/response schemas, role restrictions, forced-password guard, side effects, and route-to-view parity; complete the full accessibility/responsive/role/session browser acceptance suite.


## 13. React preview route-to-API map (verified 2026-10-27)

This table records the endpoints called by the currently merged React preview clients at source baseline `d2f513234e2ee5aa7c1883000bb79bde1558004a` (2026-10-27). It is **not** a claim that the full legacy dashboard inventory has been reconciled or that the React preview has feature parity. Confirm actual FastAPI route registration, RBAC and forced-password behavior against source before any production cutover.

| React preview view | Client module | Endpoints called by the view | Side-effect boundary |
|---|---|---|---|
| Authentication gate | `lib/auth.ts` | `POST /api/auth/login`, `GET /api/auth/me` (session bootstrap contract), `POST /api/auth/logout`, `POST /api/auth/change-password` | Authentication mutations are intentional and use the existing server contract; password-change gating and browser-level session-expiry tests remain required. Component tests now cover a 401 after successful sign-in, clearing the stale token, and returning to the sign-in screen. |
| Overview | `lib/systemStatus.ts` | `GET /api/system/status` | Sanitized status read; must not expose the admin-only `/api/settings/status` connection details. |
| Market Screener | `lib/screener.ts`, `ScreenerControlsPanel.tsx` | `GET /api/research/screener/status`, `GET /api/research/screener/signals?limit=50`, `GET /api/research/screener/stats`, `GET/POST /api/research/screener/control` | The control editor is reachable in the React preview. Reads use GET; saving requires an explicit user action and POST. Editing is limited to admin/trader roles, with server-side authorization unchanged. The worker applies saved settings on its next loop tick; no restart is triggered. |
| Paper Trading | `lib/paperTrading.ts` | `GET /api/paper/positions`, `GET /api/paper/configs`, `GET /api/paper/orders?limit=20`, `GET /api/paper/trades?limit=20`, `GET /api/paper/metrics`, `GET /api/paper/circuit-breakers` | Read-only ledger/risk monitor; no order, config, deploy, cancel, or worker mutation from this view. |
| Strategy Library | `lib/strategyLibrary.ts` | `GET /api/custom-strategy/list`, `GET /api/custom-strategy/indicators` | Catalog-only view; create/update/test/AI actions are not exposed by this client. |
| Wallet Tracking | `lib/walletTracking.ts` | `GET /api/wallets/meta`, `GET /api/wallets/settings`, `GET /api/wallets/status`, `GET /api/wallets/tracked`, `GET /api/wallets/positions?period_hours=24&limit=20`, `GET /api/wallets/token-flow?period_hours=24&limit=10`, `GET /api/wallets/events?limit=20` | Read-only snapshot; no wallet settings, tracked-address, or worker mutation. Verify that all returned settings fields are safe for every authorized role. |
| Backtest Studio | `lib/backtestStudio.ts` | `GET /api/backtest/strategies`, `GET /api/backtest/history?limit=100`, `GET /api/backtest/runs/{runId}` | Saved results only; no run, run-all, promotion, or paper deployment request. |
| ML Studio | `lib/mlStudio.ts` | `GET /api/ml/experiments`, `GET /api/ml/models`, `GET /api/ml/experiments/{experimentId}` | Persisted experiment/model reads only; no training or deploy-to-paper request. |
| Research | `lib/research.ts` | `GET /api/research/retailbot2/positions?limit=100`, `GET /api/research/retailbot2/trades?limit=50`, `GET /api/research/retailbot2/stats`, `GET /api/research/retailbot2/strategies` | Lifecycle readback only; no evaluation/paper/Telegram/report mutation. |
| News AI | `lib/news.ts` | `GET /api/news/stats`, `GET /api/news/signals?limit=50`, `GET /api/news/events?limit=30` | Saved overlay data only; no poller, inference, execution, or Telegram action. |
| Master Data | `lib/masterData.ts` | `GET /api/master-data/coverage`, `GET /api/master-data/jobs` | Coverage/job status only; no backfill, feature build, or worker launch. |
| Settings | `SettingsPanel.tsx` | No API calls; uses existing local theme state and auth-gate identity | No admin status, credentials, user administration, Telegram test, or server setting mutation. |

### Audit checkpoint (2026-10-27; refreshed after PR #501)

- PR #501 merged at `0edc71cdc93712666ec2b9de708f84a800b21e49`. On head `072f8ba7d6961890bac57455bd2141a521bac437`, the frontend workflow passed typecheck, unit tests, production build, Chromium setup, and Playwright browser smoke tests. Pytest, Wallet Tracker MariaDB integration, unit tests, and RetailBot accounting regression also passed.
- The Market Screener controls editor is now reachable in the React preview. Admin/trader sessions see editable controls; viewers see a restriction. Saving is explicit and calls `POST /api/research/screener/control`; the existing server-side role gate remains authoritative. Other strategy, paper-trading, wallet, worker, Telegram, backtest, and ML mutations remain unavailable.
- No production deployment or root-route cutover was performed. FastAPI continues to serve `web/static/index.html`.

### Audit checkpoint (2026-10-27)

- `web/app.py` includes 13 routers and serves the legacy dashboard from the FastAPI application; the React preview remains separate from the production root entrypoint.
- Source decorator count: 81 HTTP route decorators across the 13 included routers, plus `GET /` in `web/app.py`. The count is not a substitute for inspecting FastAPI's final route registry or validating each handler.
- PR #501 (`0edc71c`) connected `ScreenerControlsPanel` to the Market Screener view. The preview now identifies its limited control capability accurately. Browser smoke tests verify that admin/trader sessions see editable controls while viewer sessions see the restriction. Other strategy promotion, paper-trading, wallet, worker lifecycle, Telegram administration, backtest, and model deployment mutations remain unavailable in the React preview.
- `frontend/e2e/dashboard.spec.ts` already includes browser coverage for sign-in/rejected sign-in, forced password change and API isolation, expired sessions, sign-out, temporary session-validation outage, theme persistence, mobile navigation/focus/viewport overflow, denied read endpoints, and read-only behavior across domain views. Workflow run #132 passed these browser smoke tests. Remaining acceptance gaps include end-to-end role-difference coverage across the actual preview views, broader keyboard/screen-reader checks, and real-device validation; component tests for the screener control editor do separately cover viewer restrictions.
- `web/app.py::_role_allowed` is the central role/method gate. `GET` is the default for unclassified routes; non-GET requests fail closed unless an explicit policy allows them. Forced-password accounts are restricted to `/api/auth/me`, `/api/auth/logout`, and `/api/auth/change-password`.
- Role-policy observation to resolve: `/api/custom-strategy` is in `ADMIN_OR_TRADER_PREFIXES`, which restricts **all** methods under that prefix—including the catalog GET routes `/api/custom-strategy/list` and `/api/custom-strategy/indicators`—to `admin` or `trader`. This differs from a blanket assumption that every preview read-only panel is available to `viewer`; confirm intended product policy and add role-specific browser/API tests before changing access.
- Read/mutation policy observation: GET requests under `/api/wallets`, `/api/paper`, `/api/backtest`, and `/api/ml` are allowed to authenticated roles by the central gate; their non-GET methods require `admin` or `trader`. `/api/research/screener/control` GET is read-accessible, while its POST is restricted to `admin` or `trader`.

### Still required before accepting UIX-00

- [ ] Compare every legacy `web/static/index.html` view, dynamically created dialog, polling loop, and fetch call against the current route registry.
- [ ] Verify method, request/response schema, role restriction, forced-password guard, and side effects for every endpoint; include `/api/auth/me` and all mutation paths.
- [ ] Verify whether any component reachable from the React preview imports or calls mutation helpers outside the view's declared read-only path.
- [ ] Add browser-level tests for login, forced password change, logout/session expiry, role differences, responsive navigation, and theme persistence.
- [ ] Record unresolved discrepancies rather than silently treating draft counts or old endpoint rows as authoritative.
- [ ] Obtain explicit owner acceptance before the inventory is marked complete or any FastAPI route cutover is proposed.


## UIX-00 source audit slice: Market Screener API boundary (2026-10-27)

This slice is verified against the current source files `web/app.py`, `web/routes/research_routes.py`, `db/db.py`, `frontend/src/App.tsx`, `frontend/src/ScreenerPanel.tsx`, `frontend/src/ScreenerControlsPanel.tsx`, and `frontend/src/lib/screener.ts`. It is a focused source audit, not completion of the full 81-route register or a production-readiness claim.

| Method and path | Handler / input | Effective access policy | Result and side effects | React preview consumer |
|---|---|---|---|---|
| `GET /api/research/screener/status` | `research_routes.screener_status`; no request body | Authenticated roles including viewer; blocked if `must_change_password` is set | Combines stored screener status with the persisted `enabled` flag. If the control row/table read fails, status falls back to enabled=true; the status helper must still succeed. Does not start or stop the worker process. | `ScreenerPanel` |
| `GET /api/research/screener/signals?limit=50` | `research_routes.screener_signals`; `limit` integer 1–200, default 50 | Authenticated roles including viewer; blocked if forced password change is required | Read-only query of recent smart-money signals; handler returns `{"signals": [...]}`; failures are returned as HTTP 500 with a generic message. | `ScreenerPanel` |
| `GET /api/research/screener/stats` | `research_routes.screener_stats`; no request body | Authenticated roles including viewer; blocked if forced password change is required | Read-only aggregate of smart-money signal statistics; failures return HTTP 500 with a generic message. | `ScreenerPanel` |
| `GET /api/research/screener/control` | `research_routes.screener_control_get`; no request body | Authenticated roles including viewer; blocked if forced password change is required | Returns persisted `enabled`, `overrides`, `updated_at`, and sorted `tunable_fields`. Unlike the status endpoint, a failed control read returns HTTP 500; the React editor currently only fetches this control payload for admin/trader. | `ScreenerControlsPanel` (admin/trader only) |
| `POST /api/research/screener/control` | `BotControlRequest`: `enabled: bool`, `overrides: Dict[str, Any]` defaulting to empty | `api_security_boundary` allows admin/trader only; viewer is denied with HTTP 403. Forced-password guard runs before role policy. | Validates override keys against `SCREENER_TUNABLE_FIELDS`, checks boolean fields and finite numeric values, and applies configured bounds before calling `set_screener_control`. Persists a soft control record for the running worker to poll; it does not launch, terminate, or restart the process. Success returns status and an approximately 60-second/next-loop-tick note. | Explicit Save action in `ScreenerControlsPanel` |

### Cross-checks and known limits

- The role gate is centralized in `web/app.py`. `RESEARCH_CONTROL_PREFIXES` includes `/api/research/screener/control`; GET is allowed for authenticated roles, while non-GET methods require `admin` or `trader`. The middleware rejects forced-password-change accounts on these paths before reaching the handlers.
- The control request model accepts `enabled` and an overrides object. The override filter is an allowlist and validates known boolean toggles, rejects booleans/non-numbers where numeric values are required, rejects non-finite numbers, and applies explicit lower/upper bounds for bounded fields. The database helper/worker implementation remains the source of truth for persistence and application timing.
- The React editor exposes explicit Save and Reload actions, client-side numeric range validation, feedback states, and a viewer restriction message. Its UI role check improves usability only; the server middleware remains authoritative.
- The frontend preview remains isolated: `web/app.py` still serves `web/static/index.html` from `GET /` and mounts `web/static/` at `/static`. No deployment, production route cutover, or process-control behavior is introduced by this audit.
- This slice does not establish that every legacy fetch/dialog is mapped, nor does it independently prove live database initialization, runtime worker health, screen-reader conformance, real-device acceptance, or owner approval.

### UIX-00 audit status after this slice

- [x] Focused Market Screener status/signals/stats/control handlers and their central method/role/password gates checked against source.
- [x] React control editor call path and soft-control side-effect boundary checked against source.
- [ ] Complete runtime route-registry reconciliation and verify every route row, including schema and side effects, across all 13 routers.
- [ ] Complete legacy view/dialog/polling/fetch parity, role-specific browser acceptance, broader accessibility/device checks, and explicit owner acceptance.


## UIX-00 security discrepancy register — custom-strategy reads (2026-10-27)

**Status: documented for product/security review; no policy change made.**

The role hierarchy in Section 3 describes `viewer` as read-only for dashboards and data. However, the effective `api_security_boundary` in `web/app.py` checks `ADMIN_OR_TRADER_PREFIXES` before the default GET-read rule. That prefix list includes `/api/custom-strategy`, so **all methods under that prefix, including GET catalog/read endpoints, are restricted to `admin` or `trader`**. This is stricter than a general viewer-read-only interpretation and can make strategy catalog data unavailable to viewers.

| Source fact | Current behavior | Required decision |
|---|---|---|
| `ADMIN_OR_TRADER_PREFIXES` includes `/api/custom-strategy` | `_role_allowed` returns false for viewer before falling through to the default GET allowance; viewer receives HTTP 403 for custom-strategy GET as well as mutation methods. | Product/security owner must decide whether strategy catalog GETs should be readable by viewers or remain trader/admin-only. |
| `frontend/src/App.tsx` may hide controls by role | Client-side visibility is not an authorization boundary; server middleware is authoritative. | After policy decision, add role-specific API/browser tests and update UI copy/inventory to match. |
| `ADMIN_OR_TRADER_PREFIXES` is a prefix-wide rule | Changing it to permit GET broadly could unintentionally alter access for every route under the prefix. | If viewer reads are approved, split read and mutation policy narrowly by exact method/path rather than relaxing the prefix indiscriminately. |

**Gate:** Do not modify authorization behavior as part of a documentation-only UI migration slice. Resolve the policy with the owner, add regression tests for the chosen rule, and only then implement it in a separate code change. This discrepancy is not proof of a vulnerability; it is a mismatch between broad role documentation and the effective route policy that needs an explicit decision.


### Regression coverage follow-up (2026-10-27; PR #505)

- PR #505 adds a route-authorization sweep regression asserting that viewer requests to `GET /api/custom-strategy/list` and `GET /api/custom-strategy/indicators` receive HTTP 403 under the current prefix-wide `ADMIN_OR_TRADER_PREFIXES` rule.
- The test records existing behavior; it does not establish that this is the desired long-term product policy. The owner decision remains open. If viewer catalog access is approved, change policy narrowly and update tests/UI copy in a separate reviewed PR.
- PR #505 passed pytest, unit tests, Wallet Tracker MariaDB integration, and RetailBot paper-accounting regression before merge. No runtime authorization change, deployment, or production route cutover occurred.


## UIX-00 source audit slice: custom-strategy catalog API parity (2026-10-28)

- Corrected the route table for `GET /api/custom-strategy/list` and `GET /api/custom-strategy/indicators`: the effective prefix-wide gate permits `admin` and `trader`, not `viewer`. The earlier “Authenticated (All roles)” entries were inaccurate.
- Matched the indicator response schema to `INDICATOR_DEFINITIONS` in `web/routes/custom_strategy.py` (`name`, `label`, `category`, `description`, `default`, `type`) and documented the list endpoint as saved strategy definition dictionaries rather than a narrower invented field list.
- The React preview's catalog client calls both GET endpoints. Since viewers receive 403, this can surface as a catalog loading failure unless the view gates access or presents a deliberate restricted state. Client visibility is not server authorization.
- Regression coverage for the current viewer 403 behavior was merged in PR #505. This documentation change does not resolve the product/security decision or alter authorization.
- Still not verified by this slice: full response payload examples from live runtime, catalog behavior across browser roles, all custom-strategy mutation request validation, and full legacy-to-React view parity.


## UIX-00 source audit slice: custom-strategy mutation and quick-test boundary (2026-10-28)

This slice checks the six custom-strategy handlers in `web/routes/custom_strategy.py` against the centralized policy in `web/app.py`. It records source-level behavior and does not change runtime code or decide whether viewers should eventually read the catalog.

| Method and path | Request / validation | Effective access | Side effects and response behavior | React preview status |
|---|---|---|---|---|
| `POST /api/custom-strategy/generate-ai` | `GenerateAIStrategyRequest`: required `prompt`; optional `history: List[Dict[str,str]]` and `currentConfig: Dict[str,Any]`. Handler trims prompt and returns HTTP 400 when empty. Uses at most the last six history messages, normalizes generated indicator/operator/value fields and clamps stop-loss/RR fields. | Admin/trader only under the prefix-wide policy; forced-password-change accounts are blocked before handler execution. | Generation is draft-only: attempts configured 9Router, OpenRouter, then Anthropic providers and falls back to a local heuristic if no usable parsed response. Returns `status`, provider label, normalized `strategy`, and `chatResponse`. Handler does not persist the strategy or register it in the runtime registry. Prompt/history/current draft may be sent to the configured external LLM provider; this is a data-boundary consideration for product documentation. | Not called by the current React `StrategyLibraryPanel`; the panel is read-only and only fetches list/indicators. |
| `POST /api/custom-strategy/create` | `CustomStrategyRequest`: name/displayName, description/rationale, long/short condition lists, SL ATR multiplier 0.5–5.0, risk/reward 1.0–6.0, trailing stop settings and bounded trailing parameters. Each condition requires indicator, operator and value. Name is normalized to lowercase alphanumeric/underscore characters; empty normalized name returns HTTP 400. | Admin/trader only; forced-password-change blocked. | Replaces any saved entry with the same normalized name, writes the JSON file first, then registers normal and inverse strategy runners and metadata in memory. A file-write failure is intended to leave the in-memory registry unchanged. Success returns status, normalized name, display name and message. | No create call in the current React preview; creation is explicitly out of scope for its read-only catalog panel. |
| `DELETE /api/custom-strategy/{name}` | Path name is stripped and lowercased. No explicit 404 branch is present in the handler. | Admin/trader only; forced-password-change blocked. | Removes matching saved definitions from the JSON file, removes normal/inverse runners and metadata from in-memory registries, and returns success/message even if the name did not exist. Persistence write errors may still fail the request. | No delete call in the current React preview. |
| `POST /api/custom-strategy/quick-test` | `QuickTestRequest` wraps a `CustomStrategyRequest` plus symbol (default `BTCUSDT`), market (default `spot`) and timeframe (default `1h`). Fetches closed-candle data; returns HTTP 400 if fewer than 50 rows are available. Simulates at most the latest 1,000 candles. | Admin/trader only; forced-password-change blocked. | Reads market data/features and computes in-memory trade metrics. Returns symbol/market/timeframe, candle/trade counts, win rate, net R, profit factor, max drawdown R and expectancy R. It does not save the strategy or backtest result. Non-finite metrics are cleaned before response. | No quick-test call in the current React preview. |
| `GET /api/custom-strategy/list` | No request body; reads the saved strategy JSON file and returns `{"strategies": [...]}`. Read/parse exceptions are logged; handler returns the initialized/partially loaded list rather than surfacing a dedicated error. | Admin/trader only under the current prefix rule; forced-password-change blocked. | File-backed read. | Called by `fetchCustomStrategies` in the read-only React catalog panel. |
| `GET /api/custom-strategy/indicators` | No request body; returns indicator definitions and supported operators (`<`, `<=`, `>`, `>=`, `==`). | Admin/trader only under the current prefix rule; forced-password-change blocked. | Static metadata response; no write side effect. | Called by `fetchStrategyIndicators` in the read-only React catalog panel. |

### Contract and migration implications

- `CustomStrategyRequest` constrains SL ATR and risk/reward values through Pydantic field bounds. The source declares bounds for trailing activation/distance as well. The request model does not itself establish that every indicator/operator combination is semantically valid; generator normalization and the strategy runner determine further handling.
- `generate-ai` is a generation step, not a save/activate step. The UI must distinguish generated draft, quick-test result, and persisted strategy so users do not assume generation automatically activates a strategy.
- `create` persists to the configured JSON file before mutating the in-memory `STRATEGIES` and `STRATEGY_METADATA` registries. A same-name create replaces the saved definition. `delete` removes both normal and inverse runners, but the handler's response does not distinguish a missing name from a successfully deleted one.
- The current React preview deliberately exposes only the list and indicator reads. No create, delete, AI-generation, quick-test, promotion, or activation controls are present in `StrategyLibraryPanel`. The legacy UI call graph and full end-to-end parity for these actions remain to be independently audited.
- Effective access for all six paths follows the prefix-wide `ADMIN_OR_TRADER_PREFIXES` rule in `web/app.py`; the existing regression test records viewer 403 behavior for the two GET endpoints. No authorization policy was changed by this documentation slice.
- These are source-inspection findings, not live API contract tests. Still outstanding: runtime route-registry comparison, tests for request edge cases and file-write failures, role-specific browser acceptance, legacy UI parity, and owner decision on viewer catalog access.

### UIX-00 audit status after this slice

- [x] Source-level request models, validation branches, response shape and side-effect boundaries reviewed for custom-strategy generate/create/delete/quick-test/list/indicators handlers.
- [x] Confirmed the current React strategy library panel only calls list and indicator read endpoints and exposes no mutation or quick-test actions.
- [ ] Add or verify focused tests for create replacement, delete of missing names, JSON persistence failures, generator provider fallback, and quick-test insufficient-data handling.
- [ ] Reconcile all custom-strategy routes with the runtime FastAPI route registry and verify legacy UI callers.
- [ ] Resolve the viewer catalog policy with the owner; do not relax the prefix-wide rule without an explicit decision and narrow regression-tested policy.
- [ ] Complete all-router route parity, role-specific browser/device/accessibility acceptance, and owner sign-off before any production route cutover.


## UIX-00 source audit slice: system status data boundary and settings scope (2026-10-28)

This slice compares `web/routes/system_status.py`, `web/routes/settings.py`, centralized route policy in `web/app.py`, and the React preview's `SystemStatusPanel` and `SettingsPanel`. It is a source-level audit, not a live operational health check.

| Method and path | Effective access | Response / data boundary | React preview consumer |
|---|---|---|---|
| `GET /api/system/status` | Any authenticated role under the default GET policy; forced-password-change accounts are blocked before route execution. | Calls shared `collect_system_status()`, returns database status/table counts, heartbeat records, and Binance spot/futures connectivity/configuration. It omits database host, port and database name, and replaces the raw database error with the generic `Database unavailable` string when unhealthy. The returned heartbeat objects still include the backend's `detail` field, even though the current panel does not render it. | `SystemStatusPanel` calls `fetchSystemStatus`, displays database status/table count, spot/futures status, heartbeat count, and collector/status/last-beat rows. It handles 401/403 with specific copy, has retry, aborts stale requests, and renders loading/error/empty states. |
| `GET /api/settings/status` | Admin only under `ADMIN_ONLY_PREFIXES`; forced-password-change accounts are blocked before route execution. | Returns the full `collect_system_status()` payload, including database host, port, database name, table counts, raw error text, heartbeats and Binance status. Treat it as an administrative diagnostic endpoint, not the general-purpose dashboard status API. | No call from the React `SettingsPanel`; administrative settings are explicitly marked legacy/not connected. |
| `GET /api/auth/me` | Any authenticated role; also included in the forced-password-change allowlist. | Returns session identity fields including id, username, email, role and `must_change_password`; it does not return the bearer token. | `SettingsPanel` receives username/role as props from the app/auth flow; its comment says identity comes from the existing authentication gate. Verify separately that parent state remains synchronized after profile/role changes. |

### Findings and migration implications

- Both status endpoints call the same collector, which queries database table metadata and collector heartbeats and makes live HTTP requests to Binance spot and futures endpoints with three-second timeouts. A status request can therefore incur database/network work; UI loading and retry states are necessary and are present in `SystemStatusPanel`.
- The public-facing `/api/system/status` response sanitizes the database connection identifiers and raw database error, but currently passes through heartbeat `detail`. The React panel does not render that field today. Before exposing it in a future UI, inspect real producer content and decide whether it may contain operational or sensitive details; do not infer that an unrendered field is safe to display.
- `/api/settings/status` is admin-only and intentionally carries richer diagnostic information. The **legacy** `App.fetchSystemStatus()` explicitly chooses this route when `currentUser.role` is `admin`; all other roles use `/api/system/status`. This role-dependent call feeds the legacy dashboard status bar (`updateStatusBar()`), including the MariaDB Connected/Error/Unavailable badge. Do not point the general React `SystemStatusPanel` at the admin route or relax the admin-only prefix to make it work.
- `App.fetchSystemStatus()` runs after a usable session is loaded and again in the 15-second auto-refresh cycle (when a token exists, the tab is visible, and no refresh is already in progress). The inventory row for `view-home` therefore lists both possible endpoints rather than only the public status route. This is source-level call-path verification; it does not establish full route-by-route inventory parity.
- The React `SettingsPanel` is intentionally limited to local theme preference and basic session metadata. It does not call server settings, user administration, Telegram test, or credential endpoints. This is a scope boundary, not complete migration of legacy administrative settings.
- These findings come from source inspection. Runtime role checks, actual response payload tests for heartbeat detail, frontend parent/auth synchronization, and browser acceptance for admin/trader/viewer remain part of the migration gate.

### UIX-00 audit status update

- [x] Compared system status and administrative status routes, role policy, response sanitization, and current React consumers at source level.
- [x] Confirmed the preview uses `/api/system/status` and does not connect the Settings view to the admin-only full diagnostic endpoint.
- [ ] Add a focused response-contract test asserting that public status omits database host/port/name and raw DB error while deciding whether heartbeat `detail` should be omitted or explicitly sanitized.
- [ ] Verify auth/session parent state and role-specific browser behavior for settings and system status.
- [ ] Continue runtime route-registry reconciliation and legacy-to-React parity for all 13 routers; owner acceptance and production cutover gates remain open.



## UIX-00 source audit slice: Telegram settings and outbound-test boundary (2026-10-28)

This slice compares `web/routes/telegram_routes.py`, the central `api_security_boundary` in `web/app.py`, and the React preview's `SettingsPanel`. It is a source-level review; no Telegram message was sent and no runtime behavior changed.

| Method and path | Effective access | Request / response and side effects | React preview |
|---|---|---|---|
| `GET /api/telegram/status` | Any authenticated role under the exact-path policy; forced-password-change accounts are blocked before handler execution. | Reads `get_telegram_config()`; returns `configured`, `chatId`, and `hasToken`. It does not return the bot token. The configured chat ID is still operational metadata and should only be displayed where appropriate. | Not called by the current React preview. |
| `POST /api/telegram/test` | Admin only under the exact-path policy in `web/app.py`; forced-password-change accounts are blocked before handler execution. | The optional request model accepts `custom_message`, but `run_test` currently calls `test_telegram_connection()` without passing the request. The handler can send an outbound test message through the Telegram Bot API. A failed result without a bot object becomes HTTP 400; otherwise the helper result is returned. | Not called by the current React preview; Settings explicitly says Telegram testing remains in the legacy dashboard. |

### Findings and migration implications

- The effective permission comes from the centralized middleware, not a handler-level dependency: `GET /api/telegram/status` is authenticated read access, while `POST /api/telegram/test` is admin-only. Do not weaken the exact-path rule or expose the test action as a generic preview control without role-specific regression tests and explicit confirmation UI.
- The `TelegramTestRequest.custom_message` field appears unused by the route implementation because the helper receives no argument. Treat custom-message support as unverified/ineffective until a focused contract test and implementation review confirm intended behavior; do not document it as a working feature.
- Status returns `chatId` but not the token. The preview does not consume the endpoint, and its Settings page intentionally keeps Telegram controls disconnected. A future migration should decide whether chat ID visibility is necessary for each role and avoid displaying tokens or raw provider errors.
- A successful test is an external side effect (a real outbound message). Tests for this route should mock the Telegram helper/client and must not send real messages in CI.
- This audit does not verify every legacy Telegram UI caller, live provider configuration, provider rate limits, or browser role behavior. The React preview remains isolated from FastAPI production serving.

### UIX-00 audit status update

- [x] Compared Telegram status/test routes with central authentication, forced-password-change, and role policy.
- [x] Confirmed the React Settings preview does not call Telegram status or test endpoints and clearly marks those controls as legacy/not connected.
- [x] Recorded that the optional custom-message request field is not passed to the test helper by the current handler.
- [ ] Add/verify focused route tests for viewer/trader/admin access, forced-password-change rejection, response redaction, and mocked outbound-test behavior.
- [ ] Verify the legacy Telegram UI call path and decide whether `chatId` should be visible to all authenticated roles.
- [ ] Complete runtime route-registry parity, legacy-to-React view/dialog/fetch parity, browser/device/accessibility checks, and owner acceptance before production cutover.



## UIX-00 follow-up: verify legacy Telegram test control call path (2026-10-28)

Follow-up source review of `web/static/js/dashboard_enhancements.js` resolves part of the earlier legacy-call-path gap. The enhancement bundle installs `window.App.sendTelegramTestPing` and sends `POST /api/telegram/test` with authenticated headers and an empty JSON body from the `btn-tg-test` control. It disables the button while the request runs, restores it in `finally`, and displays success/failure toasts.

| Legacy UI behavior | Verified source behavior | Migration implication |
|---|---|---|
| Trigger | `installTelegramPing()` attaches `sendTelegramTestPing` when `window.App` is available; the button id is `btn-tg-test`. | Keep this call path in the legacy inventory until the production route is intentionally migrated. |
| Request | `fetch("/api/telegram/test", { method: "POST", headers: this.getAuthHeaders(), body: JSON.stringify({}) })`. | The legacy UI does not send `custom_message`; the request model's optional field is not used by this UI. |
| Success UI | Checks HTTP success and `data.success`, then displays bot username and `data.chat_id` if present, with text stating a test message was sent. | The test action is not a read-only health check. Preserve explicit user initiation and a clear success state if migrated. |
| Error UI | Prefers `detail`, then `error`, then HTTP status; displays the resulting error in a toast. | Review whether provider/helper errors may reveal operational details before presenting them to non-admin users. The endpoint itself remains admin-only. |
| Loading/recovery | Disables the button, shows a spinner/“Testing...” label, and restores the original button content in `finally`. | Preserve keyboard operation, accessible name/state, and recovery behavior in a replacement component. |

### Reconciled scope

- The previous statement that Telegram testing remains in the legacy dashboard is accurate for the current React preview, but the legacy enhancement bundle does have a concrete caller for `POST /api/telegram/test`.
- The outbound-test call path is verified, but a source search of `web/static/js/app.js`, `web/static/js/dashboard_enhancements.js`, and `web/static/index.html` found no direct `GET /api/telegram/status` caller or status-pill binding. The endpoint is registered and authenticated, but its consuming UI remains unverified; remove the claimed “Telegram status pill” mapping unless a concrete caller is found in a later audit.
- This negative finding is scoped to the inspected legacy entry files and direct endpoint string searches; it is not proof that no indirect/dynamic caller can exist elsewhere. The React preview still does not call either Telegram endpoint.
- The React preview still does not call either Telegram endpoint, and FastAPI continues serving `web/static/index.html`. No runtime behavior or authorization was changed.
- `tests/test_route_authorization_sweep.py` builds operations from `app.routes` and checks authentication, role-policy behavior, forced-password blocking, and selected security invariants. It does not parse this inventory or compare every route row against it.
- `tests/test_frontend_api_route_parity.py` statically extracts literal `getJson(...)` paths from `frontend/src/**/*.ts(x)` (excluding test/setup files), normalizes path parameters, and checks that each discovered preview GET path is a registered FastAPI GET and trader-readable. It does not discover `fetch()` calls, non-GET mutations, legacy `web/static/` callers, or inventory rows with no frontend caller. Template paths are only normalized for simple `${...}` expressions.
- Therefore, these tests provide useful security and connected-preview read-path checks, but they are not a complete route-to-inventory or legacy-to-React parity gate. The remaining work needs a separately reviewed runtime route inventory plus source caller mapping and role-specific browser verification.

### UIX-00 audit status update

- [x] Verified the legacy enhancement bundle's Telegram test trigger, authenticated POST body, loading state, success toast, error toast, and cleanup.
- [x] Reconciled the distinction between legacy Telegram controls and the disconnected React preview.
- [ ] Locate and verify the legacy `GET /api/telegram/status` consumer and its role-specific rendering.
- [ ] Add/verify mocked route tests for the Telegram test handler's response and outbound helper behavior; CI must never send real Telegram messages.
- [ ] Finish runtime route-to-inventory row reconciliation, full legacy-to-React parity, browser/device/accessibility checks, and owner acceptance before production cutover.


## UIX-00 source audit slice: Master Data API contracts and preview boundary (2026-10-28)

Compared `web/routes/master_data.py`, `frontend/src/lib/masterData.ts`, and `frontend/src/MasterDataPanel.tsx` against the Master Data endpoint rows above. This was source inspection only; no backfill, feature computation, or database mutation was triggered.

- `GET /api/master-data/coverage` returns `summary.totalCandles`, `summary.distinctSymbols`, and `summary.coverageEntries`, plus a `coverage` array. Each row uses camelCase fields such as `candleCount`, `featureCount`, `hasFeatures`, `minOpenTime`, `maxCloseTime`, `startDate`, and `endDate`. The inventory's prior `datasets` / `distinctDatasets` / snake_case description did not match the handler response.
- `POST /api/master-data/backfill` and `POST /api/master-data/build-features` return the created job object directly, not a `job_id` envelope. The job identifier field is `id`; initial status is `queued` (the worker can quickly transition it to `running`, then `completed` or `failed`). The backfill object also includes `years` and `candles_fetched`.
- `GET /api/master-data/jobs` returns `{"jobs": [...]} ` from live in-process jobs merged with persisted recent jobs. Job objects use `id`, `message`, numeric epoch timestamps, and may include `candles_fetched`; they are not guaranteed to expose the older documented `job_id` and `error` fields.
- `GET /api/master-data/jobs/{job_id}` returns the job object itself, with a 404 when it cannot be found. It is a registered backend route, but the current React preview does not poll this per-job endpoint.
- The legacy dashboard uses `fetch()` for its job list and has write actions for backfill and feature computation. The React `MasterDataPanel` is intentionally read-only: it loads coverage plus the jobs list through `getJson()`, offers retry/filtering, and explicitly tells users that write actions remain unavailable. It is not a feature-complete replacement for the legacy Master Data view.
- React preview contract types currently match the live coverage response and support both `id` / `job_id` and message/error fallbacks for job display. The E2E smoke fixture stubs the same read paths; that fixture verifies page smoke behavior, not actual DB-backed response contracts or legacy mutation parity.

### Migration implications and remaining gates

- [x] Corrected the inventory's coverage and job response descriptions against the route implementation.
- [x] Recorded the deliberate read-only boundary of the React preview versus legacy backfill/build-feature controls.
- [x] Added focused mocked route response-contract tests for coverage and job listing; no real Binance fetches or feature workers are invoked. Job creation is intentionally not called by this read-only test slice.
- [x] Migrated backfill and feature-build controls into the React preview with admin/trader gating, explicit confirmation, validation, error recovery, and regression tests (PR #528). Production cutover remains blocked on the overall parity and acceptance gates.
- [ ] Verify role-specific browser behavior, all legacy callers/polling, route-to-row parity, accessibility/device behavior, and owner acceptance before production cutover.


## UIX-00 source audit slice: Crypto search/popular contracts and provider dependency (2026-10-28)

Compared `web/routes/search.py` with the legacy `web/static/js/app.js` call site and the React preview scope.

- `GET /api/crypto/search` defaults to `q=""`, `market="all"`, and `limit=25` (bounded 1–100), not `market="spot"` and `limit=20`. It filters spot and/or futures USDT ticker rows by substring match on symbol/base asset and sorts the combined results by `quoteVolume`.
- The result rows contain numeric price/change/high/low/volume fields in addition to symbol/base/quote/market metadata. The old inventory schema omitted those fields.
- `GET /api/crypto/popular` is not a curated fixed list of BTC/ETH/SOL/BNB. It returns up to 15 highest-quote-volume USDT tickers from the selected market; any market value other than the exact string `futures` selects spot data.
- Both handlers depend on Binance 24-hour ticker APIs (spot and futures) with independent 60-second in-memory caches and 8-second HTTP timeouts. When a request fails, the helper logs the error and returns the existing cache, which may be empty or stale. These endpoints are authenticated by the central API security boundary.
- The inspected legacy UI calls search with an explicit `limit=20` and displays a “Querying Binance live API…” loading state. It also calls popular with `market=spot`. The React Master Data preview does not call either endpoint; it is read-only and does not currently migrate the legacy symbol search or quick-select behavior.

### Migration implications and remaining gates

- [x] Corrected documented defaults, result fields, provider dependency, and popular-list semantics against source.
- [x] Distinguished legacy consumers from the disconnected React preview.
- [ ] Add mocked provider/route tests for defaults, market selection, response schema, cache fallback, and upstream failure; tests must not depend on live Binance availability.
- [ ] Decide whether search/quick-select belong in the React Master Data migration and add browser-level tests if migrated.
- [ ] Continue route-to-inventory reconciliation, RBAC/browser/device/accessibility checks, and owner acceptance before production cutover.


## UIX-00 source audit slice: Backtest promotion policy contracts (2026-10-28)

Compared `web/routes/backtest.py` with the inventory and legacy policy/audit consumers.

- `GET /api/backtest/promotion-audit` defaults to `limit=25` (range 1–100), not 50. The compact home panel explicitly requests five events. The endpoint returns `{"events": ...}`; persisted rows represent auto-promotion decisions and may include decision statuses such as rejected, promoted, alert-failed, or reconciliation-required rather than only successful promotions.
- `PUT /api/backtest/promotion-policy` validates bounded thresholds and finite numeric values. Defaults are disabled, 30 minimum test trades, 1.2 profit factor, 0.05R expectancy, 8R max drawdown, 0.1 rank score, holdout required, Telegram enabled. Because Telegram defaults true, the UI must make alert side effects clear when enabling promotion.
- A successful policy write followed by failed readback returns HTTP 200 with `status="saved_readback_unavailable"` and an explicit warning. This is materially different from both a failed write and a fully verified readback; the UI must preserve that distinction.
- Auto-promotion is a side-effecting workflow: it selects candidates from training data, may require separate holdout evaluation, verifies eligibility against the executable paper strategy registry, activates a paper configuration, optionally sends a Telegram deployment alert, and persists a decision audit. Uncertain partial outcomes return reconciliation statuses; retry UX must not blindly repeat activation or notifications.
- This pass is a source/documentation audit, not an executed backtest or promotion. No paper strategy was activated and no Telegram message was sent.

### Migration implications and remaining gates

- [x] Corrected policy defaults/bounds, audit limit, response envelope, and readback warning behavior.
- [x] Documented that promotion audit is a decision log, not just a list of successful deployments.
- [ ] Add route contract tests for policy defaults/bounds and the saved-but-readback-unavailable response, plus audit pagination bounds.
- [ ] Verify React promotion controls preserve admin-only writes, clear Telegram side-effect consent, and reconciliation-required states before replacing legacy Backtest Studio.
- [ ] Continue full route/view/caller parity, role-specific browser acceptance, accessibility/device checks, and owner acceptance before production cutover.


### Additional Backtest Studio read-only preview contract check (2026-10-28)

- The React preview calls `GET /api/backtest/history?limit=100`, so its “Latest 100 records maximum” label reflects the explicit caller parameter, not the backend default. The backend default is 30 and the accepted range is 1–100.
- `GET /api/backtest/runs/{run_id}` currently returns `run` and `trades`; it does not return the `equityCurve` or `candles` claimed by the previous inventory row. The preview only consumes the returned run summary and closed trades, which matches the handler.
- The React preview remains read-only. It does not call backtest execution, run-all tournament, policy update, promotion, or Telegram routes. Legacy Backtest Studio still exposes those workflows, so this is a material migration gap rather than complete feature parity.
- [x] Corrected history defaults/shape and run-detail response shape against the route handler.
- [ ] If charts/equity curves/candles are required in the React replacement, design and test an explicit API contract instead of assuming those fields are returned by this endpoint.


## UIX-00 source audit slice: Backtest strategy registry and execution payloads (2026-10-28)

Compared `web/routes/backtest.py` response construction with the inventory schema.

- `GET /api/backtest/strategies` returns `id`, `baseName`, `isInverse`, `displayName`, `category`, `validated`, `description`, `params`, and `sharedParams`. It does not return the documented `name`, `type`, or `holdout_passed` fields.
- `POST /api/backtest/run` returns a numeric run ID or null, `dataSegment`, `totalCandles`, `metrics`, `equity`, `trades`, and `candles`. The previous inventory's `equityCurve` field name and simplified metrics shape were inaccurate.
- Request defaults are material: market spot, timeframe 1d, strategy `confluence_ensemble_v1`, full data segment, $5,000 initial capital, 1% risk per trade, and database persistence enabled. The route requires at least 50 source candles and at least 30 candles in the selected segment. Insufficient data returns HTTP 400 with a detail message; no simulation is run.
- If saving the run fails, the route logs the persistence error and can still return simulation results with `runId=null`. UI must not imply a saved historical run exists unless the returned ID is confirmed.
- [x] Corrected strategy-list and run response contracts from source.
- [ ] Add mocked route contract tests for registry field names, default request values, insufficient-data errors, and simulation-success/database-save-failure behavior.
- [x] Migrated single-run backtest execution into the React preview with admin/trader gating, explicit confirmation, dynamic strategy/shared parameters, persistence-status messaging, and regression tests (PR #529). Run-all, policy edits, promotion, and Telegram actions remain outside this slice.


## UIX-00 source audit slice: Custom strategy quick-test contract (2026-10-28)

Compared `web/routes/custom_strategy.py` with the inventory and React Strategy Library client.

- `POST /api/custom-strategy/quick-test` uses up to the latest 1,000 candles, not 500. It rejects insufficient history (<50 source candles) with HTTP 400.
- Its successful response includes `totalCandles`, `totalTrades`, fractional `winRate` and percentage `winRatePct`, `netR`, `profitFactor`, `maxDrawdownR`, and `expectancyR`. Non-finite numeric metrics are sanitized to null where applicable.
- The React Strategy Library only calls the list and indicator GET endpoints. It does not call AI generation, create/delete, or quick-test endpoints; all those legacy capabilities remain outside this preview. The existing inventory and role-policy notes correctly identify the prefix-wide viewer restriction, so no RBAC change is made here.
- [x] Corrected quick-test response schema and data-window behavior.
- [ ] Add mocked route tests for the 50-candle guard, latest-1,000 window, and null sanitization of NaN/Infinity metrics.
- [ ] Decide whether to migrate create, delete, AI generation, and quick-test workflows before calling Strategy Library feature parity complete.


## UIX-00 source audit slice: News AI API contracts and preview mismatch (2026-10-28)

Compared `web/routes/news_routes.py`, the `db/db.py` news read helpers, `frontend/src/lib/news.ts`, and `frontend/src/NewsAIPanel.tsx`. This was source inspection only; no collector, inference, trade, or Telegram action was triggered.

- `GET /api/news/stats` returns collection/processing counts (`total_events_collected`, `events_evaluated_by_ai`), signal counts and confidence (`high_confidence_signals`, `avg_confidence`), execution state (`trades_opened`, `pending_signals`), a nested `performance` object (`closed_trades`, `wins`, `losses`, `win_rate`, `expectancy_r`, `profit_factor`, `total_r`, `net_pnl`, `max_drawdown_r`), and collector `heartbeats`. It does not return `bullish_count`, `bearish_count`, `neutral_count`, or `active_overlay_multiplier` as expected by the React preview.
- `GET /api/news/signals?limit=N` returns `{signals: [...]} `; rows include `id`, `symbol`, `market`, `timeframe`, `bias`, `confidence`, `reasoning`, `invalidation_condition`, `time_horizon`, `trade_timing`, `acted_on`, `paper_position_id`, `skip_reason`, `created_at`, plus joined event context such as `headline`, `url`, `source`, `category`, and impact/value fields. The preview instead expects `sentiment`, `impact_horizon`, and `published_at`; those fields do not match the helper's returned names.
- `GET /api/news/events?limit=N` returns `{events: [...]} `; rows include `source`, `category`, `headline`, `url`, `symbols`, `country`, `impact`, `published_at`, `scheduled_at`, actual/forecast/previous values, and processing/fetch timestamps. The preview instead expects `event_text`, `importance`, and `detected_at`.
- The database helpers clamp signal/event limits to 1–200. Route defaults are 50 signals and 30 events; the React client explicitly requests those same limits. Stats/signals/events are read-only handlers and return HTTP 500 with sanitized route-level messages when the helper raises.
- The route module's documented purpose is visibility into data produced by separate collectors and execution processes; these GET requests do not start polling, call AI, or execute trades.

### Migration implications and remaining gates

- [x] Compared route response construction with the database read helpers and React client types.
- [x] Identified and corrected schema mismatches in News AI stats, signals, and events; the preview now uses backend-shaped fields and handles nullable metrics.
- [x] Updated `frontend/src/lib/news.ts` types and `NewsAIPanel.tsx` to match the actual stats, signal, event, and heartbeat/performance contracts, with truthful operational KPI labels.
- [x] Added mocked backend-shaped API/component tests for populated payloads and nullable confidence/performance metrics; tests do not require a live database or start collectors.
- [ ] Reconcile any remaining legacy News AI caller/polling behavior, verify role-specific browser behavior, and complete runtime route/view/caller parity, accessibility/device checks, and owner acceptance before production cutover.

The News AI contract mismatch described in the historical audit has been corrected and regression-tested. The React preview remains isolated from FastAPI production serving; this documentation update does not authorize production cutover.
