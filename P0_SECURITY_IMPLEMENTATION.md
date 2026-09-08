# P0.1 Security Implementation Status

**Date:** 2026-09-08
**Status:** IMPLEMENTED IN CODE — runtime verification still required

## What changed

### Central API authentication boundary

`web/app.py` now applies a fail-closed authentication middleware to every `/api/*` route except the explicit public login endpoint:

- `/api/auth/login` is public.
- All other API routes require a valid bearer token or `auth_token` cookie.
- The authenticated user is checked against the database and must still be active.

This prevents a newly added router from becoming public merely because a developer forgot to add `Depends(get_current_user)`.

### Role policy

The central boundary now applies the following initial policy:

| Area | Viewer | Trader | Admin |
|---|---:|---:|---:|
| Read-only authenticated APIs | Yes | Yes | Yes |
| Paper read APIs | Yes | Yes | Yes |
| Paper state-changing APIs | No | Yes | Yes |
| Backtest state-changing APIs | No | Yes | Yes |
| Master-data mutation | No | Yes | Yes |
| Custom strategy creation | No | Yes | Yes |
| System settings/status | No | No | Yes |
| User management | No | No | Yes |
| Telegram status | Yes | Yes | Yes |
| Telegram test | No | No | Yes |

Existing route-level `require_admin` dependencies remain in place for user-management endpoints.

## CORS hardening

Wildcard credentialed CORS was removed.

Allowed browser origins are controlled by:

`SIGNAL_BOT_ALLOWED_ORIGINS`

Default development value:

`http://localhost:8050,http://127.0.0.1:8050`

Production deployments must explicitly configure the dashboard origin(s).

## Authentication helper

`web/auth.py` now contains `authenticate_request(request)` as the shared request authentication implementation and `require_role(...)` for future route-level fine-grained policies.

## Important remaining security work

This change is the first P0 authorization barrier, not the complete security project.

Still required:

1. Replace hard-coded/default `admin/admin123` bootstrap.
2. Add login rate limiting.
3. Add session/token revocation.
4. Remove sensitive DB connection metadata from `/api/settings/status`.
5. Add frontend output escaping for custom strategy metadata.
6. Add automated authorization regression tests for every API route.
7. Runtime-test all frontend/API flows with viewer/trader/admin accounts.

## Verification gate

Before declaring P0.1 complete, run the application and verify at minimum:

- unauthenticated `GET /api/paper/positions` -> 401;
- unauthenticated `POST /api/paper/sync` -> 401;
- viewer `POST /api/paper/sync` -> 403;
- trader `POST /api/paper/sync` -> allowed;
- viewer `GET /api/paper/positions` -> allowed;
- viewer `GET /api/settings/status` -> 403;
- trader `GET /api/settings/status` -> 403;
- admin `GET /api/settings/status` -> allowed;
- viewer `GET /api/users` -> 403;
- trader `GET /api/users` -> 403;
- admin `GET /api/users` -> allowed;
- unauthenticated `POST /api/auth/login` remains reachable.

Do not expose the application externally until these runtime checks pass.
