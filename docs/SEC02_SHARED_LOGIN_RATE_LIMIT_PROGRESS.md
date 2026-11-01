# SEC-02 — Shared Login Rate-Limit Remediation

**Date:** 2026-10-31  
**Repository:** `kurnFR/signal-bot`  
**Branch:** `fix/security-shared-login-rate-limit`  
**Pull request:** [#606](https://github.com/kurnFR/signal-bot/pull/606)  
**Status:** In progress — do not merge until required CI and isolated MariaDB acceptance pass.

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

- [ ] Rerun focused auth and durable-state unit tests on the final PR head.
- [ ] Rerun full pytest suite on the final PR head.
- [ ] Run RetailBot accounting regression on the final PR head.
- [ ] Run the full opt-in MariaDB integration class against isolated `signal_bot_test`, never production `crypto_signals`.
- [ ] Verify separate-process worker B enforces attempts recorded by worker A.
- [ ] Verify shared-storage recovery restores login checks and health state.
- [ ] Confirm operational monitoring can observe limiter unavailable state; helper-only health reporting is not yet a confirmed external health signal.
- [ ] All required GitHub Actions checks green on the final PR head.
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
