# Work Order 71937: Production-Readiness Remediation — Commit 36164e3

## Executive Summary

This deliverable documents the complete remediation of all five engineering findings identified in the audit of commit `36164e3f3d500adada00a352dc4607272cfd37b6` on feature branch `feat/binance-futures-funding-rate-rest-collector`.

All remediations were implemented directly on the feature branch without rebuild, architectural replacement, or destructive Git operations. Production database data and running systemd services remained untouched and unmutated throughout.

---

## 1. Starting State and Branch Alignment

- **Repository**: `kurnFR/signal-bot`
- **Active Branch**: `feat/binance-futures-funding-rate-rest-collector`
- **Reviewed Starting Commit**: `36164e3f3d500adada00a352dc4607272cfd37b6`
- **Production Parent Commit**: `6c095570eabb964db0bffe5d8a374a22aefefda6`
- **Starting Git Log**:
  ```
  36164e3 feat(collectors,paper,strategies): binance futures funding rest collector, paper engine lifecycle guards, and pairs_ratio OHLC gap defense
  6c09557 Merge pull request #724 from kurnFR/fix/screener-telegram-unauthorized-test
  36a2932 test: isolate screener dispatcher error path from Telegram auth
  19f2b96 Merge pull request #723 from kurnFR/fix/telegram-delivery-authorization-boundary
  bd336fe test(telegram): cover malformed and invalid status no-retry cases
  ```
- **Local vs Remote Alignment**: Local branch HEAD and `origin/feat/binance-futures-funding-rate-rest-collector` are identical (`36164e3`).
- **Preservation Check**: All 21 modified `docs/screenshots/*.png` files and existing untracked/ignored configurations (`.env`, `.app_secret`, logs, custom strategies) are preserved intact.

---

## 2. Exact Files Modified

| File | Change Type | Purpose |
| :--- | :--- | :--- |
| `collectors/funding_poller.py` | Modified | Added HTTP 418/429 ban defense, process-level cooldown, positive jitter, `FundingPollerLock` MariaDB advisory lock guard |
| `run_collectors.py` | Modified | Eliminated dual funding poller ownership; removed inline funding poller scheduling while preserving Kline & Open Interest collectors |
| `db/db.py` | Modified | Added `fetch_settled_funding_events` single bounded query with data validation; dynamic pool configuration binding |
| `backtest/simulate.py` | Modified | Historical funding accrual for backtesting using authentic settled events, paper trading sign conventions, and explicit Pairs Ratio boundary |
| `tests/test_funding_poller.py` | Modified | Added 8 required tests covering HTTP 418/429 ban protection, Retry-After parsing (numeric & HTTP-date), shared cooldown, and retry exhaustion |
| `tests/test_run_collectors_deprecation.py` | Modified | Added dual-ownership elimination verification, Kline/OI collector preservation, and concurrent advisory lock acquisition/release tests |
| `tests/test_paper_position_lifecycle.py` | Modified | Safe test runner setup: converts unreachable database to `unittest.SkipTest`; immediately fails on database identity mismatch or missing tables |
| `tests/test_user_management_mariadb_concurrency.py` | Modified | Safe test runner setup: converts unreachable database to `unittest.SkipTest`; immediately fails on database identity mismatch or missing tables |
| `tests/test_funding_backtest_accrual.py` | Added (Untracked) | 13 comprehensive tests for backtest funding accrual, paper parity, sign conventions, interval boundaries, and missing-data fallbacks |
| `tests/test_integration_setup_guards.py` | Added (Untracked) | 6 dedicated unit tests verifying SkipTest on unreachable DB, immediate failure on identity mismatch, and immediate failure on missing schema |

---

## 3. P0 — Binance HTTP 418 and 429 Ban Protection

### Implementation (`collectors/funding_poller.py`)
1. **HTTP 418 IP Ban Classification**:
   - HTTP 418 is explicitly handled before `resp.raise_for_status()`, ensuring it never enters the generic non-retryable 4xx error path.
   - Minimum delay: 120 seconds (`HTTP_418_MIN_DELAY_SECONDS = 120.0`).
   - Exchange-provided ban duration is parsed from `Retry-After` (supporting both numeric seconds and RFC 2822/7231 HTTP-date format). If longer than 120s (including multi-hour or multi-day bans), the longer duration is preserved.
   - Small positive jitter (`random.uniform(0.1, 1.0)`) is added strictly on top of the exchange-required wait (`wait = ban_duration + jitter`).
   - High-severity error log emitted: `HIGH-SEVERITY WARNING: Binance HTTP 418 IP ban detected for {symbol}! Enforcing mandatory ban cooldown...`.
2. **HTTP 429 Rate Limit Handling**:
   - Retains existing Retry-After behavior with numeric and HTTP-date header parsing.
   - Minimum wait: 1.0s, default 5.0s.
3. **Shared Process-Level Cooldown**:
   - Thread-safe process-level cooldown recorded in `_BAN_COOLDOWN_UNTIL`.
   - Checked at entry to `fetch_funding_page`: any subsequent poll cycle or request for another symbol (e.g. `ETHUSDT`) cannot bypass the active ban window and must enforce the remaining cooldown.
4. **Retry Exhaustion Invariant**:
   - Retry exhaustion does not clear `_BAN_COOLDOWN_UNTIL`.
   - Avoids unnecessary sleep after final exhausted attempt; records cooldown and raises `RuntimeError`, allowing subsequent calls/cycles to enforce it.
5. **Credential & Sensitive Data Redaction**:
   - No request headers, authorization tokens, or query secrets are ever logged.

### Test Verification (`tests/test_funding_poller.py`)
- `test_parse_retry_after_numeric_and_http_date`: PASSED
- `test_http_418_with_numeric_retry_after`: PASSED
- `test_http_418_with_http_date_retry_after`: PASSED
- `test_http_418_without_retry_after_uses_at_least_120_seconds`: PASSED
- `test_http_418_with_retry_after_greater_than_120s_preserves_longer_duration`: PASSED
- `test_http_429_retains_existing_retry_after_behavior`: PASSED
- `test_another_symbol_cannot_bypass_shared_cooldown`: PASSED
- `test_retry_exhaustion_does_not_permit_early_request_in_next_poll_cycle`: PASSED
- `test_normal_successful_polling_remains_unchanged`: PASSED

---

## 4. P0 — Eliminate Dual Funding Poller Ownership

### Ownership Decision
- The standalone service `signal-funding-collector.service` is the default and exclusive owner of the Binance Futures funding rate collector.
- In `run_collectors.py`:
  - Removed `from collectors.funding_poller import run as funding_poller_run`.
  - Removed unconditional scheduling of `funding_poller_run()` in `main()`.
  - Preserved WebSocket Kline (`ws_kline_main()`) and Open Interest (`oi_poller_run()`) collectors without disruption.

### Defense-in-Depth Advisory Lock
- Implemented `FundingPollerLock` in `collectors/funding_poller.py`.
- **Lock Identifier**: `signal_bot_funding_poller`
- **Acquisition**: Non-blocking `SELECT GET_LOCK('signal_bot_funding_poller', 0)`.
- **Connection Isolation**: Uses a dedicated unpooled connection (`mysql.connector.connect(**cfg.to_dict())`) instantiated with least-privilege funding collector credentials loaded via `load_funding_collector_config()`. The connection is never returned to any shared pool.
- **Guaranteed Cleanup**: Lock is released via `SELECT RELEASE_LOCK('signal_bot_funding_poller')` and connection is explicitly closed in `finally` / `__exit__`.
- **Safe Skip**: If another process owns the lock, the cycle logs a clean warning and skips without modifying database records or calling external APIs.
- **Entry Points Protected**: Standalone synchronous loop (`run_standalone_loop`), asynchronous runner (`run`), and CLI one-shot execution (`main --once`).

### Test Verification (`tests/test_run_collectors_deprecation.py`)
- `test_run_collectors_does_not_import_funding_poller`: PASSED
- `test_run_collectors_main_futures_enabled_schedules_intended_tasks`: PASSED (schedules exactly 2 tasks: kline + open-interest; 0 funding or liquidation tasks)
- `test_run_collectors_main_futures_disabled_schedules_only_spot`: PASSED
- `test_two_simultaneous_funding_pollers_cannot_poll_concurrently`: PASSED
- `test_funding_poller_lock_release_after_success_and_exceptions`: PASSED
- `test_competing_funding_poller_skips_without_modifying_data`: PASSED

---

## 5. P1 — Historical Funding Accrual in Backtests

### Architecture and Financial Parity
1. **Database Event Retrieval (`db/db.py`)**:
   - `fetch_settled_funding_events(symbol, start_time, end_time, pool=None)` executes exactly **one bounded query** for the entire simulation window:
     ```sql
     SELECT event_time, mark_price, funding_rate
     FROM funding_rate
     WHERE symbol = %s
       AND event_time > %s
       AND event_time <= %s
       AND mark_price IS NOT NULL
     ORDER BY event_time ASC
     ```
   - Validates that `mark_price` is positive and `funding_rate` is finite. Rejects non-finite (NaN/inf) data.
2. **Simulation Integration (`backtest/simulate.py`)**:
   - Reuses canonical accounting primitive `calculate_funding_cost(quantity, funding_events)`.
   - Exact interval boundary matching paper trading:
     $$\text{entry\_time} < \text{event\_time} \le \text{exit\_time}$$
   - Sign convention matching paper engine:
     - LONG position: pays positive funding (`funding_cost = +cost`), receives negative funding (`funding_cost = -receipt`).
     - SHORT position: receives positive funding (`funding_cost = -receipt`), pays negative funding (`funding_cost = +cost`).
   - Integrated directly into `calculate_trade_accounting(..., funding_cost=funding_cost)`:
     $$\text{net\_pnl} = \text{gross\_pnl} - \text{fees} - \text{funding\_cost}$$
   - Backtest quantity convention (`quantity = 1.0`) preserved.
   - Spot strategies remain completely unaffected (`market == "spot"`: `funding_cost = 0.0`, `funding_status = "not_applicable"`, zero DB queries).
   - Missing funding data logs exactly one clear warning per symbol/window, defaults cleanly to zero-cost, and records `funding_status = "missing_data_zero_cost_fallback"` in trade diagnostics.

### Pairs Ratio Architectural Boundary
- Strategy inspection: `pairs_ratio_v1` trades a synthetic ratio of two symbols as a single instrument.
- Finding: There is currently **no executable two-leg sizing model** (quantities or notional dollar/beta allocation across individual legs).
- Architectural Decision: Applying arbitrary single-leg funding to a synthetic ratio would be mathematically and financially unsound. As required by Work Order 71937, this is documented as an **explicit unsupported accounting case**:
  - `funding_cost = 0.0`
  - `funding_status = "unsupported_pairs_ratio_two_leg"`
  - Documented for separate architectural approval prior to claiming complete Futures parity.

### Test Verification (`tests/test_funding_backtest_accrual.py`)
- 13/13 tests PASSED covering all single-symbol and pairs-ratio conditions.

---

## 6. P2 — Resolve Integration Test Setup Errors Safely

### Root Cause Analysis of Starting 40 Errors
In commit `36164e3`, running the full pytest suite produced 40 setup errors (28 in `test_paper_position_lifecycle.py` and 12 in `test_user_management_mariadb_concurrency.py`).
- Root cause: Preceding test suites imported `config` with default `.env` (`crypto_signals`). When `setUpClass` in those suites ran `db._pool = None` and `db.get_pool()`, `db.db` retained module-level references to `MYSQL_CONFIG["database"] = "crypto_signals"`. The suites' safety assertions properly detected that the connection targeted `crypto_signals` instead of `signal_bot_test` and failed fast as designed, producing 40 test errors.
- Additionally, if `signal_bot_test` was unreachable, tests raised unhandled connection exceptions instead of clean skips.

### Remediation
1. Explicitly point all pool dictionary references in `config` and `db.db` to `signal_bot_test` in `setUpClass`.
2. **Unreachable Database Behavior**: If connection to `signal_bot_test` fails (e.g. MariaDB down or credentials unconfigured), raises `unittest.SkipTest("Isolated test database 'signal_bot_test' is unavailable or unreachable... Please provision and configure signal_bot_test before running integration tests.")`.
3. **Database Identity Mismatch Behavior**: If a connection succeeds but `SELECT DATABASE()` does not return `'signal_bot_test'`, raises `AssertionError` immediately. **Never skips on identity mismatch.**
4. **Missing Schema Behavior**: If `signal_bot_test` is connected but required tables (e.g. `paper_trades`, `users`, `audit_logs`) are missing, raises `RuntimeError` immediately. **Never hides schema defects behind skips.**
5. In `tearDownClass`: Reconnects pools back to baseline configuration and drains connections cleanly.

### Test Verification
- `tests/test_paper_position_lifecycle.py`: 28 passed, 0 skipped, 0 errors
- `tests/test_user_management_mariadb_concurrency.py`: 12 passed, 0 skipped, 0 errors
- `tests/test_integration_setup_guards.py`: 6 passed, 0 skipped, 0 errors

---

## 7. P3 — Empty Brace-Expansion Directory

- **Target Path**: `{collectors,db}`
- **Inspection Findings**:
  ```
  {collectors,db}:
  drwxrwxr-x  3 BIS BIS 4096 Sep  4 13:25 .
  drwxrwxr-x 24 BIS BIS 4096 Nov 18 14:06 ..
  drwxrwxr-x  2 BIS BIS 4096 Sep  4 13:25 __pycache__

  {collectors,db}/__pycache__:
  -rw-rw-r-- 1 BIS BIS 3171 Sep  4 13:25 config.cpython-38.pyc
  ```
- **Cleanup Evaluation**: Attempting `rmdir "{collectors,db}"` yields `rmdir: failed to remove '{collectors,db}': Directory not empty`.
- **Policy Compliance**: Per Section 5 of the Work Order ("If it contains files or other user-created content, preserve it and report the contents. Do not run broad cleanup."), directory `{collectors,db}` and its `.pyc` contents were preserved untouched.

---

## 8. Test Execution and Verification Evidence

### Focused Suite Breakdown

| Test Suite Category | Test Files | Passed | Skipped | Failed | Errors |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Funding Poller Suite** | `tests/test_funding_poller.py` | 48 | 0 | 0 | 0 |
| **Collector Orchestration** | `tests/test_run_collectors_deprecation.py`, `tests/test_futures_recovery_poller_integration.py`, `tests/test_futures_kline_poller.py` | 38 | 0 | 0 | 0 |
| **Funding Accounting & Parity** | `tests/test_accounting.py`, `tests/test_funding_backtest_accrual.py`, `tests/test_paper_backtest_parity.py` | 28 | 0 | 0 | 0 |
| **Paper Position Lifecycle** | `tests/test_paper_position_lifecycle.py` | 28 | 0 | 0 | 0 |
| **User Management Concurrency** | `tests/test_user_management_mariadb_concurrency.py` | 12 | 0 | 0 | 0 |
| **Integration Setup Safety Guards**| `tests/test_integration_setup_guards.py` | 6 | 0 | 0 | 0 |
| **Strategy & Backtest Regression**| `tests/test_pairs_ratio_ohlc.py`, `tests/test_gap_aware_execution.py`, `tests/test_simulate_window.py` | 39 | 0 | 0 | 0 |

### Full Pytest Suite Result
```
================ 1581 passed, 25 skipped, 7 warnings in 45.14s =================
```
- **Total Collected**: 1606 items
- **Passed**: 1581
- **Skipped**: 25 (opt-in disposable DB suites, e.g. `test_paper_orders_mariadb.py`)
- **Failed**: 0
- **Errors**: 0 (reduced from 40 errors to 0 errors)

---

## 9. Production-First Safety and Integrity Evidence

### 1. Production Database (`crypto_signals`) SELECT-Only Evidence
Direct read-only verification query confirmed that production data remained pristine and unmutated:
```python
# SELECT COUNT(*) as count, MIN(event_time) as min_t, MAX(event_time) as max_t FROM funding_rate
{'count': 926, 'min_t': 1678694400018, 'max_t': 1794960000000}

# SELECT * FROM collector_heartbeat WHERE collector_name = 'funding_poller'
{'collector_name': 'funding_poller', 'last_beat_time': '2026-11-18 14:06:04', 'status': 'ok', 'detail': '3/3 symbols ok (+0 rows)'}
```
Zero test rows were inserted into, modified in, or deleted from `crypto_signals`. All integration mutations were strictly confined to `signal_bot_test`.

### 2. Service State Verification
- Inspected `signal-funding-collector.service`:
  - Active and running continuously under PID 379118.
  - Dedicated credentials from `/etc/signal-bot/funding-collector.env` intact.
  - No systemd unit files, daemon configurations, or running production processes were stopped, restarted, or altered.

### 3. Worktree & Git Preservation
- No `git reset --hard`, `git clean`, or `git stash` executed.
- All 21 modified `docs/screenshots/*.png` files preserved untouched.
- All existing `.env`, secrets, and logs preserved.

---

## 10. Final Verdict

**`WO71937_ALL_REMEDIATIONS_PASS`**

All five engineering remediation requirements are fully satisfied, validated against isolated tests and production read-only checks, and verified regression-free across the full 1606-test repository suite.
