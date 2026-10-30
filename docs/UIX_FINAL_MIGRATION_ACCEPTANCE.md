# UIX Final Migration Acceptance Report

**Repository:** `kurnFR/signal-bot`  
**Source baseline:** `eeaf8843d140701f88623a22aa925353e7e2e2d2` (`master`, 2026-10-30)  
**Review branch:** `feat/uix-final-acceptance-pass`  
**Report status:** **NOT ACCEPTED — automated owner-run checks pass; live/server and manual acceptance remain outstanding**  
**Production status:** React/Vite remains an isolated preview. FastAPI continues to serve `web/static/` as the production dashboard. This work does not authorize deployment, production DB mutation, worker restart, live trading, or Telegram messages.

## Executive decision

The migration has substantial implementation and regression coverage. The owner-run frontend foundation checks, complete Chromium Playwright suite, and full backend pytest suite passed on tested application commit `cc98744df4e1cd104ef60fd23bcc4f36a708f52e`. The original backend run reported 745 passed and 12 skipped; all 12 skipped MariaDB integration tests have since passed against the dedicated remote `signal_bot_test` database in separately executed integration groups (paper orders 4/4, wallet tracker plus durable state 12/12, migrations 22/22). Combined with the original full-suite result, the 757-test collected backend inventory is now covered with no known skipped integration gate. Remaining acceptance evidence depends on live-server read-only API/data comparison, manual accessibility and real-device/browser checks, isolated mutation/RBAC verification, and owner sign-off. GitHub Actions quota is exhausted, so fresh hosted CI evidence remains unavailable.

## Acceptance matrix

| Workstream | Evidence at review | Status | Required closure evidence |
|---|---|---|---|
| Responsive layouts | Owner-run Chromium Playwright suite passed all 28 tests, including the 375/768/1024/1440px matrix for all 11 navigation views in both persisted themes. | Automated Chromium suite passed; manual acceptance pending | Inspect real phone/tablet/desktop; verify 200% and 400% zoom/reflow and landscape. |
| Keyboard/accessibility | Existing browser tests cover skip-link activation, active-page announcement, mobile focus restoration, Escape dismissal, reduced motion, and forced-colors focus visibility. | Partial automated coverage; manual acceptance pending | Keyboard-only pass for every view/dialog, screen-reader pass, visible focus and accessible-name review, both-theme contrast, 200%/400% zoom. |
| Browser compatibility | Playwright config targets Desktop Chrome/Chromium only. | Partial | Run Chromium, Firefox and WebKit; then current Chrome/Firefox/Safari/Edge on supported real devices. |
| API response contracts | Domain adapters have runtime validators and mocked contract tests for Paper Trading, Backtest, ML Studio, Research, News AI, Screener, Strategy Library, Wallet Tracking, system status, diagnostics and Telegram. This branch adds strict login and `/api/auth/me` validation against the backend serializer. | Broad source/mock coverage; live parity pending | Compare every view to actual FastAPI responses on the owner server; retain timestamped redacted samples and verify nullable/empty/permission/error variants. |
| Runtime route inventory | Existing tests discover frontend GET paths and compare them with registered FastAPI GET routes; mutation policy tests require explicit role policy. The 81-row inventory still declares row-by-row verification pending. | Partial | Reconcile every inventory row against effective `app.routes` method/path, auth dependency, role policy, forced-password gate, caller, polling, schema and side effects. |
| Mutation workflows | Preview controls are role-gated: screener save; Master Data backfill/feature build; Backtest run; Strategy Library quick test, AI draft, create/delete; admin Telegram test. Existing component/browser/backend tests cover important flows and viewer restrictions with mocks. | Partial; no live mutation run | In isolated test DB, verify admin/trader positive paths, viewer/anonymous/forced-password denials, confirm/cancel, persistence/readback, audit records, retries and safe failure. Never use production for this. |
| Live/realtime data | Browser fixtures are mocked; repository access cannot see the owner's running server or live DB. | **Not validated** | Owner performs read-only live-data comparison for every view and records API/UI values plus capture time. Use isolated staging for mutation verification. |
| Final release decision | Preview is not mounted by FastAPI; no owner acceptance record exists. | **Pending** | Complete closure evidence, resolve defects, document residual risks, and obtain explicit owner approval. Production cutover is separate. |

## Authentication contract reconciliation in this branch

The backend `web/routes/auth_routes.py` returns login data with `token` and `user`; the profile contains `id`, `username`, nullable `email`, `role`, and boolean `must_change_password`. `GET /api/auth/me` returns that profile shape. The frontend validates these fields before persisting a token or trusting the profile, and reuses the same validator during initial session restoration and after password change.

Allowed roles are `admin`, `trader`, and `viewer`. This client validation is defense in depth only; backend authentication and authorization remain authoritative.

## Owner-run validation procedure

Owner-run frontend validation on PR head `cc98744df4e1cd104ef60fd23bcc4f36a708f52e` (2026-10-30):

```text
npm ci --no-audit --no-fund     PASS (233 packages)
npm run typecheck               PASS
npm test                        PASS (30 files, 191 tests)
npm run build                   PASS
Playwright 1.64.1 Chromium      PASS (28 tests, 49.5s; official noble container)
```

The browser suite was run in `mcr.microsoft.com/playwright:v1.64.1-noble` because the owner's Ubuntu 20.04 host is not supported by the Playwright 1.64.1 Chromium installer.

Backend validation from the repository root:

```bash
PYTHONPATH="$PWD" python -m pytest -q
```

Result: **745 passed, 12 skipped, 0 failed** (8.19s; 7 deprecation warnings). Collection-only check: **757 tests collected, 0 collection errors**. The 12 skips were opt-in MariaDB tests: durable state (2), migrations (3), paper orders (4), and Wallet Tracker (3). On 2026-10-30 the owner provisioned the dedicated remote `signal_bot_test` database on `192.168.1.30:3306` and ran the groups separately: paper orders **4 passed**; wallet tracker plus durable state **12 passed** after applying 20 migrations to `signal_bot_test`; migration integration **22 passed**. Thus all 12 tests skipped in the original full run have now been exercised successfully; combined with the original full-suite run, the 757-test collected backend inventory has coverage with no known remaining skipped integration gate. The migration command reported all 20 migrations applied. The isolated migration/accounting subset separately passed **15/15**. RetailBot paper-accounting unit coverage is included in the full pytest result. These are owner-run results, not hosted CI.

### Live-data comparison (read-only)

1. Start the preview against the owner's test/staging FastAPI service and sign in separately as `viewer`, `trader`, and `admin`.
2. Capture API response and rendered UI for Overview/system status, Screener status/signals, Paper Trading positions/configs/orders/trades/metrics, Wallet Tracking snapshots, Backtest history, ML experiments/models, Research positions/trades/stats, News AI summaries/signals/events, Master Data coverage/jobs, and Settings diagnostics (admin only).
3. Record capture time, endpoint, HTTP status, role, row counts, nullable fields and redacted value comparison. Never record bearer tokens, passwords, Telegram secrets, database credentials or private wallet identifiers.
4. Verify viewer sessions cannot read admin-only diagnostics or see mutation controls; verify trader/admin controls appear only where backend policy allows them.
5. Do not run backfill, feature builds, saved backtests, strategy create/delete, AI generation, screener saves or Telegram test against production. If mutation acceptance is needed, use an isolated test database and non-production credentials, then verify persistence and audit trail.

## Evidence and limitations

- Latest verified master at inspection: `eeaf8843d140701f88623a22aa925353e7e2e2d2`.
- Open PR #603 contained authentication response validation, but its head was one commit behind master and all four reported workflow groups concluded failure. Job metadata did not expose logs, so the root cause is unresolved. This branch ports the relevant auth validation to current master; PR #603 should be marked superseded only after this replacement PR is visible and reviewed.
- GitHub Actions usage was reported as 2,000/2,000 minutes. Fresh CI cannot be treated as available until quota/billing resets.
- Automated browser tests use mocked API responses; they do not constitute realtime data validation.
- This report does not declare UIX-00 through UIX-05 complete and does not authorize production cutover.

## Sign-off

- [x] Owner completed frontend and backend automated test commands; application code tested at `cc98744df4e1cd104ef60fd23bcc4f36a708f52e`, with later commits documentation-only. Original backend run: 745 passed / 12 skipped; all 12 MariaDB skips subsequently passed in isolated integration runs against `signal_bot_test`.
- [ ] Owner completed real-server read-only data comparison.
- [ ] Owner completed real-device/browser and manual accessibility checks.
- [ ] Owner verified mutation/RBAC workflows in an isolated test environment.
- [ ] All discovered defects are fixed and revalidated.
- [ ] Owner explicitly accepted the migration and approved a separate production cutover plan.

**Current acceptance result: NOT ACCEPTED / VALIDATION PENDING.** Automated frontend and backend checks are green, but MariaDB integration, live-server parity, manual accessibility/device checks, isolated mutation/RBAC verification, and explicit owner sign-off remain open.


## MariaDB integration completion — 2026-10-30

The owner confirmed a dedicated disposable database named `signal_bot_test` on the existing remote MariaDB server (host `192.168.1.30`, port `3306`). The application database `crypto_signals` was not used for these integration runs.

Owner-run commands and results:

```text
MYSQL_DATABASE=signal_bot_test PAPER_ORDERS_MARIADB_TESTS=1   PYTHONPATH="$PWD" python -m pytest -q tests/test_paper_orders_mariadb.py
4 passed in 0.30s

MYSQL_DATABASE=signal_bot_test WALLET_TRACKER_MARIADB_TESTS=1   PYTHONPATH="$PWD" python -m pytest -q   tests/test_wallet_tracker_mariadb.py tests/test_durable_state.py
Initial run: 10 passed, 2 failed because login_attempts was not yet migrated.
After applying MYSQL_DATABASE=signal_bot_test PYTHONPATH="$PWD" python -m db.migrate:
  20 migrations applied.
Rerun: 12 passed in 0.53s

MYSQL_DATABASE=signal_bot_test WALLET_TRACKER_MARIADB_TESTS=1   PYTHONPATH="$PWD" python -m pytest -q tests/test_migrate.py
22 passed in 3.91s
```

The initial durable-state failures were caused by the missing `login_attempts` table in the fresh disposable database; applying the repository migrations resolved them. The successful rerun passed both durable-state tests. Total integration command output: 38 passed across the three groups. The 38 count includes tests that also run in the regular suite; it is not an additional unique-test count. The 12 tests skipped by the original backend suite are now all covered by successful opt-in MariaDB runs.

Safety boundary: integration tests and migrations targeted `signal_bot_test`; no test command intentionally targeted `crypto_signals`. No production deployment, worker restart, live trade, Telegram send, or production data mutation was performed.
