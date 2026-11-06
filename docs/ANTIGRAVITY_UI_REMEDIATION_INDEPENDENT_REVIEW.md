# Independent Review — Antigravity UI Remediation Report

**Repository:** `kurnFR/signal-bot`  
**Reviewed branch:** `fix/ui-migration-review-remediation`  
**Reported audit commit:** `c2bee18d65e7dd9d3979584e14c77dc000dab11d`  
**Review date:** 2026-11-04  
**Status:** NOT ACCEPTED — corrections and independent verification required  
**Scope:** Source-level spot review of the remediation report and selected implementation files. This is not yet a complete end-to-end acceptance audit.

## 1. Executive decision

The remediation is materially improved, but its report is not internally consistent and does not yet prove acceptance. Do not switch the default dashboard, deploy, or close the migration based on this report.

The independent source review confirmed three issues:
1. Telegram status RBAC is described inconsistently with the actual middleware and route behavior.
2. Workflow totals do not reconcile with the detailed view-level counts.
3. The exact commit used for test execution is ambiguous.

## 2. P0 — Telegram status permission matrix is incorrect

Inspected `web/app.py` (`_role_allowed()` and `api_security_boundary()`) and `web/routes/telegram_routes.py`.

The middleware explicitly allows authenticated access to `/api/telegram/status`:

```python
if path == "/api/telegram/status":
    return True
```

The route returns `chatId` only to admins and sets it to null for other roles:

```python
role = str(getattr(request.state, "user", {}).get("role", "")).lower()
return {
    "configured": cfg["is_configured"],
    "chatId": cfg["chat_id"] if role == "admin" else None,
    "hasToken": bool(cfg["token"]),
}
```

Therefore, based on inspected code:
- Admin: authenticated request allowed; receives chatId.
- Trader: authenticated request allowed; chatId is null.
- Viewer: authenticated request allowed; chatId is null.
- Unauthenticated: rejected by middleware.

The report's table claiming trader/viewer receive HTTP 403 for `GET /api/telegram/status` is wrong. Its prose saying chatId is masked for non-admins is consistent with the implementation.

**Required action**
- [ ] Correct the Telegram status matrix in the worksheet, parity report and acceptance report.
- [ ] Test admin receives chatId and trader/viewer receive 200 with chatId null.
- [ ] Test unauthenticated access is denied.
- [ ] Keep `POST /api/telegram/test` admin-only and prove trader/viewer are rejected before the outbound helper is called.
- [ ] Mock all outbound Telegram calls.

Do not change backend permissions merely to match incorrect documentation. If status should instead be admin-only, obtain owner approval and change backend, frontend, tests and documents together.

## 3. P0 — Workflow totals are arithmetically inconsistent

The report states 59 workflows total, 45 complete, 14 legacy-only and 11 read-only monitor workflows.

The detailed view-level counts in the worksheet/parity report show:

| View | Total | Complete |
|---|---:|---:|
| Home & Overview | 3 | 3 |
| Master Data | 5 | 5 |
| Backtest Studio | 5 | 5 |
| Paper Trading | 10 | 6 |
| ML Studio | 6 | 4 |
| Strategy Library | 5 | 5 |
| News AI Signals | 3 | 3 |
| Research Bots | 4 | 2 |
| Market Screener | 2 | 2 |
| Wallet Tracking | 5 | 3 |
| Settings & Users | 11 | 11 |
| **Total** | **59** | **49** |

The complete counts sum to 49, not 45. The incomplete counts sum to 10, not 14.

The enumerated read-only monitor IDs are PT-01–PT-06 (6), ML-01/02/05/06 (4), RB-01/02 (2), and WT-01–WT-03 (3): 15 workflows, not 11.

**Required action**
- [ ] Recompute totals directly from final worksheet rows.
- [ ] Ensure each workflow ID appears exactly once and has exactly one status.
- [ ] Reconcile COMPLETE + PARTIAL + LEGACY_ONLY + MISSING + BLOCKED + INTENTIONALLY_REMOVED to TOTAL.
- [ ] Reconcile executive summary, parity matrix, acceptance report and PR description.
- [ ] Explain PARTIAL vs LEGACY_ONLY without double counting.
- [ ] Add an automated count/unique-ID validation if practical.

## 4. P1 — Test evidence does not identify one unambiguous tested SHA

The report says suites ran against `c2bee18`. The worksheet records a different full SHA as its audit commit: `52d331d64e2259de54d538f112d310aab45e2739`.

The repository history shows `c2bee18d65e7dd9d3979584e14c77dc000dab11d` is a later audit/documentation commit. A later docs-only commit may contain no runtime changes, but the report must distinguish the SHA actually tested from the SHA that updated the report.

**Required action**
- [ ] Identify the exact full SHA checked out for each command.
- [ ] If code tested was `52d331d64e2259de54d538f112d310aab45e2739`, record that full SHA for test evidence.
- [ ] If claiming tests ran on `c2bee18d65e7dd9d3979584e14c77dc000dab11d`, rerun on that SHA or provide verifiable proof the runtime tree is identical.
- [ ] Include exact commands, tool versions, environment assumptions, counts, logs/artifact paths and skip reasons.
- [ ] Distinguish tests actually run from historical/copied results.
- [ ] Do not claim Actions passed without a corresponding run.

## 5. P1 — Settings & Users needs deeper workflow verification

The reviewed `frontend/src/SettingsPanel.tsx` now has actual user-management controls and imports a typed client from `frontend/src/lib/users.ts`. This is a meaningful improvement over the previous placeholder, but does not by itself establish parity or security.

**Required checks**
- [ ] Verify backend list/create/update/reset/delete routes enforce admin authorization independently of UI visibility.
- [ ] Verify server-side role validation rejects arbitrary role strings.
- [ ] Verify update requests cannot change fields outside the intended schema.
- [ ] Verify self-deactivation/self-deletion and last-admin lockout protections are server-side, not only React.
- [ ] Verify password reset responses and UI do not expose temporary credentials in logs, analytics, console or persistent storage.
- [ ] Verify server-state refresh after every successful mutation.
- [ ] Verify stale requests are cancelled/ignored after unmount or role change.
- [ ] Verify 401/403/409/validation/server/network failure behavior.
- [ ] Verify admin/trader/viewer with isolated test identities.
- [ ] Confirm whether legacy supported email editing. The inspected new update client supports role and active status, not email updates; document an approved exception if email editing existed.
- [ ] Check for other omitted legacy user-management workflows.

## 6. P1 — Telegram test authorization relies on global middleware

The inspected `web/routes/telegram_routes.py` `run_test()` function does not declare a local `require_admin` dependency. Its protection comes from the global middleware path/role rule for `POST /api/telegram/test`.

This may be intentional, but depends on middleware coverage and route path classification.

**Required action**
- [ ] Keep the global guard and add an explicit route-level admin dependency, or document why middleware is the authoritative guard.
- [ ] Test requests through the ASGI application with admin/trader/viewer identities and prove denied requests do not call `test_telegram_connection()`.
- [ ] Ensure alternate route mounting or test invocation cannot bypass the intended guard.
- [ ] Never send a live Telegram message during tests.

## 7. Independent verification required after corrections

The reviewer should verify:
1. Final worksheet IDs, statuses and totals.
2. Telegram status/test behavior for admin, trader, viewer and unauthenticated callers.
3. Settings & Users workflows, backend authorization and self/last-admin safety.
4. Every view marked COMPLETE through its API contract, React control and representative browser workflow.
5. All PARTIAL views and LEGACY_ONLY actions remain disclosed.
6. Exact tested commit and test output.
7. Root route remains legacy by default; no production cutover occurred.

A passing test suite does not prove parity by itself. A parity worksheet does not prove a workflow works. Both implementation evidence and workflow verification are required.

## 8. Safety boundaries

- Do not enable `SIGNAL_BOT_USE_MODERN_UI` in production.
- Do not deploy or restart production services.
- Do not use or mutate the production database for review.
- Do not trigger paper/live trading, strategy deployment or production jobs.
- Do not send real Telegram messages.
- Use isolated test databases and mocked external side effects.
- Do not merge the remediation branch until independent review is complete and the owner authorizes it.

## 9. Antigravity response required

Please provide:
1. Corrected Telegram status matrix.
2. Reconciled workflow totals and generated status counts.
3. Full tested SHA for every test category.
4. Evidence for Settings & Users backend safeguards and unsupported legacy workflows.
5. Updated PR with exact remaining limitations.

**Acceptance decision:** Promising remediation, but **NOT ACCEPTED** pending correction of confirmed inconsistencies and independent verification.
