# Crypto Signal Bot — Production Runbook & Operations Guide

## 1. Architecture Overview

Crypto Signal Bot is an institutional quantitative trading, backtesting, paper execution, and real-time market analysis platform.

### Core Components
- **Web API & Workstation Service (`web.app`)**: FastAPI service providing authenticated REST and WebSocket endpoints (`/ws/market`, `/ws/paper`, `/ws/system`), serving the Modern UI bundle from `frontend/dist`.
- **Database (`mariadb`)**: MariaDB 10.11 storage for OHLCV bars, market microstructures, paper trading ledgers, user accounts, and immutable audit logs.
- **Market Data Collectors (`run_collectors.py`)**: Real-time Binance spot/futures tick, kline, and open interest ingestion.
- **Funding Rate Poller (`collectors/funding_poller.py`)**: 8-hour funding rate calculation and settlement tracking.
- **Paper Execution Outbox Worker (`paper/outbox_worker.py`)**: Advisory-locked transactional outbox processor for Telegram notifications and paper order fills.

---

## 2. Quickstart Deployment (Docker Compose)

### Prerequisites
- Docker Engine 24+ and Docker Compose v2+
- Port 8050 (HTTP/WS) accessible or behind reverse proxy (Caddy / Nginx)

### Pre-deploy Telegram authorization

Ensure `app_telegram_settings.delivery_authorized` is explicitly set on
`crypto_signals` through Settings. The outbox stays paused for claiming when the
setting is absent or false. The independent environment delivery ceiling still
applies. The worker's explicit-setting database list is defined by
`EXPLICIT_OUTBOX_DELIVERY_DATABASES` and defaults to `crypto_signals`.

The delivery worker also checks `service_control` on its outbox database. Grant
the delivery identity SELECT on that table. An unreadable pause control blocks
claims. Grant SELECT, INSERT, and UPDATE on `collector_heartbeat` for the
`telegram_outbox` heartbeat. Observation mode retains its existing read-only
permissions and does not write a heartbeat.

### Steps
1. Clone repository and generate secrets:
   ```bash
   cp .env.example .env
   # Set strong passwords for MYSQL_ROOT_PASSWORD, MYSQL_PASSWORD, and APP_SECRET_KEY
   ```
2. Build and launch all services:
   ```bash
   docker compose up -d --build
   ```
3. Run schema migrations:
   ```bash
   docker compose exec web python -m db.migrate
   ```
4. Verify service health:
   ```bash
   curl -s http://127.0.0.1:8050/healthz
   curl -s http://127.0.0.1:8050/readyz
   ```
5. **Production HTTPS / TLS (Automatic Let's Encrypt via Caddy):**
   ```bash
   SIGNAL_BOT_DOMAIN=desk.yourdomain.com \
   SIGNAL_BOT_ALLOWED_ORIGINS=https://desk.yourdomain.com \
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```
   This binds port `8050` to loopback only and terminates HTTPS + WebSocket upgrades via `deploy/Caddyfile`.

---

## 3. Database Administration & Backup Procedures

### Database Migrations
Always run migrations after deploying updates:
```bash
docker compose exec web python -m db.migrate
```
To check migration status without making changes:
```bash
docker compose exec web python -m db.migrate --status
```

### Automated Backup Procedure (mariadb-dump)
Execute a consistent transaction snapshot:
```bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
docker compose exec mariadb mariadb-dump \
  -u root -p"${MYSQL_ROOT_PASSWORD}" \
  --single-transaction \
  --quick \
  --routines \
  --triggers \
  "${MYSQL_DATABASE}" | gzip > "backup_signal_bot_${TIMESTAMP}.sql.gz"
```

### Database Restore Procedure
To restore from a backup file:
```bash
gunzip < backup_signal_bot_YYYYMMDD_HHMMSS.sql.gz | docker compose exec -T mariadb mariadb \
  -u root -p"${MYSQL_ROOT_PASSWORD}" "${MYSQL_DATABASE}"
```

---

## 4. Security & Compliance Checklist

1. **Secret Key & Tokens**:
   - `APP_SECRET_KEY`: 32-byte hex key for Fernet database encryption of Telegram bot credentials.
   - Initial admin password bootstrap is logged once on creation to `.initial_admin_credential` with `0600` permissions.
2. **First-Login Compliance Gate**:
   - All users must accept the Terms of Service & Financial Risk Disclaimer (`POST /api/auth/accept-terms`) and rotate default passwords before full workstation access is granted.
3. **Safety Rail**:
   - `RETAILBOT2_ALLOW_TRADING=0` is hardcoded across all environments to ensure no real funds are ever placed at risk. Real money trading is disabled.
4. **Rate Limiting**:
   - Compute-intensive routes (`POST /api/backtest/*`, `POST /api/ml/*`) are limited to 30 requests/minute per client with automatic HTTP 429 response containing `Retry-After`.

---

## 5. Monitoring & Health Probes

- **Liveness Probe**: `GET /healthz` (200 OK)
- **Readiness Probe**: `GET /readyz` (200 Ready / 503 Not Ready)
- **Status API**: `GET /api/system/status`
- **Operator Audit Log**: `GET /api/auth/audit-logs` (Admin only)

---

## 6. Commercial Licensing & Tagged Releases

### Minting a Customer License Key
Generate a signed `SB1` license key bound to a customer name, tier (`starter`, `pro`, `desk`, `enterprise`), seat limit, and expiration window:
```bash
python scripts/generate_license.py \
  --customer "Acme Quant Desk" \
  --tier pro \
  --max-users 10 \
  --days 365 \
  --signing-key "${SIGNAL_BOT_LICENSE_SIGNING_KEY}"
```
Verify any issued key:
```bash
python scripts/generate_license.py --verify "SB1...." --signing-key "${SIGNAL_BOT_LICENSE_SIGNING_KEY}"
```

### Publishing a Versioned Release (`v1.0.0`)
Pushing a semantic version tag automatically runs the release test gate, publishes the hardened Docker image to GitHub Container Registry (`ghcr.io/kurnfr/signal-bot:<tag>`), and attaches `docker-compose.yml`, `.env.example`, and this runbook to the GitHub Release:
```bash
git tag -a v1.0.0 -m "Release v1.0.0 — Commercial Quantitative Trading Workstation"
git push origin v1.0.0
```

### Preserve custom strategies before migration 038

Before running `db.migrate`, copy the approved legacy
`custom_strategies.json` into the migrating checkout or container at the
repository root. Docker ignores the JSON file at build time. For an approved
Docker rehearsal, copy the protected file into `web:/app/custom_strategies.json`
and restrict access to the migration process. On systemd, use the root of the
checkout running the migration.

If migration 038 already ran without the file, supply the protected path and
set the target `MYSQL_DATABASE` explicitly. Run the dry run before importing.

```sh
python -m tools.import_custom_strategies --path "$LEGACY_STRATEGIES" --dry-run
python -m tools.import_custom_strategies --path "$LEGACY_STRATEGIES"
python -m tools.import_custom_strategies --path "$LEGACY_STRATEGIES" --dry-run
```

Verify that the final dry run reports every definition as already present and
zero invalid definitions. Existing DB rows are preserved. In the missing-file
rehearsal, make the file visible to the web app after migration so readiness
and Strategy Library can detect the missing names. Recheck both after import.

Before rolling back to a JSON-based release, export the current DB definitions
to a new restricted file with `python -m tools.export_custom_strategies --out "$STRATEGY_EXPORT"`.
Edit and creation history after migration exists only in the DB. Preserve the
export, verify its count, then install it at the old release's expected JSON path.
