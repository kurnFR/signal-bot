# Antigravity Modern UI — Workflow-by-Workflow Parity Audit Worksheet

**Repository:** `kurnFR/signal-bot`  
**Legacy source:** `web/static/`  
**Modern source:** `frontend/src/`  
**Owner:** Antigravity  
**Independent acceptance:** ChatGPT / repository owner  
**Status:** In progress — must be rebuilt from source, not copied from previous completion claims.

## 1. Audit rules

A row is `COMPLETE` only when the modern UI supports the actual user workflow end-to-end:
- a real React control exists and is reachable for the correct role;
- it calls the correct backend contract;
- the backend validates and authorizes the action;
- success/failure is visible and state is refreshed from the server;
- side effects and safety confirmations are correct;
- a relevant test or reproducible manual verification is recorded.

Use only these statuses:

| Status | Meaning |
|---|---|
| `COMPLETE` | End-to-end behavior verified with evidence |
| `PARTIAL` | Some functionality exists; at least one required step is missing or unverified |
| `LEGACY_ONLY` | Workflow currently requires the legacy dashboard |
| `MISSING` | No modern implementation exists |
| `BLOCKED` | Cannot complete until a backend contract, permission decision or owner decision exists |
| `INTENTIONALLY_REMOVED` | Removed only with documented owner approval and rationale |

Do not use “view complete” as a substitute for workflow-level status. One view can contain complete, partial, legacy-only and missing workflows simultaneously.

## 2. Required row schema

Create one row for every menu, sub-menu, tab, panel, table action, button, form, modal, setting, refresh/polling workflow and consequential mutation.

| ID | Legacy location/control | User workflow | Modern component/control | API method + path | Role policy (UI + backend) | Side effects / safety | State/error behavior | Test/evidence | Status | Gap / owner |
|---|---|---|---|---|---|---|---|---|---|---|
| EX-001 | `web/static/...` | Describe user outcome | `frontend/src/...` or MISSING | Exact method/path or NONE | admin/trader/viewer + backend enforcement | DB/job/Telegram/trading/etc. | loading/empty/error/success/readback | exact test + command/SHA | PARTIAL | exact next action |

Do not invent API paths. If the route is unknown, write `NOT VERIFIED` and trace it in `web/routes/` or `web/app.py`.

## 3. View audit index

Complete all rows below, then expand each view into detailed workflow rows using the schema above.

| # | Legacy view | Modern view/component to inspect | Required audit focus | Status |
|---|---|---|---|---|
| 1 | Home & Overview (`view-home`) | Overview / `SystemStatusPanel.tsx` | Health, DB, collectors, recent signals, refresh/polling, unavailable services | NOT AUDITED |
| 2 | Master Data (`view-master-data`) | `MasterDataPanel.tsx` | Coverage filters, backfill form, feature build, jobs, progress, retry, authorization | NOT AUDITED |
| 3 | Backtest Studio (`view-backtest`) | `BacktestStudioPanel.tsx`, related components | Configuration, run, progress, results, trades, comparison, save, promotion policy | NOT AUDITED |
| 4 | Paper Trading (`view-paper`) | `PaperTradingPanel.tsx` | Configs, deploy/stop, positions, orders, cancel, trades, risk/circuit breaker | NOT AUDITED |
| 5 | ML Forecast Studio (`view-ml-studio`) | `MLStudioPanel.tsx`, `MLGeneratorTrainingPanel.tsx` | Existing ML filter vs independent signal generator, training, model selection, paper eligibility, Telegram controls | NOT AUDITED |
| 6 | Strategy Library (`view-strategies`) | `StrategyLibraryPanel.tsx` | Catalog, details, parameters, create/edit/test/delete, promotion, enable/disable, alert gates | NOT AUDITED |
| 7 | News AI Signals (`view-news-ai`) | `NewsAIPanel.tsx` | Feed, signal details, events, filters, performance, refresh | NOT AUDITED |
| 8 | Research Bots (`view-research`) | `ResearchPanel.tsx`, related components | Bot lifecycle, strategy selection, reports, paper settings, independent alert settings | NOT AUDITED |
| 9 | Market Screener (`view-market-screener`) | `ScreenerPanel.tsx`, `ScreenerControlsPanel.tsx` | Screener V1/V2, filters, thresholds, worker controls, save, Telegram gate | NOT AUDITED |
| 10 | Wallet Tracking (`view-wallet-tracking`) | `WalletTrackingPanel.tsx` | Supported chains, wallets/candidates, tokens, thresholds, tracking lifecycle, alerts | NOT AUDITED |
| 11 | Settings & Users (`view-settings`) | `SettingsPanel.tsx`, `AuthGate.tsx`, user/admin components | User CRUD/lifecycle, roles, credentials, password flows, app settings, Telegram permissions, diagnostics | NOT AUDITED |

## 4. Mandatory Settings & Users deep audit

The reviewed `frontend/src/SettingsPanel.tsx` contains a LEGACY UI / incomplete administrative-settings section. Do not mark this view complete until the following inventory is resolved against the actual legacy UI and backend.

| Workflow | Legacy control and API evidence | Modern control/component | Backend authorization test | Status |
|---|---|---|---|---|
| User list/detail | TO INSPECT | TO INSPECT | admin allowed; trader/viewer denied as intended | NOT AUDITED |
| Create user | TO INSPECT | TO INSPECT | admin allowed; non-admin denied | NOT AUDITED |
| Edit user | TO INSPECT | TO INSPECT | verify field-level permissions | NOT AUDITED |
| Enable/disable user | TO INSPECT | TO INSPECT | verify policy and self-lockout protection | NOT AUDITED |
| Reset/change password | TO INSPECT | TO INSPECT | verify own/admin reset rules | NOT AUDITED |
| Role assignment | TO INSPECT | TO INSPECT | admin-only unless approved otherwise | NOT AUDITED |
| Application settings | TO INSPECT | TO INSPECT | verify each setting's role and validation | NOT AUDITED |
| Credential settings | TO INSPECT | TO INSPECT | ensure secrets never returned to browser/logs | NOT AUDITED |
| Telegram status/test | TO INSPECT | TO INSPECT | reconcile admin-only backend vs docs | NOT AUDITED |
| Diagnostics/audit | TO INSPECT | TO INSPECT | admin-only data visibility | NOT AUDITED |
| Forced password change/logout | TO INSPECT | TO INSPECT | verify restricted-session exceptions | NOT AUDITED |

## 5. Mandatory Telegram permission matrix

The previous acceptance report says traders can test Telegram; the reviewed frontend/backend enforce admin-only test access. Confirm the intended policy with the owner, then fill this matrix based on the backend route—not frontend visibility alone.

| Endpoint / action | Admin | Trader | Viewer | Sensitive response fields checked | Automated test |
|---|---|---|---|---|---|
| Telegram status read | VERIFY | VERIFY | VERIFY | chat ID, token, config secrets | REQUIRED |
| Telegram test send | VERIFY | VERIFY | VERIFY | no secret disclosure on denial | REQUIRED |

All automated tests must mock Telegram delivery. No live message is permitted in test evidence.

## 6. API contract audit

For each React API call, record:

| Frontend API function | HTTP method | FastAPI route/module | Request schema | Response schema | Auth dependency | Role policy | Mutation / side effect | Contract test |
|---|---|---|---|---|---|---|---|---|
| Exact function name | GET/POST/PUT/PATCH/DELETE | Exact route | Exact schema or NONE | Exact schema | Exact dependency | admin/trader/viewer | DB/job/external | Exact test |

Verify both directions:
1. Every modern UI operation maps to an existing supported backend route.
2. Every legacy operation that must be preserved has a corresponding modern workflow or a documented exception.

An endpoint being called successfully is not proof that the UI handles all response/error cases or refreshes the correct state.

## 7. Non-UI implementation scope map

Review every non-frontend file changed by the UI migration implementation and complete:

| File | Change summary | Why UI migration needs it | API/schema impact | Independent workstream? | Test/evidence | Separate PR? |
|---|---|---|---|---|---|---|
| Exact path | Specific behavior | Specific React workflow | Exact contract | ML / paper / backtest / DB / runtime / security / none | Exact command/SHA | YES/NO + reason |

Do not treat frontend acceptance as approval of unrelated trading, accounting, database or runtime behavior.

## 8. Test evidence register

Every result must reference the exact tested commit.

| Test category | Exact command | Commit SHA | Environment | Result | Artifact / log | Limitations |
|---|---|---|---|---|---|---|
| Typecheck | TO RUN | TO RECORD | TO RECORD | NOT RUN | NONE | NONE |
| Unit/component | TO RUN | TO RECORD | TO RECORD | NOT RUN | NONE | NONE |
| Production build | TO RUN | TO RECORD | TO RECORD | NOT RUN | NONE | NONE |
| Backend regression | TO RUN | TO RECORD | Isolated test DB only | NOT RUN | NONE | NONE |
| Playwright | TO RUN | TO RECORD | Browser/version | NOT RUN | NONE | NONE |
| API RBAC | TO RUN | TO RECORD | Isolated test DB / mocked external calls | NOT RUN | NONE | NONE |
| Accessibility | TO RUN | TO RECORD | Browser/viewport | NOT RUN | NONE | NONE |
| Responsive | TO RUN | TO RECORD | Phone/tablet/desktop | NOT RUN | NONE | NONE |
| Staging read-only | Only if approved | TO RECORD | Non-production | NOT RUN | NONE | No production mutation |

Do not copy historical pass counts into this register. Mark checks not executed on the final SHA as `NOT RUN`.

## 9. Final audit summary

Fill this only after the workflow-level audit is complete.

- Audited legacy workflows: **TBD**
- Complete: **TBD**
- Partial: **TBD**
- Legacy-only: **TBD**
- Missing: **TBD**
- Blocked: **TBD**
- Intentionally removed with owner approval: **TBD**
- P0 gaps remaining: **TBD**
- P1 gaps remaining: **TBD**
- Tested commit SHA: **TBD**
- Production/staging verification status: **TBD**
- Owner-approved exceptions: **TBD**

## 10. Final rule

A polished React screen is not parity. A route existing is not proof of authorization. A mocked component test is not proof of a real workflow. A green test suite is not proof that all legacy features were migrated.

Only mark each workflow complete when the actual behavior, backend contract, role enforcement, side effects, state handling and evidence all agree.
