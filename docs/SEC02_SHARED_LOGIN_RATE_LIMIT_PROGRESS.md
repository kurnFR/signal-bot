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

## Acceptance checklist

- [ ] Run focused auth and durable-state unit tests locally.
- [ ] Run full pytest suite.
- [ ] Run RetailBot accounting regression.
- [ ] Run opt-in MariaDB integration suite against an isolated test database.
- [ ] Verify separate-process worker B enforces attempts recorded by worker A.
- [ ] Verify shared-storage recovery restores login checks and health state.
- [x] Implement per-key MariaDB advisory-lock admission with atomic count-and-reserve before password verification.
- [x] Add unit coverage for successful reservation, threshold denial, and lock timeout fail-closed behavior.
- [x] Add an opt-in two-process contention integration test; it starts two independent workers, each issuing five concurrent admissions against the same key, and expects exactly the configured maximum to be admitted.
- [ ] Run the new focused tests and the full local suite after the atomic-admission change.
- [ ] Run the expanded MariaDB integration class, including the new two-process contention test.
- [ ] Confirm operational monitoring can observe limiter unavailable state; helper-only health reporting is not yet a confirmed external health signal.
- [ ] All required GitHub Actions checks green on the final PR head.
- [ ] Review final diff and obtain explicit release approval before merge.

## Code-review notes — 2026-11-01

- Startup wiring was inspected: `web.app.lifespan` calls `enable_durable_rate_limit()` before yielding control to serve requests. If its DB probe fails, the durable-required flag remains enabled and login requests fail closed.
- Migration registration was inspected: migration `013_login_attempts` creates the shared table with a `(rate_key, attempted_at)` index. The table stores epoch timestamps as `DOUBLE`, matching the current query parameters.
- Login route handling was inspected: limiter read, failed-attempt write, and successful-login reset failures map to generic HTTP 503 responses. Token creation occurs only after a successful shared reset.
- The original count-check/record-after-password flow had a concurrency race. The current branch replaces the login route's pre-check with `admit_login_attempt()`: it takes a per-key MariaDB advisory lock, counts unexpired attempts, and inserts a reservation before password verification. The lock is explicitly released and release failure is treated as unavailable. This design still requires the newly added cross-process contention test to pass against MariaDB before acceptance.
- The current MariaDB integration test is opt-in only when `WALLET_TRACKER_MARIADB_TESTS=1`. It inherits the process environment, so before running it, explicitly confirm that the configured database is the isolated `signal_bot_test` database and not production `crypto_signals`.
- No repository runtime or SSH/remote-shell connection is available in this review session. Therefore no local pytest or MariaDB command has been executed here; tests remain authored, not verified.

## Local test evidence — user-reported, before atomic-admission changes

The user reported the following successful run on the previous branch state:

- Focused auth/durable-state files: 20 passed, 2 skipped, one non-critical Starlette/AnyIO deprecation warning (2.91s).
- MariaDB-filtered run: 2 passed, 7 deselected (0.40s).

These results predate the atomic-admission implementation and do **not** verify the current branch head. The full MariaDB integration class must be rerun after the new contention test is included.

## CI evidence

Two PR-head commits were checked during this work:

- Commit `52c16a7ee6bb4b2aaa93d563b245ae26fef9edad`: Test, tests, and RetailBot accounting regression workflows failed.
- Commit `9466ff78edfff7e48321bd50f8fc9362cce438a9`: the same three workflows failed again.

For the later run set:

- Test: run `41208345410`, job `124557941461` — failed.
- tests: run `41208345395`, jobs `124557941258` and `124557941447` — failed.
- RetailBot accounting regression: run `41208345405`, job `124557941319` — failed.

GitHub returned `404 BlobNotFound` when downloading each job log, and the job-step summaries were empty. The failure cause is therefore **unknown**. Do not classify these failures as code, environment, or workflow failures until usable logs are obtained.

## Guardrails observed

- No production database was accessed or modified.
- No live deployment or production worker was started.
- No live trade or Telegram notification was triggered.
- PR #606 remains draft and unmerged.


## Atomic admission implementation — 2026-11-01

- New `web.auth.admit_login_attempt()` performs durable admission under a per-key MariaDB `GET_LOCK` lock. The lock name is a fixed prefix plus a truncated SHA-256 digest, staying within MariaDB's 64-character lock-name limit.
- The attempt row is inserted while the lock is held. A request is denied once the shared count reaches `RATE_LIMIT_MAX_ATTEMPTS`; database, lock-acquisition, or lock-release failures fail closed with HTTP 503 through the login route.
- Durable login requests reserve before password verification. A failed password does not insert a second row; a successful login clears the reservations for that key before token creation.
- In-memory unit/tool mode retains the previous check-then-record behavior.
- Local test evidence received from the user predates these changes. No tests have been executed against this new implementation in this session.


## Local test evidence — 2026-11-01, after atomic admission

The user reported:

- Focused batch: 28 passed, 5 skipped (1.81s).
- MariaDB batch: 5 passed (2.50s).
- Full suite: 805 passed, 15 skipped, 1 failed (8.82s).
- The only failure was `test_recent_closed_trade_rows_respect_strategy_report_visibility`. The implementation executes one joined SELECT with the exact strategy/mode LEFT JOIN and `COALESCE(l.report_enabled, TRUE) = TRUE`; the test incorrectly expected a separate schema-creation query followed by the SELECT.

The test was updated to assert the actual one-query contract and to verify the join and visibility predicate on that query. **This assertion-only correction has not yet been rerun.** The full suite is not yet green.

