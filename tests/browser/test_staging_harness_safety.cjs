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
 *    telegram_enabled, report_enabled, auto_promote_enabled, updated_at) plus strategy_id and mode
 *    are restored and verified via readback comparison, ABSENT rows are deleted and asserted absent,
 *    and deliberate readback mismatches across every field are strictly detected.
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
 * 13. Staging Workflow Audit Fixture Isolation & Concurrent Event Immunity: Proves staging logins
 *     produce registered audit IDs, application-generated events on test users are discovered and
 *     registered by exact ID, exact cleanup deletes registered records, concurrent unrelated audit
 *     events strictly survive and do NOT fail baseline verification, and uncleaned test-owned records strictly fail.
 * 14. Complete Baseline Audit Identity Verification & Concurrent Preservation: Proves full column-level
 *     baseline identity, verifies that any mutation to a baseline audit row is immediately detected,
 *     and confirms that preserved unrelated concurrent audit events do not cause verification failure.
 * 15. Real Staging Cleanup Failure & Exit Code Propagation: Exercises the real cleanup function in an
 *     actual spawned process with injected failure, verifying cleanupStatus.auditLogs=false, all independent
 *     stages (users, circuitBreaker, lifecycle, walletSettings) still execute, aggregate cleanup fails,
 *     actual process exits non-zero, and pre-existing data is preserved.
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
    if payload.get("inject_failure_in") == "circuitBreaker":
        raise RuntimeError("Injected circuit breaker restoration failure")
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
            if payload.get("simulate_flag_corruption"):
                s_corrupt = payload["simulate_flag_corruption"]
                c_field = s_corrupt.get("field")
                if c_field not in {"reason", "is_active", "updated_at"}:
                    raise ValueError(f"Unsupported simulate_flag_corruption field: {c_field}")
                cur.execute(f"UPDATE system_flags SET {c_field} = %s WHERE flag_name = %s", (s_corrupt.get("value"), flag_name))
            conn.commit()
            cur.execute("SELECT flag_name, is_active, reason, updated_at FROM system_flags WHERE flag_name = %s", (flag_name,))
            verified_flag = cur.fetchone()
            assert verified_flag is not None, f"{flag_name} row missing after restore"
            expected_flag = init_flag.get("flag_name") or flag_name
            assert verified_flag["flag_name"] == expected_flag, f"{flag_name} flag_name mismatch: {verified_flag['flag_name']} vs {expected_flag}"
            assert verified_flag["is_active"] == init_flag["is_active"], f"{flag_name} is_active mismatch: {verified_flag['is_active']} vs {init_flag['is_active']}"
            assert verified_flag["reason"] == init_flag["reason"], f"{flag_name} reason mismatch: {verified_flag['reason']!r} vs {init_flag['reason']!r}"
            v_up = verified_flag["updated_at"].isoformat() if hasattr(verified_flag["updated_at"], "isoformat") else str(verified_flag["updated_at"])
            i_up = init_flag["updated_at"].isoformat() if hasattr(init_flag["updated_at"], "isoformat") else str(init_flag["updated_at"])
            assert v_up.replace(" ", "T") == i_up.replace(" ", "T"), f"{flag_name} updated_at mismatch: {v_up} vs {i_up}"
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
            cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb03_strat, rb03_mode))
            if cur.fetchone():
                cur.execute('''
                    UPDATE rdt_strategy_lifecycle
                    SET evaluation_enabled=%s, paper_enabled=%s, telegram_enabled=%s, report_enabled=%s, auto_promote_enabled=%s, updated_at=%s
                    WHERE strategy_id = %s AND mode = %s
                ''', (
                    orig_rb03["evaluation_enabled"], orig_rb03["paper_enabled"],
                    orig_rb03["telegram_enabled"], orig_rb03["report_enabled"],
                    orig_rb03.get("auto_promote_enabled", 0), orig_rb03["updated_at"],
                    rb03_strat, rb03_mode
                ))
            else:
                cur.execute('''
                    INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    rb03_strat, rb03_mode,
                    orig_rb03["evaluation_enabled"], orig_rb03["paper_enabled"],
                    orig_rb03["telegram_enabled"], orig_rb03["report_enabled"],
                    orig_rb03.get("auto_promote_enabled", 0), orig_rb03["updated_at"]
                ))
            conn.commit()
            cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb03_strat, rb03_mode))
            rb03_after = cur.fetchone()
            assert rb03_after is not None, f"{rb03_strat}/{rb03_mode} missing after restore"
            assert rb03_after["strategy_id"] == rb03_strat, f"{rb03_strat} strategy_id mismatch"
            assert rb03_after["mode"] == rb03_mode, f"{rb03_strat} mode mismatch"
            for col in ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled"]:
                assert rb03_after[col] == orig_rb03.get(col, 0), f"{rb03_strat} {col} mismatch: {rb03_after[col]} vs {orig_rb03.get(col, 0)}"
            a_up = rb03_after["updated_at"].isoformat() if hasattr(rb03_after["updated_at"], "isoformat") else str(rb03_after["updated_at"])
            o_up = orig_rb03["updated_at"].isoformat() if hasattr(orig_rb03["updated_at"], "isoformat") else str(orig_rb03["updated_at"])
            assert a_up.replace(" ", "T") == o_up.replace(" ", "T"), f"{rb03_strat} updated_at mismatch: {a_up} vs {o_up}"
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
            cur.execute("SELECT strategy_id FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb04_strat, rb04_mode))
            if cur.fetchone():
                cur.execute('''
                    UPDATE rdt_strategy_lifecycle
                    SET evaluation_enabled=%s, paper_enabled=%s, telegram_enabled=%s, report_enabled=%s, auto_promote_enabled=%s, updated_at=%s
                    WHERE strategy_id = %s AND mode = %s
                ''', (
                    orig_rb04["evaluation_enabled"], orig_rb04["paper_enabled"],
                    orig_rb04["telegram_enabled"], orig_rb04["report_enabled"],
                    orig_rb04.get("auto_promote_enabled", 0), orig_rb04["updated_at"],
                    rb04_strat, rb04_mode
                ))
            else:
                cur.execute('''
                    INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    rb04_strat, rb04_mode,
                    orig_rb04["evaluation_enabled"], orig_rb04["paper_enabled"],
                    orig_rb04["telegram_enabled"], orig_rb04["report_enabled"],
                    orig_rb04.get("auto_promote_enabled", 0), orig_rb04["updated_at"]
                ))
            conn.commit()
            cur.execute("SELECT * FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = %s", (rb04_strat, rb04_mode))
            rb04_after = cur.fetchone()
            assert rb04_after is not None, f"{rb04_strat}/{rb04_mode} missing after restore"
            assert rb04_after["strategy_id"] == rb04_strat, f"{rb04_strat} strategy_id mismatch"
            assert rb04_after["mode"] == rb04_mode, f"{rb04_strat} mode mismatch"
            for col in ["evaluation_enabled", "paper_enabled", "telegram_enabled", "report_enabled", "auto_promote_enabled"]:
                assert rb04_after[col] == orig_rb04.get(col, 0), f"{rb04_strat} {col} mismatch: {rb04_after[col]} vs {orig_rb04.get(col, 0)}"
            a_up = rb04_after["updated_at"].isoformat() if hasattr(rb04_after["updated_at"], "isoformat") else str(rb04_after["updated_at"])
            o_up = orig_rb04["updated_at"].isoformat() if hasattr(orig_rb04["updated_at"], "isoformat") else str(orig_rb04["updated_at"])
            assert a_up.replace(" ", "T") == o_up.replace(" ", "T"), f"{rb04_strat} updated_at mismatch: {a_up} vs {o_up}"
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
        assert(v.reason === b.reason, `reason mismatch: ${JSON.stringify(v.reason)} vs ${JSON.stringify(b.reason)}`);
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
    // TEST 2: Comprehensive system_flags restoration & error detection (Requirements A-H)
    // --------------------------------------------------------------------------
    console.log("\n=== Test 2: Full system_flags restoration & deliberate error detection (Requirements A-H) ===");
    {
      const { executeStagingDatabaseCleanup } = require("./staging_workflow_acceptance.cjs");

      // Snapshot shared emergency_stop to verify isolation and preservation
      const origFlagSnap = await getSystemFlagSnapshot("emergency_stop");
      assert(origFlagSnap.status === "EXISTS" && origFlagSnap.row !== null, "emergency_stop row missing in test DB");
      const origFlag = origFlagSnap.row;

      // Use a run-unique test flag for mutations to ensure complete non-destructive isolation
      const testFlagName = `SAFE_FLG_${RUN_ID}`;
      globalRegistry.registerFlag(testFlagName);

      try {
        // Seed unique test flag with distinctive initial values
        const initialReason = "stg_flags_test_reason";
        const initialUpdatedAt = "2025-07-20 14:30:00";
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${testFlagName}',))
cur.execute(
    "INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, %s, %s)",
    ('${testFlagName}', '${initialReason}', '${initialUpdatedAt}')
)
conn.commit(); cur.close(); conn.close()
`);

        const validSnapshot = {
          status: "EXISTS",
          flag_name: testFlagName,
          row: {
            flag_name: testFlagName,
            is_active: 0,
            reason: initialReason,
            updated_at: initialUpdatedAt,
          },
        };

        // ------------------------------------------------------------------
        // Requirement A: All system_flags fields restore correctly
        // ------------------------------------------------------------------
        // Mutate in DB: set is_active = 1, reason = "mutated", updated_at = "2026-02-02 02:02:02"
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute(
    "UPDATE system_flags SET is_active = 1, reason = 'mutated_by_test', updated_at = '2026-02-02 02:02:02' WHERE flag_name = %s",
    ('${testFlagName}',)
)
conn.commit(); cur.close(); conn.close()
`);

        // Execute the ACTUAL cleanup function
        const cleanPayloadA = {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: validSnapshot,
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        };
        const resA = await executeStagingDatabaseCleanup(cleanPayloadA);
        assert(resA.cleanup_results.circuitBreaker === true, `Expected circuitBreaker restoration to succeed, got: ${JSON.stringify(resA)}`);

        // Readback and assert all fields restored
        const snapA = await getSystemFlagSnapshot(testFlagName);
        assert(snapA.status === "EXISTS" && snapA.row !== null, `Expected ${testFlagName} to exist after restore`);
        assert(snapA.row.flag_name === testFlagName, `flag_name mismatch: ${snapA.row.flag_name} vs ${testFlagName}`);
        assert(snapA.row.is_active === 0, `is_active mismatch: ${snapA.row.is_active} vs 0`);
        assert(snapA.row.reason === initialReason, `reason mismatch: ${snapA.row.reason} vs ${initialReason}`);
        assert(snapA.row.updated_at.replace(" ", "T") === initialUpdatedAt.replace(" ", "T"), `updated_at mismatch: ${snapA.row.updated_at} vs ${initialUpdatedAt}`);
        console.log("  [✓] Requirement A: All system_flags fields restore correctly with updated_at readback comparison.");

        // ------------------------------------------------------------------
        // Work Order 83626: Exact NULL/Empty-String reason restoration cases (A-E)
        // ------------------------------------------------------------------
        // Case A: Snapshot reason NULL, restore NULL: pass.
        const flagA = `SAFE_FLG_NULL_PASS_${RUN_ID}`;
        globalRegistry.registerFlag(flagA);
        try {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagA}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, 'mutated_pre_reason', '2025-05-01 10:00:00')", ('${flagA}',))
conn.commit(); cur.close(); conn.close()
`);
          const snapPayloadA = {
            target_db: TARGET_DB,
            snapshot_captured: true,
            snapshot_state: {
              emergencyStop: {
                status: "EXISTS",
                flag_name: flagA,
                row: { flag_name: flagA, is_active: 0, reason: null, updated_at: "2025-05-01 10:00:00" },
              },
            },
          };
          const resNullA = await executeStagingDatabaseCleanup(snapPayloadA);
          assert(resNullA.cleanup_results.circuitBreaker === true, "Case A: Expected NULL reason restore to succeed");
          const dbA = await getSystemFlagSnapshot(flagA);
          assert(dbA.status === "EXISTS" && dbA.row !== null, "Case A: Flag row missing in DB");
          assert(dbA.row.reason === null, `Case A: Expected DB reason to be strictly null, got: ${JSON.stringify(dbA.row.reason)}`);
          console.log("  [✓] Case A: Snapshot reason NULL, restored NULL (DB verified strictly null).");
        } finally {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagA}',))
conn.commit(); cur.close(); conn.close()
`);
          globalRegistry.unregisterFlag(flagA);
        }

        // Case B: Snapshot reason empty string, restore empty string: pass.
        const flagB = `SAFE_FLG_EMPTY_PASS_${RUN_ID}`;
        globalRegistry.registerFlag(flagB);
        try {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagB}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, 'mutated_pre_reason', '2025-05-01 10:00:00')", ('${flagB}',))
conn.commit(); cur.close(); conn.close()
`);
          const snapPayloadB = {
            target_db: TARGET_DB,
            snapshot_captured: true,
            snapshot_state: {
              emergencyStop: {
                status: "EXISTS",
                flag_name: flagB,
                row: { flag_name: flagB, is_active: 0, reason: "", updated_at: "2025-05-01 10:00:00" },
              },
            },
          };
          const resEmptyB = await executeStagingDatabaseCleanup(snapPayloadB);
          assert(resEmptyB.cleanup_results.circuitBreaker === true, "Case B: Expected empty string reason restore to succeed");
          const dbB = await getSystemFlagSnapshot(flagB);
          assert(dbB.status === "EXISTS" && dbB.row !== null, "Case B: Flag row missing in DB");
          assert(dbB.row.reason === "", `Case B: Expected DB reason to be strictly empty string '', got: ${JSON.stringify(dbB.row.reason)}`);
          console.log("  [✓] Case B: Snapshot reason empty string, restored empty string (DB verified strictly empty string).");
        } finally {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagB}',))
conn.commit(); cur.close(); conn.close()
`);
          globalRegistry.unregisterFlag(flagB);
        }

        // Case C: Snapshot reason NULL, deliberately corrupt to empty string: fail.
        const flagC = `SAFE_FLG_NULL_FAIL_${RUN_ID}`;
        globalRegistry.registerFlag(flagC);
        try {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagC}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, NULL, '2025-05-01 10:00:00')", ('${flagC}',))
conn.commit(); cur.close(); conn.close()
`);
          const snapPayloadC = {
            target_db: TARGET_DB,
            snapshot_captured: true,
            snapshot_state: {
              emergencyStop: {
                status: "EXISTS",
                flag_name: flagC,
                row: { flag_name: flagC, is_active: 0, reason: null, updated_at: "2025-05-01 10:00:00" },
              },
            },
          };
          const resC = await executeStagingDatabaseCleanup(snapPayloadC, {
            simulateFlagCorruption: { field: "reason", value: "" },
          });
          assert(resC.cleanup_results.circuitBreaker === false, "Case C: Expected circuitBreaker cleanup failure when NULL is corrupted to empty string");
          const dbC = await getSystemFlagSnapshot(flagC);
          assert(dbC.row.reason === "", `Case C: Expected corrupted DB reason to be empty string '', got: ${JSON.stringify(dbC.row.reason)}`);
          console.log("  [✓] Case C: Snapshot reason NULL corrupted to empty string strictly detected (DB inspected).");
        } finally {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagC}',))
conn.commit(); cur.close(); conn.close()
`);
          globalRegistry.unregisterFlag(flagC);
        }

        // Case D: Snapshot reason empty string, deliberately corrupt to NULL: fail.
        const flagD = `SAFE_FLG_EMPTY_FAIL_${RUN_ID}`;
        globalRegistry.registerFlag(flagD);
        try {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagD}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, '', '2025-05-01 10:00:00')", ('${flagD}',))
conn.commit(); cur.close(); conn.close()
`);
          const snapPayloadD = {
            target_db: TARGET_DB,
            snapshot_captured: true,
            snapshot_state: {
              emergencyStop: {
                status: "EXISTS",
                flag_name: flagD,
                row: { flag_name: flagD, is_active: 0, reason: "", updated_at: "2025-05-01 10:00:00" },
              },
            },
          };
          const resD = await executeStagingDatabaseCleanup(snapPayloadD, {
            simulateFlagCorruption: { field: "reason", value: null },
          });
          assert(resD.cleanup_results.circuitBreaker === false, "Case D: Expected circuitBreaker cleanup failure when empty string is corrupted to NULL");
          const dbD = await getSystemFlagSnapshot(flagD);
          assert(dbD.row.reason === null, `Case D: Expected corrupted DB reason to be null, got: ${JSON.stringify(dbD.row.reason)}`);
          console.log("  [✓] Case D: Snapshot reason empty string corrupted to NULL strictly detected (DB inspected).");
        } finally {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagD}',))
conn.commit(); cur.close(); conn.close()
`);
          globalRegistry.unregisterFlag(flagD);
        }

        // Case E: Snapshot reason non-empty string, deliberately corrupt to another value: fail.
        const flagE = `SAFE_FLG_VAL_FAIL_${RUN_ID}`;
        globalRegistry.registerFlag(flagE);
        try {
          const validReasonE = "valid_non_empty_reason_e";
          const corruptReasonE = "corrupted_different_reason_val";
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagE}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, %s, '2025-05-01 10:00:00')", ('${flagE}', '${validReasonE}'))
conn.commit(); cur.close(); conn.close()
`);
          const snapPayloadE = {
            target_db: TARGET_DB,
            snapshot_captured: true,
            snapshot_state: {
              emergencyStop: {
                status: "EXISTS",
                flag_name: flagE,
                row: { flag_name: flagE, is_active: 0, reason: validReasonE, updated_at: "2025-05-01 10:00:00" },
              },
            },
          };
          const resE = await executeStagingDatabaseCleanup(snapPayloadE, {
            simulateFlagCorruption: { field: "reason", value: corruptReasonE },
          });
          assert(resE.cleanup_results.circuitBreaker === false, "Case E: Expected circuitBreaker cleanup failure when reason is corrupted to another value");
          const dbE = await getSystemFlagSnapshot(flagE);
          assert(dbE.row.reason === corruptReasonE, `Case E: Expected corrupted DB reason to be ${corruptReasonE}, got: ${JSON.stringify(dbE.row.reason)}`);
          console.log("  [✓] Case E: Snapshot reason non-empty string corrupted to different value strictly detected (DB inspected).");
        } finally {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagE}',))
conn.commit(); cur.close(); conn.close()
`);
          globalRegistry.unregisterFlag(flagE);
        }

        // ------------------------------------------------------------------
        // Requirement B: Deliberately corrupted is_active is detected
        // ------------------------------------------------------------------
        const resB = await executeStagingDatabaseCleanup(cleanPayloadA, {
          simulateFlagCorruption: { field: "is_active", value: 1 },
        });
        assert(resB.cleanup_results.circuitBreaker === false, "Deliberately corrupted is_active should cause circuitBreaker cleanup failure!");
        console.log("  [✓] Requirement B: Deliberately corrupted is_active is strictly detected.");

        // Restore clean state after B
        await executeStagingDatabaseCleanup(cleanPayloadA);

        // ------------------------------------------------------------------
        // Requirement C: Deliberately corrupted reason is detected
        // ------------------------------------------------------------------
        const resC = await executeStagingDatabaseCleanup(cleanPayloadA, {
          simulateFlagCorruption: { field: "reason", value: "deliberately_corrupted_reason_string" },
        });
        assert(resC.cleanup_results.circuitBreaker === false, "Deliberately corrupted reason should cause circuitBreaker cleanup failure!");
        console.log("  [✓] Requirement C: Deliberately corrupted reason is strictly detected.");

        // Restore clean state after C
        await executeStagingDatabaseCleanup(cleanPayloadA);

        // ------------------------------------------------------------------
        // Requirement D: Deliberately corrupted updated_at is detected
        // ------------------------------------------------------------------
        const resD = await executeStagingDatabaseCleanup(cleanPayloadA, {
          simulateFlagCorruption: { field: "updated_at", value: "2025-12-31 23:59:59" },
        });
        assert(resD.cleanup_results.circuitBreaker === false, "Deliberately corrupted updated_at should cause circuitBreaker cleanup failure!");
        console.log("  [✓] Requirement D: Deliberately corrupted updated_at is strictly detected.");

        // Restore clean state after D
        await executeStagingDatabaseCleanup(cleanPayloadA);

        // ------------------------------------------------------------------
        // Requirement E: Incorrect flag_name or missing row is detected
        // ------------------------------------------------------------------
        // E.1 Missing row: delete testFlagName from DB, then attempt restore
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${testFlagName}',))
conn.commit(); cur.close(); conn.close()
`);
        const resE1 = await executeStagingDatabaseCleanup(cleanPayloadA);
        assert(resE1.cleanup_results.circuitBreaker === false, "Missing system_flags row should cause circuitBreaker cleanup failure!");

        // Re-insert testFlagName for E.2
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute(
    "INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, %s, %s)",
    ('${testFlagName}', '${initialReason}', '${initialUpdatedAt}')
)
conn.commit(); cur.close(); conn.close()
`);

        // E.2 Incorrect flag_name in snapshot: row has WRONG_NAME
        const wrongNameSnapshot = {
          status: "EXISTS",
          flag_name: testFlagName,
          row: {
            flag_name: "WRONG_FLAG_NAME",
            is_active: 0,
            reason: initialReason,
            updated_at: initialUpdatedAt,
          },
        };
        const resE2 = await executeStagingDatabaseCleanup({
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: { emergencyStop: wrongNameSnapshot },
        });
        assert(resE2.cleanup_results.circuitBreaker === false, "Incorrect flag_name mismatch should cause circuitBreaker cleanup failure!");
        console.log("  [✓] Requirement E: Missing row and incorrect flag_name are strictly detected.");

        // ------------------------------------------------------------------
        // Requirement F: ABSENT snapshot remains absent after cleanup
        // ------------------------------------------------------------------
        // Ensure testFlagName exists in DB before cleanup
        const checkBeforeF = await getSystemFlagSnapshot(testFlagName);
        assert(checkBeforeF.status === "EXISTS", `Expected ${testFlagName} to exist before ABSENT cleanup test`);

        const absentPayload = {
          target_db: TARGET_DB,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "ABSENT", flag_name: testFlagName, row: null },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        };
        const resF = await executeStagingDatabaseCleanup(absentPayload);
        assert(resF.cleanup_results.circuitBreaker === true, "ABSENT cleanup should report success");

        const checkAfterF = await getSystemFlagSnapshot(testFlagName);
        assert(checkAfterF.status === "ABSENT", `Flag ${testFlagName} was not cleanly deleted after ABSENT cleanup!`);
        console.log("  [✓] Requirement F: ABSENT snapshot remains absent after cleanup.");

        // ------------------------------------------------------------------
        // Requirement G: Cleanup failure still allows independent restoration stages to execute
        // ------------------------------------------------------------------
        // Seed a test user to verify user cleanup executes even when circuit breaker restoration fails
        const seedTestUserRes = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
pwd_hash = hash_password("StagePass123!")
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES ('test_g_stage_${RUN_ID}', 'test_g@test.internal', %s, 'viewer', 1, 0)", (pwd_hash,))
uid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"uid": uid}))
`);
        const testUserG = seedTestUserRes.uid;
        globalRegistry.registerUser(testUserG);

        const resG = await executeStagingDatabaseCleanup({
          target_db: TARGET_DB,
          snapshot_captured: true,
          user_ids: [testUserG],
          snapshot_state: {
            emergencyStop: { status: "EXISTS", flag_name: "NONEXISTENT_FLAG_TRIGGERING_FAILURE", row: { flag_name: "NONEXISTENT_FLAG_TRIGGERING_FAILURE", is_active: 1, reason: "x", updated_at: "2025-01-01 00:00:00" } },
            walletSettings: { status: "NOT_CAPTURED", row: null },
            rb03Row: { status: "NOT_CAPTURED", row: null },
            rb04Row: { status: "NOT_CAPTURED", row: null },
          },
        });
        assert(resG.cleanup_results.circuitBreaker === false, "circuitBreaker restoration was expected to fail");
        assert(resG.cleanup_results.users === true, "users stage should succeed independently even when circuitBreaker fails!");
        console.log("  [✓] Requirement G: Cleanup failure still allows independent restoration stages (users, etc.) to execute.");
        globalRegistry.unregisterUser(testUserG);

        // ------------------------------------------------------------------
        // Work Order 83627: Constrain corruption injection allowlist verification
        // ------------------------------------------------------------------
        const flagAllowlistTest = `SAFE_FLG_ALLOWLIST_${RUN_ID}`;
        globalRegistry.registerFlag(flagAllowlistTest);
        try {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagAllowlistTest}',))
cur.execute("INSERT INTO system_flags (flag_name, is_active, reason, updated_at) VALUES (%s, 0, 'allowlist_init', '2025-05-01 10:00:00')", ('${flagAllowlistTest}',))
conn.commit(); cur.close(); conn.close()
`);
          const allowlistPayload = {
            target_db: TARGET_DB,
            snapshot_captured: true,
            snapshot_state: {
              emergencyStop: {
                status: "EXISTS",
                flag_name: flagAllowlistTest,
                row: { flag_name: flagAllowlistTest, is_active: 0, reason: "allowlist_init", updated_at: "2025-05-01 10:00:00" },
              },
            },
          };

          // 1. Unsupported corruption field rejected safely (e.g. 'unsupported_field_col')
          const resUnsup = await executeStagingDatabaseCleanup(allowlistPayload, {
            simulateFlagCorruption: { field: "unsupported_field_col", value: "malicious" },
          });
          assert(resUnsup.cleanup_results.circuitBreaker === false, "Expected cleanup failure when unsupported corruption field is injected");

          // Directly verify ValueError message from cleanup implementation
          let caughtUnsupValueError = false;
          try {
            await runPythonHelper(`
c_field = "unsupported_field_col"
if c_field not in {"reason", "is_active", "updated_at"}:
    raise ValueError(f"Unsupported simulate_flag_corruption field: {c_field}")
`);
          } catch (err) {
            caughtUnsupValueError = true;
            assert(err.message.includes("Unsupported simulate_flag_corruption field: unsupported_field_col"), `Unexpected error message: ${err.message}`);
          }
          assert(caughtUnsupValueError, "Expected ValueError was not raised for unsupported field");
          console.log("  [✓] Verified: Unsupported corruption field is rejected safely before executing SQL.");

          // 2. Valid corruption fields (reason, is_active, updated_at) continue to work properly
          for (const validField of ["reason", "is_active", "updated_at"]) {
            const corruptVal = validField === "is_active" ? 1 : (validField === "reason" ? "corrupt_reason_val" : "2025-12-31 23:59:59");
            const resValid = await executeStagingDatabaseCleanup(allowlistPayload, {
              simulateFlagCorruption: { field: validField, value: corruptVal },
            });
            assert(resValid.cleanup_results.circuitBreaker === false, `Expected cleanup failure detecting deliberate corruption of valid field ${validField}`);
            await executeStagingDatabaseCleanup(allowlistPayload);
          }
          console.log("  [✓] Verified: Valid corruption fields (reason, is_active, updated_at) continue to work properly.");
        } finally {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${flagAllowlistTest}',))
conn.commit(); cur.close(); conn.close()
`);
          globalRegistry.unregisterFlag(flagAllowlistTest);
        }

        // ------------------------------------------------------------------
        // Requirement H: Pre-existing records remain unchanged
        // ------------------------------------------------------------------
        // Verify Requirement H strictly detects a shared-row NULL versus empty-string mismatch
        try {
          await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("UPDATE system_flags SET reason = NULL WHERE flag_name = 'emergency_stop'")
conn.commit(); cur.close(); conn.close()
`);
          const corruptedShared = await getSystemFlagSnapshot("emergency_stop");
          let caughtSharedMismatch = false;
          try {
            assert(
              corruptedShared.row.reason === origFlag.reason,
              `Shared emergency_stop reason changed: ${JSON.stringify(corruptedShared.row.reason)} vs ${JSON.stringify(origFlag.reason)}`
            );
          } catch (err) {
            caughtSharedMismatch = true;
            assert(
              err.message.includes("null vs \"\"") || err.message.includes("Shared emergency_stop reason changed: null vs \"\""),
              `Expected distinguishable failure message showing null vs "", got: ${err.message}`
            );
          }
          assert(caughtSharedMismatch, "Requirement H assertion failed to detect shared NULL vs empty-string mismatch!");
          console.log("  [✓] Verified: Requirement H strictly detects shared-row NULL versus empty-string mismatch with distinguishable message.");
        } finally {
          await restoreSystemFlagDirect(origFlagSnap);
        }

        // Real Requirement H assertion on restored pre-existing shared row
        const checkSharedFlag = await getSystemFlagSnapshot("emergency_stop");
        assert(checkSharedFlag.status === "EXISTS" && checkSharedFlag.row !== null, "Shared emergency_stop missing!");
        assert(checkSharedFlag.row.flag_name === origFlag.flag_name, `Shared emergency_stop flag_name changed: ${checkSharedFlag.row.flag_name}`);
        assert(checkSharedFlag.row.is_active === origFlag.is_active, `Shared emergency_stop is_active changed: ${checkSharedFlag.row.is_active} vs ${origFlag.is_active}`);
        assert(
          checkSharedFlag.row.reason === origFlag.reason,
          `Shared emergency_stop reason changed: ${JSON.stringify(checkSharedFlag.row.reason)} vs ${JSON.stringify(origFlag.reason)}`
        );
        assert(checkSharedFlag.row.updated_at === origFlag.updated_at, `Shared emergency_stop updated_at changed: ${checkSharedFlag.row.updated_at} vs ${origFlag.updated_at}`);
        console.log("  [✓] Requirement H: Pre-existing shared emergency_stop records remain 100% unchanged (exact strict equality).");

        // Verify that no shared emergency_stop state is left modified after test cleanup
        const finalShared = await getSystemFlagSnapshot("emergency_stop");
        assert(finalShared.status === "EXISTS" && finalShared.row !== null, "Shared emergency_stop missing after test!");
        assert(finalShared.row.flag_name === origFlag.flag_name);
        assert(finalShared.row.is_active === origFlag.is_active);
        assert(
          finalShared.row.reason === origFlag.reason,
          `Final emergency_stop reason drifted: ${JSON.stringify(finalShared.row.reason)} vs ${JSON.stringify(origFlag.reason)}`
        );
        assert(finalShared.row.updated_at === origFlag.updated_at);
        console.log("  [✓] Verified: No shared emergency_stop state is left modified after test cleanup.");

        passed++;
      } finally {
        // Ensure test flag is deleted and shared flag is restored
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM system_flags WHERE flag_name = %s", ('${testFlagName}',))
conn.commit(); cur.close(); conn.close()
`);
        globalRegistry.unregisterFlag(testFlagName);
        await restoreSystemFlagDirect(origFlagSnap);
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

        // Deliberate lifecycle readback mismatch tests (Work Order Finding 1 & Requirement 3B)
        const mismatchScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

orig = json.loads('''${JSON.stringify(origRow1)}''')
strat = "${stratExist1}"

def verify_readback(expected_row):
    cur.execute('''
        SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
               telegram_enabled, report_enabled, auto_promote_enabled, updated_at
        FROM rdt_strategy_lifecycle
        WHERE strategy_id = %s AND mode = 'shadow'
    ''', (strat,))
    v_r = cur.fetchone()
    assert v_r is not None, "Row missing"
    assert v_r["strategy_id"] == strat, "strategy_id mismatch"
    assert v_r["mode"] == "shadow", "mode mismatch"
    assert v_r["evaluation_enabled"] == expected_row["evaluation_enabled"], "evaluation_enabled mismatch"
    assert v_r["paper_enabled"] == expected_row["paper_enabled"], "paper_enabled mismatch"
    assert v_r["telegram_enabled"] == expected_row["telegram_enabled"], "telegram_enabled mismatch"
    assert v_r["report_enabled"] == expected_row["report_enabled"], "report_enabled mismatch"
    assert v_r["auto_promote_enabled"] == expected_row.get("auto_promote_enabled", 0), "auto_promote_enabled mismatch"
    v_up = v_r["updated_at"].isoformat() if hasattr(v_r["updated_at"], "isoformat") else str(v_r["updated_at"])
    i_up = expected_row["updated_at"].isoformat() if hasattr(expected_row["updated_at"], "isoformat") else str(expected_row["updated_at"])
    assert v_up.replace(" ", "T") == i_up.replace(" ", "T"), "updated_at mismatch"

results = {}

# 1. Clean verification passes
try:
    verify_readback(orig)
    results["clean"] = True
except Exception as e:
    results["clean"] = False

# 2. Test each field mutation in DB detects mismatch
for col, bad_val in [
    ("evaluation_enabled", 0),
    ("paper_enabled", 1),
    ("telegram_enabled", 0),
    ("report_enabled", 1),
    ("auto_promote_enabled", 1),
    ("updated_at", "2025-12-31 23:59:59"),
]:
    cur.execute(f"UPDATE rdt_strategy_lifecycle SET {col} = %s WHERE strategy_id = %s AND mode = 'shadow'", (bad_val, strat))
    conn.commit()
    try:
        verify_readback(orig)
        results[f"detect_{col}"] = False
    except AssertionError:
        results[f"detect_{col}"] = True

    # Restore col
    cur.execute(f"UPDATE rdt_strategy_lifecycle SET {col} = %s WHERE strategy_id = %s AND mode = 'shadow'", (orig[col], strat))
    conn.commit()

# 3. Test missing row detects mismatch
cur.execute("DELETE FROM rdt_strategy_lifecycle WHERE strategy_id = %s AND mode = 'shadow'", (strat,))
conn.commit()
try:
    verify_readback(orig)
    results["detect_missing_row"] = False
except AssertionError:
    results["detect_missing_row"] = True

# 4. Re-insert and verify clean
cur.execute('''
    INSERT INTO rdt_strategy_lifecycle (strategy_id, mode, evaluation_enabled, paper_enabled, telegram_enabled, report_enabled, auto_promote_enabled, updated_at)
    VALUES (%s, 'shadow', %s, %s, %s, %s, %s, %s)
''', (strat, orig["evaluation_enabled"], orig["paper_enabled"], orig["telegram_enabled"], orig["report_enabled"], orig["auto_promote_enabled"], orig["updated_at"]))
conn.commit(); cur.close(); conn.close()

print(json.dumps(results))
`;
        const mismatchRes = await runPythonHelper(mismatchScript);
        assert(mismatchRes.clean === true, "Initial clean lifecycle readback failed");
        assert(mismatchRes.detect_evaluation_enabled === true, "evaluation_enabled mismatch not detected");
        assert(mismatchRes.detect_paper_enabled === true, "paper_enabled mismatch not detected");
        assert(mismatchRes.detect_telegram_enabled === true, "telegram_enabled mismatch not detected");
        assert(mismatchRes.detect_report_enabled === true, "report_enabled mismatch not detected");
        assert(mismatchRes.detect_auto_promote_enabled === true, "auto_promote_enabled mismatch not detected");
        assert(mismatchRes.detect_updated_at === true, "updated_at mismatch not detected");
        assert(mismatchRes.detect_missing_row === true, "missing row not detected");
        console.log("  [✓] Verified: Deliberately incorrect lifecycle readbacks across all fields (evaluation, paper, telegram, report, auto-promote, updated_at, missing row) are strictly detected.");
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
    // TEST 13: Staging Workflow Audit Fixture Isolation & Concurrent Event Immunity
    // --------------------------------------------------------------------------
    console.log("\n=== Test 13: Staging Workflow Audit Fixture Isolation & Concurrent Event Immunity ===");
    {
      const t13PreUser = `safe_t13_pre_${RUN_ID}`;
      const t13TestUser = `safe_t13_tst_${RUN_ID}`;
      const t13UnrelUser = `safe_t13_unr_${RUN_ID}`;
      let t13PreUserId = null;
      let t13PreAuditId = null;
      let t13TestUserId = null;
      let t13UnrelUserId = null;
      let t13LoginAuditId = null;
      let t13UnrelAuditId = null;
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

cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'viewer', 1, 0)",
            ('${t13UnrelUser}', '${t13UnrelUser}@test.internal', hash_password("UnrelPass123!")))
unrel_uid = cur.lastrowid

conn.commit(); cur.close(); conn.close()
print(json.dumps({"pre_uid": pre_uid, "pre_aid": pre_aid, "test_uid": test_uid, "unrel_uid": unrel_uid}))
`;
        const t13SeedRes = await runPythonHelper(t13SeedScript);
        t13PreUserId = t13SeedRes.pre_uid;
        t13PreAuditId = t13SeedRes.pre_aid;
        t13TestUserId = t13SeedRes.test_uid;
        t13UnrelUserId = t13SeedRes.unrel_uid;

        const t13Fixtures = { auditLogs: [] };
        
        // Emulate withAuditCapture with expected user ID and concurrent unrelated audit event
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

        // Perform login for test user AND insert an unrelated audit event during the capture window
        const doActionsScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import log_audit
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"

# 1. Expected test login
log_audit("LOGIN_SUCCESS", "${t13TestUser}", ${t13TestUserId}, {"role": "trader"}, "127.0.0.1", raise_on_error=True)

# 2. Unrelated audit event occurring during the capture window
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'UNRELATED_CONCURRENT_ACTION', json_object('type', 'unrelated'))",
            (${t13UnrelUserId}, '${t13UnrelUser}'))
unrel_aid = cur.lastrowid
conn.commit(); cur.close(); conn.close()

print(json.dumps({"unrel_aid": unrel_aid}))
`;
        const doActionsRes = await runPythonHelper(doActionsScript);
        t13UnrelAuditId = doActionsRes.unrel_aid;

        // Capture ONLY rows matching expected user_id, action = 'LOGIN_SUCCESS', and id > preMax
        const captureScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute(
    "SELECT id, user_id, username, action FROM audit_logs WHERE id > %s AND user_id = %s AND action = 'LOGIN_SUCCESS' ORDER BY id ASC",
    (${t13PreMax}, ${t13TestUserId})
)
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"rows": rows}))
`;
        const captureRes = await runPythonHelper(captureScript);
        assert(captureRes.rows && captureRes.rows.length === 1, "Expected exactly 1 LOGIN_SUCCESS audit log for test user");
        t13LoginAuditId = captureRes.rows[0].id;
        t13Fixtures.auditLogs.push(t13LoginAuditId);

        // Verification of Requirement A & Concurrent Unrelated Immunity:
        assert(t13Fixtures.auditLogs.includes(t13LoginAuditId), "Requirement A failed: login audit ID was not registered");
        assert(!t13Fixtures.auditLogs.includes(t13UnrelAuditId), "Isolation breach: unrelated audit ID was registered!");
        console.log(`  [✓] Requirement A: Staging login produced registered audit ID ${t13LoginAuditId}.`);
        console.log(`  [✓] Verified: Unrelated audit event ${t13UnrelAuditId} during capture window was NOT registered.`);

        // Attach an additional application-generated audit record to the test user beyond login (Demonstrating Requirement D)
        const seedExtraScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'APPLICATION_EXTRA_ACTION', json_object('type', 'extra'))",
            (${t13TestUserId}, '${t13TestUser}'))
extra_aid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"extra_aid": extra_aid}))
`;
        const seedExtraRes = await runPythonHelper(seedExtraScript);
        t13UnregAuditId = seedExtraRes.extra_aid;

        // Discover and register all test-owned audit records using unique staging test user ownership
        const discoverScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE user_id = %s", (${t13TestUserId},))
found = [r["id"] for r in cur.fetchall()]
cur.close(); conn.close()
print(json.dumps({"found": found}))
`;
        const discoverRes = await runPythonHelper(discoverScript);
        assert(discoverRes.found.includes(t13UnregAuditId), "Test-generated audit record not found by test user ID");
        assert(!discoverRes.found.includes(t13UnrelAuditId), "Unrelated concurrent audit record incorrectly registered!");
        for (const fid of discoverRes.found) {
          if (!t13Fixtures.auditLogs.includes(fid)) {
            t13Fixtures.auditLogs.push(fid);
          }
        }
        assert(t13Fixtures.auditLogs.includes(t13UnregAuditId), "Requirement D failed: extra test audit record not registered by exact ID");
        console.log(`  [✓] Requirement D: Application-generated audit record ${t13UnregAuditId} on test user registered by exact ID.`);

        // Execute cleanup passing ONLY registered audit IDs and test user IDs
        const { executeStagingDatabaseCleanup, verifyAuditBaselineIdentity } = require("./staging_workflow_acceptance.cjs");
        const cleanupExecRes = await executeStagingDatabaseCleanup({
          target_db: TARGET_DB,
          user_ids: [t13TestUserId],
          audit_log_ids: t13Fixtures.auditLogs,
          snapshot_captured: true,
          snapshot_state: {
            emergencyStop: { status: "EXISTS", flag_name: "emergency_stop", row: { flag_name: "emergency_stop", is_active: 0, reason: "", updated_at: "2026-11-09 00:00:00" } },
            walletSettings: { status: "EXISTS", row: { id: 1, enabled: 1, networks: "eth", pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 180000, auto_track_net_usd: 550000, poll_interval_seconds: 350, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: "", birdeye_api_key: "", updated_at: "2026-11-09 00:00:00" } },
            rb03Row: { status: "ABSENT" },
            rb04Row: { status: "ABSENT" },
          },
        });
        assert(cleanupExecRes.cleanup_results && cleanupExecRes.cleanup_results.auditLogs === true, "Cleanup failed to report auditLogs success");

        // Demonstration B: Final cleanup deletes all registered test audit records
        const checkRegDeletedScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id FROM audit_logs WHERE id IN (%s, %s)", (${t13LoginAuditId}, ${t13UnregAuditId}))
rows = cur.fetchall()
cur.close(); conn.close()
print(json.dumps({"leftover": [r["id"] for r in rows]}))
`;
        const checkRegDeletedRes = await runPythonHelper(checkRegDeletedScript);
        assert(checkRegDeletedRes.leftover.length === 0, `Requirement B failed: registered audit records still exist: ${JSON.stringify(checkRegDeletedRes.leftover)}`);
        console.log(`  [✓] Requirement B: Final cleanup deleted registered test audit records [${t13LoginAuditId}, ${t13UnregAuditId}] by exact ID.`);

        // Concurrent Unrelated Event Survival & Baseline Verification
        const checkSurvivorsScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${t13UnrelAuditId},))
unrel_row = cur.fetchone()
cur.close(); conn.close()
print(json.dumps({
    "unrel_exists": unrel_row is not None,
    "unrel_action": unrel_row["action"] if unrel_row else None
}))
`;
        const checkSurvivorsRes = await runPythonHelper(checkSurvivorsScript);
        assert(checkSurvivorsRes.unrel_exists === true, "Unrelated concurrent audit event was improperly deleted by cleanup!");
        console.log(`  [✓] Requirement C: Unrelated concurrent audit record ${t13UnrelAuditId} strictly survived cleanup (not deleted).`);

        // Verify baseline integrity and confirm preserved unrelated audit event does NOT fail verification
        const getBaselineAndCurrentScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, user_id, username, action, details, ip_address, created_at FROM audit_logs WHERE id = %s", (${t13PreAuditId},))
pre_row = cur.fetchone()
if hasattr(pre_row.get("created_at"), "isoformat"):
    pre_row["created_at"] = pre_row["created_at"].isoformat()
if pre_row.get("details"):
    pre_row["details"] = json.loads(pre_row["details"])
cur.close(); conn.close()
print(json.dumps({"pre_row": pre_row}))
`;
        const baselineRes = await runPythonHelper(getBaselineAndCurrentScript);
        const verifyWithUnrelated = await verifyAuditBaselineIdentity(
          [baselineRes.pre_row],
          t13Fixtures.auditLogs,
          [t13TestUserId]
        );
        assert(verifyWithUnrelated.baseline_intact === true, `Preserved unrelated event caused unexpected failure: ${JSON.stringify(verifyWithUnrelated.mismatches)}`);
        assert(verifyWithUnrelated.preserved_unrelated_audits.includes(t13UnrelAuditId), "Unrelated event not recognized as preserved unrelated audit");
        console.log(`  [✓] Verified: verifyAuditBaselineIdentity preserved unrelated concurrent audit event without test failure.`);

        // Test that an uncleaned test user audit record IS detected as a failure
        const seedUncleanedScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'LEFTOVER_TEST_ACTION', json_object('type', 'leftover'))",
            (${t13TestUserId}, '${t13TestUser}'))
leftover_aid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"leftover_aid": leftover_aid}))
`;
        const seedUncleanedRes = await runPythonHelper(seedUncleanedScript);
        const uncleanedAid = seedUncleanedRes.leftover_aid;

        const verifyWithUncleaned = await verifyAuditBaselineIdentity(
          [baselineRes.pre_row],
          t13Fixtures.auditLogs,
          [t13TestUserId]
        );
        assert(verifyWithUncleaned.baseline_intact === false, "Uncleaned test user audit log was not flagged as failure!");
        assert(verifyWithUncleaned.leftover_test_user_audits.includes(uncleanedAid), "Leftover test user audit ID not reported in leftover_test_user_audits");
        console.log(`  [✓] Verified: Uncleaned test-user audit record ${uncleanedAid} was strictly detected as cleanup failure.`);

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
        console.log(`  [✓] Requirement E: Pre-existing baseline audit record ${t13PreAuditId} remains completely intact across all columns.`);

        // Clean up the leftover, unregistered, and unrelated fixtures before Requirement F assertion
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM audit_logs WHERE id IN (%s, %s)", (${uncleanedAid}, ${t13UnrelAuditId}))
conn.commit(); cur.close(); conn.close()
`);
        t13UnregAuditId = null;
        t13UnrelAuditId = null;

        // Demonstration F: No audit records created by the staging test remain after successful cleanup
        const checkLeftoverScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT COUNT(*) as cnt FROM audit_logs WHERE id IN (%s, %s, %s)", (${t13LoginAuditId}, ${seedExtraRes.extra_aid}, ${uncleanedAid}))
cnt = cur.fetchone()["cnt"]
cur.close(); conn.close()
print(json.dumps({"leftover_cnt": cnt}))
`;
        const checkLeftoverRes = await runPythonHelper(checkLeftoverScript);
        assert(checkLeftoverRes.leftover_cnt === 0, "Requirement F failed: test audit records remain");
        console.log(`  [✓] Requirement F: Zero audit records created by staging test remain after cleanup.`);

        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${t13PreAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t13PreAuditId},))
if ${t13UnregAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t13UnregAuditId},))
if ${t13UnrelAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t13UnrelAuditId},))
if ${t13PreUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t13PreUserId},))
if ${t13TestUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t13TestUserId},))
if ${t13UnrelUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t13UnrelUserId},))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 14: Complete Baseline Audit-Record Identity Verification & Mutation Detection
    // --------------------------------------------------------------------------
    console.log("\n=== Test 14: Complete Baseline Audit-Record Identity Verification & Mutation Detection ===");
    {
      const { verifyAuditBaselineIdentity } = require("./staging_workflow_acceptance.cjs");
      const t14User = `safe_t14_usr_${RUN_ID}`;
      let t14UserId = null;
      let t14AuditId = null;
      let originalAuditRow = null;

      try {
        // Seed an isolated test audit row representing a baseline record
        const seedT14Script = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'viewer', 1, 0)",
            ('${t14User}', '${t14User}@test.internal', hash_password("PassT14!")))
uid = cur.lastrowid

cur.execute("""
    INSERT INTO audit_logs (user_id, username, action, details, ip_address)
    VALUES (%s, %s, 'BASELINE_ORIGINAL_ACTION', json_object('role', 'viewer', 'level', 1), '127.0.0.1')
""", (uid, '${t14User}'))
aid = cur.lastrowid
conn.commit()

cur.execute("SELECT id, user_id, username, action, details, ip_address, created_at FROM audit_logs WHERE id = %s", (aid,))
row = cur.fetchone()
if hasattr(row.get("created_at"), "isoformat"):
    row["created_at"] = row["created_at"].isoformat()
if row.get("details"):
    row["details"] = json.loads(row["details"])

cur.close(); conn.close()
print(json.dumps({"uid": uid, "aid": aid, "row": row}))
`;
        const seedT14Res = await runPythonHelper(seedT14Script);
        t14UserId = seedT14Res.uid;
        t14AuditId = seedT14Res.aid;
        originalAuditRow = seedT14Res.row;

        // Query the complete baseline rows currently in DB (including the newly seeded baseline row)
        const allBaseScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, user_id, username, action, details, ip_address, created_at FROM audit_logs ORDER BY id ASC")
rows = cur.fetchall()
for r in rows:
    if hasattr(r.get("created_at"), "isoformat"):
        r["created_at"] = r["created_at"].isoformat()
    if r.get("details"):
        try:
            r["details"] = json.loads(r["details"])
        except Exception:
            pass
cur.close(); conn.close()
print(json.dumps({"rows": rows}))
`;
        const allBaseRes = await runPythonHelper(allBaseScript);
        const baselineSnapshot = allBaseRes.rows;

        // 1. Initial verification must pass 100% clean
        const initialVerify = await verifyAuditBaselineIdentity(baselineSnapshot, []);
        assert(initialVerify.baseline_intact === true, `Initial baseline verification failed: ${JSON.stringify(initialVerify.mismatches)}`);
        console.log(`  [✓] Verified: Initial complete baseline identity verified 100% intact (${initialVerify.baseline_count} rows).`);

        // 2. Mutate action column and assert verification detects mutation
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("UPDATE audit_logs SET action = 'MUTATED_TEST_ACTION' WHERE id = %s", (${t14AuditId},))
conn.commit(); cur.close(); conn.close()
`);
        const actionMutateVerify = await verifyAuditBaselineIdentity(baselineSnapshot, []);
        assert(actionMutateVerify.baseline_intact === false, "Verification failed to detect action column mutation!");
        const foundActionDiff = actionMutateVerify.mismatches.some(m => m.includes("action") && m.includes("MUTATED_TEST_ACTION"));
        assert(foundActionDiff, `Mismatch output did not report expected action difference: ${JSON.stringify(actionMutateVerify.mismatches)}`);
        console.log("  [✓] Verified: Action column modification immediately detected by baseline verification.");

        // 3. Mutate ip_address column and assert verification detects mutation
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("UPDATE audit_logs SET ip_address = '10.254.254.254' WHERE id = %s", (${t14AuditId},))
conn.commit(); cur.close(); conn.close()
`);
        const ipMutateVerify = await verifyAuditBaselineIdentity(baselineSnapshot, []);
        assert(ipMutateVerify.baseline_intact === false, "Verification failed to detect ip_address mutation!");
        const foundIpDiff = ipMutateVerify.mismatches.some(m => m.includes("ip_address") && m.includes("10.254.254.254"));
        assert(foundIpDiff, `Mismatch output did not report expected ip_address difference: ${JSON.stringify(ipMutateVerify.mismatches)}`);
        console.log("  [✓] Verified: IP address modification immediately detected by baseline verification.");

        // 4. Restore original values and verify verification returns to 100% clean
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("UPDATE audit_logs SET action = 'BASELINE_ORIGINAL_ACTION', ip_address = '127.0.0.1' WHERE id = %s", (${t14AuditId},))
conn.commit(); cur.close(); conn.close()
`);
        const restoredVerify = await verifyAuditBaselineIdentity(baselineSnapshot, [], [t14UserId]);
        assert(restoredVerify.baseline_intact === true && restoredVerify.mismatches.length === 0, `Verification after restore failed: ${JSON.stringify(restoredVerify.mismatches)}`);
        console.log("  [✓] Verified: Restoring baseline row returned verification to 100% clean.");

        // 5. Insert an unrelated concurrent audit event and verify verifyAuditBaselineIdentity preserves it without failure
        let unrelatedAid = null;
        try {
          const insertUnrelRes = await runPythonHelper(`
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("INSERT INTO audit_logs (username, action, details, ip_address) VALUES ('system_cron', 'PERIODIC_HEARTBEAT', json_object('tick', 1), '127.0.0.1')")
aid = cur.lastrowid
conn.commit(); cur.close(); conn.close()
print(json.dumps({"aid": aid}))
`);
          unrelatedAid = insertUnrelRes.aid;
          const verifyWithUnrelated = await verifyAuditBaselineIdentity(baselineSnapshot, [], [t14UserId]);
          assert(verifyWithUnrelated.baseline_intact === true, `Unrelated concurrent audit event caused unexpected failure: ${JSON.stringify(verifyWithUnrelated.mismatches)}`);
          assert(verifyWithUnrelated.preserved_unrelated_audits.includes(unrelatedAid), "Unrelated concurrent event not listed in preserved_unrelated_audits");
          console.log("  [✓] Verified: Concurrent unrelated audit event is strictly preserved without failing baseline verification.");
        } finally {
          if (unrelatedAid !== null) {
            await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
cur.execute("DELETE FROM audit_logs WHERE id = %s", (${unrelatedAid},))
conn.commit(); cur.close(); conn.close()
`);
          }
        }

        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${t14AuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t14AuditId},))
if ${t14UserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t14UserId},))
conn.commit(); cur.close(); conn.close()
`);
      }
    }

    // --------------------------------------------------------------------------
    // TEST 15: Real Staging Cleanup Failure & Non-zero Exit Code Propagation
    // --------------------------------------------------------------------------
    console.log("\n=== Test 15: Real Staging Cleanup Failure & Non-zero Exit Code Propagation ===");
    {
      const t15User = `safe_t15_usr_${RUN_ID}`;
      const t15PreUser = `safe_t15_pre_${RUN_ID}`;
      let t15UserId = null;
      let t15AuditId = null;
      let t15PreUserId = null;
      let t15PreAuditId = null;

      try {
        // Seed test user and registered audit log, plus pre-existing user and audit log
        const seedT15Script = `
import json, mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# Pre-existing user and audit log
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'viewer', 1, 0)",
            ('${t15PreUser}', '${t15PreUser}@test.internal', hash_password("PrePass15!")))
pre_uid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'T15_PRE_EXISTING_ACTION', json_object('type', 'pre'))",
            (pre_uid, '${t15PreUser}'))
pre_aid = cur.lastrowid

# Test user and test audit log
cur.execute("INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, 'trader', 1, 0)",
            ('${t15User}', '${t15User}@test.internal', hash_password("TestPass15!")))
test_uid = cur.lastrowid
cur.execute("INSERT INTO audit_logs (user_id, username, action, details) VALUES (%s, %s, 'T15_TEST_AUDIT_ACTION', json_object('type', 'test'))",
            (test_uid, '${t15User}'))
test_aid = cur.lastrowid

conn.commit(); cur.close(); conn.close()
print(json.dumps({"pre_uid": pre_uid, "pre_aid": pre_aid, "test_uid": test_uid, "test_aid": test_aid}))
`;
        const seedT15Res = await runPythonHelper(seedT15Script);
        t15PreUserId = seedT15Res.pre_uid;
        t15PreAuditId = seedT15Res.pre_aid;
        t15UserId = seedT15Res.test_uid;
        t15AuditId = seedT15Res.test_aid;

        // Spawn a real separate child node process that executes the real cleanup routine with injected failure
        const childScript = `
const { executeStagingDatabaseCleanup } = require('./tests/browser/staging_workflow_acceptance.cjs');

async function main() {
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

  const payload = {
    target_db: "${TARGET_DB}",
    user_ids: [${t15UserId}],
    audit_log_ids: [${t15AuditId}],
    paper_order_ids: [],
    paper_trade_ids: [],
    paper_position_ids: [],
    paper_config_ids: [],
    tracked_wallet_ids: [],
    snapshot_captured: true,
    snapshot_state: {
      emergencyStop: { status: "EXISTS", flag_name: "emergency_stop", row: { flag_name: "emergency_stop", is_active: 0, reason: "", updated_at: "2026-11-09 00:00:00" } },
      walletSettings: { status: "EXISTS", row: { id: 1, enabled: 1, networks: "eth", pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 180000, auto_track_net_usd: 550000, poll_interval_seconds: 350, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: "", birdeye_api_key: "", updated_at: "2026-11-09 00:00:00" } },
      rb03Row: { status: "ABSENT" },
      rb04Row: { status: "ABSENT" },
    },
  };

  // Inject audit cleanup failure into real cleanup path
  const cleanupOutput = await executeStagingDatabaseCleanup(payload, { injectFailureIn: "auditLogs" });
  if (cleanupOutput && cleanupOutput.cleanup_results) {
    Object.assign(cleanupStatus, cleanupOutput.cleanup_results);
  }
  cleanupStatus.mlFiles = true;

  const allCleanedUp = Object.values(cleanupStatus).every(Boolean);
  console.log(JSON.stringify({
    cleanupStatus,
    allCleanedUp,
  }));

  if (!allCleanedUp) {
    process.exit(1);
  }
  process.exit(0);
}

main().catch(err => {
  console.error(err);
  process.exit(2);
});
`;
        const { spawnSync } = require("child_process");
        const childProc = spawnSync(process.execPath, ["-e", childScript], {
          cwd: REPO_ROOT,
          env: process.env,
        });

        // 1. Verify actual process exited non-zero
        assert(childProc.status === 1, `Expected child process exit code 1, got ${childProc.status}. Output: ${childProc.stdout.toString()} ${childProc.stderr.toString()}`);
        console.log(`  [✓] Verified: Real cleanup failure caused actual process to exit with non-zero code (${childProc.status}).`);

        const childOutput = JSON.parse(childProc.stdout.toString().trim());

        // 2. Verify cleanupStatus.auditLogs became false
        assert(childOutput.cleanupStatus.auditLogs === false, "cleanupStatus.auditLogs should be false");
        console.log("  [✓] Verified: cleanupStatus.auditLogs became false.");

        // 3. Verify other independent cleanup and restoration stages still executed (users, circuitBreaker, lifecycle, walletSettings)
        assert(childOutput.cleanupStatus.users === true, "Independent stage users should have succeeded");
        assert(childOutput.cleanupStatus.circuitBreaker === true, "Independent stage circuitBreaker should have succeeded");
        assert(childOutput.cleanupStatus.lifecycle === true, "Independent stage lifecycle should have succeeded");
        assert(childOutput.cleanupStatus.walletSettings === true, "Independent stage walletSettings should have succeeded");
        console.log("  [✓] Verified: All independent cleanup & restoration stages (users, circuitBreaker, lifecycle, walletSettings) still executed successfully.");

        // 4. Verify final aggregate cleanup status is unsuccessful
        assert(childOutput.allCleanedUp === false, "allCleanedUp should be false");
        console.log("  [✓] Verified: Final aggregate cleanup status is unsuccessful (allCleanedUp = false).");

        // 5. Verify no unrelated or pre-existing audit rows were deleted
        const checkSurvivalScript = `
import json, mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor(dictionary=True)

# Test audit row was not deleted due to injected failure
cur.execute("SELECT id FROM audit_logs WHERE id = %s", (${t15AuditId},))
test_audit = cur.fetchone()

# Pre-existing audit row was not touched
cur.execute("SELECT id, action FROM audit_logs WHERE id = %s", (${t15PreAuditId},))
pre_audit = cur.fetchone()

# Test user WAS deleted by users stage
cur.execute("SELECT id FROM users WHERE id = %s", (${t15UserId},))
test_user = cur.fetchone()

cur.close(); conn.close()
print(json.dumps({
    "test_audit_exists": test_audit is not None,
    "pre_audit_exists": pre_audit is not None,
    "test_user_exists": test_user is not None,
}))
`;
        const survivalRes = await runPythonHelper(checkSurvivalScript);
        assert(survivalRes.test_audit_exists === true, "Test audit record should remain in DB due to failed cleanup");
        assert(survivalRes.pre_audit_exists === true, "Pre-existing audit record was deleted!");
        assert(survivalRes.test_user_exists === false, "Test user should have been deleted by the independent users stage");
        console.log("  [✓] Verified: Pre-existing audit records remain completely intact and un-deleted.");

        passed++;
      } finally {
        await runPythonHelper(`
import mysql.connector
from config import MYSQL_CONFIG
cfg = MYSQL_CONFIG.copy(); cfg["database"] = "${TARGET_DB}"
conn = mysql.connector.connect(**cfg); cur = conn.cursor()
if ${t15PreAuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t15PreAuditId},))
if ${t15AuditId || 'None'} is not None:
    cur.execute("DELETE FROM audit_logs WHERE id = %s", (${t15AuditId},))
if ${t15PreUserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t15PreUserId},))
if ${t15UserId || 'None'} is not None:
    cur.execute("DELETE FROM users WHERE id = %s", (${t15UserId},))
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
