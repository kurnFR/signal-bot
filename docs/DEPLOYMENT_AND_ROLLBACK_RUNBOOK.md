# Phase 2A Deployment and Rollback Runbook

**Repository:** `kurnFR/signal-bot`  
**Target:** Phase 2A Safe Supervisor Deployment & Operational Recovery  
**Baseline Revision:** PR #729 (`3887544`) / Master (`b89d3f1`)  
**Scope:** Process supervision (`run_all.py`), Phase 2A default service set, fail-closed startup configuration, and operational rollback.

---

## 1. Purpose and Safety Boundaries

This runbook defines the exact operational procedure for deploying, verifying, restarting, and rolling back the **Phase 2A** service stack under `run_all.py`.

### Safe Default Service Set
The default supervisor startup is strictly limited to the Phase 2A stack:
1. `web_server` (`run_web.py --port 8050 --host 127.0.0.1`)
2. `collectors` (`run_collectors.py`)
3. `screener` (`Screen/screening.py`)
4. `wallet_tracker` (`run_wallet_tracker.py`)

Funding rate collection runs through the separate `signal-funding-collector.service` unit. When updating a server that already uses this unit, restart it after pulling and verify its journal and funding watermarks. Use the existing dedicated `FUNDING_DB_*` configuration. The supervisor's collectors process handles candles and open interest.

### Held-Back Processes (Strict Independent Opt-Ins)
- **Paper Engine** (`run_paper_engine.py`): Excluded by default. Requires explicit `PAPER_ENGINE_ENABLED=1`.
- **News AI** (`run_news_ai.py`): Excluded by default. Requires explicit `NEWS_AI_ENABLED=1`.
- **RetailBot2** (`paper/retailbot2.py`): Excluded by default. Requires explicit `RETAILBOT2_ENABLED=1`. Furthermore, trade execution requires an independent authorization gate: `RETAILBOT2_ALLOW_TRADING=1`.
- **RetailBot2 Outbox Worker** (`paper.outbox_worker`): Excluded by default. Requires **both** `RETAILBOT2_OUTBOX_WORKER_ENABLED=1` AND `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`.
- **Telegram Delivery**: Disabled globally by default. Enabling the supervisor or any individual service never enables live Telegram notifications.

### Database Migration Safety
- **Automatic migrations are disabled by default**: `run_all.py` does not apply schema changes on startup (`SIGNAL_BOT_AUTO_MIGRATE=0` or unset).
- Routine code deployments must **never** bundle unapproved schema migrations.
- Migrations are applied in a separately authorized deployment step (`python3 -m db.migrate`) using an account with DDL privileges.
- If automatic migrations are explicitly requested (`SIGNAL_BOT_AUTO_MIGRATE=1`) and fail, the supervisor **fails closed**, immediately shuts down all child processes (including embedded MariaDB if started), and exits with status code 1.

### Repository Hygiene
- When updating or synchronizing the production checkout, **do not overwrite or delete** modified screenshot files in `docs/screenshots/`.

---

## 2. Pre-Deployment Verification Checklist

Execute and record every check prior to deployment:

- [ ] **Deployment Authorization**: Deployment window and change ticket approved by technical lead.
- [ ] **Commit SHA Verification**: Confirm target commit SHA matches approved release tag or PR.
  ```bash
  git rev-parse HEAD
  git status
  ```
- [ ] **Screenshot Preservation**: Confirm tracked screenshots are preserved and untouched.
  ```bash
  git diff docs/screenshots/
  ```
- [ ] **Database Backup**: Take a verified pre-deployment logical snapshot of the database before any deployment action.
  ```bash
  export BACKUP_DIR="/home/cmswh/backups"
  export BACKUP_FILE="${BACKUP_DIR}/crypto_signals_pre_phase2a_$(date +%Y%m%d_%H%M%S).sql"
  mysqldump --single-transaction --routines --triggers --events \
    -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p "$MYSQL_DATABASE" > "$BACKUP_FILE"
  sha256sum "$BACKUP_FILE" > "${BACKUP_FILE}.sha256"
  ```
- [ ] **Migration Preflight Inspection (Read-Only)**: Inspect current schema version and pending migrations without mutating data.
  ```bash
  python3 -m db.migrate --status
  python3 -m db.migrate --dry-run
  ```
- [ ] **Environment Configuration Audit**: Confirm `.env` reflects safe Phase 2A defaults:
  - `SIGNAL_BOT_AUTO_MIGRATE=0` (or omitted)
  - `SKIP_DB_MIGRATE=0` (or omitted)
  - `PAPER_ENGINE_ENABLED=0` (or omitted)
  - `NEWS_AI_ENABLED=0` (or omitted)
  - `RETAILBOT2_ENABLED=0` (or omitted)
  - `RETAILBOT2_ALLOW_TRADING=0` (or omitted)
  - `RETAILBOT2_OUTBOX_WORKER_ENABLED=0` (or omitted)
  - `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=0` (or omitted)

---

## 3. Step-by-Step Deployment Procedure

### Step 3.1 — Database Migration (Separately Authorized Step)
If schema migrations are required and approved for this release:
```bash
# Execute migrations out-of-band using dedicated migration runner
python3 -m db.migrate

# Verify all migrations are recorded as applied
python3 -m db.migrate --status
```
*Note: If no migrations are scheduled, skip Step 3.1.*

### Step 3.2 — Start the Phase 2A Supervisor
Start `run_all.py` using your environment's service manager or directly in the background:

**Direct execution (or via screen / tmux / nohup):**
```bash
python3 run_all.py
```

**Systemd service execution (if configured):**
```bash
sudo systemctl restart signal-bot
sudo systemctl status signal-bot
```

### Step 3.3 — Post-Startup Verification & Health Checks

1. **Verify Running Process Table:**
   Confirm exactly four child processes are spawned under Python:
   ```bash
   ps aux | grep -E "run_all|run_web|run_collectors|screening|run_wallet_tracker" | grep -v grep
   ```
   **Pass Criteria:**
   - 1 supervisor process (`run_all.py`)
   - 1 web server process (`run_web.py`)
   - 1 collectors process (`run_collectors.py`)
   - 1 screener process (`Screen/screening.py`)
   - 1 wallet tracker process (`run_wallet_tracker.py`)
   - **Zero** `run_paper_engine.py`, `run_news_ai.py`, `retailbot2.py`, or `outbox_worker` processes.

2. **Verify HTTP API Endpoint:**
   Check that the local listener responds and enforces authentication (adjust the port if configured):
   ```bash
   curl -s -w "\nHTTP Status: %{http_code}\n" http://127.0.0.1:8050/api/system/status
   ```
   **Expected:** HTTP 401 for this credential-free probe. To check readiness, sign in to the dashboard, open the system status view, and confirm the database reports `ok` and the enabled collectors have fresh heartbeats. An HTTP response alone only confirms the listener is reachable.

3. **Verify Supervisor Logs:**
   Inspect startup log output:
   ```bash
   # Look for expected log lines:
   # [INFO] Automatic database migrations are disabled...
   # [INFO] Starting Signal Bot -- all processes
   # [INFO] Running: web_server, collectors, screener, wallet_tracker
   ```
   **Pass Criteria:**
   - No crash loops or continuous restart warnings.
   - No unauthorized services in "Running" list.
   - Clean startup messages from collectors, screener, and wallet tracker.

4. **Verify Database Connections & Advisory Locks:**
   - Screener updates `screener_heartbeat` table periodically.
   - Wallet tracker holds `signal_bot_wallet_tracker` MariaDB advisory lock (`SELECT IS_USED_LOCK('signal_bot_wallet_tracker')`).

---

## 4. Process Supervision, Restart, and Shutdown Mechanics

### Crash Isolation & Restart Backoff
- Each child runs in its own OS subprocess (not thread).
- If any individual child crashes (non-zero exit code), the supervisor logs a warning:
  `[<service>] exited (code <N>); restarting in 5s`
- The supervisor sleeps for `RESTART_BACKOFF_SECONDS` (5 seconds) before restarting the failed child.
- Other running processes continue unaffected.

### Graceful Shutdown
- Triggered by sending `SIGINT` (Ctrl+C) or `SIGTERM` to the `run_all.py` process.
- The supervisor immediately sets an internal `_shutdown` event:
  - Wakes up any worker threads waiting in restart backoff.
  - Sends `SIGTERM` to all running children.
  - Waits up to 10 seconds for all children to exit cleanly.
- **Normal Child Force-Kill**: If a normal child (`web_server`, `collectors`, `screener`, `wallet_tracker`, `paper_engine`, `news_ai`, `retailbot2`) does not terminate within the 10-second deadline, the supervisor sends `SIGKILL`.
- **Outbox Worker Lock Safety**: If `retailbot2_outbox` is running, the supervisor **never** sends `SIGKILL` after timeout. It waits indefinitely until the worker safely confirms transport shutdown and releases its MariaDB advisory lock, avoiding dual-worker lock races.

---

## 5. Failure Recovery and Incident Scenarios

### Scenario A: Database Unreachable on Startup
- **Symptom:** `ensure_database_running()` logs:
  `[WARNING] Database at <host>:<port> is not reachable and local MariaDB binary not found.`
- **Behavior:** The supervisor proceeds safely; child processes fail to connect, exit with an error, and the supervisor restarts them with 5-second backoff.
- **Recovery:**
  1. Inspect database host availability and network routing.
  2. Verify credentials in `.env`.
  3. Once DB comes online, children automatically connect on the next restart attempt without restarting `run_all.py`.

### Scenario B: Migration Failure During Startup
- **Symptom:** `SIGNAL_BOT_AUTO_MIGRATE=1` is set, but `python3 -m db.migrate` exits with non-zero code.
- **Behavior:** Supervisor logs critical error, calls `_shutdown_all()` to terminate any started child processes (such as local MariaDB), and terminates with `sys.exit(1)`.
- **Recovery:**
  1. Inspect database error details in output.
  2. Run `python3 -m db.migrate --status` manually to locate the failing script.
  3. Correct the schema conflict or set `SIGNAL_BOT_AUTO_MIGRATE=0` to keep application startup decoupled from schema remediation.

### Scenario C: Malformed Environment Variables
- **Symptom:** Operator configures invalid values (e.g. `WEB_PORT="foo"`, `MYSQL_PORT=""`, `PAPER_ENGINE_ENABLED="true"`).
- **Behavior:**
  - `WEB_PORT` safely falls back to `8050`.
  - `SIGNAL_BOT_HOST` safely falls back to `0.0.0.0`.
  - `MYSQL_PORT` safely falls back to `3306`.
  - `PAPER_ENGINE_ENABLED="true"` fails safe (remains disabled; only `"1"` enables).
  - `SIGNAL_BOT_AUTO_MIGRATE="maybe"` fails closed (refuses startup).
- **Recovery:** Correct malformed variable syntax in `.env`.

---

## 6. Rollback Procedure

If severe defects, database errors, or service regressions occur post-deployment:

### Rollback Step 1 — Stop the Running Supervisor
```bash
# Send SIGTERM to allow graceful child termination
pkill -TERM -f "run_all.py"

# Verify all child processes have stopped
ps aux | grep -E "run_all|run_web|run_collectors|screening|run_wallet_tracker" | grep -v grep

# If any process hung past grace period (non-outbox), terminate forcefully
pkill -9 -f "run_web.py|run_collectors.py|Screen/screening.py|run_wallet_tracker.py"
```

### Rollback Step 2 — Revert Source Code
Checkout the previous known-good commit:
```bash
git checkout <PREVIOUS_STABLE_COMMIT_SHA>

# Verify working tree status without modifying screenshots
git status
```

### Rollback Step 3 — Database Schema Rollback (If Migrations Were Applied)
If a newly applied migration caused data corruption or schema incompatibility:
1. Confirm application processes are stopped.
2. Restore the pre-deployment database snapshot:
   ```bash
   mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p "$MYSQL_DATABASE" < "$BACKUP_FILE"
   ```
3. Verify restored schema:
   ```bash
   python3 -m db.migrate --status
   ```

### Rollback Step 4 — Restore Previous Configuration
If `.env` was changed during deployment:
```bash
cp .env.bak .env
```

### Rollback Step 5 — Restart Previous Stable Version
```bash
python3 run_all.py
```

### Rollback Step 6 — Post-Rollback Validation
1. Re-run verification checks from Section 3.3.
2. Repeat the listener, authentication, and signed-in readiness checks in Step 3.3.
3. Confirm collectors and screener resume normal operation.
4. Document the incident, root cause, and rollback timestamp in the incident log.
