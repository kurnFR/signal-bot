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
| Production schema | **Blocked** | Production table `rdt_telegram_outbox` is currently reported missing; migration 026 has only been applied to `signal_bot_test` |
| Populated-schema migration rehearsal | **Not run** | Authorized, isolated, representative database snapshot; before/after integrity evidence |
| Modern UI cutover | **Not authorized** | Current-candidate staging acceptance, accessibility review, rollback readiness, and explicit owner approval |

An empty outbox table on the isolated test database is expected when no eligible entry/exit event has been created. It is not proof of a broken queue. The production error `crypto_signals.rdt_telegram_outbox doesn't exist` is consistent with migration 026 not having been applied there. Do not manually create the table.

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

Production currently lacks `rdt_telegram_outbox`. The worker's startup schema verification requires that table and fails closed if it is missing. Therefore:

- Do not start or restart the outbox-dependent production worker against the current production schema.
- Do not manually create `rdt_telegram_outbox`.
- Do not run `python -m db.migrate`, migration status, dry-run, SQL queries, or other database commands against production under the current authorization boundary.
- Do not infer that the successful `signal_bot_test` migration proves production is ready.

Before production migration, the database owner must separately authorize the production operation and approve a maintenance/change window. The required preparation is a verified backup with a tested restore path, an isolated populated-schema rehearsal based on an approved representative snapshot, migration checksum review, and a written rollback/abort plan. Follow `docs/POPULATED_DATABASE_MIGRATION_REHEARSAL.md`. Migration 026 is additive, but that does not remove the need for the full migration and recovery review.

After approval, the production operator must apply the reviewed migration before deploying/restarting code that requires the outbox table, then verify schema, worker startup, queue processing, retry behavior, and monitoring. External Telegram delivery remains a separate opt-in validation.

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