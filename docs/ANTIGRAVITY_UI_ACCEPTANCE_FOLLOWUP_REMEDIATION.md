# Antigravity Follow-up Work Order — UI Migration Acceptance Corrections

**Repository:** `kurnFR/signal-bot`  
**Working branch:** `fix/ui-migration-review-remediation`  
**Reviewed starting commit:** `4e3cd21c5b19dd0b87313de7b96f600982ef4100`  
**Parent commit:** `c4c97f38445755d2daf577c57acbbcd13e835ab3`

## 1. Objective and boundaries

Complete the four remaining implementation/evidence corrections and the GitHub PR tracking correction identified in the independent review. Keep this focused; do not redo the migration or add unrelated refactors.

GitHub Actions is unavailable because the Actions quota is exhausted. Do not depend on GitHub Actions, wait for CI, or claim that CI passed. Validate locally and preserve reproducible evidence tied to the exact final commit SHA.

### Hard safety boundaries

- Do not deploy, restart, or modify production services.
- Do not change the production dashboard default. Legacy remains at `/`; modern remains a preview at `/modern`.
- Do not connect to or modify production database `crypto_signals`.
- Do not use the previously designated `signal_bot_test` database. Use a uniquely named disposable database for this work, e.g. `signal_bot_ui_acceptance_<run-id>`, after verifying the application's resolved DB configuration.
- Do not send real Telegram messages.
- Do not trigger trading, paper-trading, strategy deployment, wallet-tracking, or other operational mutations.
- Do not commit credentials, tokens, passwords, or connection strings.
- Do not merge any PR.

## 2. Finding A — Prove actual MariaDB lock contention

### Current defect

In `tests/test_user_management_mariadb_concurrency.py`, the observation currently accepts:

```python
if self._check_innodb_lock_wait() > 0 or not f2.done():
    observed_lock_wait.append(True)
    break
```

`not f2.done()` only proves that the future has not completed. It does not prove MariaDB placed the second request's transaction in `LOCK WAIT`. The current test can pass without observing lock contention. Also, `_check_innodb_lock_wait()` counts all waiting transactions rather than identifying the second request's transaction.

### Required correction

Fix all three scenarios:

1. Concurrent demotion of the last two active administrators.
2. Concurrent deactivation of the last two active administrators.
3. Concurrent deletion of the last two active administrators.

The test must identify the DB transaction/connection belonging to the second request and verify that **that exact transaction** enters MariaDB's `LOCK WAIT` state while the first request holds the relevant locks.

Suggested deterministic design:

1. Thread 1 starts, acquires the active-admin row locks, and pauses at the existing test-only synchronization hook.
2. Thread 2 starts using a separate DB connection.
3. Capture Thread 2's MariaDB connection/thread ID immediately before it executes the locking query, using narrowly scoped test-only instrumentation.
4. Poll transaction metadata for that exact connection/thread ID. Require `trx_state = 'LOCK WAIT'`.
5. If the expected lock wait is not observed before a bounded timeout, fail. Never fall back to checking whether the future is unfinished.
6. Only after observing the exact lock wait, release Thread 1.
7. Wait for both requests and assert HTTP responses and final DB state.

Use metadata supported by the actual MariaDB test-server version. Confirm the available columns in `information_schema.innodb_trx`; do not assume Performance Schema tables exist without checking. The second transaction must be distinguished from unrelated transactions.

### Required assertions

For each scenario:

- Exactly one request succeeds with the expected status.
- The other request is rejected with the existing last-active-administrator protection response.
- At least one active administrator remains.
- Final roles, active flags, and deletion state are correct.
- A rejected operation leaves no unintended user or audit-log mutation.
- The test fails if the exact second transaction never enters `LOCK WAIT`.

### Test hygiene

- Put test-hook setup/reset in `try/finally`; reset global hooks even after assertion/request failure.
- Release synchronization events in cleanup paths so a failed assertion cannot strand a worker.
- Ensure each request uses its own DB connection.
- Keep synchronization test-only. Do not add production sleeps or arbitrary delays to request paths.
- Do not weaken/remove the actual `SELECT ... FOR UPDATE` protection.

Record for each scenario: second request's DB thread ID, exact `LOCK WAIT` observation, both HTTP statuses, final active-admin count/user states, command, and exit code.

## 3. Finding B — Make staging browser acceptance fail reliably

### Current defect

In `tests/browser/staging_browser_acceptance.cjs`, some checks only log success when a condition is present but do not fail when it is absent. HTTP 4xx/5xx responses, console errors, and failed requests are logged/warned about without necessarily causing a nonzero exit code.

The script can therefore print a successful acceptance message despite failed checks.

### Required correction

Make every acceptance check an executable assertion:

**Authentication and protected routes**
- Unauthenticated users see the authentication gate.
- Unauthenticated users do not see protected navigation/content.
- Sign-in and sign-out complete successfully for each role.

**Admin**
- Assert the intended admin-only user-management controls are present.
- Assert the expected admin Telegram settings/status controls match the backend authorization contract.
- Never click a Telegram test/send action.

**Trader**
- Assert admin-only user-management controls are absent.
- Assert admin-only Telegram test/send controls are absent.
- Assert expected trader-accessible views/actions remain available.

**Viewer**
- Assert admin-only user-management controls are absent.
- Assert admin-only Telegram test/send controls are absent.
- Assert unauthorized editable controls are absent.
- Assert expected read-only views remain available.

**Navigation/rendering**
- Assert every claimed view actually rendered, rather than clicking and waiting a fixed duration.
- Prefer stable selectors and explicit visibility/state assertions over `waitForTimeout()`.
- Assert an expected heading or stable content marker after navigation.

**Browser/network health**
- Collect console errors, uncaught page errors, failed requests, and unexpected HTTP error responses.
- Fail on unexpected console errors, uncaught page errors, failed API requests, and unexpected HTTP 4xx/5xx.
- If a response is intentionally expected (e.g. unauthenticated protected API), document and narrowly allowlist its exact method/path/status. Do not globally ignore 4xx/5xx.
- Ignore only understood browser cancellations such as intentional `net::ERR_ABORTED`; do not suppress unrelated failures.

A log line is not an assertion. Any failed assertion must make the script exit nonzero. Print the final success message only after every assertion passes and cleanup completes.

## 4. Finding C — Make staging setup and cleanup safe/reproducible

### Current defect

The browser script uses `admin`, `staging_trader`, and `staging_viewer` accounts but does not demonstrate restoration of pre-existing account state or reliable deletion of temporary accounts. Process cleanup uses unconditional `SIGKILL`, without graceful shutdown and exit verification.

The script names `signal_bot_test` as its DB. Do not reuse that database.

### Required correction

Use a uniquely named disposable MariaDB database created specifically for this acceptance run. It must be separate from production and `signal_bot_test`.

The script must verify the **resolved database used by the running application**, not merely assume one environment variable overrides all configuration. It must:

1. Validate required environment/configuration before starting the app.
2. Refuse to run if the resolved DB name is `crypto_signals` or `signal_bot_test`.
3. Verify it is the intended disposable DB before creating/changing users.
4. Create dedicated run-specific staging users only in that DB.
5. Never overwrite a pre-existing account; fail safely or use unique run-specific usernames.
6. Use test credentials supplied through environment variables or a documented local fixture. Do not hardcode reusable passwords.
7. Never print passwords, tokens, or secrets.
8. Run browser assertions.
9. In `finally`, close the browser, gracefully terminate the app process, wait for exit, and force-kill only after a documented timeout.
10. Remove temporary test data/accounts or drop the disposable DB only after verifying it is the run-specific test DB and never production.
11. Verify the app process stopped and record cleanup outcome.

If startup writes normal authentication, rate-limit, session, or audit records, describe those as normal application writes. Do not claim zero DB writes.

Do not change the password, role, or active state of a shared/default admin account. If a seeded admin is required, create it in the disposable DB only.

### Process cleanup

Use graceful termination first (e.g. SIGTERM), wait with a bounded timeout, and verify process exit. Force-kill only as a last resort. Ensure browser closure and process termination happen even when an assertion throws.

## 5. Finding D — Remove or justify the unrelated RetailBot2 pool-size change

The reviewed commit changes `retailbot2_pool` from `pool_size=3` to `pool_size=10` in `db/db.py`. This is unrelated to UI migration acceptance. A claim that it fixes PoolError is not enough evidence to increase DB connection capacity.

**Preferred action:** Revert this pool-size change in the follow-up unless there is independently reproducible evidence that it is necessary.

If retaining it, document:

- Exact PoolError and stack trace.
- Which pool is exhausted and under what workload.
- Measured concurrent connection demand.
- MariaDB configured connection capacity and other pools' connection allocations.
- Why 10 is appropriate rather than arbitrary.
- Reproducible test/load evidence before and after.

Do not change production DB settings. Avoid speculative pool, timeout, or connection-management changes.

## 6. GitHub PR/branch tracking correction

The independent review found PR #696 is titled **“docs: add independent review of UI remediation”** and describes a documentation-only review. It is not the code-remediation PR described in the report.

1. Inspect open PRs and identify the actual PR containing `fix/ui-migration-review-remediation`.
2. If a code-remediation PR exists, correct the report/work-order references to its actual number and URL.
3. If none exists, create a PR from `fix/ui-migration-review-remediation` to the intended base branch with an accurate title/body.
4. Do not repurpose PR #696 or change its purpose.
5. Do not merge the code PR.

Report the exact branch, base branch, PR number, and URL.

## 7. Local validation — no GitHub Actions dependency

Inspect repository scripts/configuration before selecting commands. Run at least:

```bash
PYTHONPATH=$PWD python -m pytest -q tests/test_user_management_mariadb_concurrency.py
PYTHONPATH=$PWD python -m pytest -q tests/test_user_management_security.py
PYTHONPATH=$PWD python -m pytest -q
```

Run the staging browser script against the dedicated disposable DB. Run frontend typecheck, unit tests, build, and existing Playwright suite using the actual scripts in `frontend/package.json`. Report mocked Playwright tests separately from real-FastAPI-backend browser acceptance.

For migration/index work, verify schema and query plan on the disposable DB. EXPLAIN does not replace the concurrency lock-wait test.

Do not use `signal_bot_test` or `crypto_signals` for these tests.

For every test record:
- Exact command and exit code.
- Passed/failed/skipped/deselected counts where applicable.
- Relevant output or tracked log artifact.
- Environment/dependency versions.
- Exact commit SHA tested.

Label results accurately: **Run by Antigravity**, **Independently rerun**, or **Not verified**. Do not copy old counts as new results. If code changes after testing, rerun relevant tests against the new SHA.

## 8. Required documentation updates

Update `docs/UIX_FINAL_MIGRATION_ACCEPTANCE.md` with:

- Exact final tested commit SHA.
- Local environment and dependency versions.
- Exact commands and exit codes.
- Exact MariaDB lock-wait evidence for each concurrency scenario.
- Browser assertion outcomes and any failures.
- Redacted staging DB identity, confirming it was disposable and not production or `signal_bot_test`.
- Before/after query-plan evidence where relevant.
- Pool-size decision and rationale.
- Actual code PR number and URL.
- Remaining limitations/unverified items.

Do not describe the migration as production-ready solely because tests pass. Preserve parity accounting; do not imply legacy-only workflows have been migrated.

## 9. Final review and push

Before committing:

```bash
git status --short
git diff --check
git diff --stat
git diff
```

Inspect every changed file. Confirm no secrets, unrelated refactors, production configuration changes, real Telegram sends, or trading operations are included.

Commit only intended files; do not use `git add .` without reviewing the working tree.

Then push:

```bash
git push origin fix/ui-migration-review-remediation
git rev-parse HEAD
git log -1 --oneline
git status --short
```

Report final commit SHA and actual code PR URL. Do not merge or deploy.

## 10. Definition of done

- [ ] All three concurrency tests observe the **specific second transaction** in MariaDB `LOCK WAIT`; no future-not-done fallback remains.
- [ ] Test hooks and synchronization resources are always cleaned up.
- [ ] Browser acceptance uses real assertions and exits nonzero on unexpected failures.
- [ ] Browser/API errors are not merely logged and ignored.
- [ ] Staging users and DB are isolated, disposable, and safely cleaned up.
- [ ] Script refuses production and the prohibited `signal_bot_test` DB.
- [ ] Unrelated pool-size increase is reverted or justified with reproducible evidence.
- [ ] Correct code-remediation PR is identified or created; PR #696 is not misrepresented.
- [ ] Local evidence is reproducible and tied to exact final SHA.
- [ ] Documentation accurately states what was tested and what remains unverified.
- [ ] No production deployment, DB mutation, Telegram delivery, trading action, merge, or dashboard cutover occurred.

**Stop after pushing and reporting the evidence. Wait for independent review and explicit approval before merge or deployment.**
