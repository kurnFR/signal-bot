# RetailBot2 Outbox and Modern UI Release Gates

**Repository:** `kurnFR/signal-bot`  
**Prepared against master:** `3f3a9babee75401c52600317ae1876688e1856c7`  
**Status:** Engineering checks merged; staging release gates remain open.  
**Safety boundary:** This document does not authorize production database access, production migration, Telegram delivery, worker restart, deployment, or changing the default UI route.

## 1. Verified implementation baseline

The following changes are present on the recorded master baseline:

- PR #709 — lifecycle Telegram settings persistence/readback.
- PR #710 — live worker lifecycle refresh regression coverage.
- PR #711 — durable RetailBot2 Telegram outbox and migration 026.
- PR #712 — mixed-strategy Telegram gating regression coverage.

Evidence reported by the repository owner:

- Focused outbox/accounting/lifecycle/migration suite: **47 passed**.
- Latest lifecycle-control suite: **17 passed**.
- Isolated MariaDB outbox integration smoke test: entry persistence, claim/processing, retry/reclaim, sent-state persistence, exit persistence, and cleanup all reported passing against `signal_bot_test`.

These are local/owner-reported results. GitHub Actions were not used and no CI pass is claimed. They do not establish production readiness.

## 2. Current release blockers

| Gate | Current state | Required evidence |
|---|---|---|
| Code and regression coverage | Merged | PRs #709–#712 on master; owner-reported tests above |
| Real MariaDB outbox behavior | Passed on isolated test DB, owner-reported | Preserve test output and confirm same candidate SHA |
| Staging mixed-strategy worker path | **Not yet verified** | Real staging worker creates a queued event only for the strategy whose persisted Telegram flag is enabled |
| Staging UI readback | **Not yet reverified on this baseline** | Save lifecycle settings, reload page, verify values remain correct |
| Telegram external delivery | **Not authorized / not tested** | Separate explicit approval for a controlled test message |
| Production shadow enqueue coupling | **Unresolved architectural blocker** | Entry/exit enqueue is strictly coupled to trade persistence (`save_trade` / `close_trade_and_update_stats`); shadow enqueue cannot run in production without authorized trade persistence |
| Production enqueue authorization gate | **Hardened (WO71764)** | Requires BOTH `RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1` AND `RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1` for `crypto_signals`; fail-closed DB identity verification |
| Production schema | **Observed in read-only preflight (2026-11-10)** | WO71688 reported migrations 025–027 and `rdt_telegram_outbox` present in `crypto_signals`; dated observation does not authorize worker activation |
| Populated-schema migration rehearsal | **Not run** | Authorized, isolated, representative database snapshot; before/after integrity evidence |
| Modern UI cutover | **Not authorized** | Current-candidate staging acceptance, accessibility review, rollback readiness, and explicit owner approval |

An empty outbox table on the isolated test database is expected when no eligible entry/exit event has been created. It is not proof of a broken queue. Historically, runtime startup errors such as `crypto_signals.rdt_telegram_outbox doesn't exist` occurred because migration 026 had not yet been applied to production; that historical error was superseded on 2026-11-10 when read-only preflight (WO71688) observed migrations 025–027 and `rdt_telegram_outbox` present in `crypto_signals`. Schema observation does not authorize worker startup. Do not manually create or alter tables.

## 3. Isolated staging verification — no external Telegram messages

Run only against the dedicated staging instance and `signal_bot_test`. Confirm the host and selected database before running any command. Never rely on a default database name.

### 3.1 Confirm candidate and schema

Record the exact checked-out commit. Confirm the isolated database identity without printing credentials:

```sh
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=cms MYSQL_DATABASE=signal_bot_test PYTHONPATH=$PWD \
  python3 -m db.migrate --status

MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=cms MYSQL_DATABASE=signal_bot_test PYTHONPATH=$PWD \
  python3 -m db.migrate --dry-run
```

Expected: migration 026 is already applied on this staging database and the dry-run proposes no unexpected migration. If it is pending, stop and review the migration plan before applying anything. These commands are not to be run against production.

Re-run the focused regression suite from the candidate checkout:

```sh
PYTHONPATH=$PWD python3 -m pytest -q \
  tests/test_retailbot2_telegram_outbox.py \
  tests/test_retailbot_accounting.py \
  tests/test_strategy_lifecycle_controls.py \
  tests/test_retailbot2_migration_managed_startup.py
```

This suite is a regression check; it does not replace the real MariaDB smoke test or the live-backend staging acceptance below.

### 3.2 Verify persisted strategy gating

Use the staging dashboard and its real authenticated API/backend, not mocked browser routes:

1. Record the existing lifecycle settings for the two test strategies and mode.
2. Set one selected/promoted strategy's Telegram alert flag on.
3. Keep a second, unselected strategy's Telegram alert flag off.
4. Save both, reload the dashboard, and confirm the settings read back exactly.
5. Allow the isolated staging worker to refresh its lifecycle cache; the implementation refreshes persisted lifecycle settings periodically without requiring a process restart.
6. Exercise controlled paper-entry signals for both strategies using the staging-only test procedure. Do not use live exchange credentials or live trading endpoints.
7. Verify both paper trades persist, but only the selected strategy has `telegram_enabled=1` and an entry event in `rdt_telegram_outbox`. The unselected strategy must have no entry outbox event.
8. Verify the same selected/unselected distinction for exit events if the staging test procedure safely closes the disposable paper trades.
9. Record exact test trade IDs and event keys, then remove only those test records using the approved exact-ID cleanup procedure.

**External-send safety:** Before starting this staging worker, ensure its Telegram transport is disabled and verify that it cannot reach Telegram. The dispatcher must leave queued events pending when Telegram is disabled. Do not put real bot tokens or chat IDs into the staging environment for this test. If the transport cannot be proven disabled, do not run the worker test.

Do not edit shared lifecycle rows manually unless the staging operator has recorded their complete original values and a deterministic restoration procedure. Prefer the dashboard/API workflow and exact test fixtures.

### 3.3 Re-run browser acceptance on the candidate

The existing acceptance report `docs/MODERN_UI_STAGING_ACCEPTANCE_REPORT.md` records prior evidence on an older master baseline. It must not be presented as a fresh run on this candidate.

Run the existing staging acceptance harnesses from the candidate checkout, with their documented environment guards and only against the local `127.0.0.1:3306/signal_bot_test` instance. Both harnesses now override inherited `MYSQL_HOST`/`MYSQL_PORT` values for every Python helper and staging-server child process, so a production host in `.env` cannot silently redirect the test. The workflow harness also accepts `STAGING_SCREENSHOT_DIR`; use a fresh temporary directory so existing or locally edited screenshots in `docs/screenshots/` are never overwritten:

```sh
export STAGING_SCREENSHOT_DIR="$(mktemp -d /tmp/signal-bot-staging-shots.XXXXXX)"
node tests/browser/staging_workflow_acceptance.cjs
node tests/browser/staging_browser_acceptance.cjs
```

Record the commit SHA, database identity, screenshot output directory, role coverage, workflow outcomes, browser console/network errors, cleanup register, and confirmation that no production database or external Telegram endpoint was used. Keep the temporary screenshot directory for evidence review; do not copy over tracked screenshots without reviewing them first. Do not mark skipped workflows as passed. The ML-03 tournament remains unverified unless a controlled, adequately resourced staging dataset is used and actual tournament execution is observed.

The existing Playwright suite that mocks `/api/**` is useful for frontend rendering and interaction regressions, but it is not a substitute for these real-backend staging checks.

## 4. Production database readiness — separate authorization required

### 4.1 Schema observation, production database access boundary, and activation

On 2026-11-10, an operator-provided read-only preflight inspection (WO71688) reported the following production status:
- Database: `crypto_signals`.
- Migrations observed: `025_users_role_active_index`, `026_retailbot2_telegram_outbox`, and `027_retailbot2_outbox_claim_token`.
- Table `rdt_telegram_outbox` was observed present, including columns `claim_token`, `locked_until`, `status`, `sent_at`, and the payload/event columns.
- Both activation environment flags (`RETAILBOT2_OUTBOX_WORKER_ENABLED` and `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY`) were observed unset, and the supervisor did not launch the outbox worker.

This dated observation supersedes earlier historical reports where production lacked `rdt_telegram_outbox` or where migration 026 was only applied to `signal_bot_test`. However, the following release boundaries remain strictly enforced:

1. **Dated observation, not a present guarantee or standing authorization:** The WO71688 preflight reflects an operator report at a specific point in time (2026-11-10). It is not an independently verified guarantee of the database's present state or continuity. Do not interpret the WO71688 report as standing authorization for future database access. Documentation changes and previously reported preflight evidence do not grant production access authorization.
2. **Production database access requires separate authorization:** Any access to or inspection of the production database `crypto_signals` requires separate, explicit authorization. This restriction applies to all interactions: read-only SQL queries, schema inspection, migration-status checks, dry-run commands, process or session inspection through database connections, and all writes or migrations.
3. **Schema presence is not activation authorization:** Having migrations 025–027 applied in `crypto_signals` satisfies the schema precondition, but does not authorize activating the standalone worker or modifying production configuration.
4. **Worker remains inactive by default:** In the reported production configuration, both worker flags remained unset. Enabling them requires a separate approved change window.
5. **External Telegram delivery remains unauthorized and unvalidated:** WO71688 provides no evidence of production Telegram delivery. External Telegram delivery remains unauthorized and unvalidated. The dated observation confirms the worker was not launched under the observed configuration, but the preflight report is not a comprehensive historical audit of every possible Telegram delivery path. External Telegram message transmission remains a separate, independent authorization and validation gate.

Therefore:
- Any access to or inspection of the production database `crypto_signals` requires separate, explicit authorization.
- Do not execute read-only SQL queries, schema inspection, `python -m db.migrate`, migration status checks, dry-run commands, process/session inspection through database connections, or any writes/migrations against `crypto_signals` without explicit authorization.
- Do not interpret the WO71688 report or documentation updates as standing authorization for future production database access.
- Do not start or restart the standalone outbox worker without explicit administrative authorization.
- Do not enable `RETAILBOT2_OUTBOX_WORKER_ENABLED` or `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` in production without an approved change window.
- Do not manually create or alter `rdt_telegram_outbox`.

Before any authorized activation, the database owner and operations team must confirm current database integrity, verify backup readiness, and coordinate an approved change window. Following activation, operators must verify advisory lock ownership, queue processing, and error monitoring before enabling external delivery.

### 4.2 Standalone worker advisory lock verification runbook

The standalone outbox worker synchronizes global delivery across multiple instances using the MariaDB advisory lock constant `OUTBOX_LOCK_NAME = "rdt_outbox_worker_lock"`.

To verify advisory-lock ownership after an authorized worker activation, execute these read-only inspection queries against the target database:

```sql
SELECT IS_USED_LOCK('rdt_outbox_worker_lock');

SELECT
    ID, USER, HOST, DB, COMMAND, TIME, STATE
FROM information_schema.PROCESSLIST
WHERE ID = IS_USED_LOCK('rdt_outbox_worker_lock');
```

- If `IS_USED_LOCK('rdt_outbox_worker_lock')` returns `NULL`, no worker session currently holds the advisory lock.
- If it returns a connection ID, verify in `information_schema.PROCESSLIST` that the connection corresponds to the authorized worker process connected to the target database.

### 4.3 Five distinct outbox operational modes and authorization boundaries

To prevent operational confusion during staging, deployment, and auditing, operators and engineers must strictly distinguish between five independent operational concepts:

1. **Trade persistence authorization (`RETAILBOT2_ALLOW_TRADING=1`)**:
   Authorizes RetailBot2 to execute trades and persist trade records (`rdt_trades`, `rdt_trade_events`). When absent or set to any value other than `"1"`, or when `RETAILBOT2_OBSERVATION_ONLY=1`, trading and trade persistence are disabled.
2. **Outbox enqueue authorization (`RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1` + dedicated database gates)**:
   Authorizes inserting notification events into `rdt_telegram_outbox`.
   - For `signal_bot_test`: requires `RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1`.
   - For `crypto_signals`: requires **both** `RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1` AND `RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1`. Missing, empty, or malformed values fail closed.
   - Always validates active connection identity via `SELECT DATABASE()` against the configured database.
   - Fails closed if in observation-only mode (`is_observation_only(config)`).
   - This gate authorizes event persistence ONLY; it never authorizes Telegram delivery, worker startup, or trading.
3. **Worker observation mode (`--observation` CLI flag / `OutboxWorker(observation_mode=True)`)**:
   A genuinely read-only queue monitoring mode for standalone workers.
   - **Credential & Authorization Isolation (Work Order 71765):**
     - For `signal_bot_test`: requires explicit dedicated credentials `SIGNAL_BOT_TEST_DB_USER` and `SIGNAL_BOT_TEST_DB_PASSWORD`.
     - For `crypto_signals`: requires an independent explicit authorization flag `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1`.
     - For `crypto_signals`: requires dedicated production observation credentials `RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER` and `RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD`.
     - Missing, empty, or non-`"1"` authorization flag, or missing credentials, must fail closed and abort before any database connection attempt.
     - Never falls back to generic `MYSQL_USER`, `MYSQL_PASSWORD`, or default `"cms"`.
     - This flag authorizes read-only observation ONLY; it never authorizes outbox enqueue, live trading, worker delivery, or Telegram transmission.
     - **Provisioning Constraint:** The production observation account must be provisioned separately by database administrators with SELECT-only privileges for the required outbox and schema inspection (`SELECT` on `rdt_telegram_outbox`, `information_schema.tables`, `information_schema.columns`) and advisory-lock operations (`GET_LOCK`, `RELEASE_LOCK`, `IS_USED_LOCK`). Do not provision accounts or modify grants automatically.
   - Acquires and heartbeats the MariaDB advisory lock (`rdt_outbox_worker_lock`).
   - Verifies database identity via `SELECT DATABASE()` and verifies schema presence.
   - Queries queue status totals and due item counts using read-only `SELECT` queries (`inspect_queue`).
   - Logs periodic queue status summaries.
   - **Strictly prohibited** from claiming events, mutating rows (`status`, `attempts`, `locked_until`, `claim_token`, `next_attempt_at`, `last_error`), spawning transport subprocesses, making network or HTTP calls, or requiring `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID`.
   - Releases the advisory lock cleanly on `SIGINT` or `SIGTERM`.

#### Stage B Production Observation Account Provisioning and Failure-Handling Runbook

To safely prepare and operate the read-only worker observation mode against `crypto_signals`, database administrators and operators must follow this strict execution, failure-handling, and audit runbook.

##### 1. Ten-Step Execution Sequence

1. **DBA performs account-existence preflight**:
   ```sql
   SELECT User, Host FROM mysql.user WHERE User = 'retailbot2_obs';
   ```
2. **Preflight Stop Condition**: If any matching account exists, **STOP IMMEDIATELY**. Do not reuse, alter, or drop the account. State is evaluated under existing account policy.
3. **DBA executes `CREATE USER` without `IF NOT EXISTS`**:
   ```sql
   CREATE USER 'retailbot2_obs'@'127.0.0.1' IDENTIFIED BY '<STRONG_RANDOM_PASSWORD>';
   ```
4. **Record creation result**: Record the exact execution result and timestamp in the provisioning change audit record.
5. **Handle `CREATE USER` failure**: If `CREATE USER` fails (e.g. syntax error, permission error, or conflict), **STOP**. Do not execute `GRANT`. State remains `ACCOUNT_NOT_CREATED` (or `PROVISIONING_STATE_UNKNOWN` if the connection dropped or state is unverified).
6. **Execute exact table-level grant**: If `CREATE USER` succeeds, state transitions to `ACCOUNT_CREATED_GRANT_PENDING`. Execute the exact table-level `SELECT` grant:
   ```sql
   GRANT SELECT ON `crypto_signals`.`rdt_telegram_outbox` TO 'retailbot2_obs'@'127.0.0.1';
   ```
7. **Handle `GRANT` failure**: If `GRANT` fails (e.g. `Error 1142: GRANT command denied`), **STOP IMMEDIATELY**. State remains `ACCOUNT_CREATED_GRANT_PENDING` (or `PROVISIONING_STATE_UNKNOWN` if connection was interrupted).
8. **Prohibit worker startup**: Do **NOT** configure worker credentials or start the observation worker while in any unverified or partial state.
9. **No automatic recovery**: Do **NOT** automatically retry, revoke, or drop the account upon failure.
10. **Record partial state & escalate**: Record the exact partial state and error in the audit record and request database owner and DBA review.

##### 2. Four Provisioning States State Machine

Every provisioning attempt must be tracked and classified into exactly one of four explicit states:

- **`ACCOUNT_NOT_CREATED`**: Preflight check confirmed 0 matching rows, and `CREATE USER` was either not attempted or failed closed before user creation. No user object exists in MariaDB.
- **`ACCOUNT_CREATED_GRANT_PENDING`**: `CREATE USER` completed successfully, but `GRANT SELECT` has either not yet executed, failed, or not yet been verified. The account has only implicit `USAGE` and cannot read `rdt_telegram_outbox`.
- **`ACCOUNT_CREATED_GRANT_VERIFIED`**: Both `CREATE USER` and `GRANT SELECT` completed successfully, and read-only verification (`SHOW GRANTS FOR 'retailbot2_obs'@'127.0.0.1'`) confirmed exact least-privilege grants (`USAGE` on `*.*` and `SELECT` on `crypto_signals.rdt_telegram_outbox`).
- **`PROVISIONING_STATE_UNKNOWN`**: A network timeout, disconnection, or unhandled server error occurred during `CREATE USER` or `GRANT`, leaving the server-side state unconfirmed.

> [!CRITICAL]
> **Promotion Invariant:** **No state other than `ACCOUNT_CREATED_GRANT_VERIFIED` may proceed to worker credential configuration or observation worker startup.** If the state is `ACCOUNT_NOT_CREATED`, `ACCOUNT_CREATED_GRANT_PENDING`, or `PROVISIONING_STATE_UNKNOWN`, worker execution is strictly prohibited.

##### 3. Provisioning Change and Audit Record Requirements

A formal provisioning change/audit record must be created and maintained for the deployment. It must capture:

- **Change / Incident Ticket ID**: Reference to the authorized change ticket.
- **DBA Identity**: Name and role of the executing database administrator.
- **Owner Authorization Reference**: Documented reference to explicit database owner sign-off.
- **Preflight Timestamp & Result**: Exact execution timestamp and rowcount from preflight `SELECT`.
- **Account Creation Timestamp & Result**: Exact timestamp and outcome of `CREATE USER`.
- **Grant Timestamp & Result**: Exact timestamp and outcome of `GRANT SELECT`.
- **Effective Grants Verification Timestamp & Result**: Timestamp and output of `SHOW GRANTS`.
- **Exact Account Identity**: `'retailbot2_obs'@'127.0.0.1'`.
- **Provisioning Completion Status**: Exactly one of the four provisioning states above.
- **Password Security Guarantee**: **Never** record the plaintext password or expose it in terminal output, change records, reports, application logs, or Git.

##### 4. Rollback Procedure and Provenance Conditions

An exact-account `DROP USER` removes the account and its privileges:
```sql
DROP USER 'retailbot2_obs'@'127.0.0.1';
```

> [!CAUTION]
> **Rollback Preconditions**: Rollback is strictly prohibited unless **all six** provenance conditions are satisfied:
> 1. The account was confirmed absent during the DBA preflight check.
> 2. The exact account was created by this specific authorized provisioning operation.
> 3. The creation result, timestamp, and audit record were formally documented.
> 4. The account identity is strictly and uniquely `'retailbot2_obs'@'127.0.0.1'`.
> 5. The DBA explicitly confirms that the observation worker is stopped and the account is no longer required.
> 6. The database owner authorizes rollback in writing.
>
> If creation provenance is uncertain or any condition cannot be proven: **STOP IMMEDIATELY.** Do not drop the account; escalate to the database owner. Never use wildcard host accounts (`'retailbot2_obs'@'%'`).

4. **Mock delivery on test database (`mock_transport_response`)**:
   An isolated integration test mechanism on `signal_bot_test` where the worker claims events and transitions row states based on synthetic in-memory responses without invoking Telegram or external network endpoints.
5. **Actual Telegram delivery authorization (`RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`)**:
   Authorizes the worker or supervisor to execute the external transport subprocess and transmit HTTP requests to the Telegram Bot API. Standalone worker startup against production fails closed without this flag unless running in `--observation` mode.

### 4.4 Shadow enqueue decoupling, payload builders, and testing architecture (WO71778)

Work Order 71778 introduces an isolated shadow outbox enqueue mechanism that decouples signal notification generation from live trade persistence, enabling paper/shadow signal alerts to enter the outbox during testing without authorizing live trading or creating synthetic trade records.

#### 1. Persisted Trade Notifications vs. Shadow Signal Notifications

The system strictly differentiates between two notification classes:

| Attribute | Persisted Trade Notifications | Shadow Signal Notifications (Decoupled) |
| :--- | :--- | :--- |
| **Trigger Path** | `RetailDeathTrapBot._open_trade()` and `_close_trade()` | `RetailDeathTrapBot.enqueue_shadow_entry_signal()` |
| **Persistence Boundary** | Embedded in MariaDB trade transaction (`DatabaseManager.save_trade()` / `close_trade_and_update_stats()`) | Standalone outbox insert (`DatabaseManager.enqueue_trade_signal_event()`) |
| **`trade_id` Value** | `trade_id > 0` (FK-free, references row in `rdt_trades`) | `trade_id = 0` (unlinked, signals shadow signal only) |
| **`rdt_trades` Mutation** | `INSERT` on entry, `UPDATE` on exit | **Zero mutations** (never touches `rdt_trades`) |
| **`rdt_strategy_stats` Mutation** | `UPDATE` on trade close | **Zero mutations** (never touches `rdt_strategy_stats`) |
| **Trading Authorization** | Requires `RETAILBOT2_ALLOW_TRADING=1` | **Prohibits / bypasses trading authorization** |
| **Primary Use Case** | Real paper/live execution order notifications | Test & paper evaluation of strategy signals |

#### 2. Pure Payload Builders

Outbox payloads are constructed by pure, deterministic builder functions that perform no database queries, network requests, or input mutations:

- **`build_entry_alert_payload(signal, units, risk_amt, risk_pct, mode="shadow")`**:
  - Validates required signal keys (`symbol`, `side`, `entry`, `stop`, `target`).
  - Enforces `side` must be either `'LONG'` or `'SHORT'`.
  - Enforces numeric validity (`float` conversions) for prices and risk parameters.
  - Returns a pure dictionary structured for `format_entry_message(payload)`.
- **`build_exit_alert_payload(trade, exit_price, reason, pnl, pnl_pct)`**:
  - Validates required trade dictionary keys (`symbol`, `side`, `entry`, `units`, `strategy`).
  - Validates numeric exit fields (`exit_price`, `pnl`, `pnl_pct`).
  - Returns a pure dictionary structured for `format_exit_message(payload)`.

#### 3. Test-Only Shadow Enqueue Authorization Gate

Shadow enqueue is guarded by a dedicated fail-closed authorization function `is_shadow_outbox_enqueue_authorized(config, conn, database)`:

1. **Explicit Enablement:** Requires `RETAILBOT2_SHADOW_ENQUEUE_ENABLED=1`. Any unset, empty, or other value fails closed.
2. **Strict Test Database Restriction:** The configured database must be strictly `'signal_bot_test'`.
3. **Connection Identity Verification:** The active connection's `SELECT DATABASE()` must report strictly `'signal_bot_test'`.
4. **Configuration & Connection Agreement:** Caller-supplied and configured database names must agree with the connection database.
5. **Fail-Closed on Production:** Target database `'crypto_signals'` is **strictly prohibited** and immediately rejected.
6. **Observation Mode Suppression:** Observation-only mode (`is_observation_only()`) suppresses shadow enqueue.
7. **Independence from Trading:** Does **not** require `RETAILBOT2_ALLOW_TRADING=1`.
8. **No Delivery or Production Authority:** Does **not** authorize production enqueue (`RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1`) or external Telegram transmission (`RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`).

#### 4. Event Key Namespace and Idempotency

To prevent duplicate outbox entries while supporting independent signals across symbols, strategies, and timeframes, shadow events use a dedicated deterministic key namespace:

- **Shadow Entry Key Format:**
  ```text
  retailbot2:shadow:entry:{symbol}_{strategy}_{mode}_{side}_{candle_close}
  ```
- **Shadow Exit Key Format:**
  ```text
  retailbot2:shadow:exit:{symbol}_{strategy}_{mode}_{side}_{candle_close}
  ```
- **Idempotency Guarantee:**
  Rows are enqueued using MariaDB's `INSERT ... ON DUPLICATE KEY UPDATE event_key=VALUES(event_key)`. Since `event_key` has a `UNIQUE` index on `rdt_telegram_outbox`, repeated evaluations of the same candle signal produce a harmless no-op, preventing duplicate notifications.

#### 5. Trade ID = 0 Semantics and Schema Compatibility

In migration 026, `rdt_telegram_outbox.trade_id` is defined as `int(11) NOT NULL` without a foreign key constraint to `rdt_trades`.
- `trade_id = 0` is fully compliant with schema constraints.
- `OutboxWorker.format_event_text()` and the message formatters (`format_entry_message`, `format_exit_message`) parse payloads with `trade_id = 0` seamlessly without querying `rdt_trades`.
- Delivery status updates (`acknowledge_outbox_event`, `retry_outbox_event`, `dead_letter_outbox_event`) operate on outbox `id` and `claim_token`, remaining completely agnostic to `trade_id`.

#### 6. Exit Correlation Limitations and Future Architecture

While entry signals are purely decoupled from trade records, shadow exit correlation has specific operational boundaries:
- **Decoupled Entry Signals:** Fully supported. The scan loop detects candidate entries and immediately enqueues them when `RETAILBOT2_SHADOW_ENQUEUE_ENABLED=1` without opening trades.
- **Exit Correlation Limitation:** Because unauthorized trading skips `_open_trade()`, no active trade record exists in `rdt_trades` or in-memory trade collections. Consequently, standard stop-loss, take-profit, or trend-reversal triggers cannot look up an existing shadow position to calculate realized exit P&L or determine exit timestamps.
- **Future Resolution Path:** To achieve fully automated shadow exit notifications without trade persistence, a future work order will introduce a dedicated in-memory shadow position ledger or virtual paper state engine that tracks simulated trade lifetimes separately from live execution tables.

> [!WARNING]
> **Production Boundary Warning:**
> The decoupled shadow enqueue path is authorized exclusively for testing on `signal_bot_test`. It must **never** be enabled on `crypto_signals`. Production outbox notifications remain strictly blocked until approved under Stage D and E release gates.

### 4.5 Verified schema lineage and production monitoring / recovery runbook

#### Schema Lineage
- **Migration 026 (`db/migrate_retailbot2_telegram_outbox.sql`):** Creates table `rdt_telegram_outbox` with 12 columns: `id`, `event_key`, `trade_id`, `event_type`, `payload_json`, `status`, `attempts`, `next_attempt_at`, `locked_until`, `last_error`, `created_at`, `sent_at`.
- **Migration 027 (`db/migrate_retailbot2_outbox_claim_token.sql`):** Fences stale worker leases by adding column 13: `claim_token CHAR(32) NULL AFTER locked_until`.
- **No `updated_at` column:** Table `rdt_telegram_outbox` does **not** contain an `updated_at` column. Monitoring queries, administrative inspection scripts, and incident recovery scripts must NEVER reference `updated_at`. Referencing `updated_at` will fail with MariaDB error `Unknown column 'updated_at' in 'field list'`.

#### Approved Read-Only Monitoring Queries
Use only verified schema columns:

```sql
-- 1. Queue totals by status and oldest item timestamp
SELECT
    status,
    COUNT(*) AS count,
    MIN(created_at) AS oldest_created_at
FROM rdt_telegram_outbox
GROUP BY status;

-- 2. Due items ready for processing or retry
SELECT
    id,
    event_key,
    trade_id,
    event_type,
    status,
    attempts,
    next_attempt_at,
    locked_until,
    claim_token,
    created_at
FROM rdt_telegram_outbox
WHERE attempts < 8
  AND (
      (status = 'pending' AND next_attempt_at <= UTC_TIMESTAMP())
      OR (status = 'processing' AND locked_until <= UTC_TIMESTAMP())
  )
ORDER BY next_attempt_at ASC, id ASC
LIMIT 10;

-- 3. Dead-letter items (exhausted retries or explicit fatal errors)
SELECT
    id,
    event_key,
    trade_id,
    event_type,
    status,
    attempts,
    last_error,
    created_at
FROM rdt_telegram_outbox
WHERE status = 'dead' OR attempts >= 8
ORDER BY id DESC
LIMIT 10;
```

#### Manual Recovery Preconditions, Delivery Outcome Classification, and Incident Policy

> [!CAUTION]
> **No Automated or Unconditional Requeue**: Lease expiry alone is **NOT** proof that an old transport process has terminated, nor is process termination or advisory-lock release proof that an external Telegram message was not delivered. If an HTTP request was transmitted or in-flight, it may have succeeded on Telegram's servers before a process died or before a database state acknowledgement failed. Blindly resetting `status = 'processing'` to `'pending'` can result in **duplicate Telegram deliveries**.

Automated recovery jobs and ad-hoc UPDATE queries are strictly prohibited. Before any manually authorized recovery operation on stuck or expired events, all of the following six preconditions must be satisfied:

1. **Stop the worker gracefully**: Terminate the running worker process using `SIGINT` or `SIGTERM` and ensure it shuts down cleanly.
2. **Verify process elimination**: Verify via `ps aux | grep -E "outbox_worker|outbox_transport"` and `/proc` inspection that no worker process and no transport subprocess group remains alive.
3. **Verify advisory lock release**: Execute read-only lock inspection to confirm `IS_USED_LOCK('rdt_outbox_worker_lock')` returns `NULL`.
4. **Read-only event inspection**: Inspect affected event IDs, their exact claim token, attempts, and lease expiry timestamps using read-only inspection SQL (see query 4 below).
5. **Obtain explicit authorization**: Obtain explicit operator and database owner sign-off for the specific event IDs to be recovered after evaluating the external delivery classification below.
6. **Preserve audit record**: Record an immutable audit log detailing the affected IDs, original states, claim tokens, delivery classification, and the authorized operator before making any state adjustments.

> [!IMPORTANT]
> **Satisfying process and advisory-lock checks does NOT resolve external delivery status**: Terminating processes and confirming advisory lock release guarantees only that local concurrency conflicts are eliminated; it provides **zero evidence** regarding whether an external HTTP request was received, processed, or acknowledged by the Telegram Bot API.

#### Delivery Outcome Classification and Handling Policy

Every in-flight or expired outbox event subject to incident review must be classified into exactly one of three delivery outcomes based on verifiable evidence:

1. **`CONFIRMED_NOT_DELIVERED`**:
   - **Definition**: Reliable, verifiable evidence establishes that Telegram did not accept the request (e.g. DNS failure before connection establishment, socket connect timeout before TCP handshake, or pre-connect error prior to HTTP transmission).
   - **Handling**: Eligible for manual requeueing only after all six preconditions above are met and explicit operator and database owner sign-off is obtained.
2. **`CONFIRMED_DELIVERED`**:
   - **Definition**: Reliable evidence establishes that Telegram accepted the request (e.g. Telegram API returned HTTP 200 with `{"ok": true}`, message ID observed in target channel/chat, or verified upstream log).
   - **Handling**: The event must **never** be requeued. If stuck in `status = 'processing'`, manual remediation must transition the row directly to `sent` (setting `sent_at = UTC_TIMESTAMP()`), never back to `pending`.
3. **`DELIVERY_UNKNOWN`**:
   - **Definition**: The request may have succeeded, but its outcome cannot be established from available evidence (e.g. post-send timeout, connection reset after HTTP request bytes written, worker crash during payload transit, or database connection drop before writing `sent` status).
   - **Explicit Non-Proof Guarantees**:
     - **Process termination** does NOT prove that a previous HTTP request was unsuccessful.
     - **Advisory-lock release** does NOT prove that a previous HTTP request was unsuccessful.
     - **Lease expiry** does NOT prove that a previous HTTP request was unsuccessful.
     - **Database acknowledgement failure** after successful Telegram delivery can leave the event in an apparently recoverable state (`status = 'processing'` with expired lease) despite the message having already been delivered.
     - **Duplicate delivery risk**: Requeueing an event with `DELIVERY_UNKNOWN` can produce duplicate Telegram messages.
   - **Mandatory Policy for `DELIVERY_UNKNOWN`**:
     - **Do NOT describe the event as safe to requeue.**
     - **Leave the event untouched** unless an authorized operator and database owner explicitly accept the duplicate-delivery risk for the exact event IDs.
     - **Do NOT claim that duplicate delivery can be prevented or ruled out.**
     - **Mandatory Audit Logging**: Operators must record in the incident audit record:
       - The unknown outcome (`DELIVERY_UNKNOWN`);
       - All available evidence and inspection logs;
       - The exact event IDs and `event_key` values;
       - Prior claim tokens, attempt counts, and states;
       - The explicit authorization decision and duplicate-risk acceptance;
       - The named responsible operators and database owner;
       - The resulting database action.

```sql
-- 4. Read-only inspection of processing events with expired leases (do not mutate)
SELECT
    id,
    event_key,
    trade_id,
    event_type,
    status,
    attempts,
    claim_token,
    locked_until,
    created_at,
    CASE
        WHEN locked_until <= UTC_TIMESTAMP() THEN 'EXPIRED'
        ELSE 'ACTIVE_LEASE'
    END AS lease_state
FROM rdt_telegram_outbox
WHERE status = 'processing'
ORDER BY locked_until ASC, id ASC;
```


### 4.6 Stage E Operational Hardening and Worker Supervision (WO71782)

Stage E hardens the outbox worker for reliable supervised operation, provides queue and dead-letter observability, and establishes a safe, dry-run retention planning engine:

1. **Supervised Service Unit Template (`deploy/systemd/retailbot2-outbox-worker.service`):**
   - Implements `KillMode=mixed` and `TimeoutStopSec=15s` to ensure graceful `SIGTERM` shutdown, child transport process group reaping, and clean MariaDB advisory lock release.
   - Restricts privileges via `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=read-only`, and drops capabilities.
   - Pinned to environment configuration file `/etc/retailbot2/outbox-worker.env` (mode 0600) with zero hardcoded credentials.
   - Restarts on failure with backoff (`Restart=on-failure`, `RestartSec=5s`, `StartLimitIntervalSec=60s`, `StartLimitBurst=5`).
2. **Queue and Dead-Letter Observability (`paper/outbox_health.py`):**
   - Provides read-only health snapshots reporting pending backlog, due items, processing leases, expired leases, retry distributions, dead-letter details, and advisory lock ownership.
   - Evaluates operational health states (`HEALTHY`, `WARN`, `CRITICAL`).
   - Integrated into CLI via `python3 -m paper.outbox_health` and `python3 -m paper.outbox_worker --health`.
3. **Retention Policy Proposals & Dry-Run Planner (`paper/outbox_retention.py`):**
   - Establishes defensible retention proposals (14 days for sent events, 90 days for dead letters, indefinite protection for pending/processing/uncertain events).
   - Read-only dry-run planner computes candidate counts and age ranges without modifying rows.
   - Automated purge execution is strictly locked to `signal_bot_test` and blocked on production.
4. **Comprehensive Operational Runbook:**
   - Full operational procedures and troubleshooting guidelines documented in [RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md](file:///home/BIS/signal-bot-wo71767-prod-validation/docs/RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md).

### 4.7 Stage F Production Deployment Readiness & Release Gate Matrix (WO71783)

Stage F establishes the comprehensive release gate matrix, packaging specification, operational monitoring plan, and deployment/rollback rehearsal for the RetailBot2 outbox dispatcher subsystem.

#### 1. RetailBot2 14-Gate Production Release Matrix

| Gate # | Release Gate / Requirement | Status | Evidence & Validation Scope | Owner Action / Blockers |
| :---: | :--- | :---: | :--- | :--- |
| **G-01** | **Code Lineage & Base Commit Integrity** | **PASS** | Parent lineage cleanly traces back to master `a2c850aa8f97f081e22008d1efc0d3654c379dbe`. Zero uncommitted files, all 45 worktrees intact, 21 screenshot PNGs preserved. | None. Verified in WO71779A/B, WO71782, WO71783. |
| **G-02** | **Schema Migrations (026 & 027) on Target DB** | **PASS** | 13-column `rdt_telegram_outbox` and `claim_token` verified on `signal_bot_test`. Production schema verified via WO71776 observation trial. Zero invalid references to `updated_at`. | None. |
| **G-03** | **Worker Lease & Advisory Lock Exclusivity** | **PASS** | Single-instance mutual exclusion via MariaDB `GET_LOCK('rdt_outbox_worker_lock', 0)`. Atomic lease claiming (`locked_until`, `claim_token`) verified under concurrent worker contention. | None. Validated in `tests/test_retailbot2_outbox_supervision.py`. |
| **G-04** | **Fail-Closed Delivery Authorization Gate** | **PASS** | Outbound Telegram dispatch blocked unless `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`. Default is fail-closed `0`. Verified across all test suites. | None. |
| **G-05** | **Pure Payload Builders & Shadow Enqueue Authorization** | **PASS** | Pure payload builders (`build_entry_alert_payload`, `build_exit_alert_payload`) decouple notifications from live trading (`trade_id = 0`). 8-point check in `is_shadow_outbox_enqueue_authorized` strictly enforces test-only persistence. | None. Validated in WO71779A/B and test suites. |
| **G-06** | **Staging E2E Outbox Integration Flow** | **PASS** | End-to-end signal flow verified on `signal_bot_test`: signal generation → pure payload builder → shadow enqueue → worker claim → mock transport → delivery acknowledgment (`sent`). Zero mutations to `rdt_trades`. | None. Validated in WO71781. |
| **G-07** | **Worker Supervision & Systemd Unit Hardening** | **PASS** | Systemd unit template `retailbot2-outbox-worker.service` implements `KillMode=mixed`, `TimeoutStopSec=15s`, non-root user `retailbot`, sandboxing (`ProtectSystem=strict`), and failure restart limits. Directives validated. | None. Added in WO71782; verified in WO71783. |
| **G-08** | **Operational Observability & Health Snapshot Contract** | **PASS** | Read-only observability module `paper/outbox_health.py` reports queue backlog, processing leases, dead letters, and advisory locks with zero row locks. CLI `--health` and `--json` operational. | None. Validated in `tests/test_retailbot2_outbox_observability.py`. |
| **G-09** | **Retention Policy Proposals & Dry-Run Planner** | **PASS** | Retention policy proposed (14d sent, 90d dead, indefinite protection for pending/processing/uncertain rows). Dry-run planner `paper/outbox_retention.py` computes prune candidates without row mutations. | None. Validated in `tests/test_retailbot2_outbox_retention.py`. |
| **G-10** | **Deployment Package & Verification Tooling** | **PASS** | Minimal runtime requirements `deploy/requirements-worker.txt` (`mysql-connector-python` only). Standalone verifier `deploy/verify_deployment_package.py` and SHA-256 manifest `deploy/package_manifest.json` verify integrity. | None. Verified in `tests/test_retailbot2_deployment_package.py`. |
| **G-11** | **Production Host User, Filesystem & Mode 0600 Env Setup** | **OWNER_ACTION_REQUIRED** | Dedicated user `retailbot`, directory `/opt/signal-bot`, and configuration file `/etc/retailbot2/outbox-worker.env` (chmod 0600) must be provisioned on target production host. | Owner/Operator must provision host environment prior to service startup. |
| **G-12** | **Production Observation Credential Provisioning & Rotation** | **PASS** | Dedicated SELECT-only account `'retailbot2_obs'@'127.0.0.1'` provisioned, credential rotated (WO71774), and verified via 60-second read-only trial (WO71776). Zero DML privileges. | None. Account operational for observation. |
| **G-13** | **Production Enqueue & Delivery Authorization** | **NOT_AUTHORIZED** | Production enqueue switch `RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED` and delivery switch `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` remain `0` (fail-closed). | Explicit owner approval required to enable delivery mode. |
| **G-14** | **Final Owner Deployment Approval** | **OWNER_ACTION_REQUIRED** | Formal owner authorization to install systemd service on production host and initiate staged rollout. | Owner review and sign-off on Stage F report. |

#### 2. Technical Readiness vs. Deployment Authorization

- **Technical Readiness Verdict:** **`PASS`**  
  All 10 technical release gates (G-01 through G-10, plus observation credential G-12) have achieved full implementation, rigorous multi-suite test coverage (151 tests passing), and independent verification.
- **Production Deployment Status:** **`PASS_WITH_OWNER_ACTIONS`**  
  Production deployment cannot proceed until the owner provisions the target production host filesystem/user (G-11), explicitly authorizes live delivery switches (G-13), and issues final deployment sign-off (G-14).


## 5. Modern UI migration and cutover

The modern React UI remains available at `/modern`; legacy `/` remains the default. Keep `SIGNAL_BOT_USE_MODERN_UI=0`.

The historical UI acceptance reports establish substantial parity and staging evidence, but the latest RetailBot2 changes and the current candidate still need the staging checks above. Before changing the default route:

- Re-run current-candidate staging browser acceptance with real backend persistence.
- Verify all 11 views and the key mutation workflows for Admin, Trader, and Viewer.
- Recheck keyboard navigation, dialog focus behavior, screen-reader labels/status, responsive layouts, and error/loading/empty states.
- Verify authentication/session restoration and role-based restrictions.
- Confirm built frontend assets correspond to the exact reviewed commit.
- Confirm the legacy rollback path and named operator/reviewer.
- Obtain explicit owner approval for the cutover and any service reload.

Do not change the environment switch, restart a service, or deploy as part of this checklist.

## 6. Go / no-go rule

**Current decision: HOLD.**

Move to a production change proposal only when staging mixed-strategy outbox gating, current-candidate UI acceptance, and the populated-schema migration rehearsal all have recorded evidence and independent review. Production database access/migration, Telegram delivery, deployment, and the root UI switch each require their own explicit authorization.