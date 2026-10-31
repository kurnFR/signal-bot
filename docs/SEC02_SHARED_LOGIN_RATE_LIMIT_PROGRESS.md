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
- [ ] Add/verify timeout and concurrent-attempt coverage.
- [ ] Confirm operational monitoring can observe limiter unavailable state; helper-only health reporting is not yet a confirmed external health signal.
- [ ] All required GitHub Actions checks green on the final PR head.
- [ ] Review final diff and obtain explicit release approval before merge.

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
