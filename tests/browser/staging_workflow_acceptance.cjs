/**
 * Staging Modern UI Workflow Acceptance Test Suite
 *
 * Independently exercises and verifies the ten migrated Modern UI workflows
 * against a live FastAPI backend connected strictly to 'signal_bot_test'.
 *
 * Original defect-discovery baseline: 4df758cedb507cdafccac1458082d02585084d4e
 * Current master baseline: 4623e72c563f866854b3e6e599d7ff363b9ccc14
 * PR: #704 (Branch: test/modern-ui-staging-acceptance)
 *
 * Safety & Isolation Guarantees:
 *   - Strictly connects to 'signal_bot_test'; refuses 'crypto_signals'.
 *   - Pre-existing database state in singleton and composite-key tables
 *     (system_flags, wallet_tracker_settings, rdt_strategy_lifecycle) is
 *     read and fully preserved before testing and asserted restored after cleanup.
 *   - Never drops, recreates, or wipes tables; exact-ID fixture cleanup.
 *   - Any failure in cleanup or post-cleanup state verification triggers
 *     a non-zero process exit code.
 *   - Zero live exchange orders, zero real Telegram messages dispatched.
 *   - No file renaming or DOM injection workarounds (no .stgbak, no noValidate).
 */

const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const playwrightPath = path.resolve(__dirname, "../../frontend/node_modules/@playwright/test");
const { chromium } = require(playwrightPath);

const STAGING_PORT = process.env.STAGING_PORT ? parseInt(process.env.STAGING_PORT, 10) : 8056;
const BASE_URL = `http://127.0.0.1:${STAGING_PORT}/modern`;
const REPO_ROOT = path.resolve(__dirname, "../..");
const TARGET_DB = "signal_bot_test";
const SCREENSHOT_DIR = path.resolve(REPO_ROOT, "docs/screenshots");

const RUN_ID = crypto.randomBytes(4).toString("hex");
const STAGING_PASSWORD = process.env.STAGING_TEST_PASSWORD || `Stg_${crypto.randomBytes(8).toString("hex")}!Aa1`;

const ADMIN_USER = `adm_${RUN_ID}`;
const TRADER_USER = `trd_${RUN_ID}`;
const VIEWER_USER = `viw_${RUN_ID}`;

function assert(condition, message) {
  if (!condition) {
    throw new Error(`[ASSERTION FAILED] ${message}`);
  }
}

function runPythonHelper(scriptContent, envOverrides = {}, stdinJson = null) {
  return new Promise((resolve, reject) => {
    const env = {
      ...process.env,
      MYSQL_DATABASE: TARGET_DB,
      PATH: process.env.PATH || "/home/BIS/.local/bin:/usr/local/bin:/usr/bin:/bin",
      ...envOverrides,
    };
    const pythonBin = process.env.PYTHON || "python3";
    const proc = spawn(pythonBin, ["-c", scriptContent], {
      cwd: REPO_ROOT,
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk) => { stderr += chunk.toString(); });

    proc.on("error", reject);
    proc.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`Python helper exited with code ${code}: ${stderr || stdout}`));
      } else {
        try {
          const parsed = stdout.trim() ? JSON.parse(stdout.trim()) : null;
          resolve(parsed);
        } catch {
          resolve(stdout.trim());
        }
      }
    });

    if (stdinJson !== null) {
      proc.stdin.write(JSON.stringify(stdinJson));
    }
    proc.stdin.end();
  });
}

function checkPortFree(port) {
  return new Promise((resolve, reject) => {
    const socket = new net.Socket();
    let settled = false;

    function finish(err, isFree) {
      if (settled) return;
      settled = true;
      try { socket.destroy(); } catch (_) {}
      if (err) reject(err);
      else resolve(isFree);
    }

    socket.setTimeout(1000);
    socket.on("connect", () => {
      finish(new Error(`Port ${port} is already in use by another process.`));
    });
    socket.on("timeout", () => {
      finish(new Error(`Timeout probing port ${port}`));
    });
    socket.on("error", (err) => {
      if (err.code === "ECONNREFUSED") finish(null, true);
      else finish(err);
    });
    socket.connect(port, "127.0.0.1");
  });
}

function waitForServer(port, childProc, expectedRunId, timeoutMs = 25000) {
  const start = Date.now();
  const identityUrl = `http://127.0.0.1:${port}/api/system/test-identity`;

  return new Promise((resolve, reject) => {
    let timer = null;
    let completed = false;

    function cleanup() {
      if (timer) clearTimeout(timer);
    }
    function fail(err) {
      if (completed) return;
      completed = true;
      cleanup();
      reject(err);
    }
    function succeed(val) {
      if (completed) return;
      completed = true;
      cleanup();
      resolve(val);
    }

    function checkChildAlive() {
      if (!childProc || childProc.exitCode !== null || childProc.killed) {
        fail(new Error("Child process died prematurely"));
        return false;
      }
      return true;
    }

    function ping() {
      if (completed || !checkChildAlive()) return;
      const req = http.get(identityUrl, { timeout: 1500 }, (res) => {
        let body = "";
        res.on("data", (chunk) => { body += chunk.toString(); });
        res.on("end", () => {
          if (completed || !checkChildAlive()) return;
          try {
            const data = JSON.parse(body);
            if (res.statusCode === 200 && data.status === "ok" && data.run_id === expectedRunId) {
              return succeed(data);
            }
          } catch (_) {}
          retry();
        });
      });
      req.on("timeout", () => { req.destroy(); retry(); });
      req.on("error", () => { retry(); });
    }

    function retry() {
      if (completed) return;
      if (Date.now() - start > timeoutMs) {
        return fail(new Error(`Timeout waiting for staging server identity at ${identityUrl}`));
      }
      timer = setTimeout(ping, 250);
    }

    ping();
  });
}

function apiRequest(method, endpoint, token = null, body = null) {
  return new Promise((resolve, reject) => {
    const url = new URL(endpoint, `http://127.0.0.1:${STAGING_PORT}`);
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const data = body ? JSON.stringify(body) : null;
    if (data) headers["Content-Length"] = Buffer.byteLength(data);

    const req = http.request(url, { method, headers }, (res) => {
      let responseBody = "";
      res.on("data", (chunk) => { responseBody += chunk.toString(); });
      res.on("end", () => {
        try {
          const parsed = responseBody ? JSON.parse(responseBody) : null;
          resolve({ status: res.statusCode, data: parsed });
        } catch {
          resolve({ status: res.statusCode, data: responseBody });
        }
      });
    });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

function terminateChildProcess(proc, sigtermTimeoutMs = 5000, sigkillTimeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    if (!proc || typeof proc.pid !== "number") return resolve({ exited: true });
    if (proc.exitCode !== null) return resolve({ exited: true, code: proc.exitCode });

    let settled = false;
    let killedWithSigkill = false;
    let timer = null;

    function finish(err, res) {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (err) reject(err);
      else resolve(res);
    }

    proc.once("exit", (code, signal) => {
      finish(null, { exited: true, code, signal, forcedKill: killedWithSigkill });
    });

    try { proc.kill("SIGTERM"); } catch (e) { finish(null, { exited: true }); return; }

    timer = setTimeout(() => {
      if (settled) return;
      killedWithSigkill = true;
      try { proc.kill("SIGKILL"); } catch (_) {}
      setTimeout(() => {
        finish(null, { exited: true, forcedKill: true });
      }, 500);
    }, sigtermTimeoutMs);
  });
}

// Function to verify complete baseline audit record identity across all columns
async function verifyAuditBaselineIdentity(baselineRows, registeredAuditIds = [], testUserIds = []) {
  const script = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG

payload = json.loads(sys.stdin.read())
db_name = payload["target_db"]
baseline_rows = payload["baseline_rows"]
registered_ids = payload["registered_ids"]
test_user_ids = payload.get("test_user_ids", [])

cfg = MYSQL_CONFIG.copy(); cfg["database"] = db_name
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# 1. Verify registered audit IDs are completely absent
leftover_registered = []
if registered_ids:
    placeholders = ",".join(["%s"] * len(registered_ids))
    cur.execute(f"SELECT id FROM audit_logs WHERE id IN ({placeholders})", tuple(registered_ids))
    leftover_registered = [r["id"] for r in cur.fetchall()]

# 2. Query all current rows in audit_logs with normalization
cur.execute("SELECT id, user_id, username, action, details, ip_address, created_at FROM audit_logs ORDER BY id ASC")
current_rows = cur.fetchall()
for r in current_rows:
    if hasattr(r.get("created_at"), "isoformat"):
        r["created_at"] = r["created_at"].isoformat()
    if r.get("details"):
        try:
            r["details"] = json.loads(r["details"])
        except Exception:
            pass

cur.close(); conn.close()

baseline_map = {r["id"]: r for r in baseline_rows}
current_map = {r["id"]: r for r in current_rows}

mismatches = []

# 3. Compare each pre-existing baseline row across all seven columns
for b_id, b_row in baseline_map.items():
    if b_id not in current_map:
        mismatches.append(f"Baseline audit row ID {b_id} is missing from database")
    else:
        c_row = current_map[b_id]
        diffs = []
        for col in ["user_id", "username", "action", "details", "ip_address", "created_at"]:
            b_val = b_row.get(col)
            c_val = c_row.get(col)
            if col == "created_at" and b_val and c_val:
                b_val = str(b_val).replace(" ", "T")
                c_val = str(c_val).replace(" ", "T")
            if c_val != b_val:
                diffs.append(f"{col}: expected {b_val!r} got {c_val!r}")
        if diffs:
            mismatches.append(f"Baseline audit row ID {b_id} modified: {', '.join(diffs)}")

# 4. Check non-baseline rows
# Test-owned audit records created by unique test users must be cleaned up.
# Unrelated concurrent audit records created by other users or background processes
# must be preserved and must NOT cause a test failure.
leftover_test_user_audits = []
preserved_unrelated_audits = []

for c_id, c_row in current_map.items():
    if c_id not in baseline_map:
        if test_user_ids and c_row.get("user_id") in test_user_ids:
            leftover_test_user_audits.append(c_id)
            mismatches.append(f"Leftover test-owned audit row ID {c_id} (user_id={c_row.get('user_id')}, action={c_row.get('action')}) was not cleaned up")
        else:
            preserved_unrelated_audits.append(c_id)

if leftover_registered:
    mismatches.append(f"Registered audit IDs still present in DB: {leftover_registered}")

print(json.dumps({
    "leftover_registered": leftover_registered,
    "leftover_test_user_audits": leftover_test_user_audits,
    "preserved_unrelated_audits": preserved_unrelated_audits,
    "baseline_intact": len(mismatches) == 0,
    "mismatches": mismatches,
    "baseline_count": len(baseline_rows),
    "current_count": len(current_rows)
}))
`;
  return await runPythonHelper(script, {}, {
    target_db: TARGET_DB,
    baseline_rows: baselineRows,
    registered_ids: registeredAuditIds,
    test_user_ids: testUserIds,
  });
}

// Function to execute the exact staging database cleanup
async function executeStagingDatabaseCleanup(payload, options = {}) {
  const mergedPayload = {
    ...payload,
    inject_failure_in: options.injectFailureIn || payload.inject_failure_in || null,
    simulate_flag_corruption: options.simulateFlagCorruption || payload.simulate_flag_corruption || null,
  };
  const cleanupScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG

payload = json.loads(sys.stdin.read())
db_name = payload["target_db"]
assert db_name == "signal_bot_test", f"Safety refusal: {db_name}"

cfg = MYSQL_CONFIG.copy(); cfg["database"] = db_name
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

results = {}
snapshot_captured = payload.get("snapshot_captured", False)
snapshot_state = payload.get("snapshot_state", {})

# 1. Delete individually registered audit logs by exact ID
try:
    if payload.get("inject_failure_in") == "auditLogs":
        raise RuntimeError("Injected audit cleanup failure")
    audit_ids = payload.get("audit_log_ids", [])
    if audit_ids:
        placeholders = ",".join(["%s"] * len(audit_ids))
        cur.execute(f"DELETE FROM audit_logs WHERE id IN ({placeholders})", tuple(audit_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN ({placeholders})", tuple(audit_ids))
        assert cur.fetchone()["cnt"] == 0, "Registered audit logs not fully deleted"
    results["auditLogs"] = True
except Exception as e:
    results["auditLogs"] = False
    print(f"[-] Audit logs cleanup error: {e}", file=sys.stderr)

# 2. Delete test users
try:
    user_ids = payload.get("user_ids", [])
    if user_ids:
        placeholders = ",".join(["%s"] * len(user_ids))
        cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", tuple(user_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM users WHERE id IN ({placeholders})", tuple(user_ids))
        assert cur.fetchone()["cnt"] == 0, "Users not fully deleted"
    results["users"] = True
except Exception as e:
    results["users"] = False
    print(f"[-] Users cleanup error: {e}", file=sys.stderr)

# 3. Delete paper orders
try:
    order_ids = payload.get("paper_order_ids", [])
    if order_ids:
        placeholders = ",".join(["%s"] * len(order_ids))
        cur.execute(f"DELETE FROM paper_orders WHERE id IN ({placeholders})", tuple(order_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM paper_orders WHERE id IN ({placeholders})", tuple(order_ids))
        assert cur.fetchone()["cnt"] == 0, "Orders not fully deleted"
    results["orders"] = True
except Exception as e:
    results["orders"] = False
    print(f"[-] Orders cleanup error: {e}", file=sys.stderr)

# 4. Delete paper trades
try:
    trade_ids = payload.get("paper_trade_ids", [])
    if trade_ids:
        placeholders = ",".join(["%s"] * len(trade_ids))
        cur.execute(f"DELETE FROM paper_trades WHERE id IN ({placeholders})", tuple(trade_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM paper_trades WHERE id IN ({placeholders})", tuple(trade_ids))
        assert cur.fetchone()["cnt"] == 0, "Trades not fully deleted"
    results["trades"] = True
except Exception as e:
    results["trades"] = False
    print(f"[-] Trades cleanup error: {e}", file=sys.stderr)

# 5. Delete paper positions
try:
    pos_ids = payload.get("paper_position_ids", [])
    if pos_ids:
        placeholders = ",".join(["%s"] * len(pos_ids))
        cur.execute(f"DELETE FROM paper_positions WHERE id IN ({placeholders})", tuple(pos_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM paper_positions WHERE id IN ({placeholders})", tuple(pos_ids))
        assert cur.fetchone()["cnt"] == 0, "Positions not fully deleted"
    results["positions"] = True
except Exception as e:
    results["positions"] = False
    print(f"[-] Positions cleanup error: {e}", file=sys.stderr)

# 6. Delete paper configs
try:
    cfg_ids = payload.get("paper_config_ids", [])
    if cfg_ids:
        placeholders = ",".join(["%s"] * len(cfg_ids))
        cur.execute(f"DELETE FROM paper_configs WHERE id IN ({placeholders})", tuple(cfg_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM paper_configs WHERE id IN ({placeholders})", tuple(cfg_ids))
        assert cur.fetchone()["cnt"] == 0, "Configs not fully deleted"
    results["configs"] = True
except Exception as e:
    results["configs"] = False
    print(f"[-] Configs cleanup error: {e}", file=sys.stderr)

# 7. Delete tracked wallets
try:
    wallet_ids = payload.get("tracked_wallet_ids", [])
    if wallet_ids:
        placeholders = ",".join(["%s"] * len(wallet_ids))
        cur.execute(f"DELETE FROM tracked_wallets WHERE id IN ({placeholders})", tuple(wallet_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM tracked_wallets WHERE id IN ({placeholders})", tuple(wallet_ids))
        assert cur.fetchone()["cnt"] == 0, "Tracked wallets not fully deleted"
    results["wallets"] = True
except Exception as e:
    results["wallets"] = False
    print(f"[-] Tracked wallets cleanup error: {e}", file=sys.stderr)

# 8. Restore system_flags emergency_stop
try:
    if payload.get("inject_failure_in") == "circuitBreaker":
        raise RuntimeError("Injected circuit breaker restoration failure")
    if not snapshot_captured:
        raise RuntimeError("Refusing to restore system_flags: original state was never captured!")
    em_state = snapshot_state.get("emergencyStop", {})
    em_status = em_state.get("status")
    target_flag = em_state.get("flag_name") or (em_state.get("row") and em_state["row"].get("flag_name")) or "emergency_stop"
    if em_status == "ABSENT":
        cur.execute("DELETE FROM system_flags WHERE flag_name = %s", (target_flag,))
        cur.execute("SELECT flag_name FROM system_flags WHERE flag_name = %s", (target_flag,))
        assert cur.fetchone() is None, f"{target_flag} row should not exist after cleanup"
        results["circuitBreaker"] = True
    elif em_status == "EXISTS":
        init_em = em_state["row"]
        assert init_em is not None, f"Missing snapshot row for EXISTS {target_flag}"
        cur.execute(
            "UPDATE system_flags SET is_active = %s, reason = %s, updated_at = %s WHERE flag_name = %s",
            (init_em["is_active"], init_em["reason"], init_em["updated_at"], target_flag)
        )
        if payload.get("simulate_flag_corruption"):
            s_corrupt = payload["simulate_flag_corruption"]
            cur.execute(f"UPDATE system_flags SET {s_corrupt['field']} = %s WHERE flag_name = %s", (s_corrupt["value"], target_flag))
        cur.execute("SELECT flag_name, is_active, reason, updated_at FROM system_flags WHERE flag_name = %s", (target_flag,))
        v_em = cur.fetchone()
        assert v_em is not None, f"system_flags row '{target_flag}' missing after restore"
        expected_flag = init_em.get("flag_name") or target_flag
        assert v_em["flag_name"] == expected_flag, f"{target_flag} flag_name mismatch: {v_em['flag_name']} vs {expected_flag}"
        assert v_em["is_active"] == init_em["is_active"], f"{target_flag} is_active mismatch: {v_em['is_active']} vs {init_em['is_active']}"
        assert v_em["reason"] == init_em["reason"], f"{target_flag} reason mismatch: {v_em['reason']!r} vs {init_em['reason']!r}"
        v_up = v_em["updated_at"].isoformat() if hasattr(v_em["updated_at"], "isoformat") else str(v_em["updated_at"])
        i_up = init_em["updated_at"].isoformat() if hasattr(init_em["updated_at"], "isoformat") else str(init_em["updated_at"])
        assert v_up.replace(" ", "T") == i_up.replace(" ", "T"), f"{target_flag} updated_at mismatch: {v_up} vs {i_up}"
        results["circuitBreaker"] = True
    else:
        raise ValueError(f"Invalid emergencyStop snapshot status: {em_status}")
except Exception as e:
    results["circuitBreaker"] = False
    print(f"[-] Emergency stop restoration error: {e}", file=sys.stderr)

# 9. Restore rdt_strategy_lifecycle
try:
    if not snapshot_captured:
        raise RuntimeError("Refusing to restore rdt_strategy_lifecycle: original state was never captured!")
    for key, def_strat, def_mode in [("rb03Row", "RSI_CTR", "shadow"), ("rb04Row", "DBL_PAT_INV", "inverse")]:
        row_state = snapshot_state.get(key, {})
        row_status = row_state.get("status")
        strat_id = row_state.get("strategy_id") or (row_state.get("row") and row_state["row"].get("strategy_id")) or def_strat
        mode = row_state.get("mode") or (row_state.get("row") and row_state["row"].get("mode")) or def_mode
        if row_status == "ABSENT":
            cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (strat_id, mode))
            cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (strat_id, mode))
            assert cur.fetchone() is None
        elif row_status == "EXISTS":
            init_r = row_state["row"]
            cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (strat_id, mode))
            if cur.fetchone():
                cur.execute('''
                    UPDATE rdt_strategy_lifecycle
                    SET evaluation_enabled=%s, paper_enabled=%s, telegram_enabled=%s, report_enabled=%s, auto_promote_enabled=%s, updated_at=%s
                    WHERE strategy_id=%s AND mode=%s
                ''', (init_r["evaluation_enabled"], init_r["paper_enabled"], init_r["telegram_enabled"],
                      init_r["report_enabled"], init_r.get("auto_promote_enabled", 0), init_r["updated_at"], strat_id, mode))
            else:
                cur.execute('''
                    INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (strat_id, mode, init_r["evaluation_enabled"], init_r["paper_enabled"], init_r["telegram_enabled"],
                      init_r["report_enabled"], init_r.get("auto_promote_enabled", 0), init_r["updated_at"]))

            cur.execute('''
                SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
                       telegram_enabled, report_enabled, auto_promote_enabled, updated_at
                FROM rdt_strategy_lifecycle
                WHERE strategy_id = %s AND mode = %s
            ''', (strat_id, mode))
            v_r = cur.fetchone()
            assert v_r is not None, f"rdt_strategy_lifecycle row ({strat_id}, {mode}) missing after restore"
            assert v_r["strategy_id"] == strat_id, f"strategy_id mismatch: {v_r['strategy_id']} vs {strat_id}"
            assert v_r["mode"] == mode, f"mode mismatch: {v_r['mode']} vs {mode}"
            assert v_r["evaluation_enabled"] == init_r["evaluation_enabled"], f"evaluation_enabled mismatch: {v_r['evaluation_enabled']} vs {init_r['evaluation_enabled']}"
            assert v_r["paper_enabled"] == init_r["paper_enabled"], f"paper_enabled mismatch: {v_r['paper_enabled']} vs {init_r['paper_enabled']}"
            assert v_r["telegram_enabled"] == init_r["telegram_enabled"], f"telegram_enabled mismatch: {v_r['telegram_enabled']} vs {init_r['telegram_enabled']}"
            assert v_r["report_enabled"] == init_r["report_enabled"], f"report_enabled mismatch: {v_r['report_enabled']} vs {init_r['report_enabled']}"
            assert v_r["auto_promote_enabled"] == init_r.get("auto_promote_enabled", 0), f"auto_promote_enabled mismatch: {v_r['auto_promote_enabled']} vs {init_r.get('auto_promote_enabled', 0)}"
            v_up = v_r["updated_at"].isoformat() if hasattr(v_r["updated_at"], "isoformat") else str(v_r["updated_at"])
            i_up = init_r["updated_at"].isoformat() if hasattr(init_r["updated_at"], "isoformat") else str(init_r["updated_at"])
            assert v_up.replace(" ", "T") == i_up.replace(" ", "T"), f"updated_at mismatch: {v_up} vs {i_up}"
        else:
            raise ValueError(f"Invalid lifecycle snapshot status for {strat_id}: {row_status}")
    results["lifecycle"] = True
except Exception as e:
    results["lifecycle"] = False
    print(f"[-] Lifecycle restoration error: {e}", file=sys.stderr)

# 10. Restore wallet_tracker_settings (id=1)
try:
    if not snapshot_captured:
        raise RuntimeError("Refusing to restore wallet_tracker_settings: original state was never captured!")
    wt_state = snapshot_state.get("walletSettings", {})
    wt_status = wt_state.get("status")
    target_id = wt_state.get("target_id") or (wt_state.get("row") and wt_state["row"].get("id")) or 1
    if wt_status == "ABSENT":
        cur.execute("DELETE FROM wallet_tracker_settings WHERE id = %s", (target_id,))
        cur.execute("SELECT id FROM wallet_tracker_settings WHERE id = %s", (target_id,))
        assert cur.fetchone() is None
        results["walletSettings"] = True
    elif wt_status == "EXISTS":
        init_s = wt_state["row"]
        cur.execute('''
            UPDATE wallet_tracker_settings
            SET enabled=%s, networks=%s, pools_per_network=%s, min_trade_usd=%s, min_net_usd=%s,
                auto_track_net_usd=%s, poll_interval_seconds=%s, retention_days=%s, api_delay_seconds=%s,
                token_mode=%s, token_addresses=%s, birdeye_api_key=%s, updated_at=%s
            WHERE id = %s
        ''', (
            init_s["enabled"], init_s["networks"], init_s["pools_per_network"],
            init_s["min_trade_usd"], init_s["min_net_usd"], init_s["auto_track_net_usd"],
            init_s["poll_interval_seconds"], init_s["retention_days"], init_s["api_delay_seconds"],
            init_s["token_mode"], init_s["token_addresses"], init_s["birdeye_api_key"],
            init_s["updated_at"], target_id
        ))
        cur.execute("SELECT id, enabled, networks, pools_per_network, min_trade_usd, min_net_usd, auto_track_net_usd, poll_interval_seconds, retention_days, api_delay_seconds, token_mode, token_addresses, birdeye_api_key, updated_at FROM wallet_tracker_settings WHERE id = %s", (target_id,))
        verified_s = cur.fetchone()
        assert verified_s is not None, f"wallet_tracker_settings row {target_id} missing after restore"
        assert verified_s["enabled"] == init_s["enabled"], f"enabled mismatch: {verified_s['enabled']} vs {init_s['enabled']}"
        assert verified_s["networks"] == init_s["networks"], f"networks mismatch: {verified_s['networks']} vs {init_s['networks']}"
        assert verified_s["pools_per_network"] == init_s["pools_per_network"], f"pools_per_network mismatch: {verified_s['pools_per_network']} vs {init_s['pools_per_network']}"
        assert float(verified_s["min_trade_usd"]) == float(init_s["min_trade_usd"])
        assert float(verified_s["min_net_usd"]) == float(init_s["min_net_usd"])
        assert float(verified_s["auto_track_net_usd"]) == float(init_s["auto_track_net_usd"])
        assert verified_s["poll_interval_seconds"] == init_s["poll_interval_seconds"]
        assert verified_s["retention_days"] == init_s["retention_days"]
        assert float(verified_s["api_delay_seconds"]) == float(init_s["api_delay_seconds"])
        assert verified_s["token_mode"] == init_s["token_mode"]
        assert (verified_s["token_addresses"] or "") == (init_s["token_addresses"] or "")
        assert (verified_s["birdeye_api_key"] or "") == (init_s["birdeye_api_key"] or "")
        v_updated = verified_s["updated_at"].isoformat() if hasattr(verified_s["updated_at"], "isoformat") else str(verified_s["updated_at"])
        i_updated = init_s["updated_at"].isoformat() if hasattr(init_s["updated_at"], "isoformat") else str(init_s["updated_at"])
        assert v_updated.replace(" ", "T") == i_updated.replace(" ", "T"), f"updated_at mismatch: {v_updated} vs {i_updated}"
        results["walletSettings"] = True
    else:
        raise ValueError(f"Invalid walletSettings snapshot status: {wt_status}")
except Exception as e:
    results["walletSettings"] = False
    print(f"[-] Wallet settings restoration error: {e}", file=sys.stderr)

conn.commit()
cur.close(); conn.close()
print(json.dumps({"status": "ok", "cleanup_results": results}))
`;
  return await runPythonHelper(cleanupScript, {}, mergedPayload);
}

async function run() {
  let proc = null;
  let browser = null;
  let seededUserIds = [];
  let baselineAuditRows = [];
  const testFixtures = {
    paperOrders: [],
    paperPositions: [],
    paperTrades: [],
    paperConfigs: [],
    mlFiles: [],
    trackedWallets: [],
    auditLogs: [],
  };

  // State preservation registers and snapshot capture marker
  let snapshotCaptured = false;
  const snapshotState = {
    emergencyStop: { status: "NOT_CAPTURED", row: null },
    walletSettings: { status: "NOT_CAPTURED", row: null },
    rb03Row: { status: "NOT_CAPTURED", row: null },
    rb04Row: { status: "NOT_CAPTURED", row: null },
  };

  let originalEmergencyStop = null;
  let originalWalletSettings = null;
  let originalRb03Row = null;
  let originalRb04Row = null;

  const cleanupStatus = {
    auditLogs: false,
    users: false,
    orders: false,
    positions: false,
    trades: false,
    configs: false,
    wallets: false,
    circuitBreaker: false,
    lifecycle: false,
    walletSettings: false,
    mlFiles: false,
  };

  const workflowResults = {};
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

  try {
    console.log(`\n======================================================================`);
    console.log(` MODERN UI STAGING ACCEPTANCE WORKFLOW SUITE`);
    console.log(` Defect Discovery Baseline: 4df758cedb507cdafccac1458082d02585084d4e`);
    console.log(` Current Master Baseline:   4623e72c563f866854b3e6e599d7ff363b9ccc14`);
    console.log(` Target Database: ${TARGET_DB} (strictly isolated)`);
    console.log(` Port: ${STAGING_PORT} | Run ID: ${RUN_ID}`);
    console.log(`======================================================================\n`);

    // Preflight port check
    await checkPortFree(STAGING_PORT);
    console.log(`[+] Port ${STAGING_PORT} verified free.`);

    // 1. Capture Pre-Existing State & Seed Run-Unique Users
    const initScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password

payload = json.loads(sys.stdin.read())
db_name = payload["target_db"]
assert db_name == "signal_bot_test", f"Safety refusal: {db_name}"

cfg = MYSQL_CONFIG.copy(); cfg["database"] = db_name
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# Safety check
cur.execute("SELECT DATABASE() as active_db")
active_db = cur.fetchone()["active_db"]
assert active_db == "signal_bot_test", f"Database mismatch: {active_db}"

# A. Read complete pre-existing emergency stop row
cur.execute("SELECT * FROM system_flags WHERE flag_name = 'emergency_stop'")
emergency_stop = cur.fetchone()
if emergency_stop:
    for k in list(emergency_stop.keys()):
        if hasattr(emergency_stop[k], "isoformat"):
            emergency_stop[k] = emergency_stop[k].isoformat()

# B. Read all columns of pre-existing wallet_tracker_settings
cur.execute("SELECT * FROM wallet_tracker_settings WHERE id = 1")
wallet_settings = cur.fetchone()
if wallet_settings:
    for k in list(wallet_settings.keys()):
        if hasattr(wallet_settings[k], "isoformat"):
            wallet_settings[k] = wallet_settings[k].isoformat()
        elif hasattr(wallet_settings[k], "__float__"):
            wallet_settings[k] = float(wallet_settings[k])

# C. Read all columns of pre-existing lifecycle settings for test strategies
cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = 'RSI_CTR' AND mode = 'shadow'")
rb03_row = cur.fetchone()
if rb03_row:
    for k in list(rb03_row.keys()):
        if hasattr(rb03_row[k], "isoformat"):
            rb03_row[k] = rb03_row[k].isoformat()

cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = 'DBL_PAT_INV' AND mode = 'inverse'")
rb04_row = cur.fetchone()
if rb04_row:
    for k in list(rb04_row.keys()):
        if hasattr(rb04_row[k], "isoformat"):
            rb04_row[k] = rb04_row[k].isoformat()

# D. Seed run-unique users
seeded_ids = []
for u in payload["users"]:
    pwd_hash = hash_password(u["password"])
    cur.execute(
        "INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, %s, 1, 0)",
        (u["username"], u["email"], pwd_hash, u["role"])
    )
    seeded_ids.append(cur.lastrowid)

# E. Record complete pre-existing baseline audit records with all columns
cur.execute("SELECT id, user_id, username, action, details, ip_address, created_at FROM audit_logs ORDER BY id ASC")
pre_audit_rows = cur.fetchall()
for r in pre_audit_rows:
    if hasattr(r.get("created_at"), "isoformat"):
        r["created_at"] = r["created_at"].isoformat()
    if r.get("details"):
        try:
            r["details"] = json.loads(r["details"])
        except Exception:
            pass

conn.commit()
cur.close(); conn.close()

print(json.dumps({
    "status": "ok",
    "active_db": active_db,
    "seeded_ids": seeded_ids,
    "pre_audit_rows": pre_audit_rows,
    "emergency_stop": {"status": "EXISTS" if emergency_stop else "ABSENT", "row": emergency_stop, "flag_name": "emergency_stop"},
    "wallet_settings": {"status": "EXISTS" if wallet_settings else "ABSENT", "row": wallet_settings},
    "rb03_row": {"status": "EXISTS" if rb03_row else "ABSENT", "row": rb03_row},
    "rb04_row": {"status": "EXISTS" if rb04_row else "ABSENT", "row": rb04_row}
}, default=str))
`;

    const initPayload = {
      target_db: TARGET_DB,
      users: [
        { username: ADMIN_USER, email: `${ADMIN_USER}@stg.internal`, role: "admin", password: STAGING_PASSWORD },
        { username: TRADER_USER, email: `${TRADER_USER}@stg.internal`, role: "trader", password: STAGING_PASSWORD },
        { username: VIEWER_USER, email: `${VIEWER_USER}@stg.internal`, role: "viewer", password: STAGING_PASSWORD },
      ],
    };

    const initResult = await runPythonHelper(initScript, {}, initPayload);
    assert(initResult && initResult.status === "ok", "Database initialization snapshot failed");
    seededUserIds = initResult.seeded_ids || [];
    baselineAuditRows = initResult.pre_audit_rows || [];
    const adminUserId = seededUserIds[0];
    const traderUserId = seededUserIds[1];
    const viewerUserId = seededUserIds[2];

    snapshotState.emergencyStop = initResult.emergency_stop;
    snapshotState.walletSettings = initResult.wallet_settings;
    snapshotState.rb03Row = initResult.rb03_row;
    snapshotState.rb04Row = initResult.rb04_row;

    // Strict assertions on snapshot status
    assert(["EXISTS", "ABSENT"].includes(snapshotState.emergencyStop?.status), "Invalid emergencyStop status");
    assert(["EXISTS", "ABSENT"].includes(snapshotState.walletSettings?.status), "Invalid walletSettings status");
    assert(["EXISTS", "ABSENT"].includes(snapshotState.rb03Row?.status), "Invalid rb03Row status");
    assert(["EXISTS", "ABSENT"].includes(snapshotState.rb04Row?.status), "Invalid rb04Row status");

    originalEmergencyStop = snapshotState.emergencyStop.row;
    originalWalletSettings = snapshotState.walletSettings.row;
    originalRb03Row = snapshotState.rb03Row.row;
    originalRb04Row = snapshotState.rb04Row.row;

    snapshotCaptured = true;

    console.log(`[+] Pre-existing database state safely recorded (snapshotCaptured = true):`);
    console.log(`    - Emergency Stop: status=${snapshotState.emergencyStop.status}, is_active=${originalEmergencyStop?.is_active ?? 'null'}`);
    console.log(`    - Wallet Settings (id=1): status=${snapshotState.walletSettings.status}, min_trade=${originalWalletSettings?.min_trade_usd}, poll=${originalWalletSettings?.poll_interval_seconds}`);
    console.log(`    - RB-03 Strategy (RSI_CTR/shadow): status=${snapshotState.rb03Row.status}`);
    console.log(`    - RB-04 Strategy (DBL_PAT_INV/inverse): status=${snapshotState.rb04Row.status}`);
    console.log(`    - Audit Baseline: ${baselineAuditRows.length} pre-existing audit rows recorded with complete column identity.`);
    console.log(`[+] Seeded run-unique test accounts: ${ADMIN_USER}, ${TRADER_USER}, ${VIEWER_USER} (IDs: ${seededUserIds.join(", ")})`);

    // Helper to capture exact application-generated audit log ID generated by an operation
    async function withAuditCapture(expectedUserId, actionFn, label = "") {
      assert(typeof expectedUserId === "number" && expectedUserId > 0, `Invalid expectedUserId for ${label}: ${expectedUserId}`);

      const preScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT COALESCE(MAX(id), 0) as max_id FROM audit_logs")
max_id = cur.fetchone()["max_id"]
cur.close(); conn.close()
print(json.dumps({"max_id": max_id}))
`;
      const preRes = await runPythonHelper(preScript);
      const preMaxId = preRes?.max_id || 0;

      const actionResult = await actionFn();

      const postScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute(
    "SELECT id, user_id, username, action FROM audit_logs WHERE id > %s AND user_id = %s AND action = 'LOGIN_SUCCESS' ORDER BY id ASC",
    (${preMaxId}, ${expectedUserId})
)
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"rows": rows}))
`;
      const postRes = await runPythonHelper(postScript);
      const matchingRows = postRes?.rows || [];

      // Require exactly the expected audit event for each successful login
      if (matchingRows.length !== 1) {
        throw new Error(
          `Audit capture ownership failure for ${label}: expected exactly 1 LOGIN_SUCCESS audit row for user_id=${expectedUserId} with id > ${preMaxId}, got ${matchingRows.length}`
        );
      }

      const matchingRow = matchingRows[0];
      assert(matchingRow.id, `Invalid audit log id captured for ${label}`);
      assert(matchingRow.user_id === expectedUserId, `Mismatched user_id in audit log: expected ${expectedUserId}, got ${matchingRow.user_id}`);
      assert(matchingRow.action === "LOGIN_SUCCESS", `Mismatched action in audit log: expected LOGIN_SUCCESS, got ${matchingRow.action}`);

      if (!testFixtures.auditLogs.includes(matchingRow.id)) {
        testFixtures.auditLogs.push(matchingRow.id);
        console.log(`  [+] Registered application audit record ID ${matchingRow.id} (LOGIN_SUCCESS for user_id=${expectedUserId}) [${label}]`);
      }
      return actionResult;
    }

    // 2. Spawn Staging Server
    console.log(`[*] Spawning staging FastAPI server on port ${STAGING_PORT}...`);
    const env = {
      ...process.env,
      MYSQL_DATABASE: TARGET_DB,
      WEB_PORT: String(STAGING_PORT),
      SIGNAL_BOT_HOST: "127.0.0.1",
      SIGNAL_BOT_TEST_RUN_ID: RUN_ID,
    };
    const pythonBin = process.env.PYTHON || "/home/BIS/.local/bin/python3";
    proc = spawn(pythonBin, ["-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", String(STAGING_PORT)], {
      cwd: REPO_ROOT,
      env,
    });

    const serverIdentity = await waitForServer(STAGING_PORT, proc, RUN_ID);
    console.log(`[+] Staging FastAPI server verified (run_id: ${serverIdentity.run_id}, PID: ${serverIdentity.pid}).`);

    // 3. Obtain Tokens for All Three Roles (capturing exact audit log IDs)
    const adminLoginRes = await withAuditCapture(
      adminUserId,
      () => apiRequest("POST", "/api/auth/login", null, { username: ADMIN_USER, password: STAGING_PASSWORD }),
      "Admin API Login"
    );
    assert(adminLoginRes.status === 200 && adminLoginRes.data?.token, "Admin login failed");
    const adminToken = adminLoginRes.data.token;

    const traderLoginRes = await withAuditCapture(
      traderUserId,
      () => apiRequest("POST", "/api/auth/login", null, { username: TRADER_USER, password: STAGING_PASSWORD }),
      "Trader API Login"
    );
    assert(traderLoginRes.status === 200 && traderLoginRes.data?.token, "Trader login failed");
    const traderToken = traderLoginRes.data.token;

    const viewerLoginRes = await withAuditCapture(
      viewerUserId,
      () => apiRequest("POST", "/api/auth/login", null, { username: VIEWER_USER, password: STAGING_PASSWORD }),
      "Viewer API Login"
    );
    assert(viewerLoginRes.status === 200 && viewerLoginRes.data?.token, "Viewer login failed");
    const viewerToken = viewerLoginRes.data.token;

    // 4. Launch Browser & Verify Admin Session
    browser = await chromium.launch({ headless: true });

    // Admin Browser Session
    console.log(`[*] Authenticating Admin browser session (${ADMIN_USER})...`);
    const adminPage = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await adminPage.goto(`${BASE_URL}/`);
    await adminPage.waitForSelector(".auth-form", { timeout: 10000 });
    await adminPage.locator(".auth-form input[autoComplete='username']").fill(ADMIN_USER);
    await adminPage.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await withAuditCapture(
      adminUserId,
      async () => {
        await adminPage.locator(".auth-form button.auth-submit").click();
        await adminPage.waitForSelector("nav", { timeout: 10000 });
      },
      "Admin Browser UI Login"
    );
    await adminPage.screenshot({ path: path.join(SCREENSHOT_DIR, "admin_session_authenticated.png") });
    console.log(`[+] Admin browser session authenticated and verified.`);
    await adminPage.close();

    // Trader Browser Session (Interactive View)
    console.log(`[*] Logging into Modern UI as Trader (${TRADER_USER})...`);
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        console.log("[Browser Error]", msg.text());
      }
    });

    await page.goto(`${BASE_URL}/`);
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    await page.locator(".auth-form input[autoComplete='username']").fill(TRADER_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await withAuditCapture(
      traderUserId,
      async () => {
        await page.locator(".auth-form button.auth-submit").click();
        await page.waitForSelector("nav", { timeout: 10000 });
      },
      "Trader Browser UI Login"
    );
    console.log(`[+] Trader authenticated in browser.`);

    // =================================================================
    // WORKFLOW 1: PT-07 — Cancel Simulated Paper Order
    // =================================================================
    console.log(`\n[=== PT-07: Cancel Simulated Paper Order ===]`);
    const pt07Symbol = `ORD${RUN_ID.slice(0, 4).toUpperCase()}USDT`;

    // Seed run-specific paper configuration and pending order
    const pt07SeedScript = `
import json, sys, time, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
now_ms = int(time.time() * 1000)

# 1. Seed dedicated run-specific configuration
cur.execute('''
INSERT INTO paper_configs (
    symbol, market, timeframe, strategy_name, allocated_capital,
    risk_per_trade_pct, is_active, telegram_enabled, report_enabled
) VALUES (%s, 'spot', '1h', 'trend_ema_v1', 5000.0, 1.0, 1, 0, 1)
''', ('${pt07Symbol}',))
config_id = cur.lastrowid

# 2. Seed pending order using this run-specific config_id
cur.execute('''
INSERT INTO paper_orders (
    config_id, symbol, market, timeframe, strategy_name, signal_open_time,
    entry_time, direction, requested_entry_price, stop_loss, take_profit,
    atr_at_signal, status, created_at, updated_at
) VALUES (
    %s, %s, 'spot', '1h', 'trend_ema_v1', %s,
    %s, 'LONG', 62000.0, 60000.0, 66000.0,
    500.0, 'PENDING', NOW(), NOW()
)
''', (config_id, '${pt07Symbol}', now_ms, now_ms))
conn.commit()
order_id = cur.lastrowid
cur.execute('SELECT id, status, state_changed_by_user_id FROM paper_orders WHERE id = %s', (order_id,))
before_state = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"config_id": config_id, "order_id": order_id, "before_state": before_state}, default=str))
`;
    const pt07SeedRes = await runPythonHelper(pt07SeedScript);
    const pt07ConfigId = pt07SeedRes.config_id;
    const pt07OrderId = pt07SeedRes.order_id;
    testFixtures.paperConfigs.push(pt07ConfigId);
    testFixtures.paperOrders.push(pt07OrderId);
    console.log(`  [1] Seeded config #${pt07ConfigId} (${pt07Symbol}) and pending paper order #${pt07OrderId}: Initial status = '${pt07SeedRes.before_state.status}'.`);

    // In browser: Navigate to Paper Trading
    await page.locator("button:has-text('Paper Trading')").first().click();
    await page.waitForSelector(".paper-trading-view", { timeout: 8000 });
    await page.locator("button:has-text('Refresh snapshot')").click();
    await page.waitForTimeout(500);

    // Locate Cancel button for this order
    const cancelBtn = page.locator(`button[aria-label='Cancel pending order #${pt07OrderId} for ${pt07Symbol}']`).first();
    await cancelBtn.waitFor({ state: "visible", timeout: 8000 });
    await cancelBtn.click();

    // Verify modal
    await page.waitForSelector("#cancel-order-dialog-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt07_cancel_order_modal.png") });
    console.log(`  [2] Cancel confirmation dialog displayed and captured.`);

    // Confirm cancellation
    const confirmCancelBtn = page.locator("button:has-text('Cancel Order')").first();
    await confirmCancelBtn.click();

    // Assert UI feedback
    await page.waitForSelector(".control-feedback.control-success", { timeout: 8000 });
    const cancelFeedback = await page.locator(".control-feedback.control-success").innerText();
    assert(cancelFeedback.includes(`Paper order #${pt07OrderId} was cancelled successfully.`), `Unexpected feedback: ${cancelFeedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt07_order_cancelled.png") });
    console.log(`  [3] UI feedback verified: "${cancelFeedback.trim()}".`);

    // Verify DB persistence
    const pt07VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute('SELECT id, status, state_changed_by_user_id, terminal_at FROM paper_orders WHERE id = %s', (${pt07OrderId},))
after_state = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"after_state": after_state}, default=str))
`;
    const pt07VerifyRes = await runPythonHelper(pt07VerifyScript);
    assert(pt07VerifyRes.after_state.status === "CANCELLED", `Expected CANCELLED status, got: ${pt07VerifyRes.after_state.status}`);
    assert(pt07VerifyRes.after_state.state_changed_by_user_id === traderUserId, `Actor ID mismatch: expected ${traderUserId}, got ${pt07VerifyRes.after_state.state_changed_by_user_id}`);
    console.log(`  [4] Database persistence verified: status = 'CANCELLED', state_changed_by_user_id = ${pt07VerifyRes.after_state.state_changed_by_user_id}.`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/paper/orders/{id}/cancel
    const viewerCancelRes = await apiRequest("POST", `/api/paper/orders/${pt07OrderId}/cancel`, viewerToken);
    assert(viewerCancelRes.status === 403, `Viewer cancel must return 403, got: ${viewerCancelRes.status}`);
    console.log(`  [5] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["PT-07"] = {
      classification: "BROWSER_E2E_VERIFIED",
      before: pt07SeedRes.before_state,
      after: pt07VerifyRes.after_state,
      rbac: "Viewer 403 Verified (POST /api/paper/orders/{id}/cancel)",
    };

    // =================================================================
    // WORKFLOW 2: PT-08 — Manually Close Open Position
    // =================================================================
    console.log(`\n[=== PT-08: Manually Close Open Position ===]`);
    const pt08Symbol = `CLO${RUN_ID.slice(0, 4).toUpperCase()}USDT`;

    const pt08SeedScript = `
import json, sys, time, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
now_ms = int(time.time() * 1000)

cur.execute('''
INSERT INTO paper_configs (
    symbol, market, timeframe, strategy_name, allocated_capital,
    risk_per_trade_pct, is_active, telegram_enabled, report_enabled
) VALUES (%s, 'spot', '1h', 'trend_ema_v1', 5000.0, 1.0, 1, 0, 1)
''', ('${pt08Symbol}',))
config_id = cur.lastrowid

cur.execute('''
INSERT INTO paper_positions (
    symbol, market, timeframe, strategy_name, direction, entry_time, entry_price, current_price,
    stop_loss, take_profit, current_stop, trailing_active, atr_at_signal, initial_risk,
    unrealized_pnl_pct, unrealized_r, holding_bars, max_hold_bars, last_update_time, status,
    allocated_capital, risk_per_trade_pct, risk_amount, stop_distance, quantity, notional_value,
    entry_fee, exit_fee, funding_pnl, config_id
) VALUES (
    %s, 'spot', '1h', 'trend_ema_v1', 'LONG', %s, 3000.0, 3100.0,
    2800.0, 3400.0, 2800.0, 0, 50.0, 50.0,
    3.33, 0.50, 2, 200, %s, 'OPEN',
    5000.0, 1.0, 50.0, 200.0, 0.25, 750.0,
    0.75, 0.0, 0.0, %s
)
''', ('${pt08Symbol}', now_ms, now_ms, config_id))
conn.commit()
pos_id = cur.lastrowid
cur.execute('SELECT id, status, entry_price, current_price, symbol FROM paper_positions WHERE id = %s', (pos_id,))
before_state = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"config_id": config_id, "pos_id": pos_id, "before_state": before_state}, default=str))
`;
    const pt08SeedRes = await runPythonHelper(pt08SeedScript);
    const pt08ConfigId = pt08SeedRes.config_id;
    const pt08PosId = pt08SeedRes.pos_id;
    testFixtures.paperConfigs.push(pt08ConfigId);
    testFixtures.paperPositions.push(pt08PosId);
    console.log(`  [1] Seeded config #${pt08ConfigId} and open position #${pt08PosId} (${pt08Symbol}): Initial status = '${pt08SeedRes.before_state.status}'.`);

    // Refresh Paper Trading snapshot in browser
    await page.locator("button:has-text('Refresh snapshot')").click();
    await page.waitForTimeout(1000);

    // Locate Close button for position
    const closeBtn = page.locator(`button[aria-label='Close position #${pt08PosId} for ${pt08Symbol}']`).first();
    await closeBtn.waitFor({ state: "visible", timeout: 8000 });
    await closeBtn.click();

    // Verify modal
    await page.waitForSelector("#close-position-dialog-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt08_close_position_modal.png") });
    console.log(`  [2] Close confirmation dialog displayed and captured.`);

    // Confirm close
    const confirmCloseBtn = page.locator("button:has-text('Exit Position at Market')").first();
    await confirmCloseBtn.click();

    // Assert success feedback
    await page.waitForSelector(".control-feedback.control-success", { timeout: 8000 });
    const closeFeedback = await page.locator(".control-feedback.control-success").innerText();
    assert(closeFeedback.includes(`Paper position #${pt08PosId} (${pt08Symbol}) was closed at market price.`), `Unexpected feedback: ${closeFeedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt08_position_closed.png") });
    console.log(`  [3] UI feedback verified: "${closeFeedback.trim()}".`);

    // Verify DB persistence
    const pt08VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute('SELECT id, status FROM paper_positions WHERE id = %s', (${pt08PosId},))
after_pos = cur.fetchone()
cur.execute('SELECT id, symbol, position_id, exit_reason, net_pnl FROM paper_trades WHERE position_id = %s', (${pt08PosId},))
after_trade = cur.fetchone()
if after_trade and hasattr(after_trade.get("net_pnl"), "__float__"):
    after_trade["net_pnl"] = float(after_trade["net_pnl"])
cur.close(); conn.close()
print(json.dumps({"after_pos": after_pos, "after_trade": after_trade}, default=str))
`;
    const pt08VerifyRes = await runPythonHelper(pt08VerifyScript);
    assert(pt08VerifyRes.after_pos.status === "CLOSED", `Expected status CLOSED, got: ${pt08VerifyRes.after_pos.status}`);
    assert(pt08VerifyRes.after_trade && pt08VerifyRes.after_trade.exit_reason === "MANUAL", "Expected paper_trades row with exit_reason MANUAL");
    if (pt08VerifyRes.after_trade) {
      testFixtures.paperTrades.push(pt08VerifyRes.after_trade.id);
    }
    console.log(`  [4] Database persistence verified: Position #${pt08PosId} CLOSED, trade #${pt08VerifyRes.after_trade.id} created with exit_reason = 'MANUAL' (net_pnl: ${pt08VerifyRes.after_trade.net_pnl}).`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/paper/positions/{id}/close
    const viewerCloseRes = await apiRequest("POST", `/api/paper/positions/${pt08PosId}/close`, viewerToken);
    assert(viewerCloseRes.status === 403, `Viewer position close must return 403, got: ${viewerCloseRes.status}`);
    console.log(`  [5] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["PT-08"] = {
      classification: "BROWSER_E2E_VERIFIED",
      before: pt08SeedRes.before_state,
      after: { position: pt08VerifyRes.after_pos, trade: pt08VerifyRes.after_trade },
      rbac: "Viewer 403 Verified (POST /api/paper/positions/{id}/close)",
    };

    // =================================================================
    // WORKFLOW 3: PT-09 — Deploy Strategy to Paper Trading
    // =================================================================
    console.log(`\n[=== PT-09: Deploy Strategy to Paper Trading ===]`);
    const pt09Symbol = `DEP${RUN_ID.slice(0, 4).toUpperCase()}USDT`;
    const pt09Strategy = "trend_ema_v1";

    // Initial check: Assert run-unique symbol does NOT exist in DB
    const pt09InitScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, symbol, strategy_name FROM paper_configs WHERE symbol = %s AND strategy_name = %s", ('${pt09Symbol}', '${pt09Strategy}'))
row = cur.fetchone()
cur.close(); conn.close()
assert row is None, f"Configuration collision: {row}"
print(json.dumps({"before_config": None}))
`;
    await runPythonHelper(pt09InitScript);
    console.log(`  [1] Pre-deployment check verified: No pre-existing configuration for ${pt09Symbol}/${pt09Strategy}.`);

    // In browser: Click Deploy Strategy
    await page.locator("button:has-text('Deploy Strategy')").first().click();
    await page.waitForSelector("#deploy-strategy-dialog-title", { timeout: 5000 });

    // Fill form
    await page.locator("#paper-deploy-symbol").fill(pt09Symbol);
    await page.locator("#paper-deploy-strategy").selectOption(pt09Strategy);
    await page.locator("#paper-deploy-capital").fill("6000");
    await page.locator("#paper-deploy-risk").fill("1.5");

    // Ensure send_telegram defaults to false (opt-in safety preserved)
    const tgCheckbox = page.locator("dialog[open] input[type='checkbox']").first();
    const isTgChecked = await tgCheckbox.isChecked();
    assert(isTgChecked === false, "send_telegram must default to false");
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt09_deploy_strategy_modal.png") });
    console.log(`  [2] Deploy Strategy modal filled and captured (send_telegram: false verified).`);

    // Submit deployment
    await page.locator("button[type='submit']:has-text('Deploy to Paper Trading')").first().click();

    // Verify feedback
    await page.waitForSelector(".control-feedback.control-success", { timeout: 8000 });
    const deployFeedback = await page.locator(".control-feedback.control-success").innerText();
    assert(deployFeedback.includes(`Strategy '${pt09Strategy}' deployed to Live Paper Trading on ${pt09Symbol}`), `Unexpected feedback: ${deployFeedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt09_strategy_deployed.png") });
    console.log(`  [3] UI feedback verified: "${deployFeedback.trim()}".`);

    // Verify DB persistence
    const pt09VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, symbol, market, timeframe, strategy_name, allocated_capital, risk_per_trade_pct, telegram_enabled, is_active FROM paper_configs WHERE symbol = %s AND strategy_name = %s", ('${pt09Symbol}', '${pt09Strategy}'))
row = cur.fetchone()
if row and hasattr(row.get("allocated_capital"), "__float__"):
    row["allocated_capital"] = float(row["allocated_capital"])
if row and hasattr(row.get("risk_per_trade_pct"), "__float__"):
    row["risk_per_trade_pct"] = float(row["risk_per_trade_pct"])
cur.close(); conn.close()
print(json.dumps({"after_config": row}, default=str))
`;
    const pt09VerifyRes = await runPythonHelper(pt09VerifyScript);
    assert(pt09VerifyRes.after_config && pt09VerifyRes.after_config.allocated_capital === 6000.0, "Paper config capital mismatch");
    assert(pt09VerifyRes.after_config.telegram_enabled === 0, "telegram_enabled must be 0 (opt-in safety preserved)");
    const pt09ConfigId = pt09VerifyRes.after_config.id;
    testFixtures.paperConfigs.push(pt09ConfigId);
    console.log(`  [4] Database persistence verified: Config #${pt09ConfigId} active with capital=$6,000, risk=1.5%, telegram_enabled=0.`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/paper/deploy
    const viewerDeployRes = await apiRequest("POST", "/api/paper/deploy", viewerToken, {
      symbol: pt09Symbol, market: "spot", timeframe: "1h", strategy_name: pt09Strategy,
      allocated_capital: 6000.0, risk_per_trade_pct: 1.5, send_telegram: false,
    });
    assert(viewerDeployRes.status === 403, `Viewer deploy must return 403, got: ${viewerDeployRes.status}`);
    console.log(`  [5] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["PT-09"] = {
      classification: "BROWSER_E2E_VERIFIED",
      before: null,
      after: pt09VerifyRes.after_config,
      rbac: "Viewer 403 Verified (POST /api/paper/deploy)",
    };

    // =================================================================
    // WORKFLOW 4: PT-10 — Circuit Breaker Trip and Reset
    // =================================================================
    console.log(`\n[=== PT-10: Circuit Breaker Trip and Reset ===]`);
    console.log(`  [1] Pre-test DB state: system_flags 'emergency_stop' = ${JSON.stringify(originalEmergencyStop)}.`);

    if (!originalEmergencyStop) {
      console.log(`  [!] Skipping PT-10: 'emergency_stop' row does not exist in system_flags; skipped to prevent creating untracked global state.`);
      workflowResults["PT-10"] = {
        classification: "SKIPPED_PRECONDITION",
        reason: "Pre-existing 'emergency_stop' row missing in system_flags; skipped to prevent creating untracked global state.",
        before: null,
      };
    } else if (originalEmergencyStop.is_active !== 0) {
      console.log(`  [!] Skipping PT-10: emergency stop is already active (is_active=${originalEmergencyStop.is_active}); skipped to avoid mutating active emergency stop.`);
      workflowResults["PT-10"] = {
        classification: "SKIPPED_PRECONDITION",
        reason: `Emergency stop already active in system_flags (is_active=${originalEmergencyStop.is_active}); skipped without clicking Trip or Reset.`,
        before: originalEmergencyStop,
      };
    } else {
      // Trip Emergency Stop via UI
      const tripBtn = page.locator("button:has-text('Emergency Stop')").first();
      await tripBtn.waitFor({ state: "visible", timeout: 8000 });
      await tripBtn.click();

      // Verify trip modal
      await page.waitForSelector("#breaker-dialog-title", { timeout: 5000 });
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt10_trip_modal.png") });

      // Confirm trip
      await page.locator("button:has-text('Trigger Emergency Stop')").first().click();

      // Verify feedback
      await page.waitForSelector(".control-feedback.control-warning, .control-feedback.control-success", { timeout: 8000 });
      const tripFeedback = await page.locator(".control-feedback").first().innerText();
      assert(tripFeedback.includes("Emergency stop activated"), `Unexpected feedback: ${tripFeedback}`);
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt10_circuit_breaker_tripped.png") });
      console.log(`  [2] Circuit breaker tripped via UI: "${tripFeedback.trim()}".`);

      // Verify tripped DB state
      const pt10TripScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM system_flags WHERE flag_name = 'emergency_stop'")
tripped_state = cur.fetchone()
if tripped_state and hasattr(tripped_state.get("updated_at"), "isoformat"):
    tripped_state["updated_at"] = tripped_state["updated_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"tripped_state": tripped_state}, default=str))
`;
      const pt10TripRes = await runPythonHelper(pt10TripScript);
      assert(pt10TripRes.tripped_state && pt10TripRes.tripped_state.is_active === 1, `Expected is_active=1, got: ${JSON.stringify(pt10TripRes.tripped_state)}`);
      console.log(`  [3] Database persistence verified: system_flags is_active = 1.`);

      // Reset Circuit Breaker via UI
      const resetBtn = page.locator("button:has-text('Reset Circuit Breaker')").first();
      await resetBtn.waitFor({ state: "visible", timeout: 8000 });
      await resetBtn.click();

      // Verify reset modal
      await page.waitForSelector("#breaker-dialog-title", { timeout: 5000 });
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt10_reset_modal.png") });

      // Confirm reset
      await page.locator("button:has-text('Reset Breaker')").first().click();

      // Verify reset feedback
      await page.waitForSelector(".control-feedback.control-success", { timeout: 8000 });
      const resetFeedback = await page.locator(".control-feedback.control-success").first().innerText();
      assert(resetFeedback.includes("Circuit breaker reset"), `Unexpected feedback: ${resetFeedback}`);
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "pt10_circuit_breaker_reset.png") });
      console.log(`  [4] Circuit breaker reset via UI: "${resetFeedback.trim()}".`);

      // Verify reset DB state
      const pt10ResetScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM system_flags WHERE flag_name = 'emergency_stop'")
reset_state = cur.fetchone()
if reset_state and hasattr(reset_state.get("updated_at"), "isoformat"):
    reset_state["updated_at"] = reset_state["updated_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"reset_state": reset_state}, default=str))
`;
      const pt10ResetRes = await runPythonHelper(pt10ResetScript);
      assert(pt10ResetRes.reset_state && pt10ResetRes.reset_state.is_active === 0, `Expected is_active=0, got: ${JSON.stringify(pt10ResetRes.reset_state)}`);
      console.log(`  [5] Database persistence verified: system_flags is_active = 0.`);

      // RBAC check: Viewer denied 403 on exact frontend endpoints POST /api/paper/circuit-breakers/emergency-stop and /reset
      const viewerTripRes = await apiRequest("POST", "/api/paper/circuit-breakers/emergency-stop?reason=test", viewerToken);
      assert(viewerTripRes.status === 403, `Viewer trip must return 403, got: ${viewerTripRes.status}`);
      const viewerResetRes = await apiRequest("POST", "/api/paper/circuit-breakers/reset", viewerToken);
      assert(viewerResetRes.status === 403, `Viewer reset must return 403, got: ${viewerResetRes.status}`);
      console.log(`  [6] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend circuit breaker endpoints.`);

      workflowResults["PT-10"] = {
        classification: "BROWSER_E2E_VERIFIED",
        before: originalEmergencyStop,
        tripped: pt10TripRes.tripped_state,
        after: pt10ResetRes.reset_state,
        rbac: "Viewer 403 Verified (POST /api/paper/circuit-breakers/emergency-stop and /reset)",
      };
    }

    // =================================================================
    // WORKFLOW 5: ML-03 — Launch Battle Royale Tournament
    // =================================================================
    console.log(`\n[=== ML-03: Launch Battle Royale Tournament ===]`);

    // In browser: Navigate to ML Forecast Studio
    await page.locator("button:has-text('ML Studio')").first().click();
    await page.waitForSelector(".ml-studio-view", { timeout: 8000 });

    // Open Tournament modal in browser
    const launchTourneyBtn = page.locator("button:has-text('Launch tournament')").first();
    await launchTourneyBtn.waitFor({ state: "visible", timeout: 8000 });
    await launchTourneyBtn.click();

    // Verify tournament modal rendered
    await page.waitForSelector("#tournament-modal-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "ml03_tournament_modal.png") });
    console.log(`  [1] Battle Royale tournament modal opened and captured.`);

    // Close dialog
    await page.locator("dialog[open] button:has-text('Cancel')").first().click();

    // Direct API contract verification for ML-03 (POST /api/ml/run-experiment)
    console.log(`  [*] Testing tournament execution API contract (POST /api/ml/run-experiment)...`);
    const tournamentPayload = {
      symbol: "BTCUSDT",
      market: "spot",
      timeframe: "1h",
      strategy_name: "trend_ema_v1",
      model_type: "tournament",
      target_type: "binary_positive_r",
      min_validation_trades: 10,
    };
    const experimentRes = await apiRequest("POST", "/api/ml/run-experiment", traderToken, tournamentPayload);
    // On empty OHLCV dataset in test DB, server raises handled error (500)
    console.log(`  [*] Direct API response status: HTTP ${experimentRes.status}.`);
    assert(experimentRes.status === 500, `Expected HTTP 500 from run-experiment on empty dataset, got: ${experimentRes.status}`);
    assert(
      experimentRes.data && experimentRes.data.detail === "ML experiment execution failed; check server logs for diagnostics",
      `Expected handled error detail, got: ${JSON.stringify(experimentRes.data)}`
    );
    console.log(`  [2] Handled error response contract verified: HTTP 500 with detail "${experimentRes.data.detail}".`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/ml/run-experiment
    const viewerExperimentRes = await apiRequest("POST", "/api/ml/run-experiment", viewerToken, tournamentPayload);
    assert(viewerExperimentRes.status === 403, `Viewer run-experiment must return 403, got: ${viewerExperimentRes.status}`);
    console.log(`  [3] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["ML-03"] = {
      classification: "API_CONTRACT_ONLY / RBAC_ONLY",
      boundaryReason: "Full model training execution unverified on staging; empty OHLCV dataset triggers expected handled HTTP 500 error contract. Browser verified modal render and cancel (no browser-driven error recovery); Trader direct API verified handled HTTP 500 error response (detail: 'ML experiment execution failed; check server logs for diagnostics'); RBAC strictly verified with Viewer 403.",
      apiContract: {
        status: experimentRes.status,
        detail: experimentRes.data?.detail,
      },
      rbac: "Viewer 403 Verified (POST /api/ml/run-experiment)",
    };

    // =================================================================
    // WORKFLOW 6: ML-04 — Deploy ML Filter Model to Paper
    // =================================================================
    console.log(`\n[=== ML-04: Deploy ML Filter Model to Paper ===]`);
    const ml04ModelId = `staging_ml04_${RUN_ID}`;
    const ml04Symbol = `MLS${RUN_ID.slice(0, 4)}USDT`;
    const ml04ManifestPath = path.resolve(REPO_ROOT, `ml_models/${ml04ModelId}.json`);
    const ml04WeightsPath = path.resolve(REPO_ROOT, `ml_models/${ml04ModelId}.joblib`);
    testFixtures.mlFiles.push(ml04ManifestPath, ml04WeightsPath);

    // Create real serialized scikit-learn model weights and verified manifest
    const ml04CreateScript = `
import json, hashlib, joblib, numpy as np
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from ml.artifacts import CURRENT_EVALUATION_PROTOCOL_VERSION

clf = LogisticRegression()
X = np.array([[1.0, 2.0], [2.0, 3.0], [3.0, 1.0], [4.0, 5.0], [5.0, 2.0], [1.0, 1.0], [2.0, 1.0], [3.0, 4.0], [4.0, 2.0], [5.0, 6.0]])
y = np.array([0, 1, 0, 1, 0, 0, 1, 1, 0, 1])
clf.fit(X, y)

weights_path = Path("${ml04WeightsPath}")
joblib.dump(clf, weights_path)
with open(weights_path, "rb") as f:
    weights_sha256 = hashlib.sha256(f.read()).hexdigest()

manifest = {
    "model_id": "${ml04ModelId}",
    "experiment_id": "exp_staging_${RUN_ID}",
    "model_type": "logistic_regression",
    "base_strategy": "trend_ema_v1",
    "feature_columns": ["f1", "f2"],
    "strategy_params": {
        "symbol": "${ml04Symbol}",
        "market": "spot",
        "timeframe": "1h"
    },
    "model_params": {},
    "threshold": 0.5,
    "train_metrics": {},
    "validation_metrics": {},
    "test_metrics": {
        "total_trades": 35,
        "profit_factor": 1.5,
        "max_drawdown_pct": 12.5,
        "net_pnl": 500.0
    },
    "eligible_for_paper": True,
    "weights_sha256": weights_sha256,
    "evaluation_protocol_version": CURRENT_EVALUATION_PROTOCOL_VERSION
}

manifest_path = Path("${ml04ManifestPath}")
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

# Verify model loading
loaded_clf = joblib.load(weights_path)
pred = loaded_clf.predict([[2.0, 2.0]])

print(json.dumps({
    "model_id": "${ml04ModelId}",
    "weights_sha256": weights_sha256,
    "verified_load": bool(len(pred) == 1)
}))
`;
    const ml04Created = await runPythonHelper(ml04CreateScript);
    assert(ml04Created.verified_load === true, "Model loading verification failed");
    console.log(`  [1] Created valid scikit-learn model artifact: ${ml04ModelId} (weights SHA-256: ${ml04Created.weights_sha256.slice(0, 12)}..., verified loadable).`);

    // Execute deployment mutation via Trader API (POST /api/ml/deploy-to-paper)
    const ml04DeployRes = await apiRequest("POST", "/api/ml/deploy-to-paper", traderToken, {
      model_id: ml04ModelId,
      allocated_capital: 7500.0,
      risk_per_trade_pct: 1.2,
      send_telegram: false,
    });
    assert(ml04DeployRes.status === 200, `Expected 200 from deploy-to-paper, got: ${ml04DeployRes.status}`);
    console.log(`  [2] Deployment API response verified: HTTP 200 OK.`);

    // Verify DB persistence in paper_configs
    const ml04VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, symbol, strategy_name, allocated_capital, risk_per_trade_pct, telegram_enabled, is_active, ml_model_id FROM paper_configs WHERE ml_model_id = %s", ('${ml04ModelId}',))
row = cur.fetchone()
if row and hasattr(row.get("allocated_capital"), "__float__"):
    row["allocated_capital"] = float(row["allocated_capital"])
if row and hasattr(row.get("risk_per_trade_pct"), "__float__"):
    row["risk_per_trade_pct"] = float(row["risk_per_trade_pct"])
cur.close(); conn.close()
print(json.dumps({"after_config": row}, default=str))
`;
    const ml04VerifyRes = await runPythonHelper(ml04VerifyScript);
    assert(ml04VerifyRes.after_config && ml04VerifyRes.after_config.ml_model_id === ml04ModelId, "Config ml_model_id not persisted");
    const ml04ConfigId = ml04VerifyRes.after_config.id;
    testFixtures.paperConfigs.push(ml04ConfigId);
    console.log(`  [3] Database persistence verified: Config #${ml04ConfigId} active with ml_model_id = '${ml04ModelId}', capital=$7,500, telegram_enabled=0.`);

    // Promotion gate verification: Ineligible model rejected with HTTP 409 Conflict
    const gateCheckRes = await apiRequest("POST", "/api/ml/deploy-to-paper", traderToken, {
      model_id: "real-btcusdt-spot-1h-trend_ema_v1-tournament-binary_positive_r",
      allocated_capital: 5000.0,
      risk_per_trade_pct: 1.0,
      send_telegram: false,
    });
    assert(gateCheckRes.status === 409, `Ineligible model must return 409 Conflict, got: ${gateCheckRes.status}`);
    console.log(`  [4] Gate enforcement verified: Ineligible model strictly rejected with HTTP 409 Conflict.`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/ml/deploy-to-paper
    const viewerMlDeployRes = await apiRequest("POST", "/api/ml/deploy-to-paper", viewerToken, {
      model_id: ml04ModelId,
      allocated_capital: 7500.0,
      risk_per_trade_pct: 1.2,
      send_telegram: false,
    });
    assert(viewerMlDeployRes.status === 403, `Viewer ML deploy must return 403, got: ${viewerMlDeployRes.status}`);
    console.log(`  [5] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["ML-04"] = {
      classification: "API_PERSISTENCE_VERIFIED",
      note: "Deployment configuration persistence verified. Inference model loadable via joblib; live execution loops bypassed. UI view load gated on baseline by manifest globbing defect (remediated in PR #702).",
      after: ml04VerifyRes.after_config,
      gateCheck: "HTTP 409 on Ineligible Model Verified",
      rbac: "Viewer 403 Verified (POST /api/ml/deploy-to-paper)",
    };

    // =================================================================
    // WORKFLOW 7: RB-03 — Update Strategy Lifecycle Settings
    // =================================================================
    console.log(`\n[=== RB-03: Update Strategy Lifecycle Settings ===]`);
    const rb03Strategy = "RSI_CTR";
    const rb03Mode = "shadow";
    console.log(`  [1] Pre-test DB state for ${rb03Strategy} (${rb03Mode}): ${JSON.stringify(originalRb03Row)}.`);

    // Navigate to Research view in browser
    await page.locator("button:has-text('Research')").first().click();
    await page.waitForSelector(".research-view", { timeout: 8000 });

    // Find row in Strategy lifecycle snapshot table
    const rb03Row = page.locator(`tr:has-text('${rb03Strategy}')`).filter({ hasText: rb03Mode }).first();
    await rb03Row.waitFor({ state: "visible", timeout: 8000 });

    // Toggle Paper tracking checkbox
    const paperCheckbox = rb03Row.locator(`input[aria-label='Paper tracking for ${rb03Strategy} (${rb03Mode})']`).first();
    const currentPaperChecked = await paperCheckbox.isChecked();
    if (!currentPaperChecked) {
      await paperCheckbox.check();
    }

    // Click Save on that row
    const saveRowBtn = rb03Row.locator("button:has-text('Save')").first();
    await saveRowBtn.click();

    // Verify feedback
    await page.waitForSelector(".api-success", { timeout: 8000 });
    const rb03Feedback = await page.locator(".api-success").innerText();
    assert(rb03Feedback.includes(`Lifecycle settings saved for '${rb03Strategy}' (${rb03Mode}).`), `Unexpected feedback: ${rb03Feedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "rb03_lifecycle_saved.png") });
    console.log(`  [2] UI feedback verified: "${rb03Feedback.trim()}".`);

    // Verify DB persistence
    const rb03VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", ('${rb03Strategy}', '${rb03Mode}'))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"after": row}, default=str))
`;
    const rb03VerifyRes = await runPythonHelper(rb03VerifyScript);
    assert(rb03VerifyRes.after && rb03VerifyRes.after.paper_enabled === 1, `Expected paper_enabled=1, got: ${JSON.stringify(rb03VerifyRes.after)}`);
    console.log(`  [3] Database persistence verified: ${rb03Strategy} (${rb03Mode}) paper_enabled = 1.`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/research/retailbot2/strategies
    const viewerLifeRes = await apiRequest("POST", "/api/research/retailbot2/strategies", viewerToken, {
      strategy_id: rb03Strategy, mode: rb03Mode, evaluation_enabled: false, paper_enabled: false, telegram_enabled: false, report_enabled: false, auto_promote_enabled: false,
    });
    assert(viewerLifeRes.status === 403, `Viewer lifecycle save must return 403, got: ${viewerLifeRes.status}`);
    console.log(`  [4] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["RB-03"] = {
      classification: "BROWSER_E2E_VERIFIED",
      before: originalRb03Row,
      after: rb03VerifyRes.after,
      rbac: "Viewer 403 Verified (POST /api/research/retailbot2/strategies)",
    };

    // =================================================================
    // WORKFLOW 8: RB-04 — Stop and Hide Strategy
    // =================================================================
    console.log(`\n[=== RB-04: Stop and Hide Strategy ===]`);
    const rb04Strategy = "DBL_PAT_INV";
    const rb04Mode = "inverse";
    console.log(`  [1] Pre-test DB state for ${rb04Strategy} (${rb04Mode}): ${JSON.stringify(originalRb04Row)}.`);

    // Seed active row in rdt_strategy_lifecycle for test
    const rb04SeedScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled)
VALUES (%s, %s, 1, 1, 1, 1, 0)
ON DUPLICATE KEY UPDATE evaluation_enabled=1, paper_enabled=1, telegram_enabled=1, report_enabled=1
''', ('${rb04Strategy}', '${rb04Mode}'))
conn.commit()
cur.execute("SELECT strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", ('${rb04Strategy}', '${rb04Mode}'))
active_state = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"active_state": active_state}, default=str))
`;
    await runPythonHelper(rb04SeedScript);
    console.log(`  [2] Seeded active strategy state for ${rb04Strategy} (${rb04Mode}): all flags = 1.`);

    // Refresh research view in browser
    await page.locator("button:has-text('Refresh')").first().click();
    await page.waitForTimeout(1000);

    // Locate row for DBL_PAT_INV
    const rb04Row = page.locator(`tr:has-text('${rb04Strategy}')`).filter({ hasText: rb04Mode }).first();
    await rb04Row.waitFor({ state: "visible", timeout: 8000 });

    // Click Stop & Hide button
    const stopHideBtn = rb04Row.locator("button:has-text('Stop & Hide')").first();
    await stopHideBtn.click();

    // Verify modal
    await page.waitForSelector("#stop-modal-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "rb04_stop_hide_modal.png") });
    console.log(`  [3] Stop & Hide confirmation dialog displayed and captured.`);

    // Confirm stop & hide
    const confirmStopBtn = page.locator("button:has-text('Stop & Hide Strategy')").first();
    await confirmStopBtn.click();

    // Verify feedback
    await page.waitForSelector(".api-success", { timeout: 8000 });
    const rb04Feedback = await page.locator(".api-success").innerText();
    assert(rb04Feedback.includes(`Strategy '${rb04Strategy}' (${rb04Mode}) stopped and hidden.`), `Unexpected feedback: ${rb04Feedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "rb04_strategy_stopped.png") });
    console.log(`  [4] UI feedback verified: "${rb04Feedback.trim()}".`);

    // Verify DB persistence: all flags 0
    const rb04VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", ('${rb04Strategy}', '${rb04Mode}'))
after_state = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"after": after_state}, default=str))
`;
    const rb04VerifyRes = await runPythonHelper(rb04VerifyScript);
    assert(
      rb04VerifyRes.after &&
      rb04VerifyRes.after.evaluation_enabled === 0 &&
      rb04VerifyRes.after.paper_enabled === 0 &&
      rb04VerifyRes.after.telegram_enabled === 0 &&
      rb04VerifyRes.after.report_enabled === 0,
      `Expected all flags 0, got: ${JSON.stringify(rb04VerifyRes.after)}`
    );
    console.log(`  [5] Database persistence verified: all flags reset to 0 (stopped and hidden).`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint POST /api/research/retailbot2/strategies
    const viewerStopRes = await apiRequest("POST", "/api/research/retailbot2/strategies", viewerToken, {
      strategy_id: rb04Strategy, mode: rb04Mode, evaluation_enabled: false, paper_enabled: false, telegram_enabled: false, report_enabled: false, auto_promote_enabled: false,
    });
    assert(viewerStopRes.status === 403, `Viewer stop & hide must return 403, got: ${viewerStopRes.status}`);
    console.log(`  [6] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["RB-04"] = {
      classification: "BROWSER_E2E_VERIFIED",
      before: originalRb04Row,
      after: rb04VerifyRes.after,
      rbac: "Viewer 403 Verified (POST /api/research/retailbot2/strategies)",
    };

    // =================================================================
    // WORKFLOW 9: WT-04 — Track and Untrack Wallet
    // =================================================================
    console.log(`\n[=== WT-04: Track and Untrack Wallet ===]`);
    const wt04Address = `0x22223333444455556666777788889999${RUN_ID.padEnd(8, "0")}`;
    const wt04Label = `stg_wt04_${RUN_ID}`;

    // Navigate to Wallet Tracking
    await page.locator("button:has-text('Wallet Tracking')").first().click();
    await page.waitForSelector(".wallet-tracking-view", { timeout: 8000 });

    // Click Track wallet
    await page.locator("button:has-text('Track wallet')").first().click();
    await page.waitForSelector("#track-wallet-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "wt04_track_wallet_modal.png") });

    // Fill form
    await page.locator("#w-chain").selectOption("eth");
    await page.locator("#w-address").fill(wt04Address);
    await page.locator("#w-label").fill(wt04Label);

    // Submit
    await page.locator("button[type='submit']:has-text('Start Tracking')").click();

    // Verify feedback
    await page.waitForSelector(".api-success", { timeout: 8000 });
    const wt04TrackFeedback = await page.locator(".api-success").innerText();
    assert(wt04TrackFeedback.includes("added to tracked wallets on eth"), `Unexpected feedback: ${wt04TrackFeedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "wt04_wallet_tracked.png") });
    console.log(`  [1] UI feedback verified: "${wt04TrackFeedback.trim()}".`);

    // Verify DB persistence: wallet tracked
    const wt04VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, chain, wallet, label, status FROM tracked_wallets WHERE LOWER(wallet) = LOWER(%s)", ('${wt04Address}',))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"tracked_wallet": row}, default=str))
`;
    const wt04TrackedRes = await runPythonHelper(wt04VerifyScript);
    assert(wt04TrackedRes.tracked_wallet && wt04TrackedRes.tracked_wallet.status === "ACTIVE", "Expected ACTIVE tracked wallet in DB");
    const trackedId = wt04TrackedRes.tracked_wallet.id;
    testFixtures.trackedWallets.push(trackedId);
    console.log(`  [2] Database persistence verified: Tracked wallet #${trackedId} created with status = 'ACTIVE'.`);

    // In browser, locate row and click Untrack
    const walletRow = page.locator(`tr:has-text('${wt04Label}')`).first();
    await walletRow.waitFor({ state: "visible", timeout: 8000 });
    const untrackBtn = walletRow.locator("button:has-text('Untrack')").first();
    await untrackBtn.click();

    // Verify untrack modal
    await page.waitForSelector("#untrack-wallet-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "wt04_untrack_wallet_modal.png") });

    // Confirm untrack
    await page.locator("button:has-text('Untrack Wallet')").first().click();

    // Verify feedback
    await page.waitForSelector(".api-success", { timeout: 8000 });
    const wt04UntrackFeedback = await page.locator(".api-success").innerText();
    assert(wt04UntrackFeedback.includes("removed from tracked wallets"), `Unexpected feedback: ${wt04UntrackFeedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "wt04_wallet_untracked.png") });
    console.log(`  [3] UI feedback verified: "${wt04UntrackFeedback.trim()}".`);

    // Verify DB persistence: soft-remove status = 'IGNORED'
    const wt04UntrackScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, status FROM tracked_wallets WHERE id = %s", (${trackedId},))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"untracked_row": row}, default=str))
`;
    const wt04UntrackRes = await runPythonHelper(wt04UntrackScript);
    assert(wt04UntrackRes.untracked_row && wt04UntrackRes.untracked_row.status === "IGNORED", `Expected status='IGNORED', got: ${JSON.stringify(wt04UntrackRes.untracked_row)}`);
    console.log(`  [4] Database persistence verified: Tracked wallet #${trackedId} status updated to 'IGNORED' (soft-remove to prevent auto-reflagging).`);

    // RBAC check: Viewer denied 403 on exact frontend endpoints POST /api/wallets/tracked and DELETE /api/wallets/tracked/{id}
    const viewerTrackRes = await apiRequest("POST", "/api/wallets/tracked", viewerToken, { chain: "eth", wallet: "0x1234567890123456789012345678901234567890" });
    assert(viewerTrackRes.status === 403, `Viewer track must return 403, got: ${viewerTrackRes.status}`);
    const viewerUntrackRes = await apiRequest("DELETE", `/api/wallets/tracked/${trackedId}`, viewerToken);
    assert(viewerUntrackRes.status === 403, `Viewer untrack must return 403, got: ${viewerUntrackRes.status}`);
    console.log(`  [5] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend wallet endpoints.`);

    workflowResults["WT-04"] = {
      classification: "BROWSER_E2E_VERIFIED",
      trackedState: wt04TrackedRes.tracked_wallet,
      untrackedState: wt04UntrackRes.untracked_row,
      rbac: "Viewer 403 Verified (POST/DELETE /api/wallets/tracked/...)",
    };

    // =================================================================
    // WORKFLOW 10: WT-05 — Save Scanner Settings & Alert Thresholds
    // =================================================================
    console.log(`\n[=== WT-05: Save Scanner Settings & Alert Thresholds ===]`);
    const wt05TestKey = `birdeye-key-staging-${RUN_ID}99`;
    console.log(`  [1] Pre-test DB state: wallet_tracker_settings (id=1) = min_trade: ${originalWalletSettings?.min_trade_usd}, poll: ${originalWalletSettings?.poll_interval_seconds}.`);

    // Dismiss any existing success banner before editing settings
    const dismissBtn = page.locator(".api-success button[aria-label='Dismiss message']").first();
    if (await dismissBtn.isVisible()) {
      await dismissBtn.click();
      await page.waitForTimeout(300);
    }

    // Click Edit settings button in browser
    await page.locator("button:has-text('Edit settings')").first().click();
    await page.waitForSelector("#wallet-settings-title", { timeout: 5000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "wt05_settings_modal.png") });

    // Fill settings form with ordinary round values
    await page.locator("#s-min-trade").fill("16500");
    await page.locator("#s-min-net").fill("180000");
    await page.locator("#s-auto-track").fill("550000");
    await page.locator("#s-poll").fill("350");
    await page.locator("#s-birdeye-key").fill(wt05TestKey);

    // Assert native HTML5 validity state on all inputs (validating PR #703 min=0 fix)
    const tradeValid = await page.locator("#s-min-trade").evaluate((el) => el.validity.valid);
    const netValid = await page.locator("#s-min-net").evaluate((el) => el.validity.valid);
    const autoTrackValid = await page.locator("#s-auto-track").evaluate((el) => el.validity.valid);
    const pollValid = await page.locator("#s-poll").evaluate((el) => el.validity.valid);
    assert(tradeValid && netValid && autoTrackValid && pollValid, "HTML5 native validity checks failed on form inputs");
    console.log(`  [*] Native browser form validation passed: all input fields are valid without bypass.`);

    // Submit settings form via native browser click
    console.log(`  [*] Submitting settings form via native browser click...`);
    await page.locator("dialog[open] button[type='submit']").first().click();
    await page.waitForSelector(".api-success", { timeout: 8000 });
    const wt05Feedback = await page.locator(".api-success").innerText();
    assert(wt05Feedback.includes("Wallet tracker settings updated successfully"), `Unexpected feedback: ${wt05Feedback}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "wt05_settings_saved.png") });
    console.log(`  [2] UI feedback verified: "${wt05Feedback.trim()}".`);

    // Verify DB persistence directly from the browser submission
    const wt05VerifyScript = `
import json, sys, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, enabled, networks, pools_per_network, min_trade_usd, min_net_usd, auto_track_net_usd, poll_interval_seconds, retention_days, api_delay_seconds, token_mode, token_addresses, birdeye_api_key FROM wallet_tracker_settings WHERE id = 1")
row = cur.fetchone()
if row:
    for k in ["min_trade_usd", "min_net_usd", "auto_track_net_usd", "api_delay_seconds"]:
        if hasattr(row.get(k), "__float__"):
            row[k] = float(row[k])
cur.close(); conn.close()
print(json.dumps({"settings": row}, default=str))
`;
    const wt05VerifyRes = await runPythonHelper(wt05VerifyScript);
    assert(wt05VerifyRes.settings, "wallet_tracker_settings row 1 missing");
    assert(wt05VerifyRes.settings.min_trade_usd === 16500.0, `Min trade USD mismatch: ${wt05VerifyRes.settings.min_trade_usd}`);
    assert(wt05VerifyRes.settings.min_net_usd === 180000.0, `Min net USD mismatch: ${wt05VerifyRes.settings.min_net_usd}`);
    assert(wt05VerifyRes.settings.auto_track_net_usd === 550000.0, `Auto track USD mismatch: ${wt05VerifyRes.settings.auto_track_net_usd}`);
    assert(wt05VerifyRes.settings.poll_interval_seconds === 350, `Poll interval mismatch: ${wt05VerifyRes.settings.poll_interval_seconds}`);
    assert(wt05VerifyRes.settings.birdeye_api_key === wt05TestKey, "Birdeye API key mismatch in DB");
    console.log(`  [3] Database persistence verified directly from browser submission: min_trade=16,500, poll=350s, birdeye_api_key persisted.`);

    // Read back via API to verify credential masking
    const readSettingsRes = await apiRequest("GET", "/api/wallets/settings", traderToken);
    assert(readSettingsRes.status === 200 && readSettingsRes.data.has_birdeye_key === true, "Settings GET failed or key not flagged");
    assert(readSettingsRes.data.birdeye_api_key_masked.endsWith(wt05TestKey.slice(-4)), `Masked key mismatch: ${readSettingsRes.data.birdeye_api_key_masked}`);
    assert(!("birdeye_api_key" in readSettingsRes.data), "Raw birdeye_api_key must never be exposed");
    console.log(`  [4] Credential masking verified: ${readSettingsRes.data.birdeye_api_key_masked} (raw secret strictly unexposed).`);

    // RBAC check: Viewer denied 403 on exact frontend endpoint PUT /api/wallets/settings
    const viewerSettingsRes = await apiRequest("PUT", "/api/wallets/settings", viewerToken, { enabled: true });
    assert(viewerSettingsRes.status === 403, `Viewer settings PUT must return 403, got: ${viewerSettingsRes.status}`);
    console.log(`  [5] RBAC contract verified: Viewer rejected with HTTP 403 Forbidden on exact frontend endpoint.`);

    workflowResults["WT-05"] = {
      classification: "BROWSER_E2E_VERIFIED",
      browserStatus: "Verified via native browser click (HTML5 min=0 validated)",
      before: originalWalletSettings,
      after: {
        db: { ...wt05VerifyRes.settings, birdeye_api_key: "[REDACTED]" },
        apiMasked: readSettingsRes.data.birdeye_api_key_masked,
      },
      rbac: "Viewer 403 Verified (PUT /api/wallets/settings)",
    };

    console.log(`\n======================================================================`);
    console.log(` WORKFLOW EXECUTION COMPLETED`);
    console.log(`======================================================================\n`);

  } finally {
    console.log(`[*] Initiating rigorous cleanup & state restoration phase...`);

    if (browser) {
      try { await browser.close(); } catch (e) { console.error("Browser close err:", e); }
    }

    if (proc) {
      console.log(`[*] Terminating staging server PID ${proc.pid}...`);
      await terminateChildProcess(proc);
      console.log(`[+] Staging server terminated.`);
    }

    // Before executing cleanup, discover and register any additional application-generated
    // audit records created by our unique staging test users (beyond the login event)
    try {
      if (seededUserIds && seededUserIds.length > 0) {
        const findTestAuditsScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
uids = ${JSON.stringify(seededUserIds)}
p = ",".join(["%s"] * len(uids))
cur.execute(f"SELECT id FROM audit_logs WHERE user_id IN ({p})", tuple(uids))
found_ids = [r["id"] for r in cur.fetchall()]
cur.close(); conn.close()
print(json.dumps({"found_ids": found_ids}))
`;
        const findRes = await runPythonHelper(findTestAuditsScript);
        if (findRes && findRes.found_ids) {
          for (const fid of findRes.found_ids) {
            if (!testFixtures.auditLogs.includes(fid)) {
              testFixtures.auditLogs.push(fid);
              console.log(`  [+] Registered additional staging test user audit record ID ${fid}`);
            }
          }
        }
      }
    } catch (e) {
      console.error("[-] Error discovering test user audit records:", e);
    }

    // Execute database fixture cleanup and state restoration
    try {
      const cleanupOutput = await executeStagingDatabaseCleanup({
        target_db: TARGET_DB,
        user_ids: seededUserIds,
        audit_log_ids: testFixtures.auditLogs || [],
        paper_order_ids: testFixtures.paperOrders,
        paper_trade_ids: testFixtures.paperTrades,
        paper_position_ids: testFixtures.paperPositions,
        paper_config_ids: testFixtures.paperConfigs,
        tracked_wallet_ids: testFixtures.trackedWallets,
        snapshot_captured: snapshotCaptured,
        snapshot_state: snapshotState,
      });

      if (cleanupOutput && cleanupOutput.cleanup_results) {
        Object.assign(cleanupStatus, cleanupOutput.cleanup_results);
      }
      console.log(`[+] Database fixture cleanup and state restoration results:`, JSON.stringify(cleanupStatus));
    } catch (e) {
      console.error("[-] DB fixture cleanup execution error:", e);
    }

    // Independent post-cleanup audit check: confirm all registered audit IDs deleted,
    // pre-existing baseline identity intact, and preserved unrelated records untampered
    try {
      const verifyAuditRes = await verifyAuditBaselineIdentity(
        baselineAuditRows,
        testFixtures.auditLogs || [],
        seededUserIds || []
      );
      if (verifyAuditRes) {
        if (verifyAuditRes.leftover_registered && verifyAuditRes.leftover_registered.length > 0) {
          console.error(`[-] Registered audit logs not deleted: ${JSON.stringify(verifyAuditRes.leftover_registered)}`);
          cleanupStatus.auditLogs = false;
        } else {
          console.log(`[+] Verified: All ${testFixtures.auditLogs.length} registered audit logs cleanly deleted.`);
        }
        if (verifyAuditRes.leftover_test_user_audits && verifyAuditRes.leftover_test_user_audits.length > 0) {
          console.error(`[-] Test-owned audit logs not cleaned up: ${JSON.stringify(verifyAuditRes.leftover_test_user_audits)}`);
          cleanupStatus.auditLogs = false;
        }
        if (verifyAuditRes.preserved_unrelated_audits && verifyAuditRes.preserved_unrelated_audits.length > 0) {
          console.log(`[+] Preserved ${verifyAuditRes.preserved_unrelated_audits.length} unrelated concurrent audit record(s) without failure.`);
        }
        if (!verifyAuditRes.baseline_intact) {
          console.error(`[-] Baseline audit log integrity failure detected (${verifyAuditRes.mismatches?.length || 0} mismatch(es)):`);
          if (verifyAuditRes.mismatches) {
            for (const m of verifyAuditRes.mismatches) {
              console.error(`  - ${m}`);
            }
          }
          cleanupStatus.auditLogs = false;
        } else {
          console.log(`[+] Verified: All ${verifyAuditRes.baseline_count} pre-existing baseline audit logs remain 100% intact across all columns.`);
        }
      }
    } catch (e) {
      console.error("[-] Post-cleanup audit verification error:", e);
      cleanupStatus.auditLogs = false;
    }

    // Clean up temporary ML files
    try {
      for (const filePath of testFixtures.mlFiles) {
        if (fs.existsSync(filePath)) fs.unlinkSync(filePath);
      }
      cleanupStatus.mlFiles = true;
      console.log(`[+] Temporary test model files removed.`);
    } catch (e) {
      cleanupStatus.mlFiles = false;
      console.error("[-] ML files cleanup error:", e);
    }

    // Assert that all cleanups succeeded
    const allCleanedUp = Object.values(cleanupStatus).every(Boolean);
    if (!allCleanedUp) {
      console.error("\n[CRITICAL ERROR] Cleanup failed or incomplete! One or more fixture categories could not be verified:");
      for (const [k, v] of Object.entries(cleanupStatus)) {
        if (!v) console.error(`  - ${k}: FAILED`);
      }
      process.exitCode = 1;
    } else {
      console.log(`\n[+] ALL DATABASE CLEANUP & RESTORATION CHECKS VERIFIED 100% CLEAN.`);
    }
  }

  // Print workflow results summary JSON
  console.log("\n[=== STAGING ACCEPTANCE RESULTS SUMMARY ===]");
  console.log(JSON.stringify(workflowResults, null, 2));

  console.log("\n[=== FIXTURE CLEANUP STATUS REGISTER ===]");
  console.log(JSON.stringify(cleanupStatus, null, 2));
}

if (require.main === module) {
  run().then(() => {
    if (process.exitCode && process.exitCode !== 0) {
      console.error(`[-] Exiting with code ${process.exitCode} due to cleanup or verification failures.`);
      process.exit(process.exitCode);
    }
    process.exit(0);
  }).catch((err) => {
    console.error("\n[-] Staging acceptance run failed with unhandled exception:", err);
    process.exit(1);
  });
}

module.exports = {
  run,
  executeStagingDatabaseCleanup,
  verifyAuditBaselineIdentity,
};
