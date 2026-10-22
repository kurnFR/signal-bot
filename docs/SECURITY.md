# Security

How access to the dashboard and API is controlled, and what to do when deploying it. This describes the current implementation; open hardening items are in [STATUS.md](STATUS.md).

## 1. Authentication

- **Passwords:** PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte random salt (`pbkdf2:sha256:<iterations>$<salt>$<hash>`), compared in constant time.
- **Tokens:** HMAC-SHA256-signed bearer tokens carrying user, role and expiry; **24-hour lifetime**. The signing key is `SECRET_KEY`, generated randomly and stored in `.app_secret` (gitignored). Deleting that file invalidates every session.
- **Logout and revocation:** logout stores the token's SHA-256 hash in `revoked_tokens` until it would have expired, so a revoked token is rejected even after a restart.
- **Brute-force limit:** 5 failed logins per 5 minutes per key, then `429`. Attempts are stored in the `login_attempts` table, shared across workers and retained across restarts; an in-memory fallback is used if the database is unavailable.
- **Audit log:** these events are written to `audit_logs` and are admin-readable at `GET /api/auth/audit-logs`: `LOGIN_FAILED`, `LOGIN_RATE_LIMITED`, `LOGIN_SUCCESS`, `LOGOUT`, `PASSWORD_CHANGED`, `USER_CREATED`, `USER_PASSWORD_RESET`, `USER_UPDATED`.
- **First admin:** created on the first start when no admin exists. The password is `ADMIN_DEFAULT_PASSWORD` if set, otherwise a random one-time password written to `.initial_admin_credential` with mode 0600 (gitignored). The account is flagged `must_change_password`. Until the password is changed, the API rejects every request except `/api/auth/me`, `/api/auth/logout` and `/api/auth/change-password` (403, code `password_change_required`), and the dashboard shows a blocking dialog. New passwords must be at least 10 characters, differ from the old one, and reject common/weak variants and the username. Delete `.initial_admin_credential` after first login.

## 2. Authorization

Enforced centrally in `web/app.py` for every `/api/*` route (fail-closed): no valid token means `401`; a valid token with insufficient role means `403`. Only `POST /api/auth/login` and the static home page are public. A sweep of all 74 API operations without credentials confirmed this.

| Role | Can do |
|---|---|
| `viewer` | Read dashboards, results and data. **Cannot call any mutating endpoint** (0 of 29) |
| `trader` | Everything a viewer can, plus backtests, paper configs/deploys/sync/circuit breakers, ML runs, custom strategies, master-data backfill and feature builds, research-bot control, wallet tracking changes |
| `admin` | Everything, plus user management (`/api/users`), `GET /api/settings/status`, the Telegram test message, and the audit log |

Rules live in `ADMIN_ONLY_PREFIXES`, `ADMIN_OR_TRADER_PREFIXES`, `TRADER_OR_ADMIN_PREFIXES` and `RESEARCH_CONTROL_PREFIXES` in `web/app.py`. A new router that is not covered by a prefix list is readable by any authenticated user for all methods, so add its prefix when you add mutating routes.

## 3. Web and transport

- **CORS** is limited to `SIGNAL_BOT_ALLOWED_ORIGINS` (default `http://localhost:8050,http://127.0.0.1:8050`), with credentials allowed.
- The app serves plain HTTP. For anything beyond localhost, put it behind a TLS-terminating reverse proxy.
- The web server binds to `127.0.0.1` by default. Set `SIGNAL_BOT_HOST=0.0.0.0` (or `--host`) only behind a firewall and a TLS-terminating reverse proxy; the app has no TLS of its own.
- The dashboard builds HTML from API data with an escape helper; wallet and token values are validated server-side before they are stored.

## 4. Secrets and data

- Keep keys only in `.env` (gitignored): database credentials, `TELEGRAM_BOT_TOKEN`, LLM and news API keys.
- Database credentials should belong to a dedicated MySQL user restricted to the `crypto_signals` database. The wallet tracker's startup runs an `ALTER TABLE`, so that user needs ALTER privilege.
- Telegram failures are logged without the bot token (error type and HTTP status only).
- Never commit `.env`, `.app_secret` or `.initial_admin_credential`. If a token or key is pasted into a chat, ticket or log, revoke and replace it.

## 5. Safety limits on trading

This is paper trading only, so there is no exchange API key or order endpoint in the codebase. The paper engine additionally blocks new entries when risk cannot be verified (see `paper/circuit_breaker.py`: max concurrent positions, max daily loss, portfolio notional exposure, and a shared emergency stop persisted in `system_flags`). If the shared stop state cannot be read, new entries fail closed.

## 6. Tests

`tests/test_auth_security.py` covers login rate limiting and the token revocation lifecycle, and the wallet route tests assert that a viewer cannot mutate. `tests/test_route_authorization_sweep.py` sweeps every `/api` route from the live route table: anonymous requests get 401, a viewer cannot reach any mutating route, only admins reach admin prefixes, and a flagged account is blocked everywhere except the password-change endpoints. Login attempts are stored in the `login_attempts` table (shared across workers, survives restarts) with an in-memory fallback.
