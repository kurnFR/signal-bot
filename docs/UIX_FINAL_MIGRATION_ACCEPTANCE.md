# UIX Final Migration Acceptance Report

**Repository:** `kurnFR/signal-bot`  
**Source baseline:** `eeaf8843d140701f88623a22aa925353e7e2e2d2` (`master`, 2026-10-30)  
**Review branch:** `feat/uix-final-acceptance-pass`  
**Report status:** **NOT ACCEPTED — automated owner-run checks pass; live/server and manual acceptance remain outstanding**  
**Production status:** React/Vite remains an isolated preview. FastAPI continues to serve `web/static/` as the production dashboard. This work does not authorize deployment, production DB mutation, worker restart, live trading, or Telegram messages.

## Executive decision

The migration has substantial implementation and regression coverage. The owner has now run the frontend foundation checks, the complete Chromium Playwright suite, and the full backend pytest suite successfully on PR head `cc98744df4e1cd104ef60fd23bcc4f36a708f52e`. The 12 skipped backend tests are all opt-in MariaDB integration tests and remain outstanding. Remaining acceptance evidence depends on isolated MariaDB integration, live-server read-only API/data comparison, manual accessibility and real-device/browser checks, mutation/RBAC verification in an isolated environment, and owner sign-off. GitHub Actions quota is exhausted, so fresh hosted CI evidence remains unavailable.

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

Result: **745 passed, 12 skipped, 0 failed** (8.19s; 7 deprecation warnings). Collection-only check: **757 tests collected, 0 collection errors**. The 12 skips are opt-in MariaDB tests: durable state (2), migrations (3), paper orders (4), and Wallet Tracker (3). These were intentionally not enabled because no disposable MariaDB test database has yet been confirmed. The isolated migration/accounting subset separately passed **15/15**. RetailBot paper-accounting unit coverage is included in the full pytest result; this is not a substitute for the skipped paper-order MariaDB integration. Record these as owner-run results, not hosted CI.

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

- [ ] Owner completed frontend and backend test commands on the exact PR head.
- [ ] Owner completed real-server read-only data comparison.
- [ ] Owner completed real-device/browser and manual accessibility checks.
- [ ] Owner verified mutation/RBAC workflows in an isolated test environment.
- [ ] All discovered defects are fixed and revalidated.
- [ ] Owner explicitly accepted the migration and approved a separate production cutover plan.

**Current acceptance result: NOT ACCEPTED / VALIDATION PENDING.** Automated frontend and backend checks are green, but MariaDB integration, live-server parity, manual accessibility/device checks, isolated mutation/RBAC verification, and explicit owner sign-off remain open.
