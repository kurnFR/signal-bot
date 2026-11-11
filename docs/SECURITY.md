# Security

How access to the dashboard and API is controlled, and what to do when deploying it. This describes the current implementation; open hardening items are in [STATUS.md](STATUS.md).

## 1. Authentication

- **Passwords:** PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte random salt (`pbkdf2:sha256:<iterations>$<salt>$<hash>`), compared in constant time.
- **Credential request bounds:** usernames are limited to 30 characters, email addresses to 254, and password fields to 1,024. Oversized fields are rejected with HTTP 422 before login's username lookup or any PBKDF2 verification/hashing.
- **Paper configuration request bounds:** symbol (20), market (7), timeframe (10), strategy name (50), and ML model ID (100) match the corresponding `paper_configs` column widths. Both API schemas and the persistence helper enforce these limits.
- **Tokens:** HMAC-SHA256-signed bearer tokens carrying user, role, per-user `session_version` and expiry; **24-hour lifetime**. The signing key is `SECRET_KEY`, generated randomly and stored in `.app_secret` (gitignored). Deleting that file invalidates every session.
- **Session invalidation:** admin password resets, admin role/active-state changes, and self-service password changes increment the user's `session_version`. Tokens issued with an older version are rejected with HTTP 401, including sessions that would otherwise become usable again after a forced password change.
- **Logout and revocation:** logout stores the token's SHA-256 hash in `revoked_tokens` until expiry, so revocation survives restarts. Expired rows are pruned in batches of 1,000 under a non-blocking MariaDB advisory lock, using a separate best-effort connection/transaction after the revocation and audit commit; cleanup failures cannot roll back a successful logout.
- **Browser authentication:** API requests authenticate only with an explicit `Authorization: Bearer` header. The legacy `auth_token` cookie fallback is disabled; ambient browser cookies cannot authenticate state-changing API requests.
- **Brute-force limit:** 5 failed logins per 5 minutes per key, then `429`. Attempts are stored in the `login_attempts` table, shared across workers and retained across restarts. If shared storage is unavailable at startup or during login, login fails closed with HTTP 503; process-local memory is not used as a fallback.
- **Audit log:** mandatory security and administrative events are durably written to `audit_logs` and are admin-readable at `GET /api/auth/audit-logs`: `LOGIN_FAILED`, `LOGIN_RATE_LIMITED`, `LOGIN_SUCCESS`, `LOGOUT`, `PASSWORD_CHANGED`, `USER_CREATED`, `USER_PASSWORD_RESET`, `USER_UPDATED`, `USER_DELETED`.
- **First admin:** created on the first start when no admin exists. The password is `ADMIN_DEFAULT_PASSWORD` if set, otherwise a random one-time password written to `.initial_admin_credential` with mode 0600 (gitignored). The account is flagged `must_change_password`. Until the password is changed, the API rejects every request except `/api/auth/me`, `/api/auth/logout` and `/api/auth/change-password` (403, code `password_change_required`), and the dashboard shows a blocking dialog. New passwords must be at least 10 characters, differ from the old one, and reject common/weak variants and the username. Delete `.initial_admin_credential` after first login.

## 2. Authorization

Enforced centrally in `web/app.py` for every `/api/*` route (fail-closed): no valid token means `401`; a valid token with insufficient role means `403`. Only `POST /api/auth/login` and the static home page are public. Viewer-readable GET routes are explicitly enumerated; unclassified registered GET routes are denied until their data exposure and side effects are reviewed. A sweep of the registered API operations verifies this policy.

| Role | Can do |
|---|---|
| `viewer` | Read dashboards, results, paper order history, and data. **Cannot call any mutating endpoint** |
| `trader` | Everything a viewer can, plus backtests, paper configs/deploys/sync/circuit breakers, paper-order cancellation, ML runs, custom strategies, master-data backfill and feature builds, research-bot control, wallet tracking changes |
| `admin` | Everything, plus user management (`/api/users`), `GET /api/settings/status`, the Telegram test message, and the audit log |

Rules live in the role-prefix policies and the `AUTHENTICATED_READ_PATHS` route-template allowlist in `web/app.py`. New GET routes remain denied until explicitly added to the reviewed read allowlist; new mutations remain denied unless covered by an explicit role policy.

## 3. Web and transport

- **CORS** is limited to `SIGNAL_BOT_ALLOWED_ORIGINS` (default `http://localhost:8050,http://127.0.0.1:8050`), with credentials allowed.
- The app serves plain HTTP. For anything beyond localhost, put it behind a TLS-terminating reverse proxy.
- The web server binds to `127.0.0.1` by default. Set `SIGNAL_BOT_HOST=0.0.0.0` (or `--host`) only behind a firewall and a TLS-terminating reverse proxy; the app has no TLS of its own.
- The dashboard builds HTML from API data with an escape helper; wallet and token values are validated server-side before they are stored.
- ML experiment detail IDs are filename-validated, resolved under the configured results directory, and required to point to a regular file inside that directory; symlink/path escapes are rejected.

## 4. Secrets and data

- Keep keys only in `.env` (gitignored): database credentials, `TELEGRAM_BOT_TOKEN`, LLM and news API keys.
- Database credentials should belong to a dedicated MySQL user restricted to the `crypto_signals` database. `run_all.py` does not apply migrations automatically by default. Apply `python -m db.migrate` as a separately authorized deployment step using an account with the required DDL privileges. Startup migration execution requires the explicit `SIGNAL_BOT_AUTO_MIGRATE=1` opt-in; `SKIP_DB_MIGRATE=1` remains an override that suppresses startup migrations. The web API and service workers verify the RetailBot2, Screener and Wallet Tracker schemas read-only; those runtime paths do not need ALTER privilege for these schemas.
- Telegram failures are logged without the bot token (error type and HTTP status only).
- Never commit `.env`, `.app_secret` or `.initial_admin_credential`. If a token or key is pasted into a chat, ticket or log, revoke and replace it.

## 5. Telegram Delivery Authorization Boundary

All outbound Telegram delivery across the repository is strictly opt-in and fail-closed:
- **Global Delivery Authorization:** External Telegram delivery is disabled application-wide unless `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1` is explicitly set in the environment. Any absent, empty, `0`, `false`, whitespace-padded, or unrecognized value suppresses outbound HTTP calls.
- **No Constructor Bypasses:** No public `allow_delivery` constructor parameter or setter mechanism is permitted on notifiers or alert dispatchers. Authorization is determined dynamically and exclusively via `os.getenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "").strip() == "1"`.
- **Shared Notifier Engine (`notifications/telegram_notifier.py`):** `send_telegram_message()` strictly enforces the global authorization boundary, returning a safe failure with suppression details and logging formatted previews locally without issuing HTTP requests. All caller wrappers (`send_signal_alert`, `send_trade_close_alert`, `send_deployment_alert`, `send_news_signal_alert`) fail closed.
- **Connection Test Endpoint (`POST /api/telegram/test` & `test_telegram_connection`):** Administrator RBAC is preserved, and the global authorization boundary is enforced before making `/getMe` or test ping requests. When unauthorized, the endpoint returns HTTP 400 with a clear diagnostic message without issuing any external network calls.
- **RetailBot2 Dual Authorization:** RetailBot2 direct delivery additionally requires `RETAILBOT2_ALLOW_DIRECT_TELEGRAM=1` alongside the global flag; production trade alerts route asynchronously through `rdt_telegram_outbox` consumed by `paper.outbox_worker`.
- **Smart Money Screener (`Screen/screening.py` & `Screen/smart_money_engine.py`):** Both primary and legacy screener senders fail closed when delivery authorization is missing. Generated alerts and operational messages are formatted and logged locally (`SmartMoneyDetector` log stream) to preserve operator visibility without making Telegram API requests. Candidate signals remain durably queued in `smart_money_signals` without burning retry attempts.
- **Wallet Tracker (`wallet_tracker/telegram.py`):** Fails closed when global delivery authorization is missing. Swap digest alerts are formatted and logged locally (`wallet_tracker.telegram` log stream). The worker pauses delivery while continuing to queue fresh swaps in `wallet_telegram_alerts` without exhausting attempts.
- **Zero Startup Network Validation:** Notifiers and clients perform no network calls (such as `/getMe`) during startup or instantiation. Missing tokens or chat IDs fail safely with local warning logs.

## 6. Safety limits on trading

This is paper trading only, so there is no exchange API key or order endpoint in the codebase. The paper engine additionally blocks new entries when risk cannot be verified (see `paper/circuit_breaker.py`: max concurrent positions, max daily loss, portfolio notional exposure, and a shared emergency stop persisted in `system_flags`). If the shared stop state cannot be read, new entries fail closed.

## 7. Tests

`tests/test_auth_security.py` covers login rate limiting and the token revocation lifecycle, and the wallet route tests assert that a viewer cannot mutate. `tests/test_route_authorization_sweep.py` sweeps every `/api` route from the live route table: anonymous requests get 401, a viewer cannot reach any mutating route, only admins reach admin prefixes, and a flagged account is blocked everywhere except the password-change endpoints. Login attempts are stored in the `login_attempts` table (shared across workers, survives restarts); if shared storage is unavailable, login fails closed with HTTP 503 rather than falling back to process-local counters.
