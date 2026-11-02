# SEC-02 — Shared Login Rate-Limit Remediation

**Updated:** 2026-11-02  
**Repository:** `kurnFR/signal-bot`  
**Branch:** `fix/security-shared-login-rate-limit`  
**Pull request:** [#606](https://github.com/kurnFR/signal-bot/pull/606)  
**Current code head at verification:** `587105d217bbd12a8f317f86fe9803e3ae02e0a8`  
**Release state:** CI green; draft PR; not merged or deployed.

## Finding and remediation

The original durable login limiter could fall back to process-local memory when shared storage failed, and the check-then-record flow allowed concurrent workers to pass the same threshold. The remediation:

- Keeps durable mode required after startup enables it; a failed probe leaves health as `unavailable`.
- Fails closed with HTTP 503 when identity lookup, shared admission, persistence, reservation finalization, or successful-login cleanup cannot be completed.
- Uses a MariaDB advisory lock per limiter key to serialize count-and-reserve operations across workers.
- Reserves an attempt before password verification. Migration `019_login_attempt_state` distinguishes `pending` reservations from completed `failed` attempts.
- Finalizes only the current request's reservation. Successful login removes completed failures and its own reservation without erasing concurrent pending reservations.
- Resolves known usernames through the database first and keys them by immutable user ID, so DB-collation-equivalent spellings share one limiter bucket. Unknown usernames use a normalized fallback key.
- Exposes sanitized `loginRateLimiter.mode` and `loginRateLimiter.degraded` telemetry on authenticated status endpoints; admin diagnostics remain protected.
- Includes failure-injection, route, schema-probe, threshold, lock-timeout, reservation-interleaving, and opt-in cross-process MariaDB coverage.

## Latest CI acceptance — 2026-11-02

All six check runs completed successfully on exact head `587105d217bbd12a8f317f86fe9803e3ae02e0a8`:

| Check | Result |
|---|---:|
| Full pytest suite | **812 passed, 16 skipped, 6 warnings** |
| Unit/screener/wallet batch | **105 passed** |
| MariaDB integration | **35 passed** |
| RetailBot accounting/parity regression | **36 passed** |
| Screener regression | **27 passed** |
| Frontend type-check, unit tests, production build, Playwright browser smoke | **Passed** |

The MariaDB integration workflow applies migrations to its ephemeral `wallet_tracker_test` database. The full-suite workflow uses ephemeral `signal_bot_test`. Production `crypto_signals` was not accessed. The frontend browser smoke job installed Chromium and completed successfully.

The CI run IDs are:
- Full suite: `41368456221`
- Unit + MariaDB integration: `41368456230`
- RetailBot accounting: `41368456179`
- Frontend: `41368456209`

## Review notes and remaining gates

- The latest branch diff was reviewed at the route, durable admission/finalization, migration, workflow, and monitoring surfaces.
- No unresolved inline review threads or submitted PR reviews were present at the latest review check.
- The DB identity lookup ensures known account aliases that compare equal under the configured users-table collation share the same user-ID bucket. The regression test simulates two spellings resolving to the same ID; it is a route-level test, not a dedicated assertion against every production collation.
- CI proves the automated suite on isolated databases. A local user rerun is not independently verified in this review session.
- Final human review and explicit release approval remain required before merge.

## Operational guardrails

- No production database was accessed or modified.
- No live deployment or production worker was started.
- No live trade or Telegram notification was triggered.
- PR #606 remains draft and unmerged.
