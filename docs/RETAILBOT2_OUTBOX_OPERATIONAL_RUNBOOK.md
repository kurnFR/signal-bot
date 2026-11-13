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
| **Observation Mode** | `RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1` | Acquire advisory lock `rdt_outbox_worker_lock`; verify schema; inspect queue totals via `SELECT` | Outbox claiming; updating row status; spawning transport subprocess; sending Telegram messages; inserting trades |
| **Test / Mock Delivery** | `mock_transport_response` configured on `signal_bot_test` | Claim events; execute mock in-memory response; transition to `sent` or `pending` | Any network/HTTP socket calls; connecting to `crypto_signals`; modifying live trades |
| **Production Telegram Delivery** | `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1` | Standalone worker daemon claims production events; transmits HTTP requests to Telegram API via isolated subprocess | Automated status resets without confirmation; modifying `rdt_trades` |
| **Live Trading Authorization** | `RETAILBOT2_ALLOW_TRADING=1` | Execute orders on Binance Futures; persist trade rows to `rdt_trades` and update `rdt_strategy_stats` | Live execution without operator approval; production worker does not need or inherit this flag |

> [!CRITICAL]
> **Installing, enabling, or starting the systemd service does NOT authorize outbox event enqueueing, Telegram delivery, or live trading.**
> Each boundary requires separate, explicit environment authorization and independently provisioned database credentials.

---

## 2. Worker Prerequisites

Before starting the outbox worker daemon in any environment, verify:

1. **Schema Lineage:**
   - Migration 026 (`db/migrate_retailbot2_telegram_outbox.sql`): Table `rdt_telegram_outbox` with base columns and indexes.
   - Migration 027 (`db/migrate_retailbot2_outbox_claim_token.sql`): Adds `claim_token CHAR(32) NULL AFTER locked_until`.
   - Verified 13-column schema present without references to non-existent `updated_at`.
2. **Dedicated Least-Privilege Account:**
   - Production observation: `'retailbot2_obs'@'127.0.0.1'` (SELECT only on `rdt_telegram_outbox` + advisory locks).
   - Staging: `'signal_bot_test'@'127.0.0.1'` (DML only on `signal_bot_test`).
   - Production delivery: Dedicated daemon account with SELECT/UPDATE on `rdt_telegram_outbox` only.
3. **Dedicated Configuration File:**
   - Configuration stored in `/etc/retailbot2/outbox-worker.env` with filesystem permissions `0600` owned by `retailbot:retailbot`.

---

## 3. Environment Variables Reference

| Variable | Scope | Purpose | Fail-Closed Default |
| :--- | :--- | :--- | :--- |
| `MYSQL_HOST` | All | MariaDB host address (loopback required for observation) | `127.0.0.1` |
| `MYSQL_PORT` | All | MariaDB port | `3306` |
| `MYSQL_DATABASE` | All | Target database (`signal_bot_test` or `crypto_signals`) | None (fails closed) |
| `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` | Production | Explicit gate to authorize outbound Telegram HTTP requests | Unset / `0` (delivery blocked) |
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
- `is_locked`: Advisory lock state (`rdt_outbox_worker_lock`).
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
   SELECT IS_USED_LOCK('rdt_outbox_worker_lock'), CONNECTION_ID();
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
FROM rdt_telegram_outbox
WHERE status = 'dead'
ORDER BY id DESC LIMIT 10;
```

### Common Dead-Letter Causes
1. **Permanent Telegram Rejection (HTTP 400 / 401 / 403 / 404):** Invalid token, deleted bot, chat not found, or user blocked the bot.
2. **Malformed Payload JSON:** Corrupt JSON or unparseable event payload.
3. **Exhausted Retries (`attempts >= 8`):** Persistent upstream network partition or prolonged Telegram outage exceeding exponential backoff window (~1 hour).

> [!CAUTION]
> **Never blindly execute `UPDATE rdt_telegram_outbox SET status='pending' WHERE status='dead'`!**
> Replaying dead letters without fixing the root cause will flood Telegram rate limits or produce duplicate notifications.

---

## 9. Advisory Lock Contention & Worker Conflict

Only one active dispatcher worker may hold `rdt_outbox_worker_lock` per database:

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
  WHERE pl.ID = IS_USED_LOCK('rdt_outbox_worker_lock');
  ```
- **Remediation:** Verify if a duplicate worker instance is running (`ps aux | grep outbox_worker`). Stop duplicate processes before restarting the service.

---

## 10. Worker Crash Recovery & Stale Lease Fencing

If the worker crashes or terminates abruptly:
1. **MariaDB Advisory Lock Release:** MariaDB automatically releases `rdt_outbox_worker_lock` when the connection socket drops.
2. **Lease Expiry:** Any events in `status = 'processing'` remain locked until `locked_until <= UTC_TIMESTAMP()`.
3. **Claim Token Fencing:** When the new worker starts and claims an expired event, it issues a new `claim_token`. If a zombie transport subprocess from the crashed worker attempts to acknowledge the event, MariaDB rejects the update (`WHERE claim_token = :token`), preventing stale acknowledgements.

---

## 11. Uncertain Delivery Recovery (Safe Procedure)

An event enters uncertain delivery state when a network timeout occurs while transmitting HTTP data to Telegram:

- **Preconditions for Recovery:**
  1. Gracefully stop the worker daemon (`sudo systemctl stop retailbot2-outbox-worker.service`).
  2. Confirm zero live transport processes remain in `/proc`.
  3. Verify `IS_USED_LOCK('rdt_outbox_worker_lock')` returns `NULL`.
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

- [ ] Execute `UPDATE rdt_telegram_outbox SET status = 'pending'` without completing the 6 uncertain-delivery preconditions.
- [ ] Execute `DELETE FROM rdt_telegram_outbox` directly on production without an authorized change ticket.
- [ ] Delete `pending` or `processing` events.
- [ ] Run outbox workers using `root`, `cms`, or shared application database credentials.
- [ ] Reference `updated_at` on `rdt_telegram_outbox` (column does not exist; schema is 13 columns).
- [ ] Bypass advisory lock acquisition (`GET_LOCK`) or force kill workers with `SIGKILL` without first attempting graceful `SIGTERM`.
- [ ] Replay dead-lettered events without addressing the underlying error.
