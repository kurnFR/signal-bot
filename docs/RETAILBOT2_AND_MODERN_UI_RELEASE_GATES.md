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
4. **Mock delivery on test database (`mock_transport_response`)**:
   An isolated integration test mechanism on `signal_bot_test` where the worker claims events and transitions row states based on synthetic in-memory responses without invoking Telegram or external network endpoints.
5. **Actual Telegram delivery authorization (`RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`)**:
   Authorizes the worker or supervisor to execute the external transport subprocess and transmit HTTP requests to the Telegram Bot API. Standalone worker startup against production fails closed without this flag unless running in `--observation` mode.

### 4.4 Shadow enqueue coupling constraint and unresolved rollout blocker

**Unresolved Rollout Blocker:** Safe production shadow enqueue cannot be operated independently of trade persistence under the current architecture.

- In `paper/retailbot2.py`, outbox event generation for trade entry and trade exit is embedded directly inside trade persistence transactions:
  - Entry events are enqueued inside `save_trade()`, which is called only when `_open_trade()` executes under `is_trading_authorized(config)`. If trading is unauthorized, `_open_trade()` returns `None` and `save_trade()` is never called.
  - Exit events are enqueued inside `close_trade_and_update_stats()`.
  - When running in observation-only mode (`is_observation_only(config)`), trade persistence is suppressed and `is_outbox_enqueue_authorized()` explicitly returns `False`.
- Consequently, RetailBot2 cannot enqueue shadow trade entry or exit events in production without live trade persistence and trading authorization.
- While operational events (e.g. engine startup, heartbeat, reconciliations) can technically be enqueued without trade persistence, entry and exit shadow testing against production cannot take place without persisting trades or refactoring the lifecycle pipeline to decouple signal generation from trade persistence.
- Any future production rollout proposal must treat this architectural coupling as an explicit blocker until decoupled or explicitly authorized.

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

#### Manual Recovery Preconditions, Incident Policy, and Read-Only Inspection

> [!CAUTION]
> **No Automated or Unconditional Requeue**: Lease expiry alone is **NOT** proof that an old transport process has terminated. If an old transport subprocess was spawned and encountered a network stall or high latency, its HTTP request to Telegram might still be in-flight or succeed after the database lease expires. Blindly resetting `status = 'processing'` to `'pending'` can result in **duplicate Telegram deliveries**.

Automated recovery jobs and ad-hoc UPDATE queries are strictly prohibited. Before any manually authorized recovery operation on stuck or expired events, all of the following six preconditions must be satisfied:

1. **Stop the worker gracefully**: Terminate the running worker process using `SIGINT` or `SIGTERM` and ensure it shuts down cleanly.
2. **Verify process elimination**: Verify via `ps aux | grep -E "outbox_worker|outbox_transport"` and `/proc` inspection that no worker process and no transport subprocess group remains alive.
3. **Verify advisory lock release**: Execute read-only lock inspection to confirm `IS_USED_LOCK('rdt_outbox_worker_lock')` returns `NULL`.
4. **Read-only event inspection**: Inspect affected event IDs, their exact claim token, attempts, and lease expiry timestamps using read-only inspection SQL (see query 4 below).
5. **Obtain explicit authorization**: Obtain explicit operator and database owner sign-off for the specific event IDs to be recovered after confirming no external delivery occurred.
6. **Preserve audit record**: Record an immutable audit log detailing the affected IDs, original states, claim tokens, and the authorized operator before making any state adjustments.

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