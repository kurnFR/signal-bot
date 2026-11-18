# Work Order 71938: Final Diff Review & Full Regression Gate Report

**Date:** 2026-11-18  
**Repository:** `kurnFR/signal-bot`  
**Branch:** `feat/binance-futures-funding-rate-rest-collector`  
**Base Commit / Starting HEAD:** `36164e3f3d500adada00a352dc4607272cfd37b6`  
**Parent Commit:** `6c095570eabb964db0bffe5d8a374a22aefefda6`  
**Verdict:** **`WO71938_FINAL_REVIEW_PASS`**

---

## 1. Executive Summary

This independent review and verification gate evaluated all changes introduced by **Work Order 71937** against commit `36164e3f3d500adada00a352dc4607272cfd37b6`.

The inspection covered:
1. Complete source and AST-level diff review across all 8 modified files and 2 newly created test suites.
2. Full repository pytest execution across all 1,606 test cases.
3. Live process and runtime ownership inspection of systemd services and collector processes.
4. Validation of the architectural boundary for synthetic `pairs_ratio_v1` two-leg funding limitation.
5. Worktree and repository preservation audit (confirming zero regressions, zero destructive Git operations, all 21 modified screenshot PNGs preserved byte-for-byte, and `.env`/`.app_secret` intact).

All criteria have been met with zero failures, zero errors, zero resource leaks, and zero regressions.

---

## 2. Source and Diff Review

### 2.1 HTTP 418 & HTTP 429 Ban Defense (`collectors/funding_poller.py`)
- **HTTP 418 Classification**: HTTP 418 is explicitly classified as an IP-ban response rather than entering generic non-retryable 4xx handlers.
- **Parsing of `Retry-After` (`parse_retry_after`)**:
  - Implements two-phase parsing: (1) numeric float/int seconds, (2) RFC 2822 / RFC 7231 standard HTTP-date formats via `email.utils.parsedate_to_datetime`.
  - Rejects negative, NaN, infinite, or empty strings safely by returning `None`.
- **Duration Enforcement**:
  - Minimum delay for HTTP 418 is pinned to `HTTP_418_MIN_DELAY_SECONDS = 120.0`.
  - Longer exchange-provided durations (e.g., multi-hour or multi-day bans) are strictly preserved (`max(120.0, parsed_ra)`).
  - Positive random jitter is added (`random.uniform(0.1, 1.0)` or custom injector) to prevent thundering herd without reducing the exchange-required wait.
- **Process-Level Shared Cooldown**:
  - Protected by thread-safe `_BAN_COOLDOWN_LOCK` storing `_BAN_COOLDOWN_UNTIL`.
  - Top-of-function check `get_ban_cooldown_remaining()` blocks subsequent calls for different symbols or future poll cycles from bypassing an active ban window.
- **Retry Exhaustion Semantics**:
  - Final retry attempt sets `set_ban_cooldown(wait)` before exiting the loop.
  - The final attempt avoids unnecessary `time.sleep()`, recording the window for subsequent cycles to enforce.
  - Cooldown is never cleared upon retry exhaustion.
- **Security & Logging**:
  - High-severity log emitted for HTTP 418 (`HIGH-SEVERITY WARNING: Binance HTTP 418 IP ban detected for {symbol}!`).
  - No database passwords, tokens, API keys, or request headers are logged.

### 2.2 MariaDB Advisory Lock & Single Ownership (`collectors/funding_poller.py`, `run_collectors.py`)
- **MariaDB Advisory Lock Implementation (`FundingPollerLock`)**:
  - Lock name: `signal_bot_funding_poller`.
  - Non-blocking acquisition via `SELECT GET_LOCK('signal_bot_funding_poller', 0)`.
  - Uses a **dedicated unpooled database connection** instantiated via `load_funding_collector_config()`. It is never acquired from or returned to the shared connection pool.
  - Uses `try ... finally` blocks to ensure `SELECT RELEASE_LOCK(...)` is executed and the underlying dedicated TCP connection is explicitly closed.
  - Reentrant/context-manager support (`__enter__` and `__exit__`) ensures deterministic cleanup under normal termination or unhandled exceptions.
  - Handles lock contention by cleanly returning `{"skipped": True, "reason": "advisory_lock_held"}` without modifying database records.
- **Collector Orchestration (`run_collectors.py`)**:
  - `funding_poller_run` import and scheduling removed from `run_collectors.py`.
  - `ws_kline_main` and `oi_poller_run` remain active and properly scheduled when `ENABLE_FUTURES` is enabled.

### 2.3 Historical Funding Accrual in Backtests (`db/db.py`, `backtest/simulate.py`)
- **Single Bounded Database Query (`fetch_settled_funding_events`)**:
  - Executes one query per backtest simulation window:
    ```sql
    SELECT event_time, mark_price, funding_rate
    FROM funding_rate
    WHERE symbol = %s AND event_time > %s AND event_time <= %s AND mark_price IS NOT NULL
    ORDER BY event_time ASC
    ```
  - Rejects non-finite funding data (NaN, infinity) or non-positive mark prices (`mp <= 0`) with `ValueError`.
- **Interval Boundary & Accounting Parity (`backtest/simulate.py`)**:
  - Event holding interval strictly follows paper trading convention: `entry_time < event_time <= exit_time`.
  - An event occurring exactly at `entry_time` is excluded.
  - An event occurring exactly at `exit_time` is included.
  - Signed funding cost parity:
    - Positive funding: LONG pays ($>0$), SHORT receives ($<0$).
    - Negative funding: LONG receives ($<0$), SHORT pays ($>0$).
  - Integrated into `calculate_trade_accounting(..., funding_cost=funding_cost)` where `net_pnl = gross_pnl - fees - funding_cost`.
  - Spot trades remain strictly untouched (`funding_cost = 0.0`, `funding_status = "not_applicable"`).
  - Missing historical funding data triggers a clear warning per symbol/window and records `"funding_status": "missing_data_zero_cost_fallback"`.

### 2.4 Integration Test Database Protection (`tests/test_paper_position_lifecycle.py`, `tests/test_user_management_mariadb_concurrency.py`)
- **Database Identity Guard**:
  - `setUpClass` independently inspects `SELECT DATABASE()`.
  - If the database is not `signal_bot_test`, raises `AssertionError` immediately. It is impossible for tests to mutate `crypto_signals`.
- **Unreachable Database Conversion**:
  - Connection failure to `signal_bot_test` is converted into a structured `unittest.SkipTest`, providing diagnostic guidance to provision the test database.
- **Schema Presence Validation**:
  - Checks `information_schema.tables` for required tables (`paper_trades`, `paper_positions`, `users`, etc.).
  - Missing schema raises `RuntimeError` rather than being masked as a skip.
- **Pool Cleanliness**:
  - `tearDownClass` restores `MYSQL_CONFIG["database"] = "crypto_signals"` and resets singleton pool instances to avoid poisoning subsequent tests.

---

## 3. Full Repository Test Suite Analysis

The complete pytest suite was executed across the entire repository with the following outcome:

```
================ 1581 passed, 25 skipped, 7 warnings in 44.82s =================
```

### 3.1 Test Count Breakdown
- **Total Test Cases Collected**: 1,606
- **Passed**: 1,581
- **Skipped**: 25
- **Failed**: 0
- **Errors**: 0
- **Warnings**: 7 (all pre-existing Pydantic V2 deprecation notices or unawaited mock coroutine)

### 3.2 Audit of All 25 Skipped Tests
Every skipped test was individually inspected to verify that no tests were skipped to mask defects:

| Test File | Count | Skip Reason | Verification Status |
| :--- | :---: | :--- | :--- |
| `tests/test_durable_state.py` | 7 | `opt-in MariaDB integration test` | Pre-existing requirement for explicit `MARIADB_INTEGRATION=1` |
| `tests/test_migrate.py` | 3 | `opt-in MariaDB integration test` | Pre-existing requirement for explicit `MARIADB_INTEGRATION=1` |
| `tests/test_paper_orders_mariadb.py` | 4 | `requires disposable MariaDB test database` | Pre-existing requirement for disposable test database flag |
| `tests/test_retailbot2_outbox_delivery_mysql.py` | 6 | `opt-in isolated signal_bot_test integration test` | Pre-existing opt-in flag requirement |
| `tests/test_retailbot2_worker_outbox_mysql.py` | 1 | `opt-in only; set SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION=1` | Pre-existing opt-in flag requirement |
| `tests/test_wallet_tracker_mariadb.py` | 4 | `requires disposable MariaDB test database` | Pre-existing requirement for disposable test database flag |

**Result**: Zero newly introduced skips. Zero skips in `test_funding_poller.py`, `test_run_collectors_deprecation.py`, `test_funding_backtest_accrual.py`, `test_integration_setup_guards.py`, `test_paper_position_lifecycle.py`, or `test_user_management_mariadb_concurrency.py`.

---

## 4. Funding Poller Runtime Ownership Inspection

Read-only inspection of the production host confirmed:

### 4.1 Systemd Dedicated Service
- **Service Name**: `signal-funding-collector.service`
- **State**: `active (running)` since Mon 2026-11-16 15:41:03 WIB (PID 379118)
- **Configuration**: Uses dedicated environment file `/etc/signal-bot/funding-collector.env` with least-privilege credentials; trading and notifications explicitly disabled.
- **Entry Point**: `python3 -m collectors.funding_poller`

### 4.2 Collector Process Audit
Inspection of running processes via `ps aux | grep -E "funding|run_collectors"`:
- **PID 379118**: Dedicated poller `/home/BIS/.pyenv/versions/3.12.14/bin/python3 -m collectors.funding_poller` (owned by `signal-funding-collector.service`).
- **PID 353300**: `run_collectors.py` running in the background. Because `funding_poller_run` was removed from `run_collectors.py`, this process does not own or spawn a competing poller.
- **Active Ownership**: Exactly **one** production funding poller owner exists.

### 4.3 Database Heartbeat & Ingestion Integrity
- Read-only inspection of table `funding_rate` in production database `crypto_signals`:
  - Total rows: **926** (unchanged, zero unverified mutations).
  - Maximum event time: `1794960000000`.
  - Service journal logs confirm regular 5-minute polling heartbeat: `Poll cycle completed: 3/3 symbols ok, +0 rows`.

---

## 5. Pairs Ratio Funding Limitation Boundary

Inspection of `backtest/strategies/pairs_ratio.py` and `backtest/simulate.py`:
- `pairs_ratio_v1` trades a synthetic ratio without underlying executable two-leg sizing definitions (no separate contract quantity or mark price per leg).
- Applying single-leg funding formulas to synthetic ratio units is financially invalid.
- Per Work Order 71937 / 71938 requirements, `pairs_ratio_v1` trades are explicitly flagged:
  ```python
  "funding_cost": 0.0,
  "funding_status": "unsupported_pairs_ratio_two_leg"
  ```
- Ordinary single-symbol Futures strategies calculate authentic settled funding with full sign parity.
- This boundary is covered and verified in [tests/test_funding_backtest_accrual.py](file:///home/BIS/signal-bot/tests/test_funding_backtest_accrual.py#L253-L265).

---

## 6. Git and Worktree Preservation Audit

### 6.1 Repository State
- **Current Branch**: `feat/binance-futures-funding-rate-rest-collector`
- **HEAD Commit**: `36164e3f3d500adada00a352dc4607272cfd37b6`
- **Diffstat vs HEAD**:
  ```
   backtest/simulate.py                              | 118 +++++++++-
   collectors/funding_poller.py                      | 269 ++++++++++++++++++++--
   db/db.py                                          |  75 +++++-
   run_collectors.py                                 |  13 +-
   tests/test_funding_poller.py                      | 230 ++++++++++++++++++
   tests/test_paper_position_lifecycle.py            |  66 +++++-
   tests/test_run_collectors_deprecation.py          |  90 +++++++-
   tests/test_user_management_mariadb_concurrency.py |  53 ++++-
   8 files changed, 863 insertions(+), 51 deletions(-)
  ```

### 6.2 File Preservation Checklist
- [x] **Screenshots**: All 21 modified `docs/screenshots/*.png` files match `HEAD` byte-for-byte (`git diff --quiet HEAD -- docs/screenshots/` returned 0).
- [x] **Protected Files**:
  - `.env` SHA-256: `ab193ecafada4dad795542fd8513c65960d76a8814d63e3e1fb3bfbbe2dedf22` (intact).
  - `.app_secret` SHA-256: `6de5240465b191e963140ee26454c74ff719d6521a62ffc93cd05e0f244b76af` (intact).
- [x] **Untracked Directory `{collectors,db}`**: Preserved intact containing `__pycache__/config.cpython-38.pyc`.
- [x] **New Files Created**:
  - `tests/test_funding_backtest_accrual.py`
  - `tests/test_integration_setup_guards.py`
  - `work_order_71937_production_readiness_remediation.md`
  - `work_order_71938_final_diff_and_regression_review.md`

---

## 7. Final Verdict

**`WO71938_FINAL_REVIEW_PASS`**

All audit findings have been remediated cleanly, verified against the complete repository test suite, and confirmed non-disruptive to the active production service. No commit or push has been performed.
