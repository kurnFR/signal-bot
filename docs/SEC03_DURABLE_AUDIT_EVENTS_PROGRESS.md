# SEC-03 — Durable Audit Events

**Status:** In progress  
**Branch:** `fix/security-durable-audit-events`  
**Base:** `master` (SEC-02 PR #606 merged)  
**Safety:** GitHub-only. No production database, deployment, worker, trade, or Telegram activity.

## Objective

Make security-sensitive and administrative actions fail safely when their required audit record cannot be persisted. For database-backed mutations, the business change and audit row must commit in the same MariaDB transaction.

## Current call-site inventory

Source reviewed on the rebased `master` line. All production call sites are in `web/routes/auth_routes.py`.

| Event | Trigger | Classification | Required persistence behavior |
|---|---|---|---|
| `LOGIN_RATE_LIMITED` | Login attempt denied by shared limiter | Mandatory security event | Persist before returning 429; return 503 if audit storage fails |
| `LOGIN_FAILED` (unknown/inactive) | Unknown or disabled account | Mandatory security event | Persist before returning 401; return 503 if audit storage fails |
| `LOGIN_FAILED` (bad password) | Password verification fails | Mandatory security event | Persist before returning 401; return 503 if audit storage fails |
| `LOGIN_SUCCESS` | Credentials accepted | Mandatory security event | Persist before issuing/returning an access token |
| `LOGOUT` | Session logout | Mandatory security event | Revoke token and insert audit row in one transaction |
| `PASSWORD_CHANGED` | Self-service password update | Mandatory security event | Password update and audit row in one transaction |
| `USER_CREATED` | Admin creates user | Mandatory administrative event | User insert and audit row in one transaction |
| `USER_PASSWORD_RESET` | Admin resets password | Mandatory administrative event | Password update and audit row in one transaction |
| `USER_UPDATED` | Admin changes role/active state | Mandatory authorization event | User update and audit row in one transaction |
| `USER_DELETED` | Admin deletes user | Mandatory administrative event | Already uses the same cursor/transaction; retain and strengthen failure tests |

**Best-effort production events:** none found in the reviewed call sites. The generic `log_audit(..., raise_on_error=False)` helper remains available for explicitly non-critical future telemetry, but must not be used for the events above.

## Transaction and outbox decision

All identified business actions and `audit_logs` are stored in the same MariaDB database. Therefore, use the existing transaction cursor helper (`log_audit_cursor`) for DB-backed mutations. Logout should write the token revocation row and audit row in one transaction. No cross-system audit event was identified, so a separate outbox is not justified for this scope; revisit if a required event must be delivered to another system.

Login attempt reservation/finalization remains its own shared-rate-limit operation. The audit event is a separate required record: if its write fails, the request must not be reported as an ordinary 401/429 or return a token; it should return a controlled 503. Failed-attempt counters must remain recorded even when the audit insert fails.

## Implementation plan

- [x] Inventory all audit call sites and classify mandatory vs best-effort.
- [x] Add transaction-aware token revocation helper for logout (`revoke_token_cursor`); cache only after commit.
- [x] Move password/user mutations and their audit inserts into one transaction.
- [x] Make login/security events require successful audit persistence; return HTTP 503 on audit failure.
- [x] Add failure-injection and atomicity regression tests for password change, user create/reset/update/delete, logout, login success, failed-password login, and rate-limited login.
- [x] Verify source metadata fields: actor, action, target/outcome, IP/role/reason; no passwords, password hashes, or bearer tokens are passed.
- [x] Full pytest suite on the latest code head, using the isolated MariaDB 10.11 service and migration/admin setup in GitHub Actions.
- [x] Confirm the latest documentation head completed the same full pytest workflow successfully.
- [x] Review PR patch, workflow triggers, and current review state.
- [ ] Obtain final independent security review and explicit release approval.
- [x] After SEC-02 merged, rebase SEC-03 changes onto `master`, preserve SEC-02 login-pool/cleanup hardening, and rerun required CI.

## Acceptance criteria

1. A mandatory audit insert failure cannot leave a DB-backed business mutation committed.
2. Logout cannot report success unless both token revocation and the logout audit row commit.
3. Login success cannot return an access token without a committed `LOGIN_SUCCESS` row.
4. Failed login and rate-limit outcomes are not silently returned as ordinary authentication outcomes when the required audit write fails.
5. No passwords, bearer tokens, password hashes, or raw exception details are written to audit metadata.
6. Required CI is green on the final branch head.

## Current verification

- Source-level call-site inventory completed against the SEC-02 branch.
- Implementation commits:
  - `356d3f1` transactional token-revocation cursor helper.
  - `8e97d1c` mandatory audit handling and same-transaction auth/admin mutations.
  - `d2161d0` fail-closed logout revocation transaction.
  - `369a027` initial failure-path regression coverage.
  - `3e8a25b` cleanup of obsolete direct revocation import.
  - `6e0b8e4` logout atomicity regression coverage.
- Final patch review found missing imports for `revoke_token_cursor` and `cache_revoked_token`; fixed in `9506347`.
- Added a successful-logout regression test in `a25ddc3` and corrected its continuation syntax in `6d1daeb`.
- Hosted full-suite runs on earlier commits exposed a logout test setup failure (`401 != 503`) and then malformed multiline patch syntax in the test module. The logout failure-path tests now invoke the route directly with a Starlette request, avoiding unrelated auth middleware; the test-file continuation syntax was corrected in `a74db54`.
- Additional source review found logout DB connection acquisition occurred outside the fail-closed handler; it now maps connection/cursor failures to HTTP 503 and has a dedicated regression test.
- GitHub Actions run [41373555116](https://github.com/kurnFR/signal-bot/actions/runs/41373555116) on code head `a74db543febc2d8d895327d60d734adc8ccf5af1` completed successfully: MariaDB 10.11 service healthy; migration and isolated test-admin setup passed; full pytest **820 passed, 16 skipped, 6 warnings** in 6.13s.
- GitHub Actions run [41375068218](https://github.com/kurnFR/signal-bot/actions/runs/41375068218) on documentation head `267757b008fd26aecf75e057f37c71f5418d53fc` completed successfully. A further docs-only commit `7422d0144d0ae225ca64ceb9fe989d65fe43f3e1` refreshed the PR verification notes; its full pytest workflow [41376137613](https://github.com/kurnFR/signal-bot/actions/runs/41376137613) also completed successfully on that exact head: **820 passed, 16 skipped, 6 warnings** in 5.68s. MariaDB initialization, migrations, and isolated test-admin setup passed.
- The full pytest workflow discovers and runs the repository test suite, including the RetailBot accounting and screener test modules. The separate `retailbot-accounting.yml` workflow was not triggered because it only accepts PRs targeting `master`, while this PR targets the SEC-02 branch. Do not describe the dedicated workflow as separately passed.
- No frontend files changed in SEC-03. The frontend workflow is path-filtered to `frontend/**` and its own workflow file, so it was not triggered by this PR. Frontend checks were green on the earlier SEC-02 validation, but have not been rerun on SEC-03.
- PR review inspection found no submitted reviews and no inline review threads at the time of inspection. PR #607 remains open, draft, and unmerged.
- Patch scope is four files: this progress document, `tests/test_phase1_security_reliability.py`, `web/auth.py`, and `web/routes/auth_routes.py`.
- SEC-02 PR #606 was merged into `master` at `823b5d2837383839c5c9acd0e0dacce62a602a25`. SEC-03 was rebased onto that exact master commit; its merge base is now master and it is four commits ahead with no divergence.

- Independent focused security review verified the `audit_logs` table is InnoDB, and reviewed the transaction boundaries in login, logout, password change, and admin user mutation routes. No blocking transaction/atomicity defect was identified in the reviewed paths; this is an implementation review, not an external penetration test or independent sign-off.
- Review identified a missing explicit regression for audit-store failure on unknown/inactive account login. Added coverage in `d262f50`; the first hosted run failed at test collection due to an escaped line-continuation syntax error in the new test (not an application-code failure). Corrected the test using nested context managers in `7538cbac`.
- GitHub Actions run [41376692552](https://github.com/kurnFR/signal-bot/actions/runs/41376692552) on test commit `7538cbac9a7d11a75069a43801fd10d56baa8e6b` completed successfully: MariaDB initialization, migrations, isolated admin seed, and full pytest all passed; **821 passed, 16 skipped, 6 warnings** in 4.04s.


## Rebase and post-SEC-02 verification (2026-11-02)

- SEC-02 PR #606 was explicitly approved and merged using a merge commit: `823b5d2837383839c5c9acd0e0dacce62a602a25`.
- PR #607 was retargeted to `master`. The initial branch comparison exposed that SEC-03 had forked from an earlier SEC-02 head, so the original stacked diff was not treated as a completed rebase.
- Reconstructed the SEC-03 changes on top of the merged master commit, preserving the later SEC-02 global expiry cleanup, early identity-connection release, and their regression coverage. For the login route, retained the SEC-02 route implementation and applied SEC-03 mandatory audit calls to its four login outcomes.
- Rebased code head: `ddad544482cec7ca9b8ab101acae7493dd21f567`. Compare against master: **ahead 4, behind 0**, merge base `823b5d2837383839c5c9acd0e0dacce62a602a25`. Changed-file scope remains four files.
- Exact rebased-head CI:
  - Full pytest PR run [41379024483](https://github.com/kurnFR/signal-bot/actions/runs/41379024483): **822 passed, 17 skipped, 6 warnings**.
  - Full pytest push run [41379019497](https://github.com/kurnFR/signal-bot/actions/runs/41379019497): **822 passed, 17 skipped, 6 warnings**.
  - Unit tests [41379024252](https://github.com/kurnFR/signal-bot/actions/runs/41379024252): **105 passed**.
  - MariaDB integration [41379024252](https://github.com/kurnFR/signal-bot/actions/runs/41379024252): **36 passed**.
  - RetailBot accounting/parity and screener regression [41379024215](https://github.com/kurnFR/signal-bot/actions/runs/41379024215): **36 passed** and **27 passed**.
- The frontend workflow is path-filtered and did not run for SEC-03 because no frontend files changed. The post-SEC-02 master validation separately passed frontend type-check, unit tests, build, and browser smoke tests.
- Production database, deployment, workers, trades, and Telegram sends were not used.
- PR #607 remains draft and unmerged pending final review/approval.

## Follow-up audit identity correction (2026-11-02)

- Focused review found that a failed login for an inactive account recorded the username but passed a null `user_id`, even though the account lookup had resolved its database identity.
- Updated the `LOGIN_FAILED` event to include the resolved user ID for inactive accounts while keeping it null for genuinely unknown usernames.
- Extended the unknown/inactive login audit-failure regression to assert the exact user ID passed to the audit writer for both cases.
- Follow-up code/test commits: `681a76488f0b4749900b574dbbb0ba2dcb86b2cd`, `9ccba6d67272e31d9ad616bf89a3f462d712cb76`.
- CI for this follow-up is pending; do not treat the earlier green run on `ddad544` as validation of these latest changes.
