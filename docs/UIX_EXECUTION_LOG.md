# UI/UX Migration Execution Log

**Updated:** 2026-10-28  
**Status:** In progress — React preview only; legacy dashboard remains the production default.  
**Deployment:** None. Do not mount the preview in FastAPI or change production serving without explicit owner approval.

This log records verified execution checkpoints and keeps the next actions actionable. It supplements, rather than replaces, the detailed phase plan and view inventory.

## Latest completed checkpoint

### PR #543 — Settings diagnostics and session-expiry browser coverage

- **Status:** Merged.
- **Merge commit:** `d68ff01ebc878e850ac6d56324eba0e764592797`.
- **PR:** https://github.com/kurnFR/signal-bot/pull/543
- **Coverage added:** admin-only system diagnostics visibility; diagnostic collector detail redaction; viewer role does not request admin diagnostics or Telegram configuration; Telegram test confirmation can be cancelled without a POST; a 401 from admin diagnostics clears the stale session and returns the browser to sign-in.
- **Safety:** Telegram endpoint is mocked. No real Telegram message, database mutation, worker action, or deployment occurred.
- **Exact PR-head validation:** Frontend type-check, frontend unit tests, production build, Playwright Chromium browser smoke, pytest, general unit tests, RetailBot accounting regression, and Wallet Tracker MariaDB integration all passed on `c9d31a5c0f71d2eb68644af37f389d263ba16097`.

## Current migration boundary

- React/TypeScript/Vite remains an isolated preview, not mounted by FastAPI.
- `web/static/` remains the production dashboard.
- Read-only preview slices and safe Settings are not proof of full route, role, mutation, responsive, or accessibility parity.
- CI passing is not a substitute for owner acceptance or real-device/runtime verification.

## Next execution queue

1. **Reconcile inventory against runtime routes and roles.** Compare each row in `docs/UI_UX_VIEW_INVENTORY.md` with effective FastAPI routes, HTTP methods, auth/RBAC, polling, error/empty states, and side effects. Record discrepancies and evidence; do not rely on a static decorator count alone.
2. **Close the browser-flow gaps.** Expand Playwright beyond smoke coverage to verify migrated-view navigation and role-gated requests, loading/error/empty behavior, refresh/session recovery, and narrow viewport behavior. Keep all mutation endpoints mocked unless a separately reviewed test explicitly requires otherwise.
3. **Accessibility acceptance.** Track keyboard/focus, screen-reader semantics, zoom/reflow, contrast, reduced motion, and forced-colors checks for each migrated view and both themes.
4. **Feature parity review.** Review Market Screener V2, Master Data, and Settings view-by-view; keep all unverified controls clearly non-functional or absent rather than implying they work.
5. **Release gate.** Only after the above evidence is complete, document residual risks and request explicit owner acceptance. Production cutover/deployment remains a separate decision.

## Merge policy

- Add or update regression coverage before behavior changes.
- Use one independently reviewable slice per PR.
- Merge only when every required check is green on the exact PR head.
- Never deploy, switch FastAPI routes, change production data, restart workers, execute trades, or send live Telegram messages as part of frontend migration without explicit approval.
