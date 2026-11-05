# Antigravity Next Work Order — UI Migration Safety, Review Closure, and Remaining Parity

**Repository:** `kurnFR/signal-bot`  
**Working branch:** `fix/ui-migration-review-remediation`  
**Base branch:** `master`  
**Current code PR:** [#697](https://github.com/kurnFR/signal-bot/pull/697)  
**Reviewed commit:** `e0ef1c28d2348fa51802f74bcaedd4c2b0a1ea3b`  
**Owner:** Antigravity  
**Independent reviewer:** ChatGPT / repository owner  
**Priority:** P0 safety corrections first; P1 finish remaining modern UI workflows only after P0 passes.

---

## 1. Objective

Continue the Modern UI migration from the current PR without treating the latest test summary as independent acceptance.

This work order has two outcomes:

1. Correct the remaining safety and test-harness defects identified in the source review of commit `e0ef1c2`.
2. Continue the actual legacy-to-modern workflow migration. The acceptance report currently acknowledges **10 legacy-only workflows**. Re-audit the worksheet and migrate the remaining workflows into React so the modern UI does not require users to return to the legacy dashboard for those workflows.

Keep the root dashboard on the legacy UI. Do not merge, deploy, or switch the default UI.

## 2. Updated database authorization — important

The repository owner has explicitly authorized use of **`signal_bot_test` for testing only**.

### Allowed
- Connect to `signal_bot_test` for automated tests, disposable fixtures, migrations required by the test suite, and staging browser acceptance.
- Create uniquely named test fixture rows in `signal_bot_test`, using a run-specific prefix.
- Update or delete only fixture rows created by the current test run.
- Run test-only schema setup/migrations against `signal_bot_test` when required, documenting exactly what schema changes were applied.

### Forbidden
- Do not connect to, query, migrate, write to, or otherwise access `crypto_signals` during this work.
- Do not use production credentials/configuration as a fallback if the test DB connection fails.
- Do not drop or recreate the `signal_bot_test` database.
- Do not truncate shared tables or alter the pre-existing `admin` account or any other pre-existing user.
- Do not run production services or point a staging process at production.
- Do not send real Telegram messages or execute real/paper trading operations.

### Mandatory database guard

Before any test or staging app starts:

1. Set the test environment explicitly so the application resolves `MYSQL_DATABASE=signal_bot_test` before importing configuration or initializing any DB pool.
2. Assert the resolved application configuration names `signal_bot_test`.
3. Obtain a connection from the exact pool the app/test will use and execute `SELECT DATABASE()`; assert the returned value is exactly `signal_bot_test`.
4. Fail closed if the assertion fails. Never continue against a fallback database.
5. Record the redacted DB name and verification result in the test log.

Do not create a dynamic database by first connecting through the default application pool. That pattern can initialize a pool using the production database configuration before the test override is applied.

Use unique fixture names such as `uiacc_<run-id>_<role>`. Track inserted primary keys and clean up only those exact rows in `finally`. Never use broad cleanup statements that can affect non-test users. If a test requires a schema migration, run it against `signal_bot_test` only and record the migration/version state before and after.

---

## 3. P0 — Fix database isolation in the MariaDB concurrency tests

### Source-review finding

In `tests/test_user_management_mariadb_concurrency.py`, `setUpClass()` currently calls `db_module.get_pool()` **before** changing `config.MYSQL_CONFIG["database"]`. It then creates a dynamic database through that already-initialized pool. The original configured DB is later restored and the pool is initialized again for cleanup.

This means the test harness can connect to the default database before it switches to the disposable database. The current “zero production DB access” statement is therefore not adequately demonstrated.

The test setup also contains this statement:

```python
cur.execute("UPDATE users SET is_active = 0 WHERE username = 'admin'")
```

Remove it. The concurrency tests must not alter a seeded/default admin account, even inside the authorized test database.

### Required correction

- Resolve `signal_bot_test` before importing/initializing DB pools.
- Do not create or drop a database as part of this suite.
- Assert `SELECT DATABASE() = 'signal_bot_test'` on the test pool before fixture setup and before each concurrency scenario if pool/config state could have changed.
- Seed only run-specific users with a unique prefix.
- Do not mutate any pre-existing user. Do not use the username `admin` as a fixture.
- Clean up only the exact fixture IDs created by this run, including associated audit rows.
- Ensure cleanup executes if setup, a worker, an assertion, or a DB operation fails.
- Ensure all worker futures finish or are cancelled safely before fixture cleanup.
- Reset both test hooks in `finally` and verify no worker remains blocked.
- Ensure the test pool and any secondary pool (including RetailBot2) both resolve to `signal_bot_test`; do not assume changing one config dictionary changes every pool.
- Restore process-local configuration after the test without creating a pool against `crypto_signals`.

### Concurrency assertions to retain and strengthen

For demotion, deactivation, and deletion:

- Capture the MariaDB connection/thread ID of the second request.
- Prove that exact ID enters `information_schema.innodb_trx.trx_state = 'LOCK WAIT'`.
- Never use future-not-done or generic lock-wait counts as a substitute.
- Release the first request only after the exact lock wait is observed.
- Assert exactly one success and one expected rejection.
- Assert at least one active admin remains.
- Assert the rejected operation did not change the target row or create an audit mutation.
- Assert a subsequent valid request can acquire a connection and complete.

Add an explicit failure-path test proving that a timeout/assertion failure still releases synchronization events, joins worker threads, resets hooks, and leaves the test DB usable.

---

## 4. P0 — Harden staging browser acceptance setup, execution, and cleanup

File: `tests/browser/staging_browser_acceptance.cjs`

### Finding A: cleanup does not cover setup failures

`setupDisposableDatabase()` currently runs before the `try/finally` block. If DB creation, migration, or account seeding fails after partially completing, the cleanup block is never entered.

**Required:** Put the entire lifecycle (preflight, DB verification, fixture setup, server startup, browser run, and cleanup) under one top-level `try/finally`. Cleanup must be safe when only some setup steps completed.

### Finding B: shell/Python source interpolation

The script builds Python source containing `STAGING_TEST_PASSWORD`, usernames, and database values, then invokes it through `execSync("python3 -c ...")`. Escaping double quotes is not sufficient shell escaping. A supplied password containing shell/Python metacharacters can break the command or become code injection.

**Required:**
- Do not embed secrets or environment values into generated Python source.
- Do not invoke a shell with interpolated script text.
- Use `spawn`/another argument-safe process interface and pass values through environment variables or stdin.
- Use parameterized SQL for values.
- Validate any SQL identifier with a strict allowlist/regex before interpolating it as an identifier.
- Never log passwords, tokens, or complete connection strings.

### Finding C: current “disposable DB” path violates the new test DB boundary

The current setup connects using the existing `MYSQL_CONFIG` to create a new database, and teardown connects using that same default configuration to drop it. The config may resolve to `crypto_signals` before the staging override is applied.

**Required:** Replace dynamic database creation/drop with the authorized `signal_bot_test` test database. Set the DB environment before app/config import, verify the actual app pool using `SELECT DATABASE()`, and clean only run-specific fixture rows. Never drop `signal_bot_test`.

### Finding D: readiness may accept an unrelated process

`waitForServer()` currently accepts any process returning HTTP 200 on the configured port. If another/stale server is already listening, the script can test that process rather than the child it just spawned.

**Required:**
- Fail if the configured port is already occupied before spawning.
- Handle child-process `error` and `exit` events.
- Confirm the spawned child remains alive during readiness.
- Add a deterministic test-run identity to the child environment and verify it through a test-only startup/readiness mechanism, or use an isolated ephemeral port selected and reserved by the harness.
- Never infer that the app is using `signal_bot_test` solely because the parent process passed an environment variable. Verify the exact pool used by the running app.

### Finding E: cleanup failures can still produce a successful test exit

`tearDownDisposableDatabase()` logs a drop failure and returns without failing the run. Process termination also sends SIGKILL after timeout without awaiting/recording the final child exit.

**Required:**
- Since the test DB is no longer dropped, cleanup must verify exact fixture cleanup and report any failure as a test failure.
- Close browser contexts and browser in all paths.
- Send SIGTERM, await child exit with a bounded timeout, then SIGKILL only as a last resort and await/record that exit too.
- If the child cannot be confirmed stopped, fail the run.
- Do not print the final PASS line until browser assertions and cleanup both succeed.

### Finding F: role assertions are too broad

Current checks rely partly on `page.content().includes(...)` / string absence. This does not prove a control is visible, enabled/disabled correctly, or inaccessible through the backend.

**Required:**
- Use role/label/test-id selectors and assert actual visibility for admin-only controls.
- For trader and viewer, assert the controls are absent from the rendered UI.
- Add direct API authorization checks for the protected endpoints (user create/update/delete/reset and Telegram test). Verify unauthenticated, viewer, and trader requests receive the contractually expected 401/403; verify admin access only where the endpoint is read-only/safe.
- Never call `POST /api/telegram/test` in acceptance. Test authorization with a mocked notifier or dependency override so no real Telegram request can be sent.
- Do not invoke any paper-trading, backfill, ML deployment, screener worker, wallet tracking, or other operational mutation in browser acceptance.
- Maintain narrow documented allowlists for expected HTTP statuses; all other unexpected 4xx/5xx and failed API requests must fail the run.

### Finding G: view checks do not prove all workflows

The 11 view selector checks prove that a panel rendered, not that each workflow in the parity worksheet works end-to-end. Keep this suite as navigation/rendering/RBAC smoke acceptance. Add focused workflow tests for each migrated mutation separately.

---

## 5. P0 — Reassess RetailBot2 pool-size increase

File: `db/db.py`

The reviewed code sets `retailbot2_pool` to `pool_size=10`. The report argues that MariaDB `max_connections=500` means this uses 2% of server capacity. That arithmetic does not establish safe capacity: other application pools, worker processes, services, and concurrent users also consume connections. A four-request `Promise.all` is not by itself evidence that a pool of ten is necessary.

**Required action:**
1. Preferred: revert the RetailBot2 pool increase to the previous value unless reproducible evidence demonstrates exhaustion.
2. If retaining it, provide a repeatable concurrent-load test that:
   - reproduces the original PoolError at the previous pool size;
   - measures active/peak connections and queue/wait behavior;
   - includes all relevant application pools/processes in the connection budget;
   - demonstrates no leaked connections under success and exception paths;
   - compares the previous and proposed pool sizes under the same workload;
   - documents why the selected size is the minimum justified value.
3. Do not change MariaDB server settings.
4. Do not claim “2% of max_connections” as sufficient capacity justification.

The acceptance report must reflect the actual decision and evidence, not the desired conclusion.

---

## 6. P0 — Migration and index verification

The new active-admin index migration must be verified on `signal_bot_test`, not production.

- Confirm the migration is registered exactly once and follows the repository migration ordering/conventions.
- Confirm the migration is safe/idempotent under the project's migration runner.
- Run it against `signal_bot_test` and capture the schema version/result.
- Capture `SHOW INDEX FROM users` and `EXPLAIN` for the exact active-admin locking query.
- Confirm the chosen index is actually selected by the optimizer on the test schema and representative test data.
- Do not claim that an index eliminates locks on unrelated rows without measured query-plan/locking evidence.
- Confirm rollback behavior and test behavior when the migration has already been applied.
- No schema changes or queries against `crypto_signals`.

---

## 7. P1 — Finish the remaining legacy-only modern UI workflows

After all P0 corrections are implemented and locally validated, continue the migration. Do not stop at the fact that all 11 navigation panels render.

### Required audit

Read these sources before changing UI:
- `docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`
- `docs/UIX_LEGACY_TO_MODERN_PARITY.md`
- `docs/ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md`
- Legacy workflows in `web/static/index.html` and `web/static/js/app.js`
- Existing FastAPI routes, schemas, permission checks, and frontend API clients.

Build a complete list of every row still marked `LEGACY_ONLY`, `PARTIAL`, or `BLOCKED`. Do not invent a new count; calculate it from the current worksheet and update it as work progresses.

### Implementation rules

For each workflow:
1. Identify the exact legacy control and user outcome.
2. Identify the existing API method/path, request/response schema, and backend authorization.
3. Reuse the existing backend contract where correct. Do not create duplicate endpoints just to fit the UI.
4. Implement the React control, validation, loading, success/error feedback, confirmation, and server-state refresh.
5. Apply role permissions in the UI and enforce them on the backend. Hiding a button is not authorization.
6. Add unit/component tests for the control and API client.
7. Add backend contract/RBAC tests where the workflow is protected.
8. Add browser tests for the user-visible workflow using mocked API responses where appropriate.
9. Add a separate test-database integration test for persistence/side effects where required.
10. Update the worksheet row with exact evidence and status. Mark COMPLETE only after end-to-end evidence exists.

### Operational safety

Some legacy-only workflows may deploy paper bots, close positions, cancel orders, reset circuit breakers, deploy ML models, or start/stop research or wallet workers.

- Implement and test the code paths, but do not execute real operational mutations against production.
- Use `signal_bot_test` with uniquely prefixed fixtures and explicit cleanup.
- Mock external exchanges, Telegram, blockchain RPCs, and background workers in browser/unit tests.
- For destructive or irreversible actions, require a confirmation dialog that states the exact target/action.
- Do not automatically enable strategies, start workers, promote models, or send alerts as part of migration acceptance.
- Do not change default Telegram delivery or strategy enablement settings.

### UX requirements

- Preserve current modern UI visual language, dark/light themes, responsive behavior, keyboard access, and accessible labels.
- Ensure tables remain usable on narrow screens.
- Avoid adding controls that merely duplicate a legacy feature without matching its behavior.
- Do not regress existing modern workflows.

---

## 8. Required validation and evidence

GitHub Actions quota is unavailable. Do not wait for Actions or claim CI passed.

Run the relevant tests locally after each focused change. At the end, run the full backend and frontend suites plus the real-FastAPI staging acceptance. All DB-backed tests must prove `SELECT DATABASE() = 'signal_bot_test'`.

For every command, record:
- exact command;
- exit code;
- pass/fail/skip counts;
- duration;
- runtime versions;
- exact commit SHA tested;
- whether run by Antigravity or independently rerun.

Separate:
- mocked frontend Playwright;
- real backend browser acceptance;
- MariaDB integration/concurrency tests;
- full backend regression.

Include explicit negative evidence:
- no connection to `crypto_signals`;
- no mutation of pre-existing `signal_bot_test` users;
- no real Telegram request;
- no trading/paper-trading operation;
- no production service restart/deployment.

Do not state “zero DB mutation” when the test database was intentionally written. State precisely that writes were confined to run-specific test fixtures in `signal_bot_test`, and that no production database was accessed.

---

## 9. Documentation updates

Update:
- `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md`
- `docs/ANTIGRAVITY_UI_PARITY_AUDIT_WORKSHEET.md`
- `docs/UIX_LEGACY_TO_MODERN_PARITY.md`

Required:
- exact final tested SHA;
- corrected database statement naming `signal_bot_test` as the authorized test-only DB;
- exact DB guard output proving the app pool resolved to `signal_bot_test`;
- test commands, exit codes, counts, durations, versions;
- actual concurrency thread IDs and observed lock-wait states;
- pool-size decision and reproducible evidence;
- each remaining workflow's accurate status;
- remaining limitations and unverified areas;
- clear distinction between implemented, locally tested, independently rerun, and not verified.

Do not claim full parity while any required workflow remains legacy-only or partial.

---

## 10. Git and PR requirements

- Continue on `fix/ui-migration-review-remediation` unless a separate branch is required to keep review scope manageable.
- PR #697 must remain open and unmerged.
- Before committing, inspect `git status --short`, `git diff --check`, `git diff --stat`, and the complete diff.
- Commit only intended files. Do not use an unreviewed `git add .`.
- Push the final commit and report the exact SHA.
- Keep the P0 safety corrections logically separate from P1 UI workflow implementation if the changes become too broad; use a separate PR for the feature-migration phase if needed.
- Do not merge, deploy, restart services, switch the root dashboard, or send real notifications.

## 11. Definition of done

### P0 — required before independent review of PR #697
- [ ] No test or staging code connects to `crypto_signals`.
- [ ] All DB-backed tests prove the actual app/test pool resolves to `signal_bot_test`.
- [ ] No pre-existing test DB users are altered; only run-specific fixtures are cleaned up.
- [ ] Concurrency tests still prove exact second-transaction `LOCK WAIT` for all three scenarios.
- [ ] Failure paths release workers, reset hooks, and clean up fixtures.
- [ ] Staging setup is shell-safe and secrets are not interpolated into source.
- [ ] Staging readiness verifies the child process, not an unrelated listener.
- [ ] Staging cleanup failures fail the run.
- [ ] RBAC assertions verify actual visible controls and direct backend authorization without sending Telegram or invoking operational actions.
- [ ] RetailBot2 pool increase is reverted or justified by reproducible load evidence and a complete connection budget.
- [ ] Index migration/query-plan evidence is recorded from `signal_bot_test`.
- [ ] Acceptance docs match the code and exact tested SHA.

### P1 — modern UI parity
- [ ] Every `LEGACY_ONLY`, `PARTIAL`, and `BLOCKED` workflow is listed and dispositioned.
- [ ] Required workflows are implemented in the modern React UI with matching backend contracts and RBAC.
- [ ] Each migrated workflow has appropriate frontend, backend-contract, browser, and DB integration evidence.
- [ ] The parity worksheet and acceptance report are updated without overstating completion.

### Final stop condition
Push the work and report exact evidence. **Stop before merge or deployment.** Wait for independent review and explicit owner authorization.


## 12. PR mergeability check

At the time this work order was created, the GitHub PR metadata reported PR #697 as open and unmerged, but `mergeable=false`. Before pushing further commits:

1. Fetch the latest `master` and PR head.
2. Identify whether the false mergeability state represents an actual content conflict or a transient GitHub calculation.
3. If conflicts exist, resolve them on the working branch without force-pushing over unreviewed work.
4. Re-run all affected tests after conflict resolution and update the exact tested SHA in the report.
5. Report the resulting PR mergeability state. Do not merge the PR.
