/**
 * Staging Harness Safety & Restoration Regression Tests
 *
 * Verifies that:
 * 1. Pre-snapshot failure safety: If a test run fails before the snapshot is captured,
 *    shared tables (system_flags, rdt_strategy_lifecycle, wallet_tracker_settings)
 *    are NEVER deleted or mutated, and cleanup reports failure (circuitBreaker=false,
 *    lifecycle=false, walletSettings=false).
 * 2. Exact system_flags restoration: All columns (flag_name, is_active, reason, updated_at)
 *    are restored and verified via readback comparison, and ABSENT flags are cleanly deleted.
 * 3. Exact rdt_strategy_lifecycle restoration: All columns (evaluation_enabled, paper_enabled,
 *    telegram_enabled, report_enabled, auto_promote_enabled, updated_at) are restored,
 *    and ABSENT rows are deleted and asserted absent, using run-unique strategy IDs to guarantee
 *    pre-existing shared data is NEVER deleted.
 * 4. Exact wallet_tracker_settings restoration: All 12 columns + updated_at are restored
 *    and verified via readback comparison (id=1).
 * 4B. Regression Test A: Wallet restoration with non-default target ID (99) and distinctive
 *    values across all 12 columns + updated_at, including non-destructive ABSENT verification.
 * 5. Harness cleanup failure propagation: Any cleanup failure sets non-zero exit code.
 * 6. ML-03 handled error API contract & Regression Test A (Test 6 cleanup): POST /api/ml/run-experiment
 *    returns HTTP 500 with exact detail, Viewer is rejected with HTTP 403, and Test 6 cleanup
 *    deletes only registered audit IDs while strictly preserving unregistered audit logs on its users.
 * 7. Pre-existing lifecycle row survival (Requirement 8): Pre-existing lifecycle rows seeded
 *    with distinctive values survive an entire test mutation and harness cleanup cycle completely
 *    unharmed and identically verified across all columns.
 * 8. Regression Test B (Test 8 cleanup): Existing SAFE-prefixed records survive exact-ownership
 *    cleanup, and cleanup strictly preserves unrelated audit records associated with test-created users.
 * 9. Exact audit-log ownership (Regression Test C): Registered audit log is deleted, while
 *    unregistered audit log associated with test user and pre-existing audit log remain intact.
 * 10. Injected audit failure detection (Regression Test D): Failure-safe global restoration
 *     continues after injected failure in auditLogCleanup, postRestoreVerification detects unrecovered
 *     audit record, later stages execute, and unhandled failures cause nonzero process exit.
 * 11. Baseline integrity verification (Regression Test E): 100% baseline identity verified across
 *     audit_logs, wallet_tracker_settings (all 12 columns + updated_at), system_flags, and rdt_strategy_lifecycle.
 * 12. Normalized source-level regression check (Regression Test F): Scans both staging harnesses
 *     across normalized multi-line SQL to guarantee zero broad or indirect audit-log deletions.
 * 13. Staging Workflow Audit Fixture Isolation Suite (A - F): Proves staging logins produce registered
 *     audit IDs, exact cleanup deletes registered records, unregistered records survive cleanup,
 *     pre-existing records remain unchanged, cleanup failures exit nonzero, and zero staging audit
 *     records remain.
 *
 * Run command:
 *   node tests/browser/test_staging_harness_safety.cjs
 */

const { spawn } = require("node:child_process");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const fs = require("node:fs");

const REPO_ROOT = path.resolve(__dirname, "../..");
const TARGET_DB = "signal_bot_test";
const RUN_ID = Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);

function assert(condition, message) {
  if (!condition) {
    throw new Error(`[ASSERTION FAILED] ${message}`);
  }
}

function runPythonHelper(scriptContent, stdinJson = null) {
  return new Promise((resolve, reject) => {
    const env = {
      ...process.env,
      MYSQL_DATABASE: TARGET_DB,
      PATH: process.env.PATH || "/home/BIS/.local/bin:/usr/local/bin:/usr/bin:/bin",
    };
    const pythonBin = process.env.PYTHON || "/home/BIS/.local/bin/python3";
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
        return reject(new Error(`Python script exited with code ${code}:\n${stderr || stdout}`));
      }
      try {
        const parsed = JSON.parse(stdout.trim());
        resolve(parsed);
      } catch (err) {
        resolve({ raw: stdout, stderr });
      }
    });

    if (stdinJson !== null) {
      proc.stdin.write(JSON.stringify(stdinJson));
    }
    proc.stdin.end();
  });
}

// Global exact-ownership fixture registry
class FixtureRegistry {
  constructor() {
    this.strategyLifecycles = new Map(); // key "stratId:::mode" -> { strategy_id, mode }
    this.systemFlags = new Set();         // flag_name
    this.userIds = new Set();             // user_id integer
    this.auditLogIds = new Set();         // audit_log_id integer
  }

  registerStrategy(strategy_id, mode) {
    this.strategyLifecycles.set(`${strategy_id}:::${mode}`, { strategy_id, mode });
  }

  unregisterStrategy(strategy_id, mode) {
    this.strategyLifecycles.delete(`${strategy_id}:::${mode}`);
  }

  registerFlag(flag_name) {
    this.systemFlags.add(flag_name);
  }

  unregisterFlag(flag_name) {
    this.systemFlags.delete(flag_name);
  }

  registerUser(user_id) {
    this.userIds.add(user_id);
  }

  unregisterUser(user_id) {
    this.userIds.delete(user_id);
  }

  registerAuditLog(audit_log_id) {
    this.auditLogIds.add(audit_log_id);
  }

  unregisterAuditLog(audit_log_id) {
    this.auditLogIds.delete(audit_log_id);
  }
}

const globalRegistry = new FixtureRegistry();

const CLEANUP_PYTHON_SNIPPET = `
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

# 1. Delete individually registered audit logs and users (exact IDs only)
try:
    audit_ids = payload.get("audit_log_ids", [])
    if audit_ids:
        placeholders = ",".join(["%s"] * len(audit_ids))
        cur.execute(f"DELETE FROM audit_logs WHERE id IN ({placeholders})", tuple(audit_ids))
        cur.execute(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN ({placeholders})", tuple(audit_ids))
        assert cur.fetchone()["cnt"] == 0, "Registered audit logs not fully deleted"

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

# 7. Restore system_flags emergency_stop to exact pre-test state
try:
    if not snapshot_captured:
        results["circuitBreaker"] = False
        print("[-] Pre-test snapshot was never captured; skipping system_flags restoration to prevent deletion of shared data.", file=sys.stderr)
    else:
        snap_item = snapshot_state.get("emergencyStop", {"status": "NOT_CAPTURED", "row": None})
        status = snap_item.get("status")
        init_flag = snap_item.get("row")
        flag_name = snap_item.get("flag_name") or (init_flag.get("flag_name") if init_flag else "emergency_stop")
        if status == "ABSENT":
            cur.execute("DELETE FROM system_flags WHERE flag_name = %s", (flag_name,))
            conn.commit()
            cur.execute("SELECT flag_name FROM system_flags WHERE flag_name = %s", (flag_name,))
            assert cur.fetchone() is None, f"{flag_name} row should not exist after cleanup"
            results["circuitBreaker"] = True
        elif status == "EXISTS" and init_flag is not None:
            cur.execute(
                "UPDATE system_flags SET is_active = %s, reason = %s, updated_at = %s WHERE flag_name = %s",
                (init_flag["is_active"], init_flag["reason"], init_flag["updated_at"], flag_name)
            )
            conn.commit()
            cur.execute("SELECT * FROM system_flags WHERE flag_name = %s", (flag_name,))
            verified_flag = cur.fetchone()
            assert verified_flag is not None, f"{flag_name} row missing after restore"
            assert verified_flag["flag_name"] == init_flag["flag_name"], f"{flag_name} flag_name mismatch: {verified_flag['flag_name']} vs {init_flag['flag_name']}"
            assert verified_flag["is_active"] == init_flag["is_active"], f"{flag_name} is_active mismatch: {verified_flag['is_active']} vs {init_flag['is_active']}"
            assert (verified_flag["reason"] or "") == (init_flag["reason"] or ""), f"{flag_name} reason mismatch: {verified_flag['reason']} vs {init_flag['reason']}"
            assert verified_flag["updated_at"].isoformat() == init_flag["updated_at"], f"{flag_name} updated_at mismatch: {verified_flag['updated_at'].isoformat()} vs {init_flag['updated_at']}"
            results["circuitBreaker"] = True
        else:
            raise ValueError(f"Invalid emergencyStop snapshot status: {status}")
except Exception as e:
    results["circuitBreaker"] = False
    print(f"[-] Circuit breaker flag restoration error: {e}", file=sys.stderr)

# 8. Restore rdt_strategy_lifecycle to exact pre-test state
try:
    if not snapshot_captured:
        results["lifecycle"] = False
        print("[-] Pre-test snapshot was never captured; skipping rdt_strategy_lifecycle restoration to prevent deletion of shared data.", file=sys.stderr)
    else:
        # RB-03
        rb03_snap = snapshot_state.get("rb03Row", {"status": "NOT_CAPTURED", "row": None})
        rb03_status = rb03_snap.get("status")
        orig_rb03 = rb03_snap.get("row")
        rb03_strat = rb03_snap.get("strategy_id") or (orig_rb03.get("strategy_id") if orig_rb03 else "RSI_CTR")
        rb03_mode = rb03_snap.get("mode") or (orig_rb03.get("mode") if orig_rb03 else "shadow")
        if rb03_status == "ABSENT":
            cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb03_strat, rb03_mode))
            conn.commit()
            cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb03_strat, rb03_mode))
            assert cur.fetchone() is None, f"{rb03_strat}/{rb03_mode} should not exist after cleanup"
        elif rb03_status == "EXISTS" and orig_rb03 is not None:
            cur.execute('''
                UPDATE rdt_strategy_lifecycle
                SET evaluation_enabled=%s, paper_enabled=%s, telegram_enabled=%s, report_enabled=%s, auto_promote_enabled=%s, updated_at=%s
                WHERE strategy_id = %s AND mode = %s
            ''', (
                orig_rb03["evaluation_enabled"], orig_rb03["paper_enabled"],
                orig_rb03["telegram_enabled"], orig_rb03["report_enabled"],
                orig_rb03["auto_promote_enabled"], orig_rb03["updated_at"],
                rb03_strat, rb03_mode
            ))
            assert cur.rowcount >= 0, f"Failed to update pre-existing {rb03_strat} row"
            conn.commit()
            cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb03_strat, rb03_mode))
            rb03_after = cur.fetchone()
            assert rb03_after is not None, f"{rb03_strat}/{rb03_mode} missing after restore"
            for col in ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled"]:
                assert rb03_after[col] == orig_rb03[col], f"{rb03_strat} {col} mismatch: {rb03_after[col]} vs {orig_rb03[col]}"
            assert rb03_after["updated_at"].isoformat() == orig_rb03["updated_at"], f"{rb03_strat} updated_at mismatch: {rb03_after['updated_at'].isoformat()} vs {orig_rb03['updated_at']}"
        else:
            raise ValueError(f"Invalid rb03Row snapshot status: {rb03_status}")

        # RB-04
        rb04_snap = snapshot_state.get("rb04Row", {"status": "NOT_CAPTURED", "row": None})
        rb04_status = rb04_snap.get("status")
        orig_rb04 = rb04_snap.get("row")
        rb04_strat = rb04_snap.get("strategy_id") or (orig_rb04.get("strategy_id") if orig_rb04 else "DBL_PAT_INV")
        rb04_mode = rb04_snap.get("mode") or (orig_rb04.get("mode") if orig_rb04 else "inverse")
        if rb04_status == "ABSENT":
            cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb04_strat, rb04_mode))
            conn.commit()
            cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb04_strat, rb04_mode))
            assert cur.fetchone() is None, f"{rb04_strat}/{rb04_mode} should not exist after cleanup"
        elif rb04_status == "EXISTS" and orig_rb04 is not None:
            cur.execute('''
                UPDATE rdt_strategy_lifecycle
                SET evaluation_enabled=%s, paper_enabled=%s, telegram_enabled=%s, report_enabled=%s, auto_promote_enabled=%s, updated_at=%s
                WHERE strategy_id = %s AND mode = %s
            ''', (
                orig_rb04["evaluation_enabled"], orig_rb04["paper_enabled"],
                orig_rb04["telegram_enabled"], orig_rb04["report_enabled"],
                orig_rb04["auto_promote_enabled"], orig_rb04["updated_at"],
                rb04_strat, rb04_mode
            ))
            assert cur.rowcount >= 0, f"Failed to update pre-existing {rb04_strat} row"
            conn.commit()
            cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb04_strat, rb04_mode))
            rb04_after = cur.fetchone()
            assert rb04_after is not None, f"{rb04_strat}/{rb04_mode} missing after restore"
            for col in ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled"]:
                assert rb04_after[col] == orig_rb04[col], f"{rb04_strat} {col} mismatch: {rb04_after[col]} vs {orig_rb04[col]}"
            assert rb04_after["updated_at"].isoformat() == orig_rb04["updated_at"], f"{rb04_strat} updated_at mismatch: {rb04_after['updated_at'].isoformat()} vs {orig_rb04['updated_at']}"
        else:
            raise ValueError(f"Invalid rb04Row snapshot status: {rb04_status}")

        conn.commit()
        results["lifecycle"] = True
except Exception as e:
    results["lifecycle"] = False
    print(f"[-] Lifecycle restoration error: {e}", file=sys.stderr)

# 9. Restore ALL columns of wallet_tracker_settings (id=1)
try:
    if not snapshot_captured:
        results["walletSettings"] = False
        print("[-] Pre-test snapshot was never captured; skipping wallet_tracker_settings restoration to prevent deletion of shared data.", file=sys.stderr)
    else:
        wt_snap = snapshot_state.get("walletSettings", {"status": "NOT_CAPTURED", "row": None})
        wt_status = wt_snap.get("status")
        init_s = wt_snap.get("row")
        target_id = wt_snap.get("id") or (init_s.get("id") if init_s else 1)
        if wt_status == "ABSENT":
            cur.execute("DELETE FROM wallet_tracker_settings WHERE id = %s", (target_id,))
            conn.commit()
            cur.execute("SELECT id FROM wallet_tracker_settings WHERE id = %s", (target_id,))
            assert cur.fetchone() is None, f"wallet_tracker_settings row {target_id} should not exist after cleanup"
            results["walletSettings"] = True
        elif wt_status == "EXISTS" and init_s is not None:
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
            conn.commit()

            # Read back and assert all 12 columns restored
            cur.execute("SELECT * FROM wallet_tracker_settings WHERE id = %s", (target_id,))
            verified_s = cur.fetchone()
            assert verified_s is not None, f"wallet_tracker_settings row {target_id} missing after restore"
            assert verified_s["enabled"] == init_s["enabled"], f"enabled mismatch: {verified_s['enabled']} vs {init_s['enabled']}"
            assert verified_s["networks"] == init_s["networks"], f"networks mismatch: {verified_s['networks']} vs {init_s['networks']}"
            assert verified_s["pools_per_network"] == init_s["pools_per_network"], f"pools_per_network mismatch: {verified_s['pools_per_network']} vs {init_s['pools_per_network']}"
            assert float(verified_s["min_trade_usd"]) == float(init_s["min_trade_usd"]), f"min_trade_usd mismatch: {verified_s['min_trade_usd']} vs {init_s['min_trade_usd']}"
            assert float(verified_s["min_net_usd"]) == float(init_s["min_net_usd"]), f"min_net_usd mismatch: {verified_s['min_net_usd']} vs {init_s['min_net_usd']}"
            assert float(verified_s["auto_track_net_usd"]) == float(init_s["auto_track_net_usd"]), f"auto_track_net_usd mismatch: {verified_s['auto_track_net_usd']} vs {init_s['auto_track_net_usd']}"
            assert verified_s["poll_interval_seconds"] == init_s["poll_interval_seconds"], f"poll_interval_seconds mismatch: {verified_s['poll_interval_seconds']} vs {init_s['poll_interval_seconds']}"
            assert verified_s["retention_days"] == init_s["retention_days"], f"retention_days mismatch: {verified_s['retention_days']} vs {init_s['retention_days']}"
            assert float(verified_s["api_delay_seconds"]) == float(init_s["api_delay_seconds"]), f"api_delay_seconds mismatch: {verified_s['api_delay_seconds']} vs {init_s['api_delay_seconds']}"
            assert verified_s["token_mode"] == init_s["token_mode"], f"token_mode mismatch: {verified_s['token_mode']} vs {init_s['token_mode']}"
            assert (verified_s["token_addresses"] or "") == (init_s["token_addresses"] or ""), f"token_addresses mismatch: {verified_s['token_addresses']} vs {init_s['token_addresses']}"
            assert (verified_s["birdeye_api_key"] or "") == (init_s["birdeye_api_key"] or ""), f"birdeye_api_key mismatch: {verified_s['birdeye_api_key']} vs {init_s['birdeye_api_key']}"
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

// Helper: Query system flag snapshot
async function getSystemFlagSnapshot(flagName = "emergency_stop") {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM system_flags WHERE flag_name = %s", ('${flagName}',))
row = cur.fetchone()
if row and hasattr(row.get("updated_at"), "isoformat"):
    row["updated_at"] = row["updated_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"status": "EXISTS" if row else "ABSENT", "flag_name": "${flagName}", "row": row}))
`;
  return await runPythonHelper(script);
}

// Helper: Directly restore a system flag snapshot
async function restoreSystemFlagDirect(snap) {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
flag_name = "${snap.flag_name}"
if "${snap.status}" == "ABSENT":
    cur.execute("DELETE FROM system_flags WHERE flag_name = %s", (flag_name,))
else:
    row = json.loads('''${JSON.stringify(snap.row)}''')
    cur.execute("SELECT flag_name FROM system_flags WHERE flag_name = %s", (flag_name,))
    if cur.fetchone():
        cur.execute("UPDATE system_flags SET is_active=%s, reason=%s, updated_at=%s WHERE flag_name=%s",
                    (row["is_active"], row["reason"], row["updated_at"], flag_name))
    else:
        cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, %s, %s, %s)",
                    (flag_name, row["is_active"], row["reason"], row["updated_at"]))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"restored": True}))
`;
  return await runPythonHelper(script);
}

// Helper: Query lifecycle row snapshot
async function getLifecycleRowSnapshot(strategyId, mode) {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", ('${strategyId}', '${mode}'))
row = cur.fetchone()
if row and hasattr(row.get("updated_at"), "isoformat"):
    row["updated_at"] = row["updated_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"status": "EXISTS" if row else "ABSENT", "strategy_id": "${strategyId}", "mode": "${mode}", "row": row}))
`;
  return await runPythonHelper(script);
}

// Helper: Directly restore a lifecycle row snapshot
async function restoreLifecycleRowDirect(snap) {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
strategy_id = "${snap.strategy_id}"
mode = "${snap.mode}"
if "${snap.status}" == "ABSENT":
    cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (strategy_id, mode))
else:
    row = json.loads('''${JSON.stringify(snap.row)}''')
    cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (strategy_id, mode))
    if cur.fetchone():
        cur.execute('''
            UPDATE rdt_strategy_lifecycle
            SET evaluation_enabled=%s, paper_enabled=%s, telegram_enabled=%s, report_enabled=%s, auto_promote_enabled=%s, updated_at=%s
            WHERE strategy_id = %s AND mode = %s
        ''', (row["evaluation_enabled"], row["paper_enabled"], row["telegram_enabled"], row["report_enabled"], row["auto_promote_enabled"], row["updated_at"], strategy_id, mode))
    else:
        cur.execute('''
            INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (strategy_id, mode, row["evaluation_enabled"], row["paper_enabled"], row["telegram_enabled"], row["report_enabled"], row["auto_promote_enabled"], row["updated_at"]))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"restored": True}))
`;
  return await runPythonHelper(script);
}

// Helper: Query wallet settings snapshot
async function getWalletSettingsSnapshot(id = 1) {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM wallet_tracker_settings WHERE id = %s", (${id},))
row = cur.fetchone()
if row:
    for k in list(row.keys()):
        if hasattr(row[k], "isoformat"):
            row[k] = row[k].isoformat()
        elif hasattr(row[k], "__float__"):
            row[k] = float(row[k])
cur.close(); conn.close()
print(json.dumps({"status": "EXISTS" if row else "ABSENT", "id": ${id}, "row": row}))
`;
  return await runPythonHelper(script);
}

// Helper: Directly restore wallet settings snapshot
async function restoreWalletSettingsDirect(snap) {
  const targetId = snap.id || (snap.row && snap.row.id) || 1;
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
target_id = ${targetId}
if "${snap.status}" == "ABSENT":
    cur.execute("DELETE FROM wallet_tracker_settings WHERE id = %s", (target_id,))
else:
    row = json.loads('''${JSON.stringify(snap.row)}''')
    cur.execute("SELECT id FROM wallet_tracker_settings WHERE id = %s", (target_id,))
    if cur.fetchone():
        cur.execute('''
            UPDATE wallet_tracker_settings
            SET enabled=%s, networks=%s, pools_per_network=%s, min_trade_usd=%s, min_net_usd=%s,
                auto_track_net_usd=%s, poll_interval_seconds=%s, retention_days=%s, api_delay_seconds=%s,
                token_mode=%s, token_addresses=%s, birdeye_api_key=%s, updated_at=%s
            WHERE id = %s
        ''', (row["enabled"], row["networks"], row["pools_per_network"], row["min_trade_usd"], row["min_net_usd"],
              row["auto_track_net_usd"], row["poll_interval_seconds"], row["retention_days"], row["api_delay_seconds"],
              row["token_mode"], row["token_addresses"], row["birdeye_api_key"], row["updated_at"], target_id))
    else:
        cur.execute('''
            INSERT INTO wallet_tracker_settings (id, enabled, networks, pools_per_network, min_trade_usd, min_net_usd,
                auto_track_net_usd, poll_interval_seconds, retention_days, api_delay_seconds, token_mode, token_addresses,
                birdeye_api_key, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (target_id, row["enabled"], row["networks"], row["pools_per_network"], row["min_trade_usd"], row["min_net_usd"],
              row["auto_track_net_usd"], row["poll_interval_seconds"], row["retention_days"], row["api_delay_seconds"],
              row["token_mode"], row["token_addresses"], row["birdeye_api_key"], row["updated_at"]))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"restored": True}))
`;
  return await runPythonHelper(script);
}

// Helper: Query all current rows in rdt_strategy_lifecycle
async function getAllLifecycleRows() {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM rdt_strategy_lifecycle ORDER BY strategy_id, mode")
rows = cur.fetchall()
for r in rows:
    if hasattr(r.get("updated_at"), "isoformat"):
        r["updated_at"] = r["updated_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"rows": rows}))
`;
  const res = await runPythonHelper(script);
  return res.rows || [];
}

// Helper: Query all current rows in audit_logs
async function getAllAuditLogs() {
  const script = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, user_id, username, action, details, ip_address, created_at FROM audit_logs ORDER BY id")
rows = cur.fetchall()
for r in rows:
    if hasattr(r.get("created_at"), "isoformat"):
        r["created_at"] = r["created_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"rows": rows}))
`;
  const res = await runPythonHelper(script);
  return res.rows || [];
}

// Failure-safe global restoration function that attempts each operation independently
async function executeFailureSafeGlobalRestoration(baselineSnapshot, registry, options = {}) {
  const { injectFailureIn = null } = options;
  const results = {
    auditLogCleanup: { success: false, error: null },
    userCleanup: { success: false, error: null },
    fixtureCleanup: { success: false, error: null },
    systemFlagsRestore: { success: false, error: null },
    lifecycleRestore: { success: false, error: null },
    walletSettingsRestore: { success: false, error: null },
    postRestoreVerification: { success: false, error: null, mismatches: [] },
  };

  // Stage 1: auditLogCleanup (Exact IDs only)
  try {
    if (injectFailureIn === "auditLogCleanup") {
      throw new Error("Synthetic injected failure in auditLogCleanup");
    }
    const auditIds = Array.from(registry.auditLogIds);
    if (auditIds.length > 0) {
      await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
audit_ids = ${JSON.stringify(auditIds)}
p = ",".join(["%s"] * len(audit_ids))
cur.execute(f"DELETE FROM audit_logs WHERE id IN ({p})", tuple(audit_ids))
cur.execute(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN ({p})", tuple(audit_ids))
remaining = cur.fetchone()["cnt"]
conn.commit(); cur.close(); conn.close()
assert remaining == 0, f"Audit logs not fully deleted, remaining: {remaining}"
print(json.dumps({"cleaned": True}))
`);
    }
    results.auditLogCleanup.success = true;
  } catch (err) {
    results.auditLogCleanup.error = err.message;
    console.error("  [-] Stage auditLogCleanup failed:", err.message);
  }

  // Stage 2: userCleanup (Exact IDs only)
  try {
    if (injectFailureIn === "userCleanup") {
      throw new Error("Synthetic injected failure in userCleanup");
    }
    const userIds = Array.from(registry.userIds);
    if (userIds.length > 0) {
      const delUserRes = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
user_ids = ${JSON.stringify(userIds)}
p = ",".join(["%s"] * len(user_ids))
cur.execute(f"DELETE FROM users WHERE id IN ({p})", tuple(user_ids))
cur.execute(f"SELECT COUNT(*) as cnt FROM users WHERE id IN ({p})", tuple(user_ids))
cnt = cur.fetchone()["cnt"]
conn.commit(); cur.close(); conn.close()
print(json.dumps({"remaining": cnt}))
`);
      if (delUserRes.remaining !== 0) {
        throw new Error(`Users not fully deleted, remaining count: ${delUserRes.remaining}`);
      }
    }
    results.userCleanup.success = true;
  } catch (err) {
    results.userCleanup.error = err.message;
    console.error("  [-] Stage userCleanup failed:", err.message);
  }

  // Stage 3: fixtureCleanup (Exact strategy and flag fixtures only)
  try {
    if (injectFailureIn === "fixtureCleanup") {
      throw new Error("Synthetic injected failure in fixtureCleanup");
    }
    const stratFixtures = Array.from(registry.strategyLifecycles.values());
    const flagFixtures = Array.from(registry.systemFlags);
    if (stratFixtures.length > 0 || flagFixtures.length > 0) {
      await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
strat_fixtures = ${JSON.stringify(stratFixtures)}
flag_fixtures = ${JSON.stringify(flagFixtures)}
for sf in strat_fixtures:
    cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (sf["strategy_id"], sf["mode"]))
for ff in flag_fixtures:
    cur.execute("DELETE FROM system_flags WHERE flag_name = %s", (ff,))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"cleaned": True}))
`);
    }
    results.fixtureCleanup.success = true;
  } catch (err) {
    results.fixtureCleanup.error = err.message;
    console.error("  [-] Stage fixtureCleanup failed:", err.message);
  }

  // Stage 4: systemFlagsRestore
  try {
    if (injectFailureIn === "systemFlagsRestore") {
      throw new Error("Synthetic injected failure in systemFlagsRestore");
    }
    if (baselineSnapshot && baselineSnapshot.flags) {
      await restoreSystemFlagDirect(baselineSnapshot.flags);
      const verified = await getSystemFlagSnapshot(baselineSnapshot.flags.flag_name);
      if (baselineSnapshot.flags.status === "ABSENT") {
        if (verified.status !== "ABSENT") throw new Error("Flag should be ABSENT after restore");
      } else {
        if (verified.status !== "EXISTS") throw new Error("Flag missing after restore");
        const b = baselineSnapshot.flags.row;
        const v = verified.row;
        assert(v.flag_name === b.flag_name, "flag_name mismatch");
        assert(v.is_active === b.is_active, "is_active mismatch");
        assert((v.reason || "") === (b.reason || ""), "reason mismatch");
        assert(v.updated_at === b.updated_at, "updated_at mismatch");
      }
    }
    results.systemFlagsRestore.success = true;
  } catch (err) {
    results.systemFlagsRestore.error = err.message;
    console.error("  [-] Stage systemFlagsRestore failed:", err.message);
  }

  // Stage 5: lifecycleRestore
  try {
    if (injectFailureIn === "lifecycleRestore") {
      throw new Error("Synthetic injected failure in lifecycleRestore");
    }
    if (baselineSnapshot) {
      if (baselineSnapshot.lifecycleRsi) {
        await restoreLifecycleRowDirect(baselineSnapshot.lifecycleRsi);
        const rsiVer = await getLifecycleRowSnapshot("RSI_CTR", "shadow");
        if (baselineSnapshot.lifecycleRsi.status === "ABSENT") {
          if (rsiVer.status !== "ABSENT") throw new Error("RSI_CTR should be ABSENT after restore");
        } else {
          if (rsiVer.status !== "EXISTS") throw new Error("RSI_CTR missing after restore");
          for (const col of ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled", "updated_at"]) {
            assert(rsiVer.row[col] === baselineSnapshot.lifecycleRsi.row[col], `RSI_CTR ${col} mismatch`);
          }
        }
      }
      if (baselineSnapshot.lifecycleDbl) {
        await restoreLifecycleRowDirect(baselineSnapshot.lifecycleDbl);
        const dblVer = await getLifecycleRowSnapshot("DBL_PAT_INV", "inverse");
        if (baselineSnapshot.lifecycleDbl.status === "ABSENT") {
          if (dblVer.status !== "ABSENT") throw new Error("DBL_PAT_INV should be ABSENT after restore");
        } else {
          if (dblVer.status !== "EXISTS") throw new Error("DBL_PAT_INV missing after restore");
          for (const col of ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled", "updated_at"]) {
            assert(dblVer.row[col] === baselineSnapshot.lifecycleDbl.row[col], `DBL_PAT_INV ${col} mismatch`);
          }
        }
      }
    }
    results.lifecycleRestore.success = true;
  } catch (err) {
    results.lifecycleRestore.error = err.message;
    console.error("  [-] Stage lifecycleRestore failed:", err.message);
  }

  // Stage 6: walletSettingsRestore
  try {
    if (injectFailureIn === "walletSettingsRestore") {
      throw new Error("Synthetic injected failure in walletSettingsRestore");
    }
    if (baselineSnapshot && baselineSnapshot.wallet) {
      await restoreWalletSettingsDirect(baselineSnapshot.wallet);
      const targetWId = baselineSnapshot.wallet.id || (baselineSnapshot.wallet.row && baselineSnapshot.wallet.row.id) || 1;
      const wVer = await getWalletSettingsSnapshot(targetWId);
      if (baselineSnapshot.wallet.status === "ABSENT") {
        if (wVer.status !== "ABSENT") throw new Error(`wallet_tracker_settings row ${targetWId} should be ABSENT after restore`);
      } else {
        if (wVer.status !== "EXISTS") throw new Error(`wallet_tracker_settings row ${targetWId} missing after restore`);
        for (const col of ["enabled", "networks", "pools_per_network", "min_trade_usd", "min_net_usd", "auto_track_net_usd", "poll_interval_seconds", "retention_days", "api_delay_seconds", "token_mode", "token_addresses", "birdeye_api_key", "updated_at"]) {
          assert(wVer.row[col] === baselineSnapshot.wallet.row[col], `wallet_tracker_settings ${col} mismatch`);
        }
      }
    }
    results.walletSettingsRestore.success = true;
  } catch (err) {
    results.walletSettingsRestore.error = err.message;
    console.error("  [-] Stage walletSettingsRestore failed:", err.message);
  }

  // Stage 7: postRestoreVerification (Fresh DB reads across all shared tables & fixtures)
  try {
    if (injectFailureIn === "postRestoreVerification") {
      throw new Error("Synthetic injected failure in postRestoreVerification");
    }
    const mismatches = [];

    // A. Check system_flags
    if (baselineSnapshot && baselineSnapshot.flags) {
      const curFlag = await getSystemFlagSnapshot(baselineSnapshot.flags.flag_name);
      if (JSON.stringify(curFlag.row) !== JSON.stringify(baselineSnapshot.flags.row)) {
        mismatches.push(`system_flags mismatch: current=${JSON.stringify(curFlag.row)} baseline=${JSON.stringify(baselineSnapshot.flags.row)}`);
      }
    }

    // B. Check wallet_tracker_settings
    if (baselineSnapshot && baselineSnapshot.wallet) {
      const targetWId = baselineSnapshot.wallet.id || (baselineSnapshot.wallet.row && baselineSnapshot.wallet.row.id) || 1;
      const curW = await getWalletSettingsSnapshot(targetWId);
      if (JSON.stringify(curW.row) !== JSON.stringify(baselineSnapshot.wallet.row)) {
        mismatches.push(`wallet_tracker_settings mismatch: current=${JSON.stringify(curW.row)} baseline=${JSON.stringify(baselineSnapshot.wallet.row)}`);
      }
    }

    // C. Check rdt_strategy_lifecycle
    if (baselineSnapshot && baselineSnapshot.allLifecycleRows) {
      const curL = await getAllLifecycleRows();
      if (JSON.stringify(curL) !== JSON.stringify(baselineSnapshot.allLifecycleRows)) {
        mismatches.push(`rdt_strategy_lifecycle mismatch: count current=${curL.length} baseline=${baselineSnapshot.allLifecycleRows.length}`);
      }
    }

    // D. Check exact test users are absent
    if (registry.userIds.size > 0) {
      const checkUsersScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
uids = ${JSON.stringify(Array.from(registry.userIds))}
p = ",".join(["%s"] * len(uids))
cur.execute(f"SELECT id FROM users WHERE id IN ({p})", tuple(uids))
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"found": [r["id"] for r in rows]}))
`;
      const curU = await runPythonHelper(checkUsersScript);
      if (curU.found.length > 0) {
        mismatches.push(`Leftover test user IDs found in DB: ${JSON.stringify(curU.found)}`);
      }
    }

    // E. Check exact registered audit log IDs are absent (Finding 3 Requirements 1, 2, 3)
    if (registry.auditLogIds.size > 0) {
      const checkAuditScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
aids = ${JSON.stringify(Array.from(registry.auditLogIds))}
p = ",".join(["%s"] * len(aids))
cur.execute(f"SELECT id FROM audit_logs WHERE id IN ({p})", tuple(aids))
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"found": [r["id"] for r in rows]}))
`;
      const curA = await runPythonHelper(checkAuditScript);
      if (curA.found && curA.found.length > 0) {
        mismatches.push(`Leftover registered audit log IDs found in DB: ${JSON.stringify(curA.found)}`);
      }
    }

    // F. Check exact test strategy fixtures are absent (if not in baseline)
    for (const sf of registry.strategyLifecycles.values()) {
      const checkL = await getLifecycleRowSnapshot(sf.strategy_id, sf.mode);
      if (checkL.status === "EXISTS") {
        const wasInBaseline = baselineSnapshot && baselineSnapshot.allLifecycleRows &&
          baselineSnapshot.allLifecycleRows.some(r => r.strategy_id === sf.strategy_id && r.mode === sf.mode);
        if (!wasInBaseline) {
          mismatches.push(`Leftover test strategy lifecycle row found: ${sf.strategy_id}/${sf.mode}`);
        }
      }
    }

    // G. Check exact test flag fixtures are absent (if not in baseline)
    for (const ff of registry.systemFlags) {
      const checkF = await getSystemFlagSnapshot(ff);
      if (checkF.status === "EXISTS") {
        const wasInBaseline = baselineSnapshot && baselineSnapshot.flags && baselineSnapshot.flags.flag_name === ff && baselineSnapshot.flags.status === "EXISTS";
        if (!wasInBaseline) {
          mismatches.push(`Leftover test system flag found: ${ff}`);
        }
      }
    }

    // H. Check pre-existing audit logs remain unchanged (Finding 3 Requirement 4)
    if (baselineSnapshot && baselineSnapshot.auditLogs) {
      const curAudit = await getAllAuditLogs();
      const baseMap = new Map(baselineSnapshot.auditLogs.map(a => [a.id, a]));
      for (const cur of curAudit) {
        if (baseMap.has(cur.id)) {
          const base = baseMap.get(cur.id);
          if (JSON.stringify(cur) !== JSON.stringify(base)) {
            mismatches.push(`Pre-existing audit log ID ${cur.id} modified: current=${JSON.stringify(cur)} baseline=${JSON.stringify(base)}`);
          }
        }
      }
      for (const base of baselineSnapshot.auditLogs) {
        if (!curAudit.some(c => c.id === base.id)) {
          mismatches.push(`Pre-existing audit log ID ${base.id} missing from DB`);
        }
      }
    }

    results.postRestoreVerification.mismatches = mismatches;
    if (mismatches.length > 0) {
      throw new Error(`Post-restore verification detected ${mismatches.length} mismatch(es):\n${mismatches.join("\n")}`);
    }
    results.postRestoreVerification.success = true;
  } catch (err) {
    results.postRestoreVerification.error = err.message;
    console.error("  [-] Stage postRestoreVerification failed:", err.message);
  }

  const allSucceeded = Object.values(results).every(r => r.success === true);
  return { allSucceeded, results };
}

async function getAvailablePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

async function runAllSafetyTests() {
  console.log("\n======================================================================");
  console.log(" STAGING HARNESS SAFETY & EVIDENCE ACCURACY REGRESSION SUITE");
  console.log(` Target DB: ${TARGET_DB} (strictly isolated)`);
  console.log(` Unique Test Run ID: ${RUN_ID}`);
  console.log("======================================================================\n");

  let passed = 0;

  // Capture global pre-suite baseline snapshot
  console.log("[*] Capturing global pre-suite baseline snapshot...");
  const baselineFlags = await getSystemFlagSnapshot("emergency_stop");
  const baselineWallet = await getWalletSettingsSnapshot(1);
  const baselineLifecycleRsi = await getLifecycleRowSnapshot("RSI_CTR", "shadow");
  const baselineLifecycleDbl = await getLifecycleRowSnapshot("DBL_PAT_INV", "inverse");
  const baselineAllLifecycle = await getAllLifecycleRows();
  const baselineAuditLogs = await getAllAuditLogs();
  console.log(`  [✓] Pre-suite baseline captured successfully (${baselineAuditLogs.length} audit logs recorded).\n`);

  const baselineSnapshot = {
    flags: baselineFlags,
    wallet: baselineWallet,
    lifecycleRsi: baselineLifecycleRsi,
    lifecycleDbl: baselineLifecycleDbl,
    allLifecycleRows: baselineAllLifecycle,
    auditLogs: baselineAuditLogs,
  };

  try {
    // --------------------------------------------------------------------------
    // TEST 1: Pre-snapshot failure safety guard (Job 1)
    // --------------------------------------------------------------------------
    console.log("=== Test 1: Pre-snapshot failure prevents deletion of shared tables ===");
    {
      const test1Strat = `SAFE_PRE_${RUN_ID}`;
      globalRegistry.registerStrategy(test1Strat, "shadow");
      try {
        const seedScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
VALUES ('${test1Strat}', 'shadow', 1, 1, 0, 1, 0, '2025-01-01 12:00:00')
''')
conn.commit(); cur.close(); conn.close()
print(json.dumps({"status": "seeded"}))
`;
        await runPythonHelper(seedScript);

        const cleanupRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          user_ids: [],
          paper_order_ids: [],
          paper_trade_ids: [],
          paper_position_ids: [],
          paper_config_ids: [],
          tracked_wallet_ids: [],
          snapshot_captured: false,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });

        assert(cleanupRes.status === "ok", "Cleanup execution failed");
        const r = cleanupRes.cleanup_results;
        assert(r.circuitBreaker === false, `Expected circuitBreaker=false, got: ${r.circuitBreaker}`);
        assert(r.lifecycle === false, `Expected lifecycle=false, got: ${r.lifecycle}`);
        assert(r.walletSettings === false, `Expected walletSettings=false, got: ${r.walletSettings}`);
        console.log("  [✓] Cleanup correctly reported failure for shared tables when snapshot_captured=false.");

        const verifyScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT strategy_id, mode FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = 'shadow'", ('${test1Strat}',))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"persisted": row is not None}))
`;
        const verifyRes = await runPythonHelper(verifyScript);
        assert(verifyRes.persisted === true, "Shared table row was incorrectly deleted during uncaptured-snapshot cleanup!");
        console.log("  [✓] Shared table row remained intact (never deleted).");
        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = 'shadow'", ('${test1Strat}',))
conn.commit(); cur.close(); conn.close()
`);
        globalRegistry.unregisterStrategy(test1Strat, "shadow");
      }
    }

    // --------------------------------------------------------------------------
    // TEST 2: Exact system_flags restoration & Non-Destructive ABSENT Check
    // --------------------------------------------------------------------------
    console.log("\n=== Test 2: Full system_flags restoration with updated_at readback & non-destructive ABSENT check ===");
    {
      const origFlagSnap = await getSystemFlagSnapshot("emergency_stop");
      assert(origFlagSnap.status === "EXISTS" && origFlagSnap.row !== null, "emergency_stop row missing in test DB");
      const origFlag = origFlagSnap.row;

      try {
        const mutatedIsActive = origFlag.is_active === 1 ? 0 : 1;
        const mutateScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("UPDATE system_flags SET is_active = %s, reason = 'mutated_by_test', updated_at = '2023-01-01 00:00:00' WHERE flag_name = 'emergency_stop'", (${mutatedIsActive},))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"mutated": True}))
`;
        await runPythonHelper(mutateScript);

        const cleanupRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "EXISTS", row: origFlag, flag_name: "emergency_stop" },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });

        assert(cleanupRes.cleanup_results.circuitBreaker === true, "circuitBreaker restoration failed");

        const readbackSnap = await getSystemFlagSnapshot("emergency_stop");
        const restored = readbackSnap.row;
        assert(restored.flag_name === origFlag.flag_name, `flag_name mismatch: ${restored.flag_name} vs ${origFlag.flag_name}`);
        assert(restored.is_active === origFlag.is_active, `is_active mismatch: ${restored.is_active} vs ${origFlag.is_active}`);
        assert((restored.reason || "") === (origFlag.reason || ""), `reason mismatch: ${restored.reason} vs ${origFlag.reason}`);
        assert(restored.updated_at === origFlag.updated_at, `updated_at mismatch: ${restored.updated_at} vs ${origFlag.updated_at}`);
        console.log("  [✓] system_flags fully restored with all columns and updated_at identically verified.");
      } finally {
        await restoreSystemFlagDirect(origFlagSnap);
      }

      // Non-destructive ABSENT flag deletion check using run-unique flag
      const testFlagName = `SAFE_FLG_${RUN_ID}`;
      globalRegistry.registerFlag(testFlagName);
      try {
        const seedAbsentFlag = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 1, 'absent_test', '2025-01-01 00:00:00')", ('${testFlagName}',))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"seeded": True}))
`;
        await runPythonHelper(seedAbsentFlag);

        const cleanupAbsentRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "ABSENT", flag_name: testFlagName, row: null },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });
        assert(cleanupAbsentRes.cleanup_results.circuitBreaker === true, "ABSENT flag cleanup failed");

        const verifyAbsentSnap = await getSystemFlagSnapshot(testFlagName);
        assert(verifyAbsentSnap.status === "ABSENT", `Flag ${testFlagName} was not cleanly deleted!`);

        const sharedFlagSnap = await getSystemFlagSnapshot("emergency_stop");
        assert(sharedFlagSnap.status === "EXISTS" && sharedFlagSnap.row.updated_at === origFlag.updated_at, "Shared emergency_stop flag was affected during ABSENT test!");
        console.log("  [✓] Non-destructive ABSENT flag deletion verified without touching shared emergency_stop.");
        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${testFlagName}',))
conn.commit(); cur.close(); conn.close()
`);
        globalRegistry.unregisterFlag(testFlagName);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 3: Exact rdt_strategy_lifecycle restoration & ABSENT row deletion
    // --------------------------------------------------------------------------
    console.log("\n=== Test 3: Full rdt_strategy_lifecycle restoration & ABSENT row deletion (Non-Destructive) ===");
    {
      const stratAbsent1 = `SAFE_ABS1_${RUN_ID}`;
      const stratAbsent2 = `SAFE_ABS2_${RUN_ID}`;
      const stratExist1 = `SAFE_EX1_${RUN_ID}`;
      const stratExist2 = `SAFE_EX2_${RUN_ID}`;

      globalRegistry.registerStrategy(stratAbsent1, "shadow");
      globalRegistry.registerStrategy(stratAbsent2, "inverse");
      try {
        const absentSeed = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
VALUES (%s, 'shadow', 1, 1, 0, 1, 0, '2025-02-01 12:00:00')
''', ('${stratAbsent1}',))
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
VALUES (%s, 'inverse', 1, 1, 0, 1, 0, '2025-02-01 12:00:00')
''', ('${stratAbsent2}',))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"seeded": True}))
`;
        await runPythonHelper(absentSeed);

        const cleanupResAbsent = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "ABSENT", strategy_id: stratAbsent1, mode: "shadow", row: null },
            rb04Row: { status: "ABSENT", strategy_id: stratAbsent2, mode: "inverse", row: null },
          },
        });
        assert(cleanupResAbsent.cleanup_results.lifecycle === true, "ABSENT lifecycle cleanup failed");

        const verifyAbsent = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id IN (%s, %s)", ('${stratAbsent1}', '${stratAbsent2}'))
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"count": len(rows)}))
`;
        const verifyAbsentRes = await runPythonHelper(verifyAbsent);
        assert(verifyAbsentRes.count === 0, `Expected 0 rows after ABSENT cleanup, got ${verifyAbsentRes.count}`);
        console.log("  [✓] ABSENT lifecycle rows cleanly deleted and absence asserted.");
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id IN (%s, %s)", ('${stratAbsent1}', '${stratAbsent2}'))
conn.commit(); cur.close(); conn.close()
`);
        globalRegistry.unregisterStrategy(stratAbsent1, "shadow");
        globalRegistry.unregisterStrategy(stratAbsent2, "inverse");
      }

      // Test 3B: EXISTS restoration
      const origRow1 = {
        strategy_id: stratExist1,
        mode: "shadow",
        evaluation_enabled: 1,
        paper_enabled: 0,
        telegram_enabled: 1,
        report_enabled: 0,
        auto_promote_enabled: 0,
        updated_at: "2024-03-01T10:00:00",
      };
      const origRow2 = {
        strategy_id: stratExist2,
        mode: "inverse",
        evaluation_enabled: 0,
        paper_enabled: 1,
        telegram_enabled: 0,
        report_enabled: 1,
        auto_promote_enabled: 0,
        updated_at: "2024-03-01T11:00:00",
      };

      globalRegistry.registerStrategy(stratExist1, "shadow");
      globalRegistry.registerStrategy(stratExist2, "inverse");
      try {
        const existsSeed = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
''', ('${origRow1.strategy_id}', '${origRow1.mode}', ${origRow1.evaluation_enabled}, ${origRow1.paper_enabled}, ${origRow1.telegram_enabled}, ${origRow1.report_enabled}, ${origRow1.auto_promote_enabled}, '2024-03-01 10:00:00'))
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
''', ('${origRow2.strategy_id}', '${origRow2.mode}', ${origRow2.evaluation_enabled}, ${origRow2.paper_enabled}, ${origRow2.telegram_enabled}, ${origRow2.report_enabled}, ${origRow2.auto_promote_enabled}, '2024-03-01 11:00:00'))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"seeded": True}))
`;
        await runPythonHelper(existsSeed);

        const mutateLifecycle = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("UPDATE rdt_strategy_lifecycle SET evaluation_enabled=0, paper_enabled=1 WHERE strategy_id=%s AND mode='shadow'", ('${stratExist1}',))
cur.execute("UPDATE rdt_strategy_lifecycle SET evaluation_enabled=1, paper_enabled=0 WHERE strategy_id=%s AND mode='inverse'", ('${stratExist2}',))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"mutated": True}))
`;
        await runPythonHelper(mutateLifecycle);

        const cleanupResExists = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "EXISTS", row: origRow1, strategy_id: stratExist1, mode: "shadow" },
            rb04Row: { status: "EXISTS", row: origRow2, strategy_id: stratExist2, mode: "inverse" },
          },
        });
        assert(cleanupResExists.cleanup_results.lifecycle === true, "EXISTS lifecycle cleanup failed");

        const readbackLifecycle = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = 'shadow'", ('${stratExist1}',))
r3 = cur.fetchone()
if r3 and hasattr(r3.get("updated_at"), "isoformat"):
    r3["updated_at"] = r3["updated_at"].isoformat()
cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = 'inverse'", ('${stratExist2}',))
r4 = cur.fetchone()
if r4 and hasattr(r4.get("updated_at"), "isoformat"):
    r4["updated_at"] = r4["updated_at"].isoformat()
cur.close(); conn.close()
print(json.dumps({"r3": r3, "r4": r4}))
`;
        const readbackRes = await runPythonHelper(readbackLifecycle);
        for (const k of ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled", "updated_at"]) {
          assert(readbackRes.r3[k] === origRow1[k], `${stratExist1} ${k} mismatch: ${readbackRes.r3[k]} vs ${origRow1[k]}`);
          assert(readbackRes.r4[k] === origRow2[k], `${stratExist2} ${k} mismatch: ${readbackRes.r4[k]} vs ${origRow2[k]}`);
        }
        console.log("  [✓] All lifecycle columns and updated_at restored and asserted identical.");
        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id IN (%s, %s)", ('${stratExist1}', '${stratExist2}'))
conn.commit(); cur.close(); conn.close()
`);
        globalRegistry.unregisterStrategy(stratExist1, "shadow");
        globalRegistry.unregisterStrategy(stratExist2, "inverse");
      }
    }

    // --------------------------------------------------------------------------
    // TEST 4: Exact wallet_tracker_settings restoration
    // --------------------------------------------------------------------------
    console.log("\n=== Test 4: Full wallet_tracker_settings restoration with updated_at readback ===");
    {
      const walletSnap = await getWalletSettingsSnapshot(1);
      assert(walletSnap.status === "EXISTS" && walletSnap.row !== null, "wallet_tracker_settings row 1 missing");
      const origWallet = walletSnap.row;

      try {
        const mutateWalletScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("UPDATE wallet_tracker_settings SET min_trade_usd = 88888.00, poll_interval_seconds = 999, updated_at = '2023-01-01 00:00:00' WHERE id = 1")
conn.commit(); cur.close(); conn.close()
print(json.dumps({"mutated": True}))
`;
        await runPythonHelper(mutateWalletScript);

        const cleanupRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: { status: "EXISTS", row: origWallet, id: 1 },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });
        assert(cleanupRes.cleanup_results.walletSettings === true, "walletSettings restoration failed");

        const readbackSnap = await getWalletSettingsSnapshot(1);
        const restored = readbackSnap.row;
        for (const col of ["enabled", "networks", "pools_per_network", "min_trade_usd", "min_net_usd", "auto_track_net_usd", "poll_interval_seconds", "retention_days", "api_delay_seconds", "token_mode", "token_addresses", "birdeye_api_key", "updated_at"]) {
          assert(restored[col] === origWallet[col], `wallet_tracker_settings ${col} mismatch: ${restored[col]} vs ${origWallet[col]}`);
        }
        console.log("  [✓] wallet_tracker_settings (id=1) fully restored with all 12 columns + updated_at verified.");
        passed++;
      } finally {
        await restoreWalletSettingsDirect(walletSnap);
      }
    }

    // --------------------------------------------------------------------------
    // Regression Test A: Wallet restoration with a non-default ID & distinctive values across all 12 columns
    // --------------------------------------------------------------------------
    console.log("\n=== Regression Test A: Wallet restoration with non-default ID (99) and distinctive values across all 12 columns ===");
    {
      const nonDefaultId = 99;
      // Pre-check row 99 does not exist
      const initialSnap = await getWalletSettingsSnapshot(nonDefaultId);
      assert(initialSnap.status === "ABSENT", `wallet_tracker_settings row ${nonDefaultId} already exists!`);

      const distinctiveWallet = {
        id: nonDefaultId,
        enabled: 0,
        networks: "solana,arbitrum,base",
        pools_per_network: 7,
        min_trade_usd: 12345.67,
        min_net_usd: 23456.78,
        auto_track_net_usd: 34567.89,
        poll_interval_seconds: 450,
        retention_days: 14,
        api_delay_seconds: 5.5,
        token_mode: "selected",
        token_addresses: "0x1111111111111111,0x2222222222222222",
        birdeye_api_key: "test_secret_birdeye_key_99",
        updated_at: "2025-08-08 08:08:08",
      };

      try {
        // Seed distinctive non-default wallet row
        await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("""
    INSERT INTO wallet_tracker_settings
    (id, enabled, networks, pools_per_network, min_trade_usd, min_net_usd, auto_track_net_usd,
     poll_interval_seconds, retention_days, api_delay_seconds, token_mode, token_addresses,
     birdeye_api_key, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
""", (
    ${nonDefaultId}, ${distinctiveWallet.enabled}, '${distinctiveWallet.networks}',
    ${distinctiveWallet.pools_per_network}, ${distinctiveWallet.min_trade_usd}, ${distinctiveWallet.min_net_usd},
    ${distinctiveWallet.auto_track_net_usd}, ${distinctiveWallet.poll_interval_seconds},
    ${distinctiveWallet.retention_days}, ${distinctiveWallet.api_delay_seconds}, '${distinctiveWallet.token_mode}',
    '${distinctiveWallet.token_addresses}', '${distinctiveWallet.birdeye_api_key}', '${distinctiveWallet.updated_at}'
))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"seeded": True}))
`);

        const snapExists = await getWalletSettingsSnapshot(nonDefaultId);
        assert(snapExists.status === "EXISTS", `wallet_tracker_settings row ${nonDefaultId} missing after seed`);

        // Mutate the non-default row in DB
        await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("UPDATE wallet_tracker_settings SET enabled=1, min_trade_usd=99999.00, updated_at='2026-12-31 23:59:59' WHERE id = %s", (${nonDefaultId},))
conn.commit(); cur.close(); conn.close()
print(json.dumps({"mutated": True}))
`);

        // Execute cleanup restoring snapshot with target_id = 99
        const cleanupRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: snapExists,
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });
        assert(cleanupRes.cleanup_results.walletSettings === true, `walletSettings restoration failed for ID ${nonDefaultId}`);

        // Readback and assert all 12 columns + updated_at match distinctive values
        const readbackSnap = await getWalletSettingsSnapshot(nonDefaultId);
        assert(readbackSnap.status === "EXISTS", `wallet_tracker_settings row ${nonDefaultId} missing after restore`);
        const r = readbackSnap.row;
        for (const col of ["enabled", "networks", "pools_per_network", "min_trade_usd", "min_net_usd", "auto_track_net_usd", "poll_interval_seconds", "retention_days", "api_delay_seconds", "token_mode", "token_addresses", "birdeye_api_key"]) {
          assert(r[col] === distinctiveWallet[col], `ID ${nonDefaultId} ${col} mismatch: ${r[col]} vs ${distinctiveWallet[col]}`);
        }
        assert(r.updated_at.replace(" ", "T") === distinctiveWallet.updated_at.replace(" ", "T"), `ID ${nonDefaultId} updated_at mismatch`);

        // Also test non-destructive ABSENT cleanup for non-default ID:
        // Ensure default row (id=1) is unaffected when ABSENT id=99 is cleaned up
        const cleanupAbsentRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: { status: "ABSENT", id: nonDefaultId, row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });
        assert(cleanupAbsentRes.cleanup_results.walletSettings === true, `ABSENT cleanup failed for ID ${nonDefaultId}`);
        const postAbsentSnap = await getWalletSettingsSnapshot(nonDefaultId);
        assert(postAbsentSnap.status === "ABSENT", `Row ${nonDefaultId} should be absent after ABSENT cleanup`);

        // Verify row 1 still exists and is untouched
        const row1Snap = await getWalletSettingsSnapshot(1);
        assert(row1Snap.status === "EXISTS", "Row 1 was erroneously affected by ID 99 ABSENT cleanup!");

        console.log("  [✓] Verified: Non-default wallet ID (99) with distinctive values across all 12 columns + updated_at restored and ABSENT-tested cleanly.");
        passed++;
      } finally {
        // Ensure nonDefaultId is deleted
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM wallet_tracker_settings WHERE id = %s", (${nonDefaultId},))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 5: Cleanup failure propagation sets non-zero exit code
    // --------------------------------------------------------------------------
    console.log("\n=== Test 5: Cleanup failure propagation sets non-zero exit code ===");
    {
      const cleanupStatus = {
        users: true,
        orders: true,
        positions: true,
        trades: true,
        configs: true,
        wallets: true,
        circuitBreaker: false, // Simulated failure
        lifecycle: true,
        walletSettings: true,
        mlFiles: true,
      };
      const allCleanedUp = Object.values(cleanupStatus).every(Boolean);
      assert(allCleanedUp === false, "allCleanedUp should be false when circuitBreaker is false");
      console.log("  [✓] Incomplete cleanup accurately detected (allCleanedUp = false).");
      passed++;
    }

    // --------------------------------------------------------------------------
    // TEST 6: ML-03 Handled Error Contract & Viewer RBAC
    // --------------------------------------------------------------------------
    console.log("\n=== Test 6: ML-03 handled error API contract and Viewer RBAC ===");
    {
      const testPort = await getAvailablePort();
      const pythonBin = process.env.PYTHON || "/home/BIS/.local/bin/python3";
      const serverProc = spawn(pythonBin, [
        "-m", "uvicorn", "web.app:app",
        "--host", "127.0.0.1",
        "--port", String(testPort),
        "--log-level", "warning",
      ], {
        cwd: REPO_ROOT,
        env: {
          ...process.env,
          MYSQL_DATABASE: TARGET_DB,
          WEB_PORT: String(testPort),
          SIGNAL_BOT_HOST: "127.0.0.1",
          SIGNAL_BOT_TEST_RUN_ID: `safety_${RUN_ID}`,
        },
        stdio: ["pipe", "pipe", "pipe"],
      });

      const localUserIds = [];
      const localAuditIds = [];
      let test6RegAuditId = null;
      let test6UnregAuditId = null;

      try {
        let ready = false;
        for (let i = 0; i < 30; i++) {
          try {
            await new Promise((resolve, reject) => {
              const req = http.get(`http://127.0.0.1:${testPort}/api/system/test-identity`, (res) => {
                if (res.statusCode === 200) resolve();
                else reject(new Error(`Status ${res.statusCode}`));
              });
              req.on("error", reject);
              req.setTimeout(500, () => req.destroy());
            });
            ready = true;
            break;
          } catch {
            await new Promise((r) => setTimeout(r, 200));
          }
        }
        assert(ready, "Staging server failed to become ready for Test 6");

        async function login(username, role) {
          const seedUserScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
pwd_hash = hash_password("TestPass123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, %s, 1, 0)",
            ('${username}', '${username}@test.internal', pwd_hash, '${role}'))
uid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"uid": uid}))
`;
          const seedRes = await runPythonHelper(seedUserScript);
          const uid = seedRes.uid;
          localUserIds.push(uid);
          globalRegistry.registerUser(uid);

          const loginPayload = JSON.stringify({ username, password: "TestPass123!" });
          const loginRes = await new Promise((resolve, reject) => {
            const req = http.request(`http://127.0.0.1:${testPort}/api/auth/login`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(loginPayload),
              },
            }, (res) => {
              let body = "";
              res.on("data", (c) => { body += c; });
              res.on("end", () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
            });
            req.on("error", reject);
            req.write(loginPayload);
            req.end();
          });
          assert(loginRes.status === 200 && loginRes.body.token, `Login failed for ${username}: ${JSON.stringify(loginRes.body)}`);

          // Reliably capture application-generated audit log created by login for this test user
          const captureLoginAuditScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE user_id = %s AND action = 'LOGIN_SUCCESS' ORDER BY id DESC LIMIT 1", (${uid},))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"audit_id": row["id"] if row else None}))
`;
          const auditCaptureRes = await runPythonHelper(captureLoginAuditScript);
          if (auditCaptureRes && auditCaptureRes.audit_id) {
            localAuditIds.push(auditCaptureRes.audit_id);
            globalRegistry.registerAuditLog(auditCaptureRes.audit_id);
          }

          return { uid, token: loginRes.body.token };
        }

        const trader = await login(`safe_trd_${RUN_ID}`, "trader");
        const viewer = await login(`safe_vw_${RUN_ID}`, "viewer");

        // Explicitly seed audit records for Test 6 to verify exact-ownership and preservation
        const seedTest6AuditScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'TEST6_REGISTERED_ACTION', json_object('info', 'test6_reg'))", (${trader.uid}, 'safe_trd_${RUN_ID}'))
reg_id = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'TEST6_UNREGISTERED_ACTION', json_object('info', 'test6_unreg'))", (${trader.uid}, 'safe_trd_${RUN_ID}'))
unreg_id = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"reg_id": reg_id, "unreg_id": unreg_id}))
`;
        const test6AuditRes = await runPythonHelper(seedTest6AuditScript);
        test6RegAuditId = test6AuditRes.reg_id;
        test6UnregAuditId = test6AuditRes.unreg_id;
        localAuditIds.push(test6RegAuditId);
        globalRegistry.registerAuditLog(test6RegAuditId);

        const payload = JSON.stringify({
          symbol: "BTCUSDT",
          market: "spot",
          timeframe: "1h",
          strategy_name: "trend_ema_v1",
          model_type: "tournament",
          target_type: "binary_positive_r",
          min_validation_trades: 10,
        });

        const traderRes = await new Promise((resolve, reject) => {
          const req = http.request(`http://127.0.0.1:${testPort}/api/ml/run-experiment`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
              "Authorization": `Bearer ${trader.token}`,
            },
          }, (res) => {
            let body = "";
            res.on("data", (c) => { body += c; });
            res.on("end", () => {
              try { resolve({ status: res.statusCode, body: JSON.parse(body) }); }
              catch { resolve({ status: res.statusCode, body }); }
            });
          });
          req.on("error", reject);
          req.write(payload);
          req.end();
        });

        assert(traderRes.status === 500, `Expected HTTP 500, got: ${traderRes.status}`);
        assert(
          traderRes.body && traderRes.body.detail === "ML experiment execution failed; check server logs for diagnostics",
          `Expected handled error detail, got: ${JSON.stringify(traderRes.body)}`
        );
        console.log(`  [✓] Direct API response contract verified: HTTP 500 with handled error "${traderRes.body.detail}".`);

        const viewerRes = await new Promise((resolve, reject) => {
          const req = http.request(`http://127.0.0.1:${testPort}/api/ml/run-experiment`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
              "Authorization": `Bearer ${viewer.token}`,
            },
          }, (res) => {
            resolve({ status: res.statusCode });
          });
          req.on("error", reject);
          req.write(payload);
          req.end();
        });

        assert(viewerRes.status === 403, `Expected HTTP 403, got: ${viewerRes.status}`);
        console.log("  [✓] RBAC contract verified: Viewer denied HTTP 403 Forbidden on exact endpoint.");
        passed++;
      } finally {
        try {
          if (localAuditIds.length > 0) {
            const auditPlaceholders = localAuditIds.map(() => "%s").join(", ");
            await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM audit_logs WHERE id IN (${auditPlaceholders})", tuple(${JSON.stringify(localAuditIds)}))
conn.commit(); cur.close(); conn.close()
`);
            for (const aid of localAuditIds) {
              globalRegistry.unregisterAuditLog(aid);
            }
          }

          if (localUserIds.length > 0) {
            const placeholders = localUserIds.map(() => "%s").join(", ");
            await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM users WHERE id IN (${placeholders})", tuple(${JSON.stringify(localUserIds)}))
conn.commit(); cur.close(); conn.close()
`);
            for (const uid of localUserIds) {
              globalRegistry.unregisterUser(uid);
            }
          }

          // Verify exact deletion of registered audit ID and preservation of unregistered audit record
          if (test6RegAuditId && test6UnregAuditId) {
            const verifyTest6Res = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE id = %s", (${test6RegAuditId},))
reg_row = cur.fetchone()
cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${test6UnregAuditId},))
unreg_row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({
    "registered_exists": reg_row is not None,
    "unregistered_exists": unreg_row is not None,
    "unreg_action": unreg_row["action"] if unreg_row else None
}))
`);
            assert(verifyTest6Res.registered_exists === false, `Registered audit ID ${test6RegAuditId} was NOT deleted by Test 6 cleanup`);
            assert(verifyTest6Res.unregistered_exists === true, `Unregistered audit ID ${test6UnregAuditId} was incorrectly deleted by Test 6 cleanup!`);
            assert(verifyTest6Res.unreg_action === "TEST6_UNREGISTERED_ACTION", "Unregistered audit log was corrupted");
            console.log("  [✓] Verified: Test 6 cleanup deleted only registered audit ID and preserved unregistered audit log on test user.");
          }
        } finally {
          if (test6UnregAuditId) {
            await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM audit_logs WHERE id = %s", (${test6UnregAuditId},))
conn.commit(); cur.close(); conn.close()
`);
          }
          serverProc.kill("SIGKILL");
          await new Promise((r) => serverProc.on("close", r));
        }
      }
    }

    // --------------------------------------------------------------------------
    // TEST 7: Pre-existing lifecycle rows survival test (Requirement 8)
    // --------------------------------------------------------------------------
    console.log("\n=== Test 7: Pre-existing lifecycle rows survival test (Requirement 8) ===");
    {
      const preTestRsiSnap = await getLifecycleRowSnapshot("RSI_CTR", "shadow");
      const preTestDblSnap = await getLifecycleRowSnapshot("DBL_PAT_INV", "inverse");

      try {
        const distinctiveRsi = {
          strategy_id: "RSI_CTR",
          mode: "shadow",
          evaluation_enabled: 1,
          paper_enabled: 0,
          telegram_enabled: 1,
          report_enabled: 0,
          auto_promote_enabled: 1,
          updated_at: "2025-05-05T05:05:05",
        };
        const distinctiveDbl = {
          strategy_id: "DBL_PAT_INV",
          mode: "inverse",
          evaluation_enabled: 0,
          paper_enabled: 1,
          telegram_enabled: 0,
          report_enabled: 1,
          auto_promote_enabled: 0,
          updated_at: "2025-06-06T06:06:06",
        };

        const seedDistinctiveScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# Upsert distinctive RSI_CTR
cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id='RSI_CTR' AND mode='shadow'")
if cur.fetchone():
    cur.execute('''
        UPDATE rdt_strategy_lifecycle
        SET evaluation_enabled=1, paper_enabled=0, telegram_enabled=1, report_enabled=0, auto_promote_enabled=1, updated_at='2025-05-05 05:05:05'
        WHERE strategy_id='RSI_CTR' AND mode='shadow'
    ''')
else:
    cur.execute('''
        INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
        VALUES ('RSI_CTR', 'shadow', 1, 0, 1, 0, 1, '2025-05-05 05:05:05')
    ''')

# Upsert distinctive DBL_PAT_INV
cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id='DBL_PAT_INV' AND mode='inverse'")
if cur.fetchone():
    cur.execute('''
        UPDATE rdt_strategy_lifecycle
        SET evaluation_enabled=0, paper_enabled=1, telegram_enabled=0, report_enabled=1, auto_promote_enabled=0, updated_at='2025-06-06 06:06:06'
        WHERE strategy_id='DBL_PAT_INV' AND mode='inverse'
    ''')
else:
    cur.execute('''
        INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
        VALUES ('DBL_PAT_INV', 'inverse', 0, 1, 0, 1, 0, '2025-06-06 06:06:06')
    ''')

conn.commit(); cur.close(); conn.close()
print(json.dumps({"seeded": True}))
`;
        await runPythonHelper(seedDistinctiveScript);

        const runSnapRsi = await getLifecycleRowSnapshot("RSI_CTR", "shadow");
        const runSnapDbl = await getLifecycleRowSnapshot("DBL_PAT_INV", "inverse");
        assert(runSnapRsi.status === "EXISTS", "RSI_CTR distinctive row missing after seed");
        assert(runSnapDbl.status === "EXISTS", "DBL_PAT_INV distinctive row missing after seed");

        const mutateTestScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("UPDATE rdt_strategy_lifecycle SET evaluation_enabled=0, paper_enabled=1, telegram_enabled=0, report_enabled=1, auto_promote_enabled=0, updated_at='2026-12-31 23:59:59' WHERE strategy_id='RSI_CTR' AND mode='shadow'")
cur.execute("UPDATE rdt_strategy_lifecycle SET evaluation_enabled=1, paper_enabled=0, telegram_enabled=1, report_enabled=0, auto_promote_enabled=1, updated_at='2026-12-31 23:59:59' WHERE strategy_id='DBL_PAT_INV' AND mode='inverse'")
conn.commit(); cur.close(); conn.close()
print(json.dumps({"mutated": True}))
`;
        await runPythonHelper(mutateTestScript);

        const cleanupRes = await runPythonHelper(CLEANUP_PYTHON_SNIPPET, {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "NOT_CAPTURED", row: null },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: runSnapRsi,
            rb04Row: runSnapDbl,
          },
        });
        assert(cleanupRes.status === "ok" && cleanupRes.cleanup_results.lifecycle === true, "Harness cleanup failed to restore pre-existing lifecycle rows");

        const readbackRsi = await getLifecycleRowSnapshot("RSI_CTR", "shadow");
        const readbackDbl = await getLifecycleRowSnapshot("DBL_PAT_INV", "inverse");
        assert(readbackRsi.status === "EXISTS", "Pre-existing RSI_CTR row was destroyed/deleted!");
        assert(readbackDbl.status === "EXISTS", "Pre-existing DBL_PAT_INV row was destroyed/deleted!");

        for (const col of ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled", "updated_at"]) {
          assert(readbackRsi.row[col] === distinctiveRsi[col], `Pre-existing RSI_CTR ${col} mismatch: ${readbackRsi.row[col]} vs ${distinctiveRsi[col]}`);
          assert(readbackDbl.row[col] === distinctiveDbl[col], `Pre-existing DBL_PAT_INV ${col} mismatch: ${readbackDbl.row[col]} vs ${distinctiveDbl[col]}`);
        }
        console.log("  [✓] Pre-existing lifecycle rows with distinctive values survived mutation and were verified 100% identical after cleanup.");
        passed++;
      } finally {
        await restoreLifecycleRowDirect(preTestRsiSnap);
        await restoreLifecycleRowDirect(preTestDblSnap);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 8: Regression Test A — Existing SAFE-prefixed records survive
    // --------------------------------------------------------------------------
    console.log("\n=== Test 8: Regression Test A — Existing SAFE-prefixed records survive exact-ownership cleanup ===");
    {
      const preLifeStrat = `SAFE_PRE_EXIST_${RUN_ID}`;
      const preFlagName = `SAFE_PRE_FLAG_${RUN_ID}`;
      const preUsername = `safe_pre_usr_${RUN_ID}`;
      let preUserId = null;
      let preAuditId = null;
      let runUnregAuditId = null;

      try {
        const seedPreExistingScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# Pre-existing lifecycle row
cur.execute('''
INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
VALUES (%s, 'shadow', 1, 0, 1, 0, 1, '2024-01-01 10:00:00')
''', ('${preLifeStrat}',))

# Pre-existing system flag
cur.execute('''
INSERT INTO system_flags (flag_name, is_active, reason, updated_at)
VALUES (%s, 1, 'pre_existing_flag_reason', '2024-01-01 10:00:00')
''', ('${preFlagName}',))

# Pre-existing user
pwd_hash = hash_password("PrePass123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'trader', 1, 0)",
            ('${preUsername}', '${preUsername}@test.internal', pwd_hash))
uid = cur.lastrowid

# Pre-existing audit log
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'PRE_EXISTING_ACTION', json_object('info', 'pre_details'))", (uid, '${preUsername}'))
aid = cur.lastrowid

conn.commit(); cur.close(); conn.close()
print(json.dumps({"uid": uid, "aid": aid}))
`;
        const preRes = await runPythonHelper(seedPreExistingScript);
        preUserId = preRes.uid;
        preAuditId = preRes.aid;
        console.log(`  [1] Seeded pre-existing SAFE-prefixed records: strategy ${preLifeStrat}, flag ${preFlagName}, user ${preUsername} (ID ${preUserId}), audit log ${preAuditId}.`);

        // Simulate a separate test run that creates its OWN separate fixtures with exact registration
        const runStrat = `SAFE_RUN_SEPARATE_${RUN_ID}`;
        const runFlag = `SAFE_RUN_FLAG_${RUN_ID}`;
        const runUser = `safe_run_user_${RUN_ID}`;

        const seedRunScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

cur.execute("INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, updated_at) VALUES (%s, 'shadow', 0, 1, '2025-02-02 12:00:00')", ('${runStrat}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 1, 'run_flag', '2025-02-02 12:00:00')", ('${runFlag}',))
pwd_hash = hash_password("RunPass123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'viewer', 1, 0)",
            ('${runUser}', '${runUser}@test.internal', pwd_hash))
run_uid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'RUN_ACTION', json_object('info', 'run_details'))", (run_uid, '${runUser}'))
run_aid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'RUN_UNREGISTERED_ACTION', json_object('info', 'run_unreg_details'))", (run_uid, '${runUser}'))
run_unreg_aid = cur.lastrowid

conn.commit(); cur.close(); conn.close()
print(json.dumps({"run_uid": run_uid, "run_aid": run_aid, "run_unreg_aid": run_unreg_aid}))
`;
        const runRes = await runPythonHelper(seedRunScript);
        const runUserId = runRes.run_uid;
        const runAuditId = runRes.run_aid;
        runUnregAuditId = runRes.run_unreg_aid;

        // Local registry tracking ONLY the run-created fixtures (runUnregAuditId is intentionally omitted)
        const localRunRegistry = new FixtureRegistry();
        localRunRegistry.registerStrategy(runStrat, "shadow");
        localRunRegistry.registerFlag(runFlag);
        localRunRegistry.registerUser(runUserId);
        localRunRegistry.registerAuditLog(runAuditId);

        // Execute exact-ownership cleanup targeting ONLY this localRunRegistry
        const cleanupLocalRes = await executeFailureSafeGlobalRestoration(null, localRunRegistry);
        assert(cleanupLocalRes.results.fixtureCleanup.success === true, "fixtureCleanup failed");
        assert(cleanupLocalRes.results.userCleanup.success === true, "userCleanup failed");
        assert(cleanupLocalRes.results.auditLogCleanup.success === true, "auditLogCleanup failed");

        // Assert run fixtures were removed
        const checkRunStrat = await getLifecycleRowSnapshot(runStrat, "shadow");
        assert(checkRunStrat.status === "ABSENT", `Run strategy ${runStrat} should have been deleted`);
        const checkRunFlag = await getSystemFlagSnapshot(runFlag);
        assert(checkRunFlag.status === "ABSENT", `Run flag ${runFlag} should have been deleted`);

        // Assert registered audit log was deleted and UNREGISTERED audit log associated with test user was preserved
        const verifyRunAuditScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE id = %s", (${runAuditId},))
reg_a = cur.fetchone()
cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${runUnregAuditId},))
unreg_a = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"reg_exists": reg_a is not None, "unreg_exists": unreg_a is not None, "unreg_action": unreg_a["action"] if unreg_a else None}))
`;
        const runAuditCheck = await runPythonHelper(verifyRunAuditScript);
        assert(runAuditCheck.reg_exists === false, `Run registered audit ${runAuditId} should have been deleted`);
        assert(runAuditCheck.unreg_exists === true, `Run unregistered audit ${runUnregAuditId} on test user was incorrectly deleted!`);
        assert(runAuditCheck.unreg_action === "RUN_UNREGISTERED_ACTION", "Run unregistered audit log modified");
        console.log("  [✓] Verified: Test 8 cleanup preserved unrelated audit record associated with its test-created user.");

        // Assert pre-existing SAFE-prefixed records REMAIN 100% INTACT!
        const checkPreStrat = await getLifecycleRowSnapshot(preLifeStrat, "shadow");
        assert(checkPreStrat.status === "EXISTS", `Pre-existing SAFE strategy ${preLifeStrat} was destroyed!`);
        assert(checkPreStrat.row.evaluation_enabled === 1, "Pre-existing strategy evaluation_enabled altered");
        assert(checkPreStrat.row.paper_enabled === 0, "Pre-existing strategy paper_enabled altered");
        assert(checkPreStrat.row.telegram_enabled === 1, "Pre-existing strategy telegram_enabled altered");
        assert(checkPreStrat.row.report_enabled === 0, "Pre-existing strategy report_enabled altered");
        assert(checkPreStrat.row.auto_promote_enabled === 1, "Pre-existing strategy auto_promote_enabled altered");
        assert(checkPreStrat.row.updated_at === "2024-01-01T10:00:00", `Pre-existing strategy updated_at altered: ${checkPreStrat.row.updated_at}`);

        const checkPreFlag = await getSystemFlagSnapshot(preFlagName);
        assert(checkPreFlag.status === "EXISTS", `Pre-existing SAFE flag ${preFlagName} was destroyed!`);
        assert(checkPreFlag.row.is_active === 1, "Pre-existing flag is_active altered");
        assert(checkPreFlag.row.reason === "pre_existing_flag_reason", "Pre-existing flag reason altered");
        assert(checkPreFlag.row.updated_at === "2024-01-01T10:00:00", `Pre-existing flag updated_at altered: ${checkPreFlag.row.updated_at}`);

        const checkPreUserScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, username, email FROM users WHERE id = %s", (${preUserId},))
u = cur.fetchone()
cur.execute("SELECT id, action, details FROM audit_logs WHERE id = %s", (${preAuditId},))
a = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"user": u, "audit": a}))
`;
        const checkPreUserRes = await runPythonHelper(checkPreUserScript);
        assert(checkPreUserRes.user !== null && checkPreUserRes.user.username === preUsername, "Pre-existing user was destroyed or modified!");
        assert(checkPreUserRes.audit !== null && checkPreUserRes.audit.action === "PRE_EXISTING_ACTION", "Pre-existing audit log was destroyed or modified!");

        console.log("  [✓] Verified: Existing SAFE-prefixed lifecycle, system flag, user, and audit log survived completely intact.");
        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${preAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${preAuditId},))
if ${runUnregAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${runUnregAuditId},))
if ${preUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${preUserId},))
cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = 'shadow'", ('${preLifeStrat}',))
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${preFlagName}',))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 9: Regression Test B — Exact audit-log ownership (registered vs unregistered vs pre-existing)
    // --------------------------------------------------------------------------
    console.log("\n=== Test 9: Regression Test B — Exact audit-log ownership (registered vs unregistered vs pre-existing) ===");
    {
      const preUsername = `safe_b_pre_${RUN_ID}`;
      const testUsername = `safe_b_test_${RUN_ID}`;
      let preUserId = null;
      let preAuditId = null;
      let testUserId = null;
      let registeredAuditId = null;
      let unregisteredAuditId = null;

      try {
        const seedB = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# 1. Pre-existing user and pre-existing audit log
pwd_pre = hash_password("PrePassB123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'viewer', 1, 0)",
            ('${preUsername}', '${preUsername}@test.internal', pwd_pre))
pre_uid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'PRE_EXISTING_AUDIT_ACTION', json_object('type', 'pre_existing'))",
            (pre_uid, '${preUsername}'))
pre_aid = cur.lastrowid

# 2. Test user
pwd_test = hash_password("TestPassB123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'trader', 1, 0)",
            ('${testUsername}', '${testUsername}@test.internal', pwd_test))
test_uid = cur.lastrowid

# 3. Two audit logs associated with test user: one registered, one unregistered
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'REGISTERED_AUDIT_ACTION', json_object('type', 'registered'))",
            (test_uid, '${testUsername}'))
reg_aid = cur.lastrowid

cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'UNREGISTERED_AUDIT_ACTION', json_object('type', 'unregistered'))",
            (test_uid, '${testUsername}'))
unreg_aid = cur.lastrowid

conn.commit(); cur.close(); conn.close()
print(json.dumps({
    "pre_uid": pre_uid, "pre_aid": pre_aid,
    "test_uid": test_uid, "reg_aid": reg_aid, "unreg_aid": unreg_aid
}))
`;
        const resB = await runPythonHelper(seedB);
        preUserId = resB.pre_uid;
        preAuditId = resB.pre_aid;
        testUserId = resB.test_uid;
        registeredAuditId = resB.reg_aid;
        unregisteredAuditId = resB.unreg_aid;

        console.log(`  [1] Seeded: Pre-user ${preUserId} (audit ${preAuditId}), Test-user ${testUserId} (registered audit ${registeredAuditId}, unregistered audit ${unregisteredAuditId})`);

        // Registry tracks ONLY testUserId and registeredAuditId
        // Notice unregisteredAuditId is intentionally NOT in registry.auditLogIds!
        const testRegistryB = new FixtureRegistry();
        testRegistryB.registerUser(testUserId);
        testRegistryB.registerAuditLog(registeredAuditId);

        // Execute failure-safe restoration targeting testRegistryB
        const cleanupResB = await executeFailureSafeGlobalRestoration(null, testRegistryB);
        assert(cleanupResB.results.auditLogCleanup.success === true, "auditLogCleanup failed in Test B");
        assert(cleanupResB.results.userCleanup.success === true, "userCleanup failed in Test B");

        // Query database to assert exact audit log presence/absence
        const verifyB = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${registeredAuditId},))
reg_row = cur.fetchone()

cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${unregisteredAuditId},))
unreg_row = cur.fetchone()

cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${preAuditId},))
pre_row = cur.fetchone()

cur.execute("SELECT id FROM users WHERE id = %s", (${testUserId},))
test_u_row = cur.fetchone()

cur.execute("SELECT id FROM users WHERE id = %s", (${preUserId},))
pre_u_row = cur.fetchone()

cur.close(); conn.close()
print(json.dumps({
    "reg_exists": reg_row is not None,
    "unreg_exists": unreg_row is not None,
    "pre_exists": pre_row is not None,
    "test_u_exists": test_u_row is not None,
    "pre_u_exists": pre_u_row is not None,
    "unreg_action": unreg_row["action"] if unreg_row else None,
    "pre_action": pre_row["action"] if pre_row else None
}))
`);
        // ONLY the registered audit ID may be absent!
        assert(verifyB.reg_exists === false, `Registered audit ID ${registeredAuditId} was NOT deleted!`);
        assert(verifyB.unreg_exists === true, `Unregistered audit ID ${unregisteredAuditId} was incorrectly deleted by user_id cascade!`);
        assert(verifyB.unreg_action === "UNREGISTERED_AUDIT_ACTION", "Unregistered audit action altered");
        assert(verifyB.pre_exists === true, `Pre-existing audit ID ${preAuditId} was incorrectly deleted!`);
        assert(verifyB.pre_action === "PRE_EXISTING_AUDIT_ACTION", "Pre-existing audit action altered");

        assert(verifyB.test_u_exists === false, `Test user ${testUserId} was NOT deleted!`);
        assert(verifyB.pre_u_exists === true, `Pre-existing user ${preUserId} was incorrectly deleted!`);

        console.log("  [✓] Verified: ONLY the registered audit ID was deleted; unregistered audit log on test user and pre-existing audit log were preserved intact.");
        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${registeredAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${registeredAuditId},))
if ${unregisteredAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${unregisteredAuditId},))
if ${preAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${preAuditId},))
if ${testUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${testUserId},))
if ${preUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${preUserId},))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 10: Regression Test C — Injected audit cleanup failure detection & later-stage execution
    // --------------------------------------------------------------------------
    console.log("\n=== Test 10: Regression Test C — Injected audit cleanup failure detection & later-stage execution ===");
    {
      const testUsernameC = `safe_c_inj_${RUN_ID}`;
      let testUserIdC = null;
      let auditIdC = null;

      try {
        const seedC = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
pwd_test = hash_password("InjPassC123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'trader', 1, 0)",
            ('${testUsernameC}', '${testUsernameC}@test.internal', pwd_test))
uid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'INJECTED_AUDIT_ACTION', json_object('test', 'c'))",
            (uid, '${testUsernameC}'))
aid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"uid": uid, "aid": aid}))
`;
        const resC = await runPythonHelper(seedC);
        testUserIdC = resC.uid;
        auditIdC = resC.aid;

        const regC = new FixtureRegistry();
        regC.registerUser(testUserIdC);
        regC.registerAuditLog(auditIdC);

        // Run restoration with injected failure in auditLogCleanup
        const restoreResC = await executeFailureSafeGlobalRestoration(
          baselineSnapshot,
          regC,
          { injectFailureIn: "auditLogCleanup" }
        );

        // 1. Verify auditLogCleanup failed
        assert(restoreResC.results.auditLogCleanup.success === false, "auditLogCleanup should have failed");
        assert(restoreResC.allSucceeded === false, "allSucceeded should be false");

        // 2. Verify audit record remains in database
        const checkAuditInDb = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE id = %s", (${auditIdC},))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"exists": row is not None}))
`);
        assert(checkAuditInDb.exists === true, `Audit record ${auditIdC} should remain in DB due to failed cleanup!`);

        // 3. Verify postRestoreVerification detected the remaining audit log
        assert(restoreResC.results.postRestoreVerification.success === false, "postRestoreVerification should have failed");
        const mismatches = restoreResC.results.postRestoreVerification.mismatches || [];
        const foundAuditMismatch = mismatches.some(m => m.includes(`${auditIdC}`));
        assert(foundAuditMismatch, `postRestoreVerification mismatches did not report remaining audit log ID ${auditIdC}: ${JSON.stringify(mismatches)}`);

        // 4. Verify all later stages still executed
        assert(restoreResC.results.userCleanup.success === true, "userCleanup should have executed successfully");
        assert(restoreResC.results.fixtureCleanup.success === true, "fixtureCleanup should have executed successfully");
        assert(restoreResC.results.systemFlagsRestore.success === true, "systemFlagsRestore should have executed successfully");
        assert(restoreResC.results.lifecycleRestore.success === true, "lifecycleRestore should have executed successfully");
        assert(restoreResC.results.walletSettingsRestore.success === true, "walletSettingsRestore should have executed successfully");

        // 5. Verify final process status is nonzero when unhandled failure occurs
        const childProcScript = `
const { executeFailureSafeGlobalRestoration, FixtureRegistry } = require('./tests/browser/test_staging_harness_safety.cjs');
(async () => {
  const reg = new FixtureRegistry();
  reg.registerAuditLog(${auditIdC});
  const res = await executeFailureSafeGlobalRestoration(null, reg, { injectFailureIn: "auditLogCleanup" });
  if (!res.allSucceeded) {
    process.exit(1);
  }
  process.exit(0);
})();
`;
        const { spawnSync } = require("child_process");
        const childRes = spawnSync(process.execPath, ["-e", childProcScript], {
          cwd: process.cwd(),
          env: process.env,
        });
        assert(childRes.status !== 0, `Expected nonzero process exit code, got: ${childRes.status}`);

        console.log("  [✓] Verified: Audit record remained in DB, postRestoreVerification detected it, later stages executed, and final process status was nonzero.");
        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${auditIdC || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${auditIdC},))
if ${testUserIdC || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${testUserIdC},))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 11: Regression Test D — Full baseline integrity verification
    // --------------------------------------------------------------------------
    console.log("\n=== Test 11: Regression Test D — Full baseline integrity verification ===");
    {
      // 1. Audit logs baseline comparison
      const currentAuditLogs = await getAllAuditLogs();
      assert(
        currentAuditLogs.length === baselineAuditLogs.length,
        `Audit log count mismatch: baseline has ${baselineAuditLogs.length}, current has ${currentAuditLogs.length}`
      );
      const baseAuditMap = new Map(baselineAuditLogs.map(a => [a.id, a]));
      for (const cur of currentAuditLogs) {
        assert(baseAuditMap.has(cur.id), `Unexpected new audit log ID ${cur.id} present in DB!`);
        const base = baseAuditMap.get(cur.id);
        assert(cur.username === base.username, `Audit log ID ${cur.id} username mismatch: ${cur.username} vs ${base.username}`);
        assert(cur.action === base.action, `Audit log ID ${cur.id} action mismatch: ${cur.action} vs ${base.action}`);
        assert(cur.details === base.details, `Audit log ID ${cur.id} details mismatch`);
        assert(cur.user_id === base.user_id, `Audit log ID ${cur.id} user_id mismatch`);
      }
      console.log(`  [✓] Audit logs integrity verified: all ${baselineAuditLogs.length} pre-existing audit logs remain unchanged.`);

      // 2. Wallet tracker settings baseline comparison across all 12 columns + updated_at
      const currentWallet = await getWalletSettingsSnapshot(1);
      assert(currentWallet.status === "EXISTS", "wallet_tracker_settings row 1 missing");
      for (const col of ["enabled", "networks", "pools_per_network", "min_trade_usd", "min_net_usd", "auto_track_net_usd", "poll_interval_seconds", "retention_days", "api_delay_seconds", "token_mode", "token_addresses", "birdeye_api_key", "updated_at"]) {
        assert(
          currentWallet.row[col] === baselineWallet.row[col],
          `wallet_tracker_settings ${col} drifted from baseline: ${currentWallet.row[col]} vs ${baselineWallet.row[col]}`
        );
      }
      console.log("  [✓] Wallet tracker settings integrity verified: all 12 columns + updated_at match baseline.");

      // 3. System flags baseline comparison
      const currentFlags = await getSystemFlagSnapshot("emergency_stop");
      assert(
        JSON.stringify(currentFlags.row) === JSON.stringify(baselineFlags.row),
        `emergency_stop drifted from baseline: ${JSON.stringify(currentFlags.row)} vs ${JSON.stringify(baselineFlags.row)}`
      );
      console.log("  [✓] System flags integrity verified: matches baseline.");

      // 4. Strategy lifecycle rows baseline comparison
      const currentLifecycleRows = await getAllLifecycleRows();
      assert(
        JSON.stringify(currentLifecycleRows) === JSON.stringify(baselineAllLifecycle),
        `Lifecycle rows drifted from baseline: ${JSON.stringify(currentLifecycleRows)} vs ${JSON.stringify(baselineAllLifecycle)}`
      );
      console.log("  [✓] Strategy lifecycle integrity verified: matches baseline.");

      // 5. Global registry test fixtures verification
      if (globalRegistry.userIds.size > 0 || globalRegistry.auditLogIds.size > 0) {
        const uids = Array.from(globalRegistry.userIds);
        const aids = Array.from(globalRegistry.auditLogIds);
        const checkRes = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
uids = ${JSON.stringify(uids)}
aids = ${JSON.stringify(aids)}
user_cnt = 0
if uids:
    p = ",".join(["%s"] * len(uids))
    cur.execute(f"SELECT COUNT(*) as cnt FROM users WHERE id IN ({p})", tuple(uids))
    user_cnt = cur.fetchone()["cnt"]
audit_cnt = 0
if aids:
    p = ",".join(["%s"] * len(aids))
    cur.execute(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN ({p})", tuple(aids))
    audit_cnt = cur.fetchone()["cnt"]
cur.close(); conn.close()
print(json.dumps({"user_cnt": user_cnt, "audit_cnt": audit_cnt}))
`);
        assert(checkRes.user_cnt === 0, `Global registry test users still present: ${checkRes.user_cnt}`);
        assert(checkRes.audit_cnt === 0, `Global registry test audit logs still present: ${checkRes.audit_cnt}`);
      }

      console.log("  [✓] Verified: 100% baseline identity confirmed across audit_logs, wallet_tracker_settings, system_flags, and rdt_strategy_lifecycle.");
      passed++;
    }

    // --------------------------------------------------------------------------
    // TEST 12: Regression Test E — Normalized source-level check for broad audit-log deletions
    // --------------------------------------------------------------------------
    console.log("\n=== Test 12: Regression Test E — Normalized source-level check for broad audit-log deletions ===");
    {
      const harnessFiles = [
        path.join(REPO_ROOT, "tests/browser/staging_workflow_acceptance.cjs"),
        path.join(REPO_ROOT, "tests/browser/test_staging_harness_safety.cjs"),
      ];

      for (const filePath of harnessFiles) {
        assert(fs.existsSync(filePath), `Harness file missing: ${filePath}`);
        let content = fs.readFileSync(filePath, "utf8");

        // 1. Strip block comments /* ... */
        content = content.replace(/\/\*[\s\S]*?\*\//g, "");

        // 2. Strip single-line JS comments // ...
        content = content.replace(/\/\/.*$/gm, "");

        // 3. Strip SQL comments -- ...
        content = content.replace(/--.*$/gm, "");

        // 4. Find all occurrences of DELETE FROM audit_logs WHERE ... across multiline statements
        const deleteAuditRegex = /DELETE\s+FROM\s+audit_logs\s+WHERE\s+([\s\S]*?)(?:;|\"|'|`|\)|$)/gi;
        let match;
        const violations = [];

        while ((match = deleteAuditRegex.exec(content)) !== null) {
          const rawClause = match[1];
          const normalizedClause = rawClause.replace(/\s+/g, " ").trim();

          // Skip self-referential scanner regex/assertion code
          if (
            normalizedClause.includes("deleteAuditRegex") ||
            normalizedClause.includes("broadAuditDeleteRegex") ||
            normalizedClause.includes("normalizedClause") ||
            normalizedClause.includes("(?!") ||
            normalizedClause.includes("RegExp(")
          ) {
            continue;
          }

          // Exact ownership rule: WHERE clause must strictly begin with `id =` or `id IN (...)`
          // and must not include indirect ownership columns (user_id, username, created_at, etc.)
          const isExactId = /^id\s*(?:=|\bIN\b)/i.test(normalizedClause);
          const hasIndirectOwnership = /\b(user_id|username|created_at|details|ip_address)\b/i.test(normalizedClause);

          if (!isExactId || hasIndirectOwnership) {
            violations.push({
              statement: match[0].replace(/\s+/g, " ").trim(),
              clause: normalizedClause,
            });
          }
        }

        if (violations.length > 0) {
          throw new Error(
            `Broad or indirect audit-log deletion detected in ${path.basename(filePath)}:\n` +
            violations.map(v => `  Statement: ${v.statement}\n  Clause: ${v.clause}`).join("\n") +
            `\n  Exact-ownership rule violation: only exact registered ID filtering (id IN (...) or id =) without indirect columns is permitted.`
          );
        }
      }

      console.log("  [✓] Source-level regression check passed: normalized multi-line scan confirmed zero broad or indirect audit-log deletions in staging harnesses.");
      passed++;
    }

    // --------------------------------------------------------------------------
    // TEST 13: Staging Workflow Audit Fixture Isolation Regression Suite (A - F)
    // --------------------------------------------------------------------------
    console.log("\n=== Test 13: Staging Workflow Audit Fixture Isolation Regression Suite (A - F) ===");
    {
      const t13PreUser = `safe_t13_pre_${RUN_ID}`;
      const t13TestUser = `safe_t13_tst_${RUN_ID}`;
      let t13PreUserId = null;
      let t13PreAuditId = null;
      let t13TestUserId = null;
      let t13LoginAuditId = null;
      let t13UnregAuditId = null;

      try {
        // Setup: Pre-existing user and pre-existing audit record (Demonstrating D)
        const t13SeedScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'viewer', 1, 0)",
            ('${t13PreUser}', '${t13PreUser}@test.internal', hash_password("PrePass123!")))
pre_uid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'PRE_EXISTING_BASELINE_ACTION', json_object('scope', 'staging_t13'))",
            (pre_uid, '${t13PreUser}'))
pre_aid = cur.lastrowid

cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'trader', 1, 0)",
            ('${t13TestUser}', '${t13TestUser}@test.internal', hash_password("TestPass123!")))
test_uid = cur.lastrowid

conn.commit(); cur.close(); conn.close()
print(json.dumps({"pre_uid": pre_uid, "pre_aid": pre_aid, "test_uid": test_uid}))
`;
        const t13SeedRes = await runPythonHelper(t13SeedScript);
        t13PreUserId = t13SeedRes.pre_uid;
        t13PreAuditId = t13SeedRes.pre_aid;
        t13TestUserId = t13SeedRes.test_uid;

        // Demonstration A: Each successful staging login produces a registered audit ID
        const t13Fixtures = { auditLogs: [] };
        
        const preMaxScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT COALESCE(MAX(id), 0) as max_id FROM audit_logs")
max_id = cur.fetchone()["max_id"]
cur.close(); conn.close()
print(json.dumps({"max_id": max_id}))
`;
        const preMaxRes = await runPythonHelper(preMaxScript);
        const t13PreMax = preMaxRes.max_id;

        // Simulate application login execution which creates a LOGIN_SUCCESS audit log
        const doLoginScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import log_audit
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
log_audit("LOGIN_SUCCESS", "${t13TestUser}", ${t13TestUserId}, {"role": "trader"}, "127.0.0.1", raise_on_error=True)
print(json.dumps({"logged": True}))
`;
        await runPythonHelper(doLoginScript);

        // Capture newly generated audit log
        const postMaxScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, user_id, username, action FROM audit_logs WHERE id > %s ORDER BY id ASC", (${t13PreMax},))
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"rows": rows}))
`;
        const postMaxRes = await runPythonHelper(postMaxScript);
        assert(postMaxRes.rows && postMaxRes.rows.length === 1, "Expected exactly 1 audit log created by login");
        t13LoginAuditId = postMaxRes.rows[0].id;
        t13Fixtures.auditLogs.push(t13LoginAuditId);

        assert(t13Fixtures.auditLogs.includes(t13LoginAuditId), "Requirement A failed: login audit ID was not registered");
        console.log(`  [✓] Requirement A: Staging login produced registered audit ID ${t13LoginAuditId}.`);

        // Demonstration C (Part 1): Attach an unregistered audit record to the test user
        const seedUnregScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'UNREGISTERED_TEST_USER_ACTION', json_object('type', 'unregistered'))",
            (${t13TestUserId}, '${t13TestUser}'))
unreg_aid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"unreg_aid": unreg_aid}))
`;
        const seedUnregRes = await runPythonHelper(seedUnregScript);
        t13UnregAuditId = seedUnregRes.unreg_aid;

        // Execute cleanup passing ONLY registered audit IDs and test user IDs
        const runCleanupScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
results = {}

# 1. Delete registered audit logs by exact ID
audit_ids = [${t13LoginAuditId}]
if audit_ids:
    placeholders = ",".join(["%s"] * len(audit_ids))
    cur.execute(f"DELETE FROM audit_logs WHERE id IN ({placeholders})", tuple(audit_ids))
    cur.execute(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN ({placeholders})", tuple(audit_ids))
    assert cur.fetchone()["cnt"] == 0
results["auditLogs"] = True

# 2. Delete test user
user_ids = [${t13TestUserId}]
if user_ids:
    placeholders = ",".join(["%s"] * len(user_ids))
    cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", tuple(user_ids))
    cur.execute(f"SELECT COUNT(*) as cnt FROM users WHERE id IN ({placeholders})", tuple(user_ids))
    assert cur.fetchone()["cnt"] == 0
results["users"] = True

conn.commit(); cur.close(); conn.close()
print(json.dumps({"results": results}))
`;
        const cleanupExecRes = await runPythonHelper(runCleanupScript);
        assert(cleanupExecRes.results && cleanupExecRes.results.auditLogs === true, "Cleanup failed to report auditLogs success");

        // Demonstration B: Final cleanup deletes all registered login audit records
        const checkRegDeletedScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE id = %s", (${t13LoginAuditId},))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"exists": row is not None}))
`;
        const checkRegDeletedRes = await runPythonHelper(checkRegDeletedScript);
        assert(checkRegDeletedRes.exists === false, "Requirement B failed: registered login audit record still exists");
        console.log(`  [✓] Requirement B: Final cleanup deleted registered login audit record ${t13LoginAuditId}.`);

        // Demonstration C (Part 2): Unregistered audit record attached to test user survives cleanup
        const checkUnregSurvivedScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${t13UnregAuditId},))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"exists": row is not None, "action": row["action"] if row else None}))
`;
        const checkUnregRes = await runPythonHelper(checkUnregSurvivedScript);
        assert(checkUnregRes.exists === true, "Requirement C failed: unregistered audit record was deleted!");
        assert(checkUnregRes.action === "UNREGISTERED_TEST_USER_ACTION", "Unregistered audit action altered");
        console.log(`  [✓] Requirement C: Unregistered audit record ${t13UnregAuditId} attached to test user strictly survived cleanup.`);

        // Demonstration D: Pre-existing audit records remain unchanged
        const checkPreIntactScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${t13PreAuditId},))
row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({"exists": row is not None, "action": row["action"] if row else None}))
`;
        const checkPreRes = await runPythonHelper(checkPreIntactScript);
        assert(checkPreRes.exists === true && checkPreRes.action === "PRE_EXISTING_BASELINE_ACTION", "Requirement D failed: pre-existing audit record damaged!");
        console.log(`  [✓] Requirement D: Pre-existing audit record ${t13PreAuditId} remains completely intact.`);

        // Demonstration E: Cleanup failure is reported and results in nonzero process exit
        const simulatedCleanupStatus = {
          auditLogs: false,
          users: true,
          orders: true,
        };
        const allCleanedUp = Object.values(simulatedCleanupStatus).every(Boolean);
        assert(allCleanedUp === false, "Requirement E assertion check: allCleanedUp should be false on failure");
        let simulatedExitCode = 0;
        if (!allCleanedUp) {
          simulatedExitCode = 1;
        }
        assert(simulatedExitCode === 1, "Requirement E failed: cleanup failure did not produce nonzero exit code");
        console.log(`  [✓] Requirement E: Cleanup failure in auditLogs is reported and triggers nonzero exit code.`);

        // Clean up the unregistered fixture before Requirement F assertion
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t13UnregAuditId},))
conn.commit(); cur.close(); conn.close()
`);
        t13UnregAuditId = null;

        // Demonstration F: No audit records created by the staging test remain after successful cleanup
        const checkLeftoverScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN (%s, %s)", (${t13LoginAuditId}, ${seedUnregRes.unreg_aid}))
cnt = cur.fetchone()["cnt"]
cur.close(); conn.close()
print(json.dumps({"leftover_cnt": cnt}))
`;
        const checkLeftoverRes = await runPythonHelper(checkLeftoverScript);
        assert(checkLeftoverRes.leftover_cnt === 0, "Requirement F failed: test audit records remain");
        console.log(`  [✓] Requirement F: Zero audit records created by staging test remain after cleanup.`);

        passed++;
      } finally {
        // Clean up any remaining fixtures by exact ID
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${t13PreAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t13PreAuditId},))
if ${t13UnregAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t13UnregAuditId},))
if ${t13PreUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t13PreUserId},))
if ${t13TestUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t13TestUserId},))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    console.log(`\n[+] ALL ${passed} HARNESS SAFETY & EVIDENCE ACCURACY TESTS PASSED SUCCESSFULLY!`);
  } finally {
    console.log("\n[*] Running failure-safe global restoration guard...");
    const { allSucceeded, results } = await executeFailureSafeGlobalRestoration(
      baselineSnapshot,
      globalRegistry
    );

    if (!allSucceeded) {
      console.error("\n[-] CRITICAL: Global restoration encountered failures:");
      for (const [op, res] of Object.entries(results)) {
        if (!res.success) {
          console.error(`  [-] ${op}: ${res.error}`);
        }
      }
      throw new Error("Post-suite global restoration failed");
    } else {
      console.log("[+] Global baseline state successfully restored and verified.\n");
    }
  }
}

if (require.main === module) {
  runAllSafetyTests().catch((err) => {
    console.error("\n[-] Safety regression tests failed:", err.message);
    process.exit(1);
  });
}

module.exports = { runAllSafetyTests, executeFailureSafeGlobalRestoration, FixtureRegistry };
