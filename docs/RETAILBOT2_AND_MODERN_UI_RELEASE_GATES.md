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

### 4.1 Schema observation and activation boundary

On 2026-11-10, an operator-provided read-only preflight inspection (WO71688) reported the following production status:
- Database: `crypto_signals`.
- Migrations observed: `025_users_role_active_index`, `026_retailbot2_telegram_outbox`, and `027_retailbot2_outbox_claim_token`.
- Table `rdt_telegram_outbox` was observed present, including columns `claim_token`, `locked_until`, `status`, `sent_at`, and the payload/event columns.
- Both activation environment flags (`RETAILBOT2_OUTBOX_WORKER_ENABLED` and `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY`) were observed unset, and the supervisor did not launch the outbox worker.

This dated observation supersedes earlier historical reports where production lacked `rdt_telegram_outbox` or where migration 026 was only applied to `signal_bot_test`. However, the following release boundaries remain strictly enforced:

1. **Dated observation, not a present guarantee:** The WO71688 preflight reflects an operator report at a specific point in time (2026-11-10). It is not an independently verified guarantee of the database's present state or continuity.
2. **Schema presence is not activation authorization:** Having migrations 025–027 applied in `crypto_signals` satisfies the schema precondition, but does not authorize activating the standalone worker or modifying production configuration.
3. **Worker remains inactive by default:** In the reported production configuration, both worker flags remained unset. Enabling them requires a separate approved change window.
4. **External Telegram delivery is not validated:** Production Telegram message transmission has not been authorized or validated. No real Telegram delivery has occurred. External delivery remains an independent validation gate.

Therefore:
- Do not start or restart the standalone outbox worker without explicit administrative authorization.
- Do not enable `RETAILBOT2_OUTBOX_WORKER_ENABLED` or `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` in production without an approved change window.
- Do not manually create or alter `rdt_telegram_outbox`.
- Do not execute unreviewed migrations or ad-hoc SQL mutations against `crypto_signals`.

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