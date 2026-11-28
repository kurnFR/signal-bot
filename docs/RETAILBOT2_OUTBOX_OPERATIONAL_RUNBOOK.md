# RetailBot2 Telegram Outbox Dispatcher: Operational Runbook

**Subsystem:** RetailBot2 Outbox Dispatcher & Telegram Notification Engine  
**Target Environments:** `signal_bot_test` (Staging / Development) & `crypto_signals` (Production)  
**Reference Work Orders:** WO71764, WO71765, WO71770, WO71773, WO71774, WO71776, WO71778, WO71781, WO71782 (Stage E)  
**Security Boundary:** Gated, Least-Privilege, Fail-Closed  

---

## 1. Operating Modes & Authorization Boundaries

The RetailBot2 system enforces strict separation of privileges across four distinct operational boundaries:

| Mode / Capability | Required Flag | Permitted Actions | Prohibited Actions |
| :--- | :--- | :--- | :--- |
| **Observation Mode** | `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1` | Acquire advisory lock `telegram_outbox_worker_lock`; verify schema; inspect queue totals via `SELECT` | Outbox claiming; updating row status; spawning transport subprocess; sending Telegram messages; inserting trades |
| **Test / Mock Delivery** | `mock_transport_response` configured on `signal_bot_test` | Claim events; execute mock in-memory response; transition to `sent` or `pending` | Any network/HTTP socket calls; connecting to `crypto_signals`; modifying live trades |
| **Production Telegram Delivery** | `TELEGRAM_DELIVERY_ENABLED=1` (legacy alias `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`) | Standalone worker daemon claims production events; transmits HTTP requests to Telegram API via isolated subprocess | Automated status resets without confirmation; modifying `paper_trades` |
| **Live Trading Authorization** | `RETAILBOT2_ALLOW_TRADING=1` | Execute orders on Binance Futures; persist trade rows to `paper_trades` | Live execution without operator approval; production worker does not need or inherit this flag |

> [!CRITICAL]
> **Installing, enabling, or starting the systemd service does NOT authorize outbox event enqueueing, Telegram delivery, or live trading.**
> Each boundary requires separate, explicit environment authorization and independently provisioned database credentials.

---

## 2. Worker Prerequisites

Before starting the outbox worker daemon in any environment, verify:

1. **Schema Lineage:**
   - Migration 026 (`db/migrate_retailbot2_telegram_outbox.sql`): Table `telegram_outbox` with base columns and indexes.
   - Migration 027 (`db/migrate_retailbot2_outbox_claim_token.sql`): Adds `claim_token CHAR(32) NULL AFTER locked_until`.
   - Verified 13-column schema present without references to non-existent `updated_at`.
2. **Dedicated Least-Privilege Account:**
   - Production observation: `'retailbot2_obs'@'127.0.0.1'` (SELECT only on `telegram_outbox` + advisory locks).
   - Staging: `'signal_bot_test'@'127.0.0.1'` (DML only on `signal_bot_test`).
   - Production delivery: Dedicated daemon account with SELECT/UPDATE on `telegram_outbox` only.
3. **Dedicated Configuration File:**
   - Configuration stored in `/etc/retailbot2/outbox-worker.env` with filesystem permissions `0600` owned by `retailbot:retailbot`.

---

## 3. Environment Variables Reference

| Variable | Scope | Purpose | Fail-Closed Default |
| :--- | :--- | :--- | :--- |
| `MYSQL_HOST` | All | MariaDB host address (loopback required for observation) | `127.0.0.1` |
| `MYSQL_PORT` | All | MariaDB port | `3306` |
| `MYSQL_DATABASE` | All | Target database (`signal_bot_test` or `crypto_signals`) | None (fails closed) |
| `TELEGRAM_DELIVERY_ENABLED` (alias `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY`) | Production | Explicit master gate to authorize outbound Telegram HTTP requests | Unset / `0` (delivery blocked) |
| `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED` | Production | Explicit gate to authorize read-only queue observation | Unset / `0` (observation blocked) |
| `RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER` | Production | Dedicated read-only observation database user | None (no generic fallback) |
| `RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD` | Production | Dedicated read-only observation database password | None (no generic fallback) |
| `SIGNAL_BOT_TEST_DB_USER` | Staging | Dedicated test database user for `signal_bot_test` | None (no generic fallback) |
| `SIGNAL_BOT_TEST_DB_PASSWORD` | Staging | Dedicated test database password for `signal_bot_test` | None (no generic fallback) |
| `TELEGRAM_BOT_TOKEN` | Delivery | Telegram bot API authentication token | Empty (required for delivery) |
| `TELEGRAM_CHAT_ID` | Delivery | Target Telegram chat ID | Empty (required for delivery) |

---

## 4. Service Installation Procedure (For Authorized Production Deployment)

When formal authorization to deploy the supervised worker daemon is granted:

```bash
# 1. Create dedicated system user
sudo useradd -r -s /bin/false -d /opt/signal-bot retailbot

# 2. Deploy environment configuration file with 0600 permissions
sudo mkdir -p /etc/retailbot2
sudo cp deploy/env/outbox-worker.env.example /etc/retailbot2/outbox-worker.env
sudo chown -R retailbot:retailbot /etc/retailbot2
sudo chmod 0600 /etc/retailbot2/outbox-worker.env

# 3. Install systemd unit template
sudo cp deploy/systemd/retailbot2-outbox-worker.service /etc/systemd/system/
sudo systemctl daemon-reload

# 4. Verify unit configuration syntax
systemd-analyze verify /etc/systemd/system/retailbot2-outbox-worker.service
```

---

## 5. Startup, Shutdown, and Process Management Commands

```bash
# Check service status
sudo systemctl status retailbot2-outbox-worker.service

# Start the worker daemon
sudo systemctl start retailbot2-outbox-worker.service

# Graceful shutdown (sends SIGTERM, releases advisory lock, waits up to 15s)
sudo systemctl stop retailbot2-outbox-worker.service

# Inspect live journal logs
sudo journalctl -u retailbot2-outbox-worker.service -f
```

---

## 6. Health & Queue Inspection

Operators can run the read-only observability inspector at any time:

```bash
# Formatted health summary
python3 -m paper.outbox_health --database crypto_signals --observation

# Machine-readable JSON output for monitoring tools
python3 -m paper.outbox_health --database crypto_signals --observation --json

# Sample recent dead-lettered events
python3 -m paper.outbox_health --database crypto_signals --observation --dead-letters --limit 10
```

### Health Metrics Evaluated
- `is_locked`: Advisory lock state (`telegram_outbox_worker_lock`).
- `status_counts`: Event counts grouped by status (`pending`, `processing`, `sent`, `dead`).
- `due_pending_count`: Pending events ready for immediate claim.
- `expired_processing_count`: In-flight events whose lease expired before worker acknowledgement.
- `oldest_pending_age_sec`: Age in seconds of oldest pending event.

---

## 7. Backlog & Latency Diagnosis

When `due_pending_count` or `oldest_pending_age_sec` exceeds warning thresholds:

1. **Verify Worker State:**
   ```bash
   sudo systemctl status retailbot2-outbox-worker.service
   ```
   Confirm worker is `active (running)`.
2. **Verify Advisory Lock:**
   Execute read-only lock check:
   ```sql
   SELECT IS_USED_LOCK('telegram_outbox_worker_lock'), CONNECTION_ID();
   ```
   If `NULL`, worker is not holding the dispatch lock.
3. **Inspect Network / Telegram Status:**
   Check journal logs for Telegram API rate-limiting (HTTP 429) or connection timeouts:
   ```bash
   sudo journalctl -u retailbot2-outbox-worker.service --since "15 minutes ago" | grep -E "Transient|rate|timeout"
   ```

---

## 8. Dead-Letter Diagnosis & Forensic Triage

When `dead` events accumulate (`status = 'dead'`):

```sql
SELECT id, event_key, trade_id, event_type, attempts, last_error, created_at
FROM telegram_outbox
WHERE status = 'dead'
ORDER BY id DESC LIMIT 10;
```

### Common Dead-Letter Causes
1. **Permanent Telegram Rejection (HTTP 400 / 401 / 403 / 404):** Invalid token, deleted bot, chat not found, or user blocked the bot.
2. **Malformed Payload JSON:** Corrupt JSON or unparseable event payload.
3. **Exhausted Retries (`attempts >= 8`):** Persistent upstream network partition or prolonged Telegram outage exceeding exponential backoff window (~1 hour).

> [!CAUTION]
> **Never blindly execute `UPDATE telegram_outbox SET status='pending' WHERE status='dead'`!**
> Replaying dead letters without fixing the root cause will flood Telegram rate limits or produce duplicate notifications.

---

## 9. Advisory Lock Contention & Worker Conflict

Only one active dispatcher worker may hold `telegram_outbox_worker_lock` per database:

- **Symptom:** Worker startup exits with error code `1` and log message:
  `Could not acquire advisory lock; exiting worker`.
- **Diagnosis:**
  Identify which MariaDB connection holds the lock:
  ```sql
  SELECT
      pl.ID AS connection_id,
      pl.USER,
      pl.HOST,
      pl.TIME AS seconds_connected,
      pl.STATE,
      pl.INFO
  FROM information_schema.processlist pl
  WHERE pl.ID = IS_USED_LOCK('telegram_outbox_worker_lock');
  ```
- **Remediation:** Verify if a duplicate worker instance is running (`ps aux | grep outbox_worker`). Stop duplicate processes before restarting the service.

---

## 10. Worker Crash Recovery & Stale Lease Fencing

If the worker crashes or terminates abruptly:
1. **MariaDB Advisory Lock Release:** MariaDB automatically releases `telegram_outbox_worker_lock` when the connection socket drops.
2. **Lease Expiry:** Any events in `status = 'processing'` remain locked until `locked_until <= UTC_TIMESTAMP()`.
3. **Claim Token Fencing:** When the new worker starts and claims an expired event, it issues a new `claim_token`. If a zombie transport subprocess from the crashed worker attempts to acknowledge the event, MariaDB rejects the update (`WHERE claim_token = :token`), preventing stale acknowledgements.

---

## 11. Uncertain Delivery Recovery (Safe Procedure)

An event enters uncertain delivery state when a network timeout occurs while transmitting HTTP data to Telegram:

- **Preconditions for Recovery:**
  1. Gracefully stop the worker daemon (`sudo systemctl stop retailbot2-outbox-worker.service`).
  2. Confirm zero live transport processes remain in `/proc`.
  3. Verify `IS_USED_LOCK('telegram_outbox_worker_lock')` returns `NULL`.
  4. Inspect affected event IDs and query the target Telegram chat to confirm whether the message was received.
  5. If the message **was received**: Mark `status = 'sent', sent_at = UTC_TIMESTAMP()`.
  6. If the message **was not received**: Mark `status = 'pending', attempts = attempts + 1`.
  7. Maintain an immutable audit record documenting the operator, event ID, and delivery verification.

---

## 12. Retention Policy Proposals & Dry-Run Planner

No production records should be deleted without an explicit, approved retention policy. The recommended retention matrix is:

| Category | Recommended Retention | Rationale & Operational Impact |
| :--- | :--- | :--- |
| **Sent Events** | **14 Days** | Historical audit trail, duplicate event detection (`ON DUPLICATE KEY UPDATE`), and trade execution reconciliation. |
| **Dead-Letter Events** | **90 Days** | Forensic evidence for software bugs, invalid payload structures, and Telegram API incompatibilities. |
| **Pending Events** | **Indefinite (Protected)** | Unserved signal work; must **never** be deleted by automated retention. |
| **Processing Events** | **Indefinite (Protected)** | In-flight claimed leases; must **never** be deleted by automated retention. |
| **Uncertain Delivery** | **Manual Review Required** | Ambiguous delivery states requiring operator verification before state change or purging. |

### Running the Retention Planner (Dry-Run Only)
```bash
python3 -m paper.outbox_retention --database crypto_signals --sent-days 14 --dead-days 90
```
The retention planner runs in **dry-run mode by default**, computing cutoffs, counting candidate records, and modifying zero database rows.

---

## 13. Explicit Prohibitions for Operators

Operators and administrators must **NEVER**:

- [ ] Execute `UPDATE telegram_outbox SET status = 'pending'` without completing the 6 uncertain-delivery preconditions.
- [ ] Execute `DELETE FROM telegram_outbox` directly on production without an authorized change ticket.
- [ ] Delete `pending` or `processing` events.
- [ ] Run outbox workers using `root`, `cms`, or shared application database credentials.
- [ ] Reference `updated_at` on `telegram_outbox` (column does not exist; schema is 13 columns).
- [ ] Bypass advisory lock acquisition (`GET_LOCK`) or force kill workers with `SIGKILL` without first attempting graceful `SIGTERM`.
- [ ] Replay dead-lettered events without addressing the underlying error.

---

## 14. Deployment Package Integrity & Verification (Stage F)

Prior to deploying or upgrading the RetailBot2 outbox dispatcher worker on any host, run the standalone verification tool:

```bash
# Verify integrity, SHA-256 manifest, security defaults, and systemd syntax
python3 deploy/verify_deployment_package.py --check-manifest

# Structured JSON output for CI / deployment automation
python3 deploy/verify_deployment_package.py --json
```

### Verification Checks Performed:
1. **File Presence & Integrity:** Validates SHA-256 checksums of all 9 core deployment artifacts against `deploy/package_manifest.json`.
2. **Fail-Closed Security Defaults:** Confirms `TELEGRAM_DELIVERY_ENABLED=0`, `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=0`, and `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=0` in environment templates; guarantees zero hardcoded secrets.
3. **Systemd Unit Directives:** Confirms `KillMode=mixed`, `TimeoutStopSec=15s`, non-root execution (`User=retailbot`), restart limits, and sandboxing directives.
4. **Python Module Imports:** Tests clean import of `paper.outbox_worker`, `paper.outbox_transport`, `paper.outbox_health`, and `paper.outbox_retention`.
5. **CLI Entrypoints:** Validates return codes and help flag parsing.

---

## 15. Operational Monitoring Matrix (10 Alerts)

The following 10 alerts constitute the production monitoring specification for the outbox subsystem:

| Alert ID | Condition / Query | Severity | Window | Root Cause & Operator Action |
| :--- | :--- | :--- | :--- | :--- |
| **`OutboxBacklogHigh`** | `due_pending_count > 100` | Warning | 5 min | High signal generation frequency or slow worker loop. Check CPU/DB load and worker status. |
| **`OutboxDeliveryStalled`** | `oldest_pending_age_sec > 900` while `due_pending_count > 0` | Critical | 15 min | Worker stopped, hung, or lost database connectivity. Check `systemctl status retailbot2-outbox-worker`. |
| **`OutboxDeadLetterSpike`** | `dead_count` delta >= 5 | Warning | 15 min | Intermittent Telegram API errors or schema/payload format incompatibility. Query `paper.outbox_health --dead-letters`. |
| **`OutboxDeadLetterCritical`** | `dead_count > 20` | Critical | Immediate | Systemic failure (invalid Telegram token, blocked chat, unrecoverable bug). Halt queue claiming if necessary. |
| **`OutboxLockContention`** | `IS_USED_LOCK('telegram_outbox_worker_lock')` holds foreign connection ID | Critical | Immediate | Duplicate worker or orphaned zombie connection. Terminate conflicting process before restarting worker. |
| **`OutboxWorkerCrashLoop`** | Service restarts >= 5 times within 60s | Critical | Immediate | Configuration error, invalid credentials, or missing database connection. Check `journalctl -u retailbot2-outbox-worker.service`. |
| **`OutboxExpiredLeaseAccumulation`** | `expired_processing_count > 5` | Warning | 10 min | Transport subprocess crashes, worker termination, or network timeouts exceeding the 90-second lease duration (with 10.0s transport HTTP deadline + 2.0s ack budget). An expired lease (`status='processing' AND locked_until <= UTC_TIMESTAMP()`) means claim ownership has lapsed; it is NOT proof that delivery failed. Do not blindly reset rows; inspect individual event forensic evidence. |
| **`OutboxSlowTransport`** | Delivery loop duration > 15s per batch | Warning | 15 min | Telegram API rate limits (HTTP 429), proxy latency, or DB lock contention. Inspect Telegram response latency. |
| **`OutboxRetentionStagnation`** | `sent_events > 10000` with age > 30 days | Info / Warn | 24 hours | Automated retention schedule absent or failing dry-run checks. Run `outbox_retention.py` dry-run. |
| **`OutboxSchemaDivergence`** | Worker preflight column count != 13 | Critical | Immediate | Unapplied or rolled-back migrations on target database. Verify migrations 026 and 027. |

> [!IMPORTANT]
> **Expired Lease Semantics & Investigation Guidelines:**
> 1. **Expired Lease vs. Old Processing Row:** A row is only an expired lease when `status = 'processing' AND locked_until <= UTC_TIMESTAMP()`. A processing row whose `created_at` is old but whose `locked_until` is in the future is still actively leased.
> 2. **Uncertain Delivery Outcome:** If a worker transmitted a payload to Telegram but crashed or experienced network partition before recording `status = 'sent'`, its 90-second lease will expire even though Telegram successfully delivered the alert. Blindly resetting expired leases to `pending` risks duplicate broadcasts.
> 3. **No Automatic Blanket Resets:** Never execute automated or blanket reset queries against processing events. If the worker is running, it naturally re-claims expired events under fenced tokens. If the worker is stopped, follow the per-event forensic recovery procedure in Section 17.

---

## 16. Staged 7-Step Deployment & Verification Sequence

When authorized for production deployment, operators must execute the rollout in seven strictly sequential steps:

1. **Step 1: Pre-Deployment Package Verification**
   - Run `python3 deploy/verify_deployment_package.py --check-manifest`.
   - Confirm target Git commit SHA matches authorized release tag.
   - Verify MariaDB migrations 026 and 027 applied to production database.
2. **Step 2: Host Filesystem & System User Provisioning**
   - Create system user: `sudo useradd -r -s /bin/false -d /opt/signal-bot retailbot`.
   - Provision directory structure: `sudo mkdir -p /opt/signal-bot /etc/retailbot2 /var/log/retailbot2`.
   - Install minimal runtime venv: `python3 -m venv /opt/signal-bot/venv && /opt/signal-bot/venv/bin/pip install -r deploy/requirements-worker.txt`.
3. **Step 3: Configuration & Credential Provisioning**
   - Deploy `/etc/retailbot2/outbox-worker.env` with permissions `0600` owned by `retailbot:retailbot`.
   - **Fail-Closed Default Contract (WO71795):** When copied from `deploy/env/outbox-worker.env.example`, observation authorization is disabled by default (`RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=0`). The service strictly refuses database access and exits with code 1 until the system owner explicitly enables observation mode and configures dedicated observation credentials (`retailbot2_obs`).
   - **Enabling Observation Mode:** To authorize read-only queue observation, configure:
     - `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1`
     - `RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER=retailbot2_obs`
     - `RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD=<dedicated_observation_password>`
     - `TELEGRAM_DELIVERY_ENABLED=0` and `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=0` (must remain 0; observation never authorizes delivery)
   - **Delivery Authorization Separation:** Never set `TELEGRAM_DELIVERY_ENABLED=1` (or legacy `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1`) merely to make health checks or observation work. Telegram delivery is an independent stage requiring separate owner authorization, delivery credentials (`retailbot2_dispatcher`), and removal of `--observation` from the systemd unit.
4. **Step 4: Systemd Service Installation & Timeout Verification**
   - Copy `deploy/systemd/retailbot2-outbox-worker.service` to `/etc/systemd/system/`.
   - Run `sudo systemctl daemon-reload`.
   - Run `systemd-analyze verify /etc/systemd/system/retailbot2-outbox-worker.service`.
   - **Shutdown Timeout:** The deployment unit template establishes `TimeoutStopSec=30s` (revised from 15s in WO71785), providing a safe operating margin over the worst-case 15.0s cleanup duration (10.0s transport HTTP deadline + 1.5s SIGTERM grace + 1.5s SIGKILL grace + 1.0s group verification + 1.0s DB lock release). This ensures clean MariaDB advisory lock release and prevents premature SIGKILL escalation under heavy system load.
5. **Step 5: Controlled Observation Trial (Stage C Verification)**
   - Verify fail-closed behavior: If `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED` is left at `0`, starting the service immediately fails closed with `Database credential resolution failed: Production outbox observation authorization rejected`.
   - Once observation is explicitly enabled and credentials provisioned in `/etc/retailbot2/outbox-worker.env`, start worker: `sudo systemctl start retailbot2-outbox-worker.service`.
   - Confirm advisory lock acquired: `SELECT IS_USED_LOCK('telegram_outbox_worker_lock');`.
   - Run health snapshot: `python3 -m paper.outbox_worker --database crypto_signals --health --observation`. (Note: health-only inspection on `crypto_signals` requires `--observation` with dedicated observation credentials and never consults delivery credentials or delivery authorization flags).
   - Verify zero outbox row updates and zero outbound HTTP requests for 60 seconds.
6. **Step 6: Delivery Authorization Cutover (Upon Explicit Owner Approval)**
   - Update `/etc/retailbot2/outbox-worker.env`: `TELEGRAM_DELIVERY_ENABLED=1`.
   - Update ExecStart in service unit to remove `--observation`.
   - Reload and restart: `sudo systemctl daemon-reload && sudo systemctl restart retailbot2-outbox-worker.service`.
   - Tail logs: `sudo journalctl -u retailbot2-outbox-worker.service -f`.
   - Confirm first event claims and transitions cleanly to `sent`.
7. **Step 7: Post-Deployment Monitoring & Health Sign-Off**
   - Confirm `python3 -m paper.outbox_health` returns `status: HEALTHY`.
   - Verify no dead letters, expired leases, or crash loops.
   - Record deployment completion audit record with operator name, timestamp, and commit SHA.

---

## 17. Emergency Stop and Conservative Recovery Procedures

If anomalous behavior, unexpected delivery loops, or operational failures occur, operators must execute the applicable rollback scenario:

### Scenario A: Rapid Emergency Stop & Conservative Per-Event Recovery
1. **Stop Worker Activity (Authorized Environment Only):**
   ```bash
   sudo systemctl stop retailbot2-outbox-worker.service
   ```
   *Action:* `SIGTERM` requests graceful stop. Worker stops claiming new events, allows active transport to complete or terminates subprocess group within bounded grace, and releases MariaDB advisory lock.
2. **Confirm Process & Descendant Termination:**
   Verify zero remaining dispatcher or transport processes in `/proc`:
   ```bash
   pgrep -a -f "paper.outbox_worker|paper.outbox_transport"
   ```
   Confirm no live PID/PGID remains.
3. **Verify Advisory Lock & Session Release:**
   Query MariaDB to verify lock is released and socket dropped:
   ```sql
   SELECT IS_USED_LOCK('telegram_outbox_worker_lock');
   ```
   Must return `NULL`.
4. **Disable Delivery Authorization Switch:**
   In `/etc/retailbot2/outbox-worker.env`, set:
   ```bash
   TELEGRAM_DELIVERY_ENABLED=0
   RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=0
   ```
5. **Inspect Affected Processing Events Individually:**
   Query all in-flight or expired processing events, capturing the active `claim_token`:
   ```sql
   SELECT id, claim_token, event_key, trade_id, event_type, attempts, locked_until, created_at,
          TIMESTAMPDIFF(SECOND, locked_until, UTC_TIMESTAMP()) AS expired_seconds_ago
   FROM telegram_outbox
   WHERE status = 'processing'
   ORDER BY id ASC;
   ```
6. **Classify Each Event Individually Using Forensic Evidence:**
   Every event must be evaluated under strict evidentiary standards:
   
   - **Confirmed NOT Sent:**
     - *Evidentiary Requirement:* Requires **positive evidence** proving the transport request was never transmitted or could not have been accepted by Telegram. Examples include verified failure before request dispatch supported by process/journald logs (e.g., DNS resolution failure, network interface down prior to socket open, missing token exception, payload serialization error).
     - *Negative Rule:* **Absence of a Telegram message in a chat is NOT sufficient proof that delivery did not occur.** (Chat UI latency, wrong chat inspection, message deletion, Telegram internal pipeline delays, or client sync lag can make a delivered message appear missing).
     - *Recovery Action:* Only when positive evidence confirms zero transmission and written operator approval is obtained, execute optimistic conditional update fencing the exact ID, status, and observed claim token:
       ```sql
       UPDATE telegram_outbox
       SET status = 'pending', claim_token = NULL, locked_until = NULL, attempts = attempts + 1
       WHERE id = <event_id>
         AND status = 'processing'
         AND claim_token = '<observed_claim_token>';
       SELECT ROW_COUNT();
       ```
   
   - **Confirmed Sent:**
     - *Evidentiary Requirement:* Requires **positive evidence** identifying the exact delivered Telegram message or affirmative API response.
     - *Correlation Criteria:* Operators must affirmatively correlate:
       1. The `event_key` (e.g., `retailbot2:shadow:entry:BTC/USDT_...`) or trade ID.
       2. Exact timestamp of the event against message timestamp in the Telegram channel.
       3. Payload parameters (symbol, side LONG/SHORT, entry price, stop-loss, target, units).
       4. Transport logs recording HTTP 200 with Telegram `message_id`.
     - *Recovery Action:* With written approval, reconcile database state via optimistic conditional update:
       ```sql
       UPDATE telegram_outbox
       SET status = 'sent', sent_at = UTC_TIMESTAMP(), claim_token = NULL, locked_until = NULL
       WHERE id = <event_id>
         AND status = 'processing'
         AND claim_token = '<observed_claim_token>';
       SELECT ROW_COUNT();
       ```
   
   - **Mandatory Optimistic Fencing & Row Count Verification:**
     - Operators MUST verify the affected-row count (`SELECT ROW_COUNT();`) immediately after executing the conditional update.
     - **Exactly 1 row affected:** Success. The event was securely updated under its verified forensic ownership.
     - **Zero rows affected (0):** **STOP IMMEDIATELY.** Do NOT broaden the WHERE clause or retry blindly. Zero affected rows indicates that the event's state mutated between forensic inspection and update execution (e.g., status changed, claim token expired and was reclaimed by a running worker with a new token, or the row was modified by another session). Modifying an event without matching the exact claim token risks corrupting an active worker's in-flight transaction or re-queuing an already resolved event.
     - **Never omit `AND claim_token = '<observed_claim_token>'` or `AND status = 'processing'`.**

   - **Delivery Outcome Uncertain:**
     - *Definition:* Any event where evidence is ambiguous, partial, or missing (e.g., worker killed during HTTP POST, socket timeout during response read, network partition without receipt confirmation).
     - *Strict Handling Rule:* **PRESERVE UNCERTAIN EVENTS FOR MANUAL OWNER REVIEW.**
     - *Duplicate-Delivery Risk:* **Do NOT reset uncertain events to pending.** Re-queuing an uncertain event creates a severe risk of transmitting duplicate signal notifications to live traders. Uncertain events remain in `status = 'processing'` or are moved to an owner triage queue only with written authorization.

7. **Maintain Immutable Audit Record:**
   Document every recovered event ID, evidence examined, operator name, timestamp, and approved action.
8. **Explicit Prohibitions for Operators:**
   > [!CAUTION]
   > 1. **Never execute blanket resets such as `UPDATE telegram_outbox SET status='pending' WHERE status='processing';`!**
   > 2. **Never create or run automated blanket reset scripts.**
   > 3. **Never treat chat message absence as proof of non-delivery.**
   > 4. **Never omit optimistic claim-token fencing (`AND claim_token = '<observed_claim_token>'`) or status checks from recovery queries.**
   > Blanket resets bypass forensic classification and risk re-sending messages that were already delivered to Telegram.

### Scenario B: Service Downgrade / Code Rollback
1. Stop running worker: `sudo systemctl stop retailbot2-outbox-worker.service`.
2. Check out previous approved commit SHA in `/opt/signal-bot`.
3. Verify package files: `python3 deploy/verify_deployment_package.py`.
4. Start worker in `--observation` mode first to verify database compatibility before re-authorizing delivery.

### Scenario C: Emergency Database Account Isolation (DBA Cut-Off)
If the host or service process is unresponsive or compromised:
```sql
-- Revoke dispatcher privileges immediately
REVOKE ALL PRIVILEGES ON crypto_signals.* FROM 'retailbot2_dispatcher'@'127.0.0.1';
-- Terminate active dispatcher connections holding locks
KILL <dispatcher_connection_id>;
```
Worker connections drop immediately, freeing all locks and halting all queue mutations without data loss.

