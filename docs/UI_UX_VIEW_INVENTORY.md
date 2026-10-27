# Signal Bot UI/UX Comprehensive View & API Inventory (UIX-00)

**Document Version:** 1.1.0  
**Status:** In review — React preview endpoint mapping added; lockfile committed and CI uses `npm ci`; full route-by-route source verification and owner acceptance pending  
**Target Architecture:** React 18+ + TypeScript + Vite + Tailwind CSS + shadcn/ui + TanStack Query v5 + TanStack Table v8  
**Source baseline for reconciliation:** `13f2823541367cce1c676db83116eb390b8be552` (master after PR #439; source-level route decorator count checked on 2026-10-27, runtime route registry and endpoint-to-row parity still pending)  
**Execution Brief Reference:** [`docs/UI_UX_ANTIGRAVITY_EXECUTION_BRIEF.md`](UI_UX_ANTIGRAVITY_EXECUTION_BRIEF.md)  
**Modernization Plan Reference:** [`docs/UI_UX_MODERNIZATION_PLAN.md`](UI_UX_MODERNIZATION_PLAN.md)  

---

## 1. Executive Summary & Audit Scope

This document is Antigravity's detailed baseline inventory draft for **Phase UIX-00** of the Signal Bot UI/UX modernization program. It contains a row-by-row register intended to cover dashboard views, modal dialogs, polling, FastAPI routes, RBAC, request/response schemas, and side effects. The register has not yet been independently verified row-by-row against the current `master` source; do not treat its entries or totals as authoritative until that review is complete.

### Audit Summary Metrics
- **Navigation Views Audited:** 11 primary views (`view-home`, `view-master-data`, `view-backtest`, `view-paper`, `view-ml-studio`, `view-strategies`, `view-news-ai`, `view-research`, `view-market-screener`, `view-wallet-tracking`, `view-settings`).
- **Modal Dialogs Audited:** 7 interactive dialogs (`login-modal`, `force-password-modal`, `backfill-modal`, `deploy-modal`, `modal-run-ml`, `modal-deploy-ml`, `modal-inspect-ml`).
- **Backend API Endpoint Rows Listed:** Source inspection counted 81 `@router` HTTP endpoint decorators across the 13 modules explicitly included by `web/app.py` (`auth`, `paper`, `search`, `master_data`, `backtest`, `settings`, `system_status`, `custom_strategy`, `telegram`, `ml`, `news`, `research`, and `wallet`). `web/app.py` also declares `GET /` outside those routers. This verifies the static decorator count, not runtime registry completeness or one-to-one parity with all 81 rows in the draft register.
- **Frontend Tooling State:** The isolated React preview now exists under `frontend/` with `package.json`, TypeScript, Vite, and Vitest. The npm lockfile is committed in PR #456 and CI now uses `npm ci` after PR #457. Browser-level Playwright coverage remains outstanding; CI component tests are not a substitute for browser/device acceptance.
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

### 2.2 Repository Tooling & Serving State (rechecked 2026-10-27)
- **Frontend manifests:** `frontend/package.json`, `frontend/tsconfig.json`, and `frontend/vite.config.ts` exist. The preview uses React, TypeScript, Vite, and Vitest; `npm run build` runs the TypeScript check before the production build.
- **Lockfile:** `frontend/package-lock.json` is now committed on `master` after PR #456. The follow-up workflow change is switching CI to `npm ci` and caching against this lockfile; that CI change must pass before the reproducible-install phase is considered complete.
- **Host tooling:** The older note that Node.js was unavailable came from a prior host audit and is not evidence of current CI or execution-environment availability. Verify Node/npm versions in the actual environment used for any build.
- **Serving boundary:** Modern frontend code remains isolated under `frontend/`, with build output to `frontend/dist/`. FastAPI still serves the legacy `web/static/` dashboard; do not switch routes or mount the preview before browser, parity, RBAC, accessibility, and owner-acceptance gates pass.

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
| `GET /api/master-data/coverage` | `get_data_coverage`<br>`master_data.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"summary": {"totalCandles": int, "distinctSymbols": int, "distinctDatasets": int}, "datasets": List[{"symbol": str, "market": str, "timeframe": str, "count": int, "first_candle": str, "last_candle": str, "has_features": bool}]}` | Aggregates OHLCV metadata from MariaDB `ohlcv` and `features` tables | Overview KPI cards & Master Data dataset table |
| `POST /api/master-data/backfill` | `trigger_backfill`<br>`master_data.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `BackfillRequest`<br>`{symbol: str, market: str, timeframe: str, years: float, auto_compute_features: bool}` | **200 OK:**<br>`{"job_id": str, "status": "pending", "progress": 0, "message": str}` | Creates job in `job_store`; spawns asynchronous background kline backfill task; queries Binance REST API | `#backfill-modal`<br>`App.submitBackfill()` |
| `POST /api/master-data/build-features` | `trigger_build_features`<br>`master_data.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `FeatureBuildRequest`<br>`{symbol: str, market: str, timeframe: str}` | **200 OK:**<br>`{"job_id": str, "status": "pending", "progress": 0, "message": str}` | Spawns background worker to calculate TA-Lib/pandas technical features; writes to `features` table | Dataset table action (`#view-master-data`) |
| `GET /api/master-data/jobs` | `list_jobs`<br>`master_data.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"jobs": List[{"job_id": str, "type": str, "symbol": str, "market": str, "timeframe": str, "status": str, "progress": int, "error": Optional[str], "created_at": str}]}` | Reads active and recent job state from memory and persistent `job_store` | Active Jobs table (`#view-master-data`) |
| `GET /api/master-data/jobs/{job_id}` | `get_job`<br>`master_data.py` | Authenticated (All roles) | Blocked (403 required) | **Path:** `job_id: str` | **200 OK:**<br>`{"job_id": str, "status": str, "progress": int, "error": Optional[str]}`<br>**404:** Job not found | Reads discrete job progress; used by polling worker | Polling cycle (`App.pollJobs`) |

---

### 4.3 Market Asset Search (`search.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/crypto/search` | `search_crypto`<br>`search.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `q: str`, `market: str = "spot"`, `limit: int = 20` | **200 OK:**<br>`{"query": str, "market": str, "count": int, "results": List[{"symbol": str, "baseAsset": str, "quoteAsset": str, "market": str}]}` | Queries cached Binance exchange info | Symbol search inputs (`#modal-backfill-symbol`, search inputs) |
| `GET /api/crypto/popular` | `popular_crypto`<br>`search.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `market: str = "spot"` | **200 OK:**<br>`{"market": str, "items": List[{"symbol": str, "name": str, "volume24h": float}]}` | Reads curated benchmark pairs (BTC, ETH, SOL, BNB) | Quick-select pair tags (`#view-master-data`) |

---

### 4.4 Backtest Studio & Visual Simulator (`backtest.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/backtest/strategies` | `list_strategies`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"strategies": List[{"id": str, "name": str, "description": str, "type": str, "params": dict, "holdout_passed": bool}]}` | Introspects built-in `STRATEGIES` registry | Strategy dropdowns across Backtest and Paper views |
| `POST /api/backtest/run` | `execute_backtest`<br>`backtest.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `BacktestRunRequest`<br>`{symbol: str, market: str, timeframe: str, strategy: str, data_segment: str, initial_capital: float, risk_per_trade_pct: float, save_to_db: bool, params: dict}` | **200 OK:**<br>`{"runId": str, "symbol": str, "strategy": str, "metrics": {"total_trades": int, "win_rate": float, "profit_factor": float, "expectancy_r": float, "net_pnl": float, "max_drawdown_pct": float}, "trades": List[dict], "equityCurve": List[dict], "candles": List[dict]}` | Runs event-driven historical simulation; if `save_to_db=True`, writes run results into `backtest_runs` | "Run Backtest" form (`#view-backtest`) |
| `POST /api/backtest/run-all` | `run_all_strategies`<br>`backtest.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `RunAllStrategiesRequest`<br>`{symbol: str, market: str, timeframe: str, data_segment: str, initial_capital: float, risk_per_trade_pct: float}` | **200 OK:**<br>`{"symbol": str, "market": str, "timeframe": str, "leaderboard": List[dict], "aiInsight": Optional[str]}` | Executes Battle Royale tournament across all registered strategies; ranks by profit factor / expectancy | "Battle Royale" button (`#view-backtest`) |
| `POST /api/backtest/ai-insight` | `get_standalone_ai_insight`<br>`backtest.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `{symbol: str, market: str, timeframe: str, strategy: str}` | **200 OK:**<br>`{"symbol": str, "strategy": str, "metrics": dict, "insight": str}` | Generates quantitative performance diagnosis and trade risk critique | "AI Quantitative Insight" panel (`#view-backtest`) |
| `GET /api/backtest/history` | `get_backtest_history`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `symbol: Optional[str]`, `timeframe: Optional[str]`, `strategy: Optional[str]`, `limit: int = 20` | **200 OK:**<br>`{"runs": List[{"id": str, "symbol": str, "strategy": str, "timeframe": str, "net_pnl": float, "win_rate": float, "created_at": str}]}` | Reads persisted simulation history from `backtest_runs` | Leaderboard table (`#view-backtest`, `#view-home`) |
| `GET /api/backtest/runs/{run_id}` | `get_run_details`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | **Path:** `run_id: str` | **200 OK:**<br>`{"run": dict, "trades": List[dict], "equityCurve": List[dict], "candles": List[dict]}`<br>**404:** Not found | Fetches full historical run artifacts, candlestick slices, and trade execution markers | Historical run drill-down (`#view-backtest`) |
| `GET /api/backtest/promotion-policy` | `get_promotion_policy`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"policy": {"enabled": bool, "min_test_trades": int, "min_profit_factor": float, "min_expectancy_r": float, "max_drawdown_r": float, "min_rank_score": float, "require_holdout": bool, "send_telegram": bool}}` | Reads auto-promotion qualification criteria | Promotion Policy drawer (`#view-backtest`) |
| `PUT /api/backtest/promotion-policy` | `update_promotion_policy`<br>`backtest.py` | `admin` only (`_role_allowed` enforcement) | Blocked (403 required) | **Body:** `StrategyAutoPromotionPolicyRequest`<br>`{enabled: bool, min_test_trades: int, ...}` | **200 OK:**<br>`{"status": "ok", "policy": dict}`<br>**400:** Threshold validation error | Persists updated qualification thresholds for paper deployment | "Save Policy" button (`#btn-save-promotion-policy`) |
| `GET /api/backtest/promotion-audit` | `list_promotion_audit`<br>`backtest.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 50` | **200 OK:**<br>`{"events": List[{"id": int, "strategy_name": str, "symbol": str, "action": str, "status": str, "config_id": int, "created_at": str}]}` | Reads audit trail of promoted strategies from `strategy_promotions` | Promotion Audit modal/table (`#view-backtest`) |

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
| `GET /api/custom-strategy/indicators` | `get_indicators`<br>`custom_strategy.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"indicators": List[{"id": str, "name": str, "category": str, "params": dict}], "operators": List[str]}` | Returns registered technical indicators and relational operators | Visual Rule Builder dropdowns (`#view-strategies`) |
| `GET /api/custom-strategy/list` | `list_custom_strategies`<br>`custom_strategy.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"strategies": List[{"name": str, "displayName": str, "description": str, "created_at": str}]}` | Reads user-created strategies from `custom_strategies.json` | Custom Strategy Cards (`#view-strategies`) |
| `POST /api/custom-strategy/generate-ai` | `generate_ai_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `GenerateAIStrategyRequest`<br>`{prompt: str, history: Optional[List[dict]], currentConfig: Optional[dict]}` | **200 OK:**<br>`{"status": "success", "provider": str, "strategy": dict, "chatResponse": str}` | Queries configured LLM (OpenAI/Anthropic/heuristic fallback) to generate valid rule JSON | AI Strategy Prompt input (`#view-strategies`) |
| `POST /api/custom-strategy/create` | `create_custom_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `CustomStrategyRequest`<br>`{name: str, displayName: str, description: str, longConditions: List[dict], shortConditions: List[dict], slAtrMult: float, riskRewardRatio: float}` | **200 OK:**<br>`{"status": "success", "name": str, "displayName": str, "message": str}`<br>**400:** Invalid syntax | Validates conditions; writes strategy definition into `custom_strategies.json` | "Save Strategy" button (`#view-strategies`) |
| `DELETE /api/custom-strategy/{name}` | `delete_custom_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Path:** `name: str` | **200 OK:**<br>`{"status": "success", "message": str}`<br>**404:** Not found | Removes strategy from `custom_strategies.json` | Delete button on strategy card (`#view-strategies`) |
| `POST /api/custom-strategy/quick-test` | `quick_test_strategy`<br>`custom_strategy.py` | `trader`, `admin` | Blocked (403 required) | **Body:** `QuickTestRequest`<br>`{strategy: CustomStrategyRequest, symbol: str, market: str, timeframe: str}` | **200 OK:**<br>`{"status": "success", "symbol": str, "trades": int, "winRate": float, "profitFactor": float}` | Executes fast in-memory simulation against recent 500 candles | "Quick Test" button (`#view-strategies`) |

---

### 4.8 News AI Overlay Signals (`news_routes.py`)

| Endpoint | Handler & Source | Role Restriction | Forced-Password Guard | Request Contract | Response Contract | Consequential Side Effects | Consuming UI Component |
|---|---|---|---|---|---|---|---|
| `GET /api/news/stats` | `news_stats`<br>`news_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"bullish_count": int, "bearish_count": int, "neutral_count": int, "average_confidence": float, "active_overlay_multiplier": float}` | Computes sentiment distributions across recent headlines | Sentiment KPI Gauges (`#view-news-ai`) |
| `GET /api/news/signals` | `news_signals`<br>`news_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 50` | **200 OK:**<br>`{"signals": List[{"id": int, "symbol": str, "headline": str, "sentiment": str, "confidence": float, "impact_horizon": str, "published_at": str}]}` | Reads processed news sentiment overlay signals | News Signals Table (`#view-news-ai`) |
| `GET /api/news/events` | `news_events`<br>`news_routes.py` | Authenticated (All roles) | Blocked (403 required) | **Query:** `limit: int = 30` | **200 OK:**<br>`{"events": List[{"id": int, "category": str, "event_text": str, "importance": str, "detected_at": str}]}` | Reads raw macroeconomic & crypto news event feed | Macro Events Feed (`#view-news-ai`) |

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
| `GET /api/telegram/status` | `get_status`<br>`telegram_routes.py` | Authenticated (All roles) | Blocked (403 required) | None | **200 OK:**<br>`{"configured": bool, "chatId": Optional[str], "hasToken": bool}` | Checks whether Telegram bot token and chat ID are configured in environment | Telegram status pill (`#view-settings`) |
| `POST /api/telegram/test` | `run_test`<br>`telegram_routes.py` | `admin` only (`_role_allowed` enforcement) | Blocked (403 required) | **Body:** `TelegramTestRequest`<br>`{custom_message: Optional[str]}` | **200 OK:**<br>`{"success": bool, "message": str}`<br>**400:** Not configured or send failure | Sends outbound test alert via Telegram Bot API (`sendMessage`) to configured chat | "Test Telegram Connection" button (`#view-settings`) |

---

## 5. Reconciled Dashboard Views Inventory (11 Views)

| View ID / Label | Current Source | Consumed Endpoints | User Actions & Controls | Risk Level | Accessibility Requirements | Responsive Reflow Behavior | Required Test Coverage |
|---|---|---|---|---|---|---|---|
| `view-home`<br>**Home & Overview** | `index.html` (`#view-home`), `app.js` (`renderHome`, `fetchSystemStatus`) | `GET /api/system/status`<br>`GET /api/master-data/coverage`<br>`GET /api/backtest/history` | Navigation tabs, "New Backfill" header action, "Sign Out" button | Low (Read-only) | `aria-labelledby`, `aria-live="polite"` header, live clock, keyboard focusable skip-link | 4-col KPI collapses to 1-col on mobile; 3-col main grid collapses to 1-col | `tests/test_system_status.py`, pilot view Vitest + Playwright smoke test |
| `view-master-data`<br>**Master Data** | `index.html` (`#view-master-data`), `app.js` (`renderMasterData`, `submitBackfill`, `triggerComputeFeatures`, `pollJobs`) | `GET /api/master-data/coverage`<br>`GET /api/crypto/search`<br>`GET /api/crypto/popular`<br>`POST /api/master-data/backfill`<br>`POST /api/master-data/build-features`<br>`GET /api/master-data/jobs` | Search symbol, trigger backfill modal, compute features per dataset, filter jobs | High (Long async jobs, Binance rate limits) | Form field associations (`for`/`id`), table header `scope="col"`, sort announcements, progress bar `aria-valuenow` | Table horizontal scroll on mobile; filter inputs wrap | `tests/test_master_data.py`, `tests/test_search.py`, form validation tests |
| `view-backtest`<br>**Backtest Studio** | `index.html` (`#view-backtest`), `app.js` (`renderBacktestStudio`, `runBacktest`, `runBattleRoyale`, `renderBacktestChart`) | `GET /api/backtest/strategies`<br>`POST /api/backtest/run`<br>`POST /api/backtest/run-all`<br>`GET /api/backtest/history`<br>`GET /api/backtest/runs/{id}`<br>`GET/PUT /api/backtest/promotion-policy`<br>`GET /api/backtest/promotion-audit` | Select strategy & pair, set date range/capital, run backtest, run Battle Royale, inspect candlestick & equity chart, deploy to paper, update promotion policy | High (CPU compute, promotion gating) | Candlestick canvas fallback description, form labels, focus trap on deploy dialog | Charts flex/resize responsively; parameter controls stack vertically on tablet/mobile | `tests/test_backtest.py`, `tests/test_accounting.py`, `tests/test_paper_backtest_parity.py` |
| `view-paper`<br>**Paper Trading** | `index.html` (`#view-paper`), `app.js` (`fetchPaperData`, `addPaperStrategyConfig`, `togglePaperStrategy`, `togglePaperTelegram`, `togglePaperReport`, `manualClosePaperPosition`, `cancelPaperOrder`) | `GET /api/paper/configs`<br>`POST /api/paper/configs`<br>`GET /api/paper/metrics`<br>`GET /api/paper/positions`<br>`POST /api/paper/positions/{id}/close`<br>`GET /api/paper/orders`<br>`POST /api/paper/orders/{id}/cancel`<br>`GET /api/paper/trades`<br>`POST /api/paper/sync`<br>`GET /api/paper/circuit-breakers`<br>`POST /api/paper/circuit-breakers/*` | Add strategy config, toggle evaluation/telegram/report, filter order status, cancel pending order, close position, sync ticks, trip/reset circuit breaker | Critical (Simulation ledger, crash recovery, Telegram gating) | ARIA switch roles for toggles, table sort announcements, confirm modals before close/cancel | Horizontal scroll wrappers for order ledger and positions tables; responsive KPI cards | `tests/test_paper_routes.py`, `tests/test_paper_orders_mariadb.py`, `tests/test_paper_metrics_visibility.py`, `tests/test_retailbot_accounting.py` |
| `view-ml-studio`<br>**ML Forecast Studio** | `index.html` (`#view-ml-studio`), `app.js` (`fetchMLData`, `runMlTraining`, `deployMlModel`, `inspectMLExperiment`) | `GET /api/ml/models`<br>`GET /api/ml/experiments`<br>`GET /api/ml/experiments/{id}`<br>`POST /api/ml/run-experiment`<br>`POST /api/ml/deploy-to-paper`<br>`GET/POST /api/paper/circuit-breakers/*` | Run tournament experiment modal, inspect experiment (ROC/calibration/features), deploy model filter to paper, toggle circuit breaker | High (Heavy ML compute, manifest integrity) | Focus management on modals; chart accessible descriptions; progress bar semantics | 2-col tournament board collapses to 1-col; metrics tables scroll horizontally | `tests/test_ml_routes.py`, `tests/test_ml_promotion.py` |
| `view-strategies`<br>**Strategy Library** | `index.html` (`#view-strategies`), `app.js` (`renderStrategyLibrary`, `initStrategyArchitect`, `fetchStrategies`, `saveCustomStrategy`, `deleteCustomStrategy`) | `GET /api/backtest/strategies`<br>`GET /api/custom-strategy/list`<br>`GET /api/custom-strategy/indicators`<br>`POST /api/custom-strategy/create`<br>`POST /api/custom-strategy/generate-ai`<br>`POST /api/custom-strategy/quick-test`<br>`DELETE /api/custom-strategy/{name}` | Browse strategy cards, build visual rule tree, prompt AI strategy generator, quick-test strategy, save/delete custom strategy, send to backtest | Medium (Custom code/rule safety, JSON schema validation) | Accessible names on dynamic rule builder inputs; delete confirmation alerts | Builder grid wraps from 2-col to 1-col on mobile; card grid auto-reflows | `tests/test_custom_strategy.py` |
| `view-news-ai`<br>**News AI Signals** | `index.html` (`#view-news-ai`), `app.js` (`fetchNewsData`, `renderNewsSignals`, `renderNewsFeed`) | `GET /api/news/signals`<br>`GET /api/news/events`<br>`GET /api/news/stats` | Filter news feed by symbol/keyword, filter sentiment (Bullish/Bearish), manual refresh | Low (Read-only overlay display) | Sentiment badge contrast compliance; screen-reader labels for confidence gauges | News feed and metrics panel stack vertically on tablets and mobile devices | `tests/test_news_routes.py` |
| `view-research`<br>**Research Bots** | `index.html` (`#view-research`), `app.js` (`fetchResearchData`, `_fetchBotControl`, `toggleBotLifecycleSetting`) | `GET /api/research/retailbot2/stats`<br>`GET /api/research/retailbot2/positions`<br>`GET /api/research/retailbot2/trades`<br>`GET/POST /api/research/retailbot2/strategies`<br>`GET/POST /api/research/retailbot2/control` | Toggle lifecycle (paper, telegram, report) per strategy/mode (shadow/inverse), start/stop/restart bot daemon, filter closed trades | Critical (Daemon process control, strict boolean flags, SQL column aliases) | ARIA switch roles, distinct shadow vs inverse labels, sortable trade headers | Dual shadow/inverse comparison grid stacks into single column on mobile | `tests/test_retailbot2_lifecycle.py`, `tests/test_retailbot_accounting.py` |
| `view-market-screener`<br>**Market Screener** | `index.html` (`#view-market-screener`), `app.js` (`fetchScreenerData`, `renderScreenerTable`, `filterScreener`) | `GET /api/research/screener/status`<br>`GET /api/research/screener/signals`<br>`GET /api/research/screener/stats`<br>`GET/POST /api/research/screener/control` | Filter signals by volume/quality/symbol, sort columns, screener daemon control, "Send to Backtest" shortcut | Medium (Real-time screening, worker control) | Table sort announcements with counts; filter form labels; keyboard navigable table | Table horizontal scroll wrapper; responsive filter toolbar | `tests/test_screener.py` |
| `view-wallet-tracking`<br>**Wallet Tracking** | `index.html` (`#view-wallet-tracking`), `app.js` (`fetchWalletData`, `_wtLoadSettings`, `saveWalletSettings`, `walletTrack`, `walletUntrack`, `walletToggleTrack`) | `GET /api/wallets/status`<br>`GET /api/wallets/tracked`<br>`POST/DELETE /api/wallets/tracked`<br>`POST /api/wallets/tracked/toggle`<br>`GET /api/wallets/events`<br>`GET /api/wallets/positions`<br>`GET /api/wallets/token-flow`<br>`GET/PUT /api/wallets/settings` | Add tracked address, remove wallet, toggle tracking active, filter events, change token-flow period (6h/24h/7d), update alert thresholds | Medium (External RPC/scan parsing) | Form input labels, monospace wallet address styling with copy feedback, table headers | Flow charts and transaction tables stack on smaller screens | `tests/test_wallet_tracker.py`, `tests/test_wallet_tracker_mariadb.py` |
| `view-settings`<br>**Settings & Users** | `index.html` (`#view-settings`), `app.js` (`renderSettings`, `fetchUsers`, `handleCreateUser`, `toggleUserStatus`, `submitResetPassword`, `testTelegram`) | `GET /api/settings/status`<br>`GET/POST /api/users`<br>`PUT/DELETE /api/users/{id}`<br>`POST /api/users/{id}/reset-password`<br>`GET /api/telegram/status`<br>`POST /api/telegram/test` | Create user, toggle user active status, admin password reset, delete user, test Telegram bot connection, inspect DB metrics | Critical (Security, RBAC credentials, session revocation) | Confirmation modals for deactivation/deletion; explicit role select labels; focus trapping | User table scrolls horizontally; configuration cards wrap | `tests/test_auth_routes.py`, `tests/test_users.py` |

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
- [x] **API route-count baseline:** Source inspection of all 13 routers included by `web/app.py` confirms 81 `@router` HTTP endpoint decorators, plus the app-level `GET /` route. This confirms the decorator count only; runtime route registration, handler-by-handler authorization, schemas, side effects, and caller parity remain open.
- [x] **Available CI Evidence:** The `pytest` check passed on Antigravity commit `74d67c2`; the reported total of 116 tests and complete suite coverage have not been independently audited in this review.
- [ ] **Acceptance Review Gate:** **In Review / Pending Acceptance**. UIX-00 will be marked complete only after the user/owner has reviewed and approved this baseline inventory.
- [x] **UIX-01 foundation status:** The React/Vite preview, committed lockfile, and frontend CI are already present. PR #499 is merged, and its `Frontend foundation` workflow run #132 completed successfully, including Playwright browser smoke tests. This does not complete UIX-00 or authorize production cutover.
- [ ] **Remaining audit scope:** Verify route methods, request/response schemas, role restrictions, forced-password guard, side effects, and route-to-view parity; complete the full accessibility/responsive/role/session browser acceptance suite.


## 13. React preview route-to-API map (verified 2026-10-27)

This table records the endpoints called by the currently merged React preview clients at source baseline `d2f513234e2ee5aa7c1883000bb79bde1558004a` (2026-10-27). It is **not** a claim that the full legacy dashboard inventory has been reconciled or that the React preview has feature parity. Confirm actual FastAPI route registration, RBAC and forced-password behavior against source before any production cutover.

| React preview view | Client module | Endpoints called by the view | Side-effect boundary |
|---|---|---|---|
| Authentication gate | `lib/auth.ts` | `POST /api/auth/login`, `GET /api/auth/me` (session bootstrap contract), `POST /api/auth/logout`, `POST /api/auth/change-password` | Authentication mutations are intentional and use the existing server contract; password-change gating and browser-level session-expiry tests remain required. Component tests now cover a 401 after successful sign-in, clearing the stale token, and returning to the sign-in screen. |
| Overview | `lib/systemStatus.ts` | `GET /api/system/status` | Sanitized status read; must not expose the admin-only `/api/settings/status` connection details. |
| Market Screener | `lib/screener.ts` | `GET /api/research/screener/status`, `GET /api/research/screener/signals?limit=50`, `GET /api/research/screener/stats` | The read snapshot uses GET only. The module also exports a separate control save helper; its reachability, rendering, and role behavior must be reviewed independently before claiming the whole feature is read-only. |
| Paper Trading | `lib/paperTrading.ts` | `GET /api/paper/positions`, `GET /api/paper/configs`, `GET /api/paper/orders?limit=20`, `GET /api/paper/trades?limit=20`, `GET /api/paper/metrics`, `GET /api/paper/circuit-breakers` | Read-only ledger/risk monitor; no order, config, deploy, cancel, or worker mutation from this view. |
| Strategy Library | `lib/strategyLibrary.ts` | `GET /api/custom-strategy/list`, `GET /api/custom-strategy/indicators` | Catalog-only view; create/update/test/AI actions are not exposed by this client. |
| Wallet Tracking | `lib/walletTracking.ts` | `GET /api/wallets/meta`, `GET /api/wallets/settings`, `GET /api/wallets/status`, `GET /api/wallets/tracked`, `GET /api/wallets/positions?period_hours=24&limit=20`, `GET /api/wallets/token-flow?period_hours=24&limit=10`, `GET /api/wallets/events?limit=20` | Read-only snapshot; no wallet settings, tracked-address, or worker mutation. Verify that all returned settings fields are safe for every authorized role. |
| Backtest Studio | `lib/backtestStudio.ts` | `GET /api/backtest/strategies`, `GET /api/backtest/history?limit=100`, `GET /api/backtest/runs/{runId}` | Saved results only; no run, run-all, promotion, or paper deployment request. |
| ML Studio | `lib/mlStudio.ts` | `GET /api/ml/experiments`, `GET /api/ml/models`, `GET /api/ml/experiments/{experimentId}` | Persisted experiment/model reads only; no training or deploy-to-paper request. |
| Research | `lib/research.ts` | `GET /api/research/retailbot2/positions?limit=100`, `GET /api/research/retailbot2/trades?limit=50`, `GET /api/research/retailbot2/stats`, `GET /api/research/retailbot2/strategies` | Lifecycle readback only; no evaluation/paper/Telegram/report mutation. |
| News AI | `lib/news.ts` | `GET /api/news/stats`, `GET /api/news/signals?limit=50`, `GET /api/news/events?limit=30` | Saved overlay data only; no poller, inference, execution, or Telegram action. |
| Master Data | `lib/masterData.ts` | `GET /api/master-data/coverage`, `GET /api/master-data/jobs` | Coverage/job status only; no backfill, feature build, or worker launch. |
| Settings | `SettingsPanel.tsx` | No API calls; uses existing local theme state and auth-gate identity | No admin status, credentials, user administration, Telegram test, or server setting mutation. |

### Audit checkpoint (2026-10-27)

- `web/app.py` includes 13 routers and serves the legacy dashboard from the FastAPI application; the React preview remains separate from the production root entrypoint.
- Source decorator count: 81 HTTP route decorators across the 13 included routers, plus `GET /` in `web/app.py`. The count is not a substitute for inspecting FastAPI's final route registry or validating each handler.
- `frontend/src/App.tsx` currently renders `ScreenerPanel` but does not import/render `ScreenerControlsPanel`. The latter is a standalone component that calls `saveScreenerControl` (`POST /api/research/screener/control`). Therefore, its mutation UI is not reachable from the current preview navigation at this source baseline; the preview notice and roadmap copy should be reconciled with this fact before any claim that the control UI is shipped.
- The current workflow run #132 confirms the forced-password-change browser smoke test path passes, but does not establish completion of the full login, logout/session-expiry, role-difference, responsive-navigation, and theme-persistence acceptance suite.
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
