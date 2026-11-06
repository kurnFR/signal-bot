# Signal Bot — Legacy to Modern UI Parity Inventory

**Repository:** `kurnFR/signal-bot`
**Target:** Complete parity verification between the legacy dashboard (`web/static/`) and the modern React + TypeScript frontend (`frontend/src/`).
**Status:** AUDITED — 100% Workflow Parity Achieved (11 Views Complete, 59 Workflows Complete, 0 Legacy-Only, 0 Missing)
**Date:** November 2026
**Baseline Commit:** `53bd8713d20e3348a5d23d951737037e916ba67f` (Independent UI remediation review baseline)
**Reviewed Implementation Baseline:** `32348c2042f0c8d028e98f977b4d224d3bc33a58` (PR #697 independent review baseline)
**Previous Independently Reviewed Baseline:** `577544e4578ae46988a2cba96bcdc6338ba7b59f` (PR #697 acceptance blockers review)
**Intermediate Remediation Commit:** `a72f81f34e1e9013e1b05e9bbdff751d6f6741f6` (Full 11-view browser acceptance & process termination)
**Final Reviewed PR #697 Commit:** `98817a100bfe89e35237676f09c308c374ea0305` (Complete user profile contract, active-admin serialization, loopback staging identity)
**Remaining Workflows Implementation Commit:** `5c30ee4b5e579114bcf0e6fd1fe06d46b3ac0a80` (PT-07-PT-10, ML-03-ML-04, RB-03-RB-04, WT-04-WT-05 remediation)
**Reference:** [`docs/ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md`](ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md), [`docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md), [`docs/ANTIGRAVITY_MODERN_UI_REMAINING_WORKFLOWS_WORK_ORDER.md`](ANTIGRAVITY_MODERN_UI_REMAINING_WORKFLOWS_WORK_ORDER.md)

---

## 1. Executive Summary

This document establishes the feature-by-feature parity inventory between the legacy dashboard (`web/static/index.html`, `web/static/js/app.js`) and the modern React/TypeScript frontend (`frontend/src/`).

In accordance with the workflow parity audit ([`docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`](ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md)) and the PR #698 remaining workflows work order, all 59 cataloged workflows across all 11 domain navigation views are now fully implemented and verified in the modern React application.

### Summary Metrics
- **Navigation Views Audited:** 11 / 11 views mapped and verified `COMPLETE`.
- **Workflow Parity Status:** 59 Workflows `COMPLETE`, 0 `LEGACY_ONLY`, 0 `MISSING` (59 Total).
- **View-Level Breakdown:** 11 Views `COMPLETE`, 0 Views `PARTIAL`, 0 `MISSING`.
- **Backend API Endpoints Covered:** 93 unique API method/path routes mapped across 13 FastAPI modules.
- **Automated Frontend Test Suite:** 31 test files, 276 unit/component tests passing (`npm test`).
- **End-to-End Browser Tests:** 28 Playwright tests passing across Desktop Chrome, responsive mobile, tablet, and dark/light themes (`npx playwright test`; 100% using mocked API routes).
- **Backend Test Suite:** 1,119 unit/integration tests passing (`pytest`).

---

## 2. Navigation & View Mapping Matrix

| # | Legacy Menu / View ID | Modern UI Navigation Label | Primary React Component | Status | Detailed Workflow Parity |
|---|---|---|---|---|---|
| 1 | `view-home` ("Home & Overview") | **Overview** | `SystemStatusPanel.tsx` | `COMPLETE` | Health KPIs, MariaDB pool status, collector threads, open positions count. |
| 2 | `view-master-data` ("Master Data") | **Master Data** | `MasterDataPanel.tsx` | `COMPLETE` | Coverage inspector, backfill launch modal, feature build modal, jobs polling. |
| 3 | `view-backtest` ("Backtest Studio") | **Backtest Studio** | `BacktestStudioPanel.tsx`, `PromotionPolicyEditor.tsx` | `COMPLETE` | Multi-factor backtest form, run execution, closed trades ledger, saved runs, auto-promotion policy. |
| 4 | `view-paper` ("Paper Trading") | **Paper Trading** | `PaperTradingPanel.tsx` | `COMPLETE` | Positions, orders, performance, circuit breaker reads, order cancellation, manual position close, deploy bot modal, emergency stop, reset circuit breaker. |
| 5 | `view-ml-studio` ("ML Forecast Studio") | **ML Studio** | `MLStudioPanel.tsx`, `MLGeneratorTrainingPanel.tsx` | `COMPLETE` | Tournament results, model inspection, independent ML Signal Generator training, battle royale tournament launch modal, deploy ML filter modal. |
| 6 | `view-strategies` ("Strategy Library") | **Strategy Library** | `StrategyLibraryPanel.tsx` | `COMPLETE` | Strategy catalog, custom rule architect, quick-test simulation, AI draft generator, delete confirmation. |
| 7 | `view-news-ai` ("News AI Signals") | **News AI** | `NewsAIPanel.tsx` | `COMPLETE` | Aggregate sentiment index, real-time news feed, macroeconomic calendar, sentiment trade signals. |
| 8 | `view-research` ("Research Bots") | **Research** | `ResearchPanel.tsx`, `PromotionStatusPanel.tsx` | `COMPLETE` | RetailBot2 positions, trades, bot lifecycle state, stats, interactive lifecycle settings update, stop/hide bot. |
| 9 | `view-market-screener` ("Market Screener") | **Market Screener** | `ScreenerPanel.tsx`, `ScreenerControlsPanel.tsx` | `COMPLETE` | Real-time multi-pair quantitative signals, worker pause/resume, threshold controls, Telegram alert gate. |
| 10 | `view-wallet-tracking` ("Wallet Tracking") | **Wallet Tracking** | `WalletTrackingPanel.tsx` | `COMPLETE` | Tracked wallets table, token transfers feed, multi-chain filters, summary metrics, track/untrack address mutations, wallet alert settings configuration. |
| 11 | `view-settings` ("Settings & Users") | **Settings** | `SettingsPanel.tsx`, `AuthGate.tsx`, `lib/users.ts` | `COMPLETE` | Live user accounts catalog, create user, reset password, toggle active status, delete user, change password form, Telegram test ping (admin only), diagnostics. |

---

## 3. View-by-View Parity Specifications

### 3.1 Overview (`view-home` → `SystemStatusPanel`)
- **Legacy Functionality:** Executive trading overview, engine status, MariaDB pool health, collector status, backtest leaderboard, recent signal events.
- **Components & Layout:**
  - Status KPI Cards: Engine health (`ONLINE`/`DEGRADED`), MariaDB connection status, collector thread counts, API round-trip latency, system uptime.
  - Collector Health Grid: Binance Spot & Futures collectors, WebSocket stream state, last candle timestamp.
  - Operational Summaries: Active simulated positions count, pending paper orders.
- **Interactive Controls:**
  - `Refresh` button: Re-queries `/api/system/status` and `/api/paper/positions`.
- **API Contracts:**
  - `GET /api/system/status` (Public / Authenticated read, no mutation).
  - `GET /api/paper/positions` (Authenticated read).
- **Roles & RBAC:** Accessible to all authenticated roles (`viewer`, `trader`, `admin`).
- **Safety & Error Behavior:** Safe empty states when database is unreachable; clear retry banner with error details; no mutation capabilities.
- **Status:** `COMPLETE`.

---

### 3.2 Master Data (`view-master-data` → `MasterDataPanel`)
- **Legacy Functionality:** Historical candle coverage inspector, backfill launch modal, feature generation launch, background job monitoring.
- **Components & Layout:**
  - Coverage Table: Symbol, market (`spot`/`futures`), timeframe (`15m`, `1h`, `4h`, `1d`), earliest candle date, latest candle date, row count, status.
  - Filters: Symbol search filter, market selector, timeframe dropdown.
  - Interactive Action Modals:
    - Backfill Dialog: Symbol, market, timeframe, start date, end date.
    - Feature Build Dialog: Symbol, market, timeframe.
  - Background Jobs Table: Job ID, job type (`backfill`/`build-features`), progress indicator, status badge (`running`, `completed`, `failed`), error banner.
- **Interactive Controls:**
  - `Launch Backfill`: Requires browser confirmation dialog before calling `POST /api/master-data/backfill`.
  - `Build Features`: Requires browser confirmation dialog before calling `POST /api/master-data/build-features`.
  - `Refresh Jobs`: Polls `GET /api/master-data/jobs`.
  - `Refresh Coverage`: Polls `GET /api/master-data/coverage`.
- **API Contracts:**
  - `GET /api/master-data/coverage`
  - `GET /api/master-data/jobs`
  - `POST /api/master-data/backfill` (Payload: `{ symbol, market, timeframe, start_date, end_date }`)
  - `POST /api/master-data/build-features` (Payload: `{ symbol, market, timeframe }`)
- **Roles & RBAC:**
  - Reads (`GET`): Available to `viewer`, `trader`, `admin`.
  - Mutations (`POST`): Restricted to `trader` and `admin`. Viewer sessions see disabled controls with a defensive permission notice.
- **Safety & Error Behavior:** Explicit confirmation step with parameter summary to prevent unintended heavy I/O workloads; sanitized error display.
- **Status:** `COMPLETE`.

---

### 3.3 Backtest Studio (`view-backtest` → `BacktestStudioPanel`, `PromotionPolicyEditor`)
- **Legacy Functionality:** Historical multi-factor strategy simulation, performance metric audit, closed trade log, auto-promotion policy management.
- **Components & Layout:**
  - Parameter Configuration Form: Strategy dropdown (built-in + custom strategies), Market, Symbol, Timeframe, Date range, Capital, Fee/Slippage overrides.
  - Results Grid: Total Return %, Sharpe Ratio, Max Drawdown %, Win Rate %, Profit Factor, Total Executed Trades.
  - Closed Trade Ledger: Entry/Exit timestamps, Direction (`LONG`/`SHORT`), Prices, Executed Size, Net PnL ($ and %), R-multiple, Exit Reason (`STOP_LOSS`, `TAKE_PROFIT`, `SIGNAL_EXIT`).
  - Saved Backtests Table: Run ID, Strategy, Symbol, Timeframe, Metrics, interactive row selection, ascending/descending column sorting.
  - Auto-Promotion Policy Panel: Configurable thresholds (Min trades, Min Win Rate %, Min Profit Factor, Max Drawdown %, Sharpe floor).
- **Interactive Controls:**
  - `Run Backtest`: Confirmation dialog before calling `POST /api/backtest/run`.
  - `Save Promotion Policy`: Submits `PUT /api/backtest/promotion/policy` with immediate readback validation.
  - `Filter Saved Runs`: Client-side search and multi-column sorting.
- **API Contracts:**
  - `GET /api/backtest/strategies`
  - `GET /api/backtest/runs`
  - `GET /api/backtest/runs/{run_id}`
  - `POST /api/backtest/run` (Payload: strategy parameters and execution constraints)
  - `GET /api/backtest/promotion/policy`
  - `PUT /api/backtest/promotion/policy` (Payload: auto-promotion thresholds)
- **Roles & RBAC:**
  - Reads: `viewer`, `trader`, `admin`.
  - Execution & Policy Mutation: `trader`, `admin` only.
- **Safety & Error Behavior:** Research results are strictly isolated from paper trading; backtest execution never generates live or paper orders; null `run_id` responses are not falsified as saved runs.
- **Status:** `COMPLETE`.

---

### 3.4 Paper Trading (`view-paper` → `PaperTradingPanel`)
- **Legacy Functionality:** Simulated execution monitoring, active bot configurations, open position tracking, pending order management, realized PnL audit, risk circuit breaker, order cancellation, manual position exit, deploy bot modal, circuit breaker trip/reset.
- **Components & Layout:**
  - Circuit Breaker Status Banner: `ACTIVE` / `TRIPPED` indicator with daily loss limit tracking, `Emergency Stop` button (trips breaker), and `Reset Breaker` button.
  - Summary Metrics: Active bot count, Total simulated equity, Open position count, Cumulative realized PnL.
  - Bot Configurations: Strategy name, Symbol, Market, Status (`ACTIVE`/`PAUSED`), Trailing stop settings, `Deploy strategy` button.
  - Open Positions Table: Symbol, Direction, Entry Price, Mark Price, Unrealized PnL ($ and %), Stop Loss / Take Profit prices, `Close` action button opening market-exit confirmation dialog.
  - Open Orders Table: Order ID, Type (`LIMIT`, `MARKET`, `STOP`), Side, Price, Quantity, Status, `Cancel` action button opening order cancellation dialog for pending orders.
  - Closed Trades History: Timestamp, Strategy, Symbol, Side, Execution price, Realized PnL, Fee.
  - Accessible Dialog Modals:
    - Order Cancel Dialog: Shows order ID, side, symbol, and quantity with confirm/cancel buttons and focus trap.
    - Position Close Dialog: Confirms simulated market exit with current mark price and clear simulation notice.
    - Deploy Strategy Modal: Validates strategy selection, symbol, market, capital (min $100), risk % (0.1%–10%), and Telegram alert toggle.
    - Circuit Breaker Confirmation Dialogs: Explicit confirmations for emergency stop and reset with server readback.
- **Interactive Controls:**
  - `Cancel order`: Submits `POST /api/paper/orders/{order_id}/cancel`.
  - `Close position`: Submits `POST /api/paper/positions/{position_id}/close`.
  - `Deploy strategy`: Submits `POST /api/paper/deploy`.
  - `Emergency Stop`: Submits `POST /api/paper/circuit-breakers/emergency-stop`.
  - `Reset Circuit Breaker`: Submits `POST /api/paper/circuit-breakers/reset`.
  - Refresh data button.
  - Tab navigation across Configurations, Positions, Orders, and Trade History.
- **API Contracts:**
  - `GET /api/paper/positions`
  - `GET /api/paper/orders`
  - `GET /api/paper/trades`
  - `GET /api/paper/config`
  - `GET /api/paper/circuit-breaker/status`
  - `POST /api/paper/orders/{order_id}/cancel`
  - `POST /api/paper/positions/{position_id}/close`
  - `POST /api/paper/deploy`
  - `POST /api/paper/circuit-breakers/emergency-stop`
  - `POST /api/paper/circuit-breakers/reset`
- **Roles & RBAC:**
  - Reads: Accessible to all authenticated users (`viewer`, `trader`, `admin`).
  - Mutations: Restricted to `trader` and `admin`. Viewer sessions see disabled/hidden action buttons and receive HTTP 403 on API invocation.
- **Safety & Error Behavior:** Paper execution is strictly isolated from exchange/live trading; read-only operations cannot execute fills; cancel and close operations require deliberate confirmation; circuit breaker protects against runaway simulated drawdowns.
- **Status:** `COMPLETE`.

---

### 3.5 ML Forecast Studio (`view-ml-studio` → `MLStudioPanel`, `MLGeneratorTrainingPanel`)
- **Legacy Functionality:** Machine learning experiment tracking, model artifacts evaluation, ML filter tournaments, independent ML signal generator training, tournament launch modal, deploy ML filter modal.
- **Components & Layout:**
  - Independent ML Signal Generator Panel (`MLGeneratorTrainingPanel`):
    - Inputs: Symbol, Market, Timeframe, Model architecture (`hist_gradient_boosting`, `random_forest`, `logistic_regression`), Minimum validation trades, Stop distance (ATR), Risk/Reward ratio, Maximum holding bars.
    - Research-only badge: Clearly indicates models are saved as research artifacts.
    - Result card: Model ID, Decision policy confidence & margin, Dataset row counts (Train/Val/Test), Out-of-sample test trades, profit factor, drawdown.
  - Completed Tournaments Table: Experiment ID, Market, Base Strategy, Winner Model, Threshold, Baseline R vs ML Test R, Win Rate, Trades. Header features `Launch tournament` button.
  - Model Manifests Table: Model ID, Base Strategy, Threshold, Weights presence, Manifest flag, Eligibility gate status with explanation reasons. Eligible models feature `Deploy to paper` button.
  - Experiment Details Inspector: Raw verified JSON artifact inspector.
  - Accessible Dialog Modals:
    - Launch Tournament Modal: Symbol, market, timeframe, base strategy, model type (`xgboost`, `lightgbm`, `catboost`, `random_forest`, `all`), target type (`binary_win`, `r_multiple`, `sharpe_ratio`), and min validation trades.
    - Deploy ML Filter Modal: Shows model ID, source experiment, target environment (`paper_trading`), confidence threshold, and explicit Telegram alert toggle.
- **Interactive Controls:**
  - `Launch tournament`: Submits `POST /api/ml/run-experiment`.
  - `Deploy to paper`: Submits `POST /api/ml/deploy-to-paper` (strictly gated to models passing eligibility protocol).
  - `Train generator`: Submits `POST /api/ml/generator/run-experiment`.
  - `Refresh`: Reloads tournaments and models via `GET /api/ml/experiments` and `GET /api/ml/models`.
  - Filter experiments: Real-time client-side search.
- **API Contracts:**
  - `GET /api/ml/experiments`
  - `GET /api/ml/models`
  - `GET /api/ml/experiments/{id}`
  - `POST /api/ml/run-experiment` (Payload: `{ symbol, market, timeframe, strategy, model_type, target_type, min_validation_trades }`)
  - `POST /api/ml/deploy-to-paper` (Payload: `{ model_id, target, threshold, telegram_alerts }`)
  - `POST /api/ml/generator/run-experiment` (Payload: `{ symbol, market, timeframe, model_type, min_net_return, min_validation_trades, stop_atr_multiple, risk_reward_ratio, max_hold_bars, seed }`)
- **Roles & RBAC:**
  - Reads: All authenticated roles.
  - Mutations (Tournament launch, Filter deploy, Generator training): `trader`, `admin`. Viewer sessions see actions hidden and receive server-side 403.
- **Safety & Error Behavior:** ML filter tournaments and independent ML generators are kept strictly separate; training never activates paper trading or enables Telegram notifications without separate explicit opt-in; strict eligibility gate prevents deployment of unvalidated or negative-expectation models.
- **Status:** `COMPLETE`.

---

### 3.6 Strategy Library (`view-strategies` → `StrategyLibraryPanel`)
- **Legacy Functionality:** Built-in quantitative strategies catalog, custom strategy rule creation, indicator condition builder, simulated quick-test, AI strategy draft generation, deletion.
- **Components & Layout:**
  - Strategy Catalog: Built-in and custom strategies with parameter descriptions, indicator requirements, and category pills.
  - Condition Builder Form:
    - Long & Short entry conditions (Indicator, Comparison Operator `<`, `>`, `<=`, `>=`, `==`, Numeric threshold).
    - Risk parameters: Stop Loss ATR multiple, Risk/Reward ratio, Trailing stop toggle and parameters.
  - Quick Test Drawer: Runs 1,000-candle simulation; returns Win Rate, Profit Factor, Net R, Trade Count.
  - AI Strategy Draft Dialog: Natural language prompt input, external AI generation, structured review before save.
  - Delete Strategy Dialog: Destructive action confirmation with name confirmation.
- **Interactive Controls:**
  - `Create Strategy`: Submits `POST /api/custom-strategy/create`.
  - `Quick Test`: Calls `POST /api/custom-strategy/quick-test`.
  - `Generate with AI`: Calls `POST /api/custom-strategy/generate-ai`.
  - `Delete Strategy`: Calls `DELETE /api/custom-strategy/{name}`.
  - `Backtest`: Navigates to Backtest Studio with strategy pre-selected.
- **API Contracts:**
  - `GET /api/custom-strategy/list`
  - `POST /api/custom-strategy/create`
  - `POST /api/custom-strategy/quick-test`
  - `POST /api/custom-strategy/generate-ai`
  - `DELETE /api/custom-strategy/{name}`
- **Roles & RBAC:**
  - Reads: All authenticated roles.
  - Mutations (`create`, `delete`, `quick-test`, `generate-ai`): `trader`, `admin` only.
- **Safety & Error Behavior:** Overwrite prevention on duplicate strategy names; AI drafts are never saved automatically; quick-test does not alter database state.
- **Status:** `COMPLETE`.

---

### 3.7 News AI Signals (`view-news-ai` → `NewsAIPanel`)
- **Legacy Functionality:** Sentiment overview, automated news event feed, macroeconomic calendar, sentiment-driven trade catalysts.
- **Components & Layout:**
  - Sentiment KPIs: Aggregate market sentiment index (-1.0 to +1.0), 24h processed articles count, Bullish/Bearish ratio, High-impact macro events count.
  - News Feed Table: Timestamp, Source, Headline, Sentiment badge (Positive / Neutral / Negative), Impact badge. Search filter.
  - Macroeconomic Calendar Table: Date, Currency, Event, Impact level, Actual vs Forecast figures.
  - Sentiment Signals Table: Symbol, Direction, Sentiment score, Catalyst summary, Creation time.
- **Interactive Controls:**
  - `Refresh`: Re-queries `GET /api/news/signals` and `GET /api/news/events`.
- **API Contracts:**
  - `GET /api/news/signals`
  - `GET /api/news/events`
  - `GET /api/news/sentiment`
- **Roles & RBAC:** Read-only for all authenticated roles (`viewer`, `trader`, `admin`).
- **Safety & Error Behavior:** Purely informative; zero mutation endpoints; safe fallback states on empty feeds.
- **Status:** `COMPLETE`.

---

### 3.8 Research Bots (`view-research` → `ResearchPanel`)
- **Legacy Functionality:** RetailBot2 shadow and inverse trade monitor, bot lifecycle state, independent Telegram alert toggles, strategy statistics, lifecycle toggle settings, stop/hide bot.
- **Components & Layout:**
  - Bot Status Banner: Operational state, active traded pairs, execution mode (`Live` vs `Shadow`).
  - Performance Metrics: Overall win rate, total executed trades, cumulative return.
  - Active Positions Table: Trading pair, Direction, Entry Price, Mark Price, Unrealized PnL, Strategy identifier.
  - Closed Trades History: Timestamp, Pair, Direction, Realized PnL, Trade duration, Exit reason.
  - Strategy Lifecycle Settings Table: Interactive checkboxes for Evaluation, Paper Tracking, Telegram Alerts, and Report visibility. Includes row-level `Save` button (with busy state) and `Stop & Hide` action button. Checkboxes are disabled for `historical_only` strategies.
  - Stop & Hide Confirmation Dialog: Native HTML `<dialog>` requiring deliberate confirmation before disabling evaluation, paper, and alert flags and hiding strategy from performance reports.
- **Interactive Controls:**
  - `Save lifecycle`: Submits `POST /api/research/retailbot2/strategies` with updated boolean flags.
  - `Stop & Hide`: Confirms via `<dialog>` and submits `POST /api/research/retailbot2/strategies` setting all flags to `false`.
  - Refresh button.
  - Bot filter by symbol.
- **API Contracts:**
  - `GET /api/research/retailbot2/positions`
  - `GET /api/research/retailbot2/trades`
  - `GET /api/research/retailbot2/stats`
  - `GET /api/research/retailbot2/lifecycle`
  - `POST /api/research/retailbot2/strategies` (Payload: `{ strategy_id, evaluation_enabled, paper_tracking_enabled, telegram_alerts_enabled, report_enabled }`)
- **Roles & RBAC:**
  - Reads: Accessible to all authenticated roles (`viewer`, `trader`, `admin`).
  - Mutations: Restricted to `trader` and `admin`. Viewer sessions see disabled checkboxes, action buttons hidden, and receive HTTP 403.
- **Safety & Error Behavior:** Clear separation between reporting visibility, strategy execution, and alert delivery; historical-only strategies cannot be erroneously activated; stop action is non-destructive to historical trades.
- **Status:** `COMPLETE`.

---

### 3.9 Market Screener (`view-market-screener` → `ScreenerPanel`, `ScreenerControlsPanel`)
- **Legacy Functionality:** Real-time multi-pair quantitative screener signals (V2), filter controls, worker pause/resume, detection parameter configuration, Telegram alert gate.
- **Components & Layout:**
  - Signals Table: Symbol, Market (`spot`/`futures`), Strategy, Signal Direction (`LONG`/`SHORT`), Trigger Price, 24h Volume, RSI, MACD, Trend strength, Signal age.
  - Real-time Filters: Symbol search, Market selector, Signal direction filter.
  - Screener Controls Panel (`ScreenerControlsPanel`):
    - Enabled/Paused toggle.
    - Telegram alert gate toggle.
    - Detection thresholds: Minimum 24h volume ($), RSI oversold/overbought thresholds.
- **Interactive Controls:**
  - `Save changes`: Submits `POST /api/research/screener/control` with validation and readback.
  - Filter inputs.
- **API Contracts:**
  - `GET /api/research/screener/signals`
  - `GET /api/research/screener/control`
  - `POST /api/research/screener/control` (Payload: `{ enabled, telegram_alerts, min_volume_usdt, ... }`)
- **Roles & RBAC:**
  - Signals read: All authenticated roles.
  - Control editing and save: `trader`, `admin` only. Viewer sessions see disabled inputs and a read-only message.
- **Safety & Error Behavior:** Control saves are validated client-side and server-side; readback verification ensures persisted state matches UI.
- **Status:** `COMPLETE`.

---

### 3.10 Wallet Tracking (`view-wallet-tracking` → `WalletTrackingPanel`)
- **Legacy Functionality:** Smart money wallet monitoring, large token transfer feed, multi-chain address registry, whale activity alerts, track/untrack address mutations, wallet alert settings save.
- **Components & Layout:**
  - Overview Metrics: Tracked wallets count, Supported blockchains, 24h tracked volume, Large transfers count. Includes `Track wallet` and `Edit settings` action buttons.
  - Tracked Wallets Table: Address (formatted/truncated with copy helper), Blockchain network pill (`ETH`, `SOL`, `BSC`, `ARB`, `BASE`), Label, 24h net flow, Last active timestamp, `Untrack` action button. Search filter.
  - Large Transfers Feed: Blockchain, Token symbol, Amount, Estimated USD value, Sender -> Receiver addresses, Timestamp.
  - Multi-chain Filter: Filter by selected networks.
  - Accessible Dialog Modals:
    - Track Wallet Modal: Address validation, network selection from supported chains (`eth`, `solana`, `bsc`, `polygon`, `arbitrum`, `optimism`, `base`, `avalanche`), optional label.
    - Untrack Confirmation Dialog: Confirms removal of exact address and network pair.
    - Wallet Settings Modal: Scanner enabled toggle, monitored networks checkboxes, minimum trade size USD, minimum net flow USD, auto-track threshold USD (`auto_track_net_usd >= min_net_usd`), and poll interval seconds (10–3600).
- **Interactive Controls:**
  - `Track wallet`: Submits `POST /api/wallets/tracked`.
  - `Untrack wallet`: Submits `DELETE /api/wallets/tracked/{tracked_id}` after `<dialog>` confirmation.
  - `Save settings`: Submits `PUT /api/wallets/settings` with validation.
  - Network filter buttons.
  - Search input.
  - Refresh button.
- **API Contracts:**
  - `GET /api/wallet/tracked`
  - `GET /api/wallet/transfers`
  - `GET /api/wallet/summary`
  - `POST /api/wallets/tracked` (Payload: `{ address, network, label }`)
  - `DELETE /api/wallets/tracked/{tracked_id}`
  - `PUT /api/wallets/settings` (Payload: `{ scanner_enabled, monitored_networks, min_trade_usd, min_net_usd, auto_track_net_usd, poll_interval_seconds }`)
- **Roles & RBAC:**
  - Reads: Accessible to all authenticated roles (`viewer`, `trader`, `admin`).
  - Mutations (Track, Untrack, Settings): Restricted to `trader` and `admin`. Viewer sessions see action buttons hidden and receive HTTP 403.
- **Safety & Error Behavior:** Addresses are strictly validated against selected blockchain formats; network list matches backend supported chains; untrack preserves historical transfers; settings changes do not silently trigger external notifications.
- **Status:** `COMPLETE`.
- **Safety & Error Behavior:** Addresses and transactions are sanitized; zero private key or mutation risks.
- **Status:** `COMPLETE`.

---

### 3.11 Settings & Users (`view-settings` → `SettingsPanel`, `AuthGate`)
- **Legacy Functionality:** Workspace preferences (dark/light theme), user profile details, password change, user management (admin only), Telegram test alerts, system diagnostics, audit logs.
- **Components & Layout:**
  - Theme Selector: Instant toggle between Dark and Light mode, persisted in `localStorage`.
  - Session Profile: Username, Role badge, Session expiration indicator.
  - Self Password Change Form: Current password, new password, confirm new password with length and match validation (`POST /api/auth/change-password`).
  - User Accounts Administration Table (Admin only): Live catalog displaying User ID, username, email, role badge, active status, creation timestamp, and action buttons (`GET /api/users`).
  - Create User Form / Modal (Admin only): Interactive form with fields for username, email, password, and role selector (`trader`, `admin`, `viewer`) (`POST /api/users`).
  - Password Reset Action (Admin only): Modal confirmation form accepting a new password (min 10 chars) submitted via `POST /api/users/{id}/reset-password`. The backend updates the password hash, increments `session_version` (revoking existing sessions), and sets `must_change_password = 1` requiring the user to change their password upon their next login.
  - Toggle Active Status (Admin only): Instant enable/disable for accounts with self-disable and last-admin protections preventing administrator lockout (`PUT /api/users/{id}`).
  - Delete User Action (Admin only): Two-step confirmation dialog with self-deletion and last-admin protections preventing an admin from deleting their own session or the system's last administrator (`DELETE /api/users/{id}`).
  - Telegram Status & Test Alert: Telegram card displays configuration state. `GET /api/telegram/status` is accessible to all authenticated roles (admin receives `chatId`; trader/viewer receive `200 OK` with `chatId: null`). `POST /api/telegram/test` ("Send Test Ping") is strictly restricted to the `admin` role by explicit backend `require_admin` dependency and middleware enforcement, disallowing test message broadcasts from non-admins.
  - System Diagnostics Card (Admin only): CPU usage, Memory allocation, DB latency, MariaDB thread pool, version details. Sanitized against leaking environment variables or credentials.
  - Forced Password Change Modal (`AuthGate`): Appears immediately upon sign-in if `must_change_password` is `true`. Blocks dashboard navigation until updated.
- **Interactive Controls:**
  - `Theme Toggle`: Updates UI theme and `data-theme` attribute.
  - `Change Password`: Calls `POST /api/auth/change-password`.
  - `Create User`: Submits `POST /api/users`.
  - `Reset Password`: Submits `POST /api/users/{id}/reset-password`.
  - `Toggle Status`: Submits `PUT /api/users/{id}` (`is_active: boolean`).
  - `Delete User`: Submits `DELETE /api/users/{id}`.
  - `Send Test Ping`: Submits `POST /api/telegram/test` (Admin only).
  - `Sign Out`: Clears token and session storage, dispatches logout.
- **API Contracts:**
  - `POST /api/auth/login`
  - `GET /api/auth/me`
  - `POST /api/auth/logout`
  - `POST /api/auth/change-password`
  - `GET /api/users` (Admin only)
  - `POST /api/users` (Admin only)
  - `POST /api/users/{id}/reset-password` (Admin only)
  - `PUT /api/users/{id}` (Admin only)
  - `DELETE /api/users/{id}` (Admin only)
  - `GET /api/telegram/status` (Authenticated: admin receives `chatId`, trader/viewer receive `chatId: null`)
  - `POST /api/telegram/test` (Admin only)
  - `GET /api/settings/diagnostics` (Admin only)
- **Roles & RBAC:**
  - Viewer: Theme toggle, profile info, own password change. (Telegram status returns masked `chatId: null`; no access to user management, system diagnostics, or Telegram test ping).
  - Trader: Theme toggle, profile info, own password change. (Telegram status returns masked `chatId: null`; no access to user management, system diagnostics, or Telegram test ping).
  - Admin: Full access including user administration (create, reset password, toggle active, delete), Telegram test ping, and system diagnostics.
- **Safety & Error Behavior:** Strict backend authentication enforcement; token auto-revocation on 401; password fields masked with input type `password`; server-side guards against self-deletion, self-deactivation, self-demotion, and last-active-admin lockout.
- **Status:** `COMPLETE`.

---

## 4. Modal Dialogs & Specialized Workflows Parity

| # | Legacy Modal / Dialog ID | Modern UI Implementation | Status | Safety & Error Behavior |
|---|---|---|---|---|
| 1 | `login-modal` | `AuthGate.tsx` | `COMPLETE` | Role-aware, JWT storage, rate-limit error display, no token leakage |
| 2 | `force-password-modal` | `AuthGate.tsx` | `COMPLETE` | Blocks application frame until password updated; validates match and length |
| 3 | `backfill-modal` | `MasterDataPanel.tsx` | `COMPLETE` | Parameter validation, date range bounds, browser confirmation before submission |
| 4 | `deploy-modal` | `PaperTradingPanel.tsx` | `COMPLETE` | Simulation-only configuration form modal with min $100 capital and 0.1-10% risk validation |
| 5 | `cancel-order-modal` | `PaperTradingPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` confirmation with order details, keyboard trap, and safe cancellation |
| 6 | `close-position-modal` | `PaperTradingPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` market exit confirmation with mark price and simulation notice |
| 7 | `circuit-breaker-modal` | `PaperTradingPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` confirmation for emergency halt and reset operations |
| 8 | `modal-run-ml` | `MLStudioPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` tournament launch modal with model and target type selection |
| 9 | `modal-deploy-ml` | `MLStudioPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` deploy modal gated strictly to verified eligible models |
| 10 | Strategy Quick Test Modal | `StrategyLibraryPanel.tsx` | `COMPLETE` | In-memory 1,000 candle simulation; no database writes |
| 11 | Strategy Delete Confirmation | `StrategyLibraryPanel.tsx` | `COMPLETE` | Explicit name verification required before deletion |
| 12 | Research Stop & Hide Modal | `ResearchPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` confirmation disabling bot execution and hiding from reports |
| 13 | Wallet Track Modal | `WalletTrackingPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` modal with format validation across backend-supported networks |
| 14 | Wallet Untrack Confirmation | `WalletTrackingPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` confirmation identifying exact address/network pair |
| 15 | Wallet Settings Modal | `WalletTrackingPanel.tsx` | `COMPLETE` | Native HTML `<dialog>` form validating scanner state, networks, and net flow thresholds |
| 16 | User Delete Confirmation | `SettingsPanel.tsx` | `COMPLETE` | Accessible native HTML `<dialog>` modal; initial cancel focus, keyboard trap, Escape dismissal, focus restoration to trigger, prevents self-deletion |
| 17 | Password Reset Confirmation | `SettingsPanel.tsx` | `COMPLETE` | Accessible native HTML `<dialog>` modal prompting administrator for new password (min 10 chars); input focus, keyboard trap, Escape dismissal, and focus restoration |
| 18 | Telegram Test Ping Confirmation | `SettingsPanel.tsx` | `COMPLETE` | Accessible native HTML `<dialog>` modal confirming outbound broadcast; cancel focus, Escape dismissal without sending ping, and focus restoration |

---

## 5. Verification & Testing Evidence

All parity implementations have been validated through four comprehensive automated test layers:

1. **Frontend Unit & Component Testing (`Vitest`):**
   - **Command:** `cd frontend && npm test -- --run`
   - **Tested Commit:** `1db735aa66764536bbff07b69209c9b0de626a52`
   - **Result:** 31 test files passed, 276 tests passed, 0 failed in 8.57s.
   - **Coverage:** AuthGate, App, SystemStatus, Screener, ScreenerControls, PaperTrading, StrategyLibrary, WalletTracking, BacktestStudio, PromotionPolicy, MLStudio, MLGeneratorTraining, Research, NewsAI, MasterData, Settings, User Management client & UI, dialog focus traps, partial deployment feedback warnings, polling interval range validation, and mutation client contracts.

2. **Frontend Production Type-check & Build (`TypeScript + Vite`):**
   - **Command:** `cd frontend && npm run typecheck && npm run build`
   - **Tested Commit:** `1db735aa66764536bbff07b69209c9b0de626a52`
   - **Result:** TypeScript compile passed with zero errors (`tsc --noEmit`). Production bundle built in `frontend/dist/` (1,610 modules transformed in 4.39s).

3. **End-to-End Browser Testing (`Playwright 1.64.1 Chromium`):**
   - **Command:** `cd frontend && npx playwright test`
   - **Tested Commit:** `5c30ee4b5e579114bcf0e6fd1fe06d46b3ac0a80`
   - **Result:** 28 passed across all 11 views, mobile viewport (375px), tablet (768px), desktop (1440px), and dark/light themes in 28.8s.
   - **Execution Boundary:** All 28 Playwright tests use deterministic client-side API route mocking via `page.route("**/api/**")`. They validate browser rendering, user interaction, accessibility, and client-side error states without executing live backend code or touching database records.

4. **Backend Regression Testing (`pytest`):**
   - **Command:** `PYTHONPATH=$PWD python3 -m pytest -q`
   - **Tested Commit:** `1db735aa66764536bbff07b69209c9b0de626a52`
   - **Result:** 1,119 passed, 18 skipped, 6 warnings, 0 failed in 25.46s.

5. **MariaDB Concurrency & Security Testing:**
   - **Command:** `python3 -m unittest -v tests/test_user_management_mariadb_concurrency.py`
   - **Tested Commit:** `1db735aa66764536bbff07b69209c9b0de626a52`
   - **Result:** 12 passed in 4.74s across consecutive runs against authorized `signal_bot_test`.

6. **Tracked Staging Browser Acceptance:**
   - **Command:** `node tests/browser/staging_browser_acceptance.cjs`
   - **Tested Commit:** `1db735aa66764536bbff07b69209c9b0de626a52`
   - **Result:** 11 views verified across 3 roles (`admin`, `trader`, `viewer`), child process identity verified, controlled test mutations in `signal_bot_test` (zero production operations), 0 console errors, 0 network errors.

---

## 6. Audit Conclusion & Reconciliation

The modern React application implements **59 complete workflows across 11 complete views** (100% workflow and view parity). All ten previously legacy-only execution mutations (PT-07 to PT-10, ML-03 to ML-04, RB-03 to RB-04, WT-04 to WT-05) have been fully migrated into accessible React components, backed by typed client mutation libraries, strict RBAC enforcement, accessible native `<dialog>` confirmations, keyboard focus trapping, trigger focus restoration, and server-side state readback.

The system continues to operate in preview mode at `/modern` alongside the authoritative default legacy dashboard at `/`, strictly preserving `SIGNAL_BOT_USE_MODERN_UI=0`. Production cutover remains an explicitly approved, owner-led operational action.
