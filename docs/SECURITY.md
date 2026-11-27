# Security

How access, transport, secrets, rate limits, Telegram delivery, and commercial licensing are enforced in production.

## 1. Authentication & Session Lifecycle

- **Passwords:** PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte random salt (`pbkdf2:sha256:<iterations>$<salt>$<hash>`), compared in constant time.
- **Credential & Config Bounds:** Usernames (30 chars), emails (254), passwords (1,024), and `paper_configs` columns are strictly bounded in Pydantic schemas and persistence helpers before any DB lookup or PBKDF2 work.
- **Tokens & Production TTL:** HMAC-SHA256-signed bearer tokens carrying user ID, role, per-user `session_version`, and expiry. Default lifetime is **1 hour (`3600s`)** when `SIGNAL_BOT_ENV=production` (`86400s` in development; overridable via `ACCESS_TOKEN_EXPIRE_SECONDS`). The Modern UI schedules proactive silent refresh via `POST /api/auth/refresh` 5 minutes before expiry.
- **Production Secret Fail-Fast:** `validate_production_secret_config()` runs at FastAPI startup and refuses to start if `SIGNAL_BOT_ENV=production` and `APP_SECRET_KEY` is missing or shorter than 32 characters. `docker-compose.yml` enforces mandatory `APP_SECRET_KEY`, `MYSQL_PASSWORD`, and `MYSQL_ROOT_PASSWORD` via `:?` syntax.
- **Single-Use WebSocket Tickets:** Browser WebSocket connections request a 30-second, single-use opaque ticket from `POST /api/auth/ws-ticket` (stored hashed as SHA-256 in MariaDB `ws_auth_tickets`, migration 035, for multi-worker consistency) instead of exposing long-lived bearer tokens in URLs.
- **Session Invalidation & Revocation:** Logout, admin password resets, role/active-state changes, and self-service password changes increment `users.session_version` and record revoked token hashes in `revoked_tokens`. Expired rows are pruned in bounded batches under a non-blocking MariaDB advisory lock.
- **Browser Authentication:** API requests authenticate exclusively via `Authorization: Bearer` headers (no ambient cookie authentication).
- **Brute-Force & Compute Rate Limiting:**
  - **Login:** 5 failed attempts per 5 minutes per key (`login_attempts`, shared across workers; fails closed with HTTP 503 if DB is unavailable).
  - **Compute-heavy POST routes** (`/api/backtest/*`, `/api/ml/*`, `/api/custom-strategy/generate-ai`, `/api/wallets/poll`): 30 requests / 60s per user backed by MariaDB `compute_rate_limits` (migration 035), returning HTTP `429` with `Retry-After`.
- **Immutable Audit Log:** Security and administrative events are durably persisted to `audit_logs` and viewable/exportable (CSV) by admins in **Settings → Operator Audit Logs** (`GET /api/auth/audit-logs`, filterable by `action` and `username`): `LOGIN_FAILED`, `LOGIN_RATE_LIMITED`, `LOGIN_SUCCESS`, `LOGOUT`, `PASSWORD_CHANGED`, `TERMS_ACCEPTED`, `TELEGRAM_CONFIG_UPDATE`, `USER_CREATED`, `USER_PASSWORD_RESET`, `USER_UPDATED`, `USER_DELETED`.
- **First Admin & Compliance Gate:** On first start, an admin account is seeded (`ADMIN_DEFAULT_PASSWORD` or random one-time password in `.initial_admin_credential`, mode `0600`) with `must_change_password=1`. First login enforces password rotation and Terms & Financial Risk Disclaimer acceptance (`POST /api/auth/accept-terms`, recorded in `users.terms_accepted_at`).

## 2. Authorization (RBAC)

Enforced centrally in `web/app.py` (`api_security_boundary`) for every `/api/*` route (fail-closed: missing/invalid token → `401`, insufficient role → `403`). Only `/healthz`, `/readyz`, `POST /api/auth/login`, `/api/system/test-identity` (loopback only), and the web UI entrypoints are public.

| Role | Can do |
|---|---|
| `viewer` | Read allowlisted dashboards, results, paper history, and data (`AUTHENTICATED_READ_PATHS`), plus manage own session (`/api/auth/logout`, `/refresh`, `/ws-ticket`, `/accept-terms`, `/change-password`). **Cannot call any mutating domain endpoint.** |
| `trader` | Everything a viewer can, plus backtests, paper configs/deploys/sync/circuit breakers, order cancellation, ML runs, custom strategies, master-data backfill/features, screener/research controls, and wallet tracking mutations. |
| `admin` | Everything, plus user & role management (`/api/users`), admin diagnostics (`/api/settings/status`), encrypted Telegram config & test (`PUT /api/telegram/config`, `POST /api/telegram/test`), and Operator Audit Logs (`/api/auth/audit-logs`). |

## 3. Web, Transport & Container Hardening

- **Security Headers Middleware (`web/app.py`):** Attaches `X-Request-ID`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Content-Security-Policy`, and `Strict-Transport-Security` (HSTS in production / HTTPS).
- **CORS:** Restricted to `SIGNAL_BOT_ALLOWED_ORIGINS` (defaults to loopback-only in production if unset); allows `GET, POST, PUT, PATCH, DELETE, OPTIONS` and `Authorization, Content-Type, X-Request-ID`.
- **TLS Termination:** FastAPI binds to `127.0.0.1` by default. For internet-facing deployments, use the bundled Caddy HTTPS overlay (`docker-compose.prod.yml` + `deploy/Caddyfile`) for automatic Let's Encrypt TLS and WebSocket upgrades while keeping port `8050` bound to loopback.
- **Container Hardening:** Multi-stage `Dockerfile` runs as non-root `signalbot` (uid `1001`) with a `.dockerignore` excluding `.git`, `.env`, `.app_secret`, `venv/`, and `frontend/node_modules/`.
- **Path Containment:** ML experiment and model artifact endpoints (`GET /api/ml/models/{model_id}`) validate IDs against strict regexes and reject path traversal or symlink escapes.

## 4. Secrets and Data

- Store credentials only in `.env` (gitignored) or encrypted in MariaDB (`app_telegram_settings` uses Fernet encryption derived from `APP_SECRET_KEY`).
- Never commit `.env`, `.app_secret`, or `.initial_admin_credential`.
- Telegram errors are logged without exposing the bot token (error type and HTTP status only).
- `run_all.py` does not auto-migrate by default; apply `python -m db.migrate` as a separate authorized step. Startup schema checks are **read-only** verification only (`verify_signal_schema()` / `verify_retailbot2_schema()` inspect the live schema and warn toward `python -m db.migrate`); the runtime never issues DDL — schema changes require the separate, privileged migration step, so runtime database accounts need no ALTER/CREATE privileges.

## 5. Telegram Delivery Authorization Boundary

All outbound Telegram delivery across `notifications/telegram_notifier.py`, `Screen/screening.py`, `paper/outbox_worker.py`, and `wallet_tracker/telegram.py` shares unified credential and authorization resolution (`get_telegram_config()`, `is_telegram_delivery_authorized()`) and fails closed:
- **Encrypted DB Settings with Env Fallback:** Admins can configure Bot Token, Target Chat ID, and delivery authorization in **Settings** (`PUT /api/telegram/config` → `app_telegram_settings`, migration 034). When no DB row is configured, the system falls back to `TELEGRAM_BOT_TOKEN` / `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` / `TELEGRAM_TARGET_CHAT_ID`, and `TELEGRAM_DELIVERY_ENABLED=1` (deprecated alias: `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`). **Env hard ceiling:** `TELEGRAM_DELIVERY_ENABLED` (or its deprecated alias `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY`) is a hard ceiling over the DB setting — when neither is `1`, delivery is suppressed everywhere no matter what the Settings toggle says; when it is `1`, the DB `delivery_authorized` flag decides (no DB row → the env flag alone decides). All consumers (notifier, wallet tracker, report scheduler, outbox worker, Screener) gate on the single shared check `is_telegram_delivery_authorized()`, and `GET /api/telegram/status` reports that effective value.
- **Status Transparency:** `GET /api/telegram/status` explicitly returns `deliveryAuthorized` so the UI never displays a false-positive healthy state while outbound delivery is suppressed.
- **Independent Screener Gate:** Smart Money Screener alerts are gated by both the master screener toggle and the independent `telegram_enabled` override.
- **Paper Trading Alerts:** Both trade entries and position closes check per-deployment `telegram_enabled` before dispatching.

## 6. Safety Limits on Trading

This platform performs **paper/simulated execution only** — no live exchange trading keys or order-submission endpoints exist.
- **Paper Circuit Breakers (`paper/circuit_breaker.py`):** Fail closed on max concurrent positions, max daily realized loss, max total open notional, and cross-process emergency stop (`system_flags`).
- **RetailBot2 Dual Gate (retired with the worker):** The old gate
  (`bot_control.enabled=TRUE` plus explicit `RETAILBOT2_ALLOW_TRADING=1`,
  with `RETAILBOT2_OBSERVATION_ONLY=1` suppressing all mutations) died
  with `paper/retailbot2.py`. There is no trading worker left for it to
  guard: the platform is paper-only, the saved `bot_control` row is
  archive history served read-only by `GET /api/research/retailbot2/control`,
  and Telegram delivery is governed solely by the §5 ceiling
  (`TELEGRAM_DELIVERY_ENABLED`, deprecated alias
  `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY`).

## 7. Commercial Licensing

- `web/licensing.py` verifies HMAC-SHA256 signed `SB1` license keys (`SIGNAL_BOT_LICENSE_KEY`, signed with `SIGNAL_BOT_LICENSE_SIGNING_KEY` or `APP_SECRET_KEY`).
- `POST /api/users` enforces the licensed `maxUsers` active-seat limit (returning HTTP `403` when exceeded or unlicensed in production).
- In non-production (`SIGNAL_BOT_ENV != production`) without a license key, the platform runs in unrestricted `development` mode for frictionless local development and CI.
- Mint and verify keys using `scripts/generate_license.py`.

## 8. Security Test Coverage

`tests/test_auth_security.py`, `tests/test_route_authorization_sweep.py`, `tests/test_wo71955_production_hardening.py`, and `tests/test_wo71956_commercial_launch.py` verify fail-closed RBAC across every registered route, security headers, CORS `PATCH`, production secret fail-fast, multi-worker durable WS tickets and compute rate limits, login rate limiting, token revocation, and license key verification.
