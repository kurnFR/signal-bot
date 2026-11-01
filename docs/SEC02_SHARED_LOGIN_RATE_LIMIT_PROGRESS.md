# SEC-02 — Shared Login Rate-Limit Remediation

**Date:** 2026-10-31  
**Repository:** `kurnFR/signal-bot`  
**Branch:** `fix/security-shared-login-rate-limit`  
**Pull request:** [#606](https://github.com/kurnFR/signal-bot/pull/606)  
**Status:** Implementation and automated acceptance complete; PR remains draft pending final diff review and explicit release approval.

## Finding confirmed on master

The durable limiter in `web/auth.py` fell back to the process-local `_login_attempts` dictionary when the shared `login_attempts` database table could not be read or written. Startup also disabled durable mode when its availability probe failed. In a multi-worker deployment, separate local dictionaries do not enforce one shared attempt count.

## Remediation implemented

- [x] Durable mode remains required after the application enables it, including when the startup probe fails.
- [x] Startup probe failure leaves the limiter in `unavailable` health mode.
- [x] Runtime counter-read failure raises `RateLimitBackendUnavailable`; no memory fallback.
- [x] Failed-attempt persistence failure raises `RateLimitBackendUnavailable`; no memory fallback.
- [x] Successful-login counter-reset failure prevents token issuance and returns HTTP 503.
- [x] Login endpoint maps shared limiter unavailability to a generic HTTP 503 response.
- [x] Health helper distinguishes `in-memory` (unit/tool mode), `database`, and `unavailable`.
- [x] Security documentation no longer promises an in-memory fallback.
- [x] Regression tests authored for startup failure, runtime check/write/reset failure, malformed count result, recovery, API 503, and separate-process MariaDB enforcement.
- [x] Per-key MariaDB advisory-lock admission with atomic count-and-reserve before password verification.
- [x] Unit coverage for successful reservation, threshold denial, and lock timeout fail-closed behavior.
- [x] Opt-in two-process contention integration test: two independent workers each issue five concurrent admissions against the same key; expected total admitted equals the configured maximum.
- [x] Added the missing pinned Gymnasium dependency after CI test collection exposed its absence.

## Acceptance checklist

- [x] Focused auth, durable-state, and status endpoint tests passed on the final PR head (user-confirmed).
- [x] Full pytest suite passed on the final PR head (user-confirmed; CI: 810 passed, 16 skipped).
- [x] RetailBot accounting regression passed on the final PR head (CI: 36 accounting/parity tests passed; regression workflow successful).
- [x] Full opt-in MariaDB integration passed in isolated CI database (35 passed); production `crypto_signals` was not accessed.
- [x] Cross-process contention and sibling-reservation interleaving tests passed in MariaDB CI.
- [x] Shared-storage recovery and fail-closed behavior covered by passing unit/API/integration tests.
- [x] Public authenticated `/api/system/status` and admin `/api/settings/status` now expose sanitized `loginRateLimiter.mode` and `degraded` state.
- [x] All required GitHub Actions checks green on final PR head `fee81622444145f8e3540fef1a30eb8ab3d9d881`.
- [ ] Review final diff and obtain explicit release approval before merge.

## Code-review notes — 2026-11-01

- Startup wiring was inspected: `web.app.lifespan` calls `enable_durable_rate_limit()` before yielding control to serve requests. If its DB probe fails, the durable-required flag remains enabled and login requests fail closed.
- Migration registration was inspected: migration `013_login_attempts` creates the shared table with a `(rate_key, attempted_at)` index. The table stores epoch timestamps as `DOUBLE`, matching the current query parameters.
- Login route handling was inspected: limiter admission, failed-attempt write, and successful-login reset failures map to generic HTTP 503 responses. Token creation occurs only after a successful shared reset.
- The count-check/record-after-password flow had a concurrency race. The current branch replaces the login route's pre-check with `admit_login_attempt()`: it takes a per-key MariaDB advisory lock, counts unexpired attempts, and inserts a reservation before password verification. The lock is explicitly released and release failure is treated as unavailable. Cross-process MariaDB contention acceptance remains required.
- The MariaDB integration test is opt-in only when `WALLET_TRACKER_MARIADB_TESTS=1`. Before running it, explicitly confirm that the configured database is isolated `signal_bot_test`, not production `crypto_signals`.
- No repository runtime or SSH/remote-shell connection is available in this review session. No local pytest or MariaDB command has been executed here.

## Local test evidence — user-reported, 2026-11-01

- Focused batch after atomic admission: 28 passed, 5 skipped (1.81s).
- MariaDB batch: 5 passed (2.50s); exact test selection and database target still need confirmation before treating this as the full isolated integration acceptance.
- Full suite: 805 passed, 15 skipped, 1 failed (8.82s). The failure was `test_recent_closed_trade_rows_respect_strategy_report_visibility`; production code correctly executes one joined SELECT, while the test expected a separate schema-creation query.
- The assertion-only test correction was committed in `0221f2bd6dbc557c9c12c0d254e8ebc4c99259da`. The corrected isolated test and full suite have not yet been rerun by the user.

## CI evidence

On PR head `a0cfe5acc70da38dce249d60edbdeb210a55d195`:

- `tests` workflow: success.
- `RetailBot accounting regression` workflow: success.
- `Test` workflow: failed during test collection, before test execution. `tests/test_ai_advanced_features.py` imports `ml.rl.environment`, which imports `gymnasium`; CI failed with `ModuleNotFoundError: No module named 'gymnasium'`.
- The CI log was retrieved and the failure cause is confirmed. This was a missing declared project dependency, not a failure in the SEC-02 test assertions.

Fix committed on 2026-11-01:

- Commit `be7306baec5916b4e54a50008c366e606848011b` adds `gymnasium==1.0.0` to `requirements.txt`.
- CI has not yet been verified on this new head. Recheck all workflows after the dependency change.

## Guardrails observed

- No production database was accessed or modified.
- No live deployment or production worker was started.
- No live trade or Telegram notification was triggered.
- PR #606 remains draft and unmerged.


## Additional concurrency review — 2026-11-01

A second-order race was found after introducing pre-verification reservations: the previous successful-login reset deleted every row for a rate key, including reservations belonging to concurrent requests that had not yet completed password verification. A sibling bad-password request could therefore disappear from the limiter count.

The branch now distinguishes reservation lifecycle states:

- Migration `019_login_attempt_state` adds `attempt_state` with existing rows defaulting to `failed`; new admission rows are `pending`.
- A failed password converts only its own pending row to `failed`.
- A successful password deletes completed `failed` rows and only its own pending row. Other concurrent `pending` rows remain counted.
- Admission and finalization use the same per-key MariaDB advisory lock, serializing state transitions across workers.
- The login route uses `finalize_login_attempt()` for durable requests. The legacy full reset remains only for in-memory mode and explicit test/maintenance cleanup.
- Added unit tests for preserving sibling pending reservations and converting only the request's own reservation; added an opt-in MariaDB interleaving test and an API route regression test.

**Verification required:** apply migration 019 to the isolated test database before running the MariaDB integration class. No test result yet verifies this latest lifecycle implementation. The current PR-head GitHub Actions runs have started; their final results are pending.


## CI verification — 2026-11-01, head `30c9f13369a30a860ef37039d95fec30c8f51871`

The following GitHub Actions checks completed successfully on the code immediately before the final startup-schema probe refinement:

- **Test** workflow run 4439: full suite **809 passed, 16 skipped, 6 warnings** (3.69s). The job used an ephemeral MariaDB service, applied all migrations, and seeded an ephemeral admin identity for authenticated API tests.
- **tests** workflow run 1644: unit-tests job passed; MariaDB integration job passed with **35 passed** (3.21s), including the durable login integration class and the new reservation interleaving test.
- **RetailBot accounting regression** workflow run 1637: passed.

The earlier CI failures were addressed:
- Missing `gymnasium` was declared in `requirements.txt`.
- Full-suite CI now has an isolated MariaDB service and applies migrations before tests.
- Auth API tests now have a disposable admin row in the test database.
- The RetailBot DB-config test explicitly sets `MYSQL_DATABASE=crypto_signals`, so it tests the intended config behavior while the CI suite itself uses `signal_bot_test`.
- The MariaDB integration job installs the complete project requirements, including FastAPI.

## Final startup-schema guard — 2026-11-01

After the green run above, the startup health probe was tightened to query `attempt_state` rather than merely checking that `login_attempts` exists. This makes startup report `unavailable` if migration 019 has not been applied, instead of reporting a healthy database backend that would fail on the first reservation insert. A unit regression test covers the missing-column case.

This final probe refinement is committed on head `6e51fc98eedb7604ebe20de1fb7f12fe5045b25c`; **the green CI results above do not yet verify this final commit**. Wait for the new head's workflows before considering SEC-02 CI acceptance complete.

## Remaining release gates

- [ ] Confirm all required workflows green on final head `6e51fc9` or its descendant.
- [ ] Confirm the current final diff, including migration 019 and workflow changes.
- [ ] Confirm operational monitoring exposes the limiter `unavailable` state; the helper alone is not yet a confirmed external health signal.
- [ ] Obtain explicit release approval before merge. PR #606 remains draft and unmerged.
- [ ] Local verification on the user's machine remains outstanding; CI is isolated and does not touch production.


## Final-head CI acceptance — 2026-11-01, head `4a718877df922329c393dcacac3d9e2636d5530a`

The startup-schema probe refinement and its regression test passed the complete CI matrix:

- **Test** workflow run 4445: **810 passed, 16 skipped, 6 warnings** (3.87s).
- **tests** workflow run 1647:
  - Unit-tests job: success.
  - MariaDB integration job: **35 passed** (3.70s), including the opt-in durable login tests, cross-process contention, and successful-login/sibling-pending interleaving.
- **RetailBot accounting regression** workflow run 1640: success.

The full-suite workflow applied migrations and seeded only its ephemeral `signal_bot_test` database. The integration workflow applied migrations to its ephemeral `wallet_tracker_test` database. No production database or deployment was involved.

The final startup probe verifies the `attempt_state` column, and the missing-migration unit test passed within the 810-test full suite. CI acceptance is green on head `4a71887`.

Remaining before release:
- Local rerun on the user's machine is recommended but not yet reported.
- Confirm the external monitoring path exposes the helper's `unavailable` state; the helper alone is not a confirmed external health signal.
- Complete final diff review and obtain explicit release approval. PR #606 remains draft and unmerged.


## Final verification — 2026-11-01, head `fee81622444145f8e3540fef1a30eb8ab3d9d881`

User confirmed that all local tests passed after updating to the current branch head. Exact local per-suite counts were not supplied, so this records the confirmation without inventing totals.

GitHub Actions independently completed successfully on this exact head:

- **Test** workflow run 4455: full suite **810 passed, 16 skipped, 6 warnings** (5.58s).
- **tests** workflow run 1651:
  - Unit-tests job: success; screener/wallet batch **105 passed**.
  - MariaDB integration job: **35 passed** (4.85s), including `TestDurableStateOnMariaDB`, cross-process login contention, and sibling-reservation interleaving.
- **Frontend foundation** workflow run 366: **27 passed** (33.2s).
- **RetailBot accounting regression** workflow run 1644: paper accounting/parity batch **36 passed**; screener regression batch **27 passed**; job success.

The public authenticated status endpoint now exposes only the limiter mode and degraded boolean, not DB connection details or raw backend errors. The regression test asserts that the unavailable state is visible. Frontend status typing accepts and validates this optional telemetry.

All test and CI acceptance gates are green. Remaining release gate is a final human diff review and explicit approval to merge. PR #606 remains draft and unmerged. No deployment, production DB access, live workers, trades, or Telegram sends were performed.
