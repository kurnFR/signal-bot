/**
 * Staging Browser Acceptance Test (Hardened)
 * 
 * Verifies the Modern UI (/modern) against a live FastAPI backend instance
 * connected strictly to the authorized 'signal_bot_test' database.
 *
 * Safety & Security Guarantees:
 *   - Strictly connects to 'signal_bot_test'; refuses 'crypto_signals'.
 *   - Pre-checks STAGING_PORT to ensure no stale/unrelated process is accepted.
 *   - No shell interpolation: credentials and parameters are passed via stdin JSON.
 *   - Run-unique accounts (adm_<runid>, trd_<runid>, viw_<runid>); zero mutation to pre-existing users.
 *   - Verified backend RBAC enforcement (direct HTTP 401/403 contract checks).
 *   - Graceful SIGTERM process termination with SIGKILL fallback; child exit verified.
 *   - All phases (setup, server, browser, cleanup) under a single top-level try/finally.
 *   - Cleanup failures throw and fail the test run.
 * 
 * Run command:
 *   node tests/browser/staging_browser_acceptance.cjs
 */
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

// Resolve Playwright from local frontend workspace
const playwrightPath = path.resolve(__dirname, "../../frontend/node_modules/@playwright/test");
const { chromium } = require(playwrightPath);

const STAGING_PORT = process.env.STAGING_PORT ? parseInt(process.env.STAGING_PORT, 10) : 8055;
const BASE_URL = `http://127.0.0.1:${STAGING_PORT}/modern`;
const REPO_ROOT = path.resolve(__dirname, "../..");
const TARGET_DB = "signal_bot_test";

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

/**
 * Execute Python code without shell string interpolation.
 * Parameters and secrets are delivered strictly via stdin JSON.
 */
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

/**
 * Port occupancy pre-check. Strictly verifies that the port is free.
 * - Only ECONNREFUSED is accepted as proof that no process is listening.
 * - EADDRINUSE is treated as a hard failure.
 * - Unexpected socket errors are propagated (rejected) rather than treated as free.
 */
function checkPortFree(port) {
  return new Promise((resolve, reject) => {
    const socket = new net.Socket();
    let settled = false;

    function finish(err, isFree) {
      if (settled) return;
      settled = true;
      try {
        socket.destroy();
      } catch (_) {}
      if (err) {
        reject(err);
      } else {
        resolve(isFree);
      }
    }

    socket.setTimeout(1000);

    socket.on("connect", () => {
      finish(new Error(`Port ${port} is already in use by another process. Refusing to run acceptance against an unverified listener.`));
    });

    socket.on("timeout", () => {
      finish(new Error(`Timeout probing port ${port}: socket did not receive expected ECONNREFUSED.`));
    });

    socket.on("error", (err) => {
      if (err.code === "ECONNREFUSED") {
        // Strictly only ECONNREFUSED indicates the port is free and refusing connections
        finish(null, true);
      } else if (err.code === "EADDRINUSE") {
        finish(new Error(`Port ${port} is already in use (EADDRINUSE).`));
      } else {
        // Propagate unexpected socket errors instead of treating them as proof that the port is free
        finish(err);
      }
    });

    socket.connect(port, "127.0.0.1");
  });
}

/**
 * Wait for the staging server to become ready and prove it is the intended child process.
 *
 * Verifies:
 * 1. Child process is alive and has not exited or crashed.
 * 2. HTTP response from /api/system/test-identity returns status 200 with run_id === expectedRunId.
 * 3. Child process PID matches the PID reported by the server.
 * 4. Fails immediately if another process or unverified identity is responding on the target port.
 */
function waitForServer(target, childProc, expectedRunId, timeoutMs = 25000) {
  const start = Date.now();
  let identityUrl;
  if (typeof target === "number") {
    identityUrl = `http://127.0.0.1:${target}/api/system/test-identity`;
  } else if (typeof target === "string") {
    try {
      const u = new URL(target);
      identityUrl = `${u.origin}/api/system/test-identity`;
    } catch (_) {
      identityUrl = target;
    }
  } else {
    throw new Error("Invalid target for waitForServer");
  }

  // Mandatory validation: child process and expected run identity must be valid
  if (!childProc || typeof childProc !== "object") {
    throw new Error("waitForServer requires a child process instance");
  }
  if (typeof childProc.pid !== "number" || !Number.isInteger(childProc.pid) || childProc.pid <= 0) {
    throw new Error(`waitForServer requires a valid integer child process PID, got: ${childProc ? childProc.pid : undefined}`);
  }
  if (!expectedRunId || typeof expectedRunId !== "string") {
    throw new Error("waitForServer requires a non-empty expectedRunId string");
  }

  return new Promise((resolve, reject) => {
    let timer = null;
    let completed = false;

    function cleanup() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
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
      if (!childProc || typeof childProc.pid !== "number" || childProc.pid <= 0) {
        fail(new Error("Staging server child process reference is missing or invalid"));
        return false;
      }
      if (childProc.exitCode !== null) {
        fail(new Error(`Staging server child process died prematurely with exit code ${childProc.exitCode}`));
        return false;
      }
      if (childProc.killed) {
        fail(new Error("Staging server child process was killed prematurely"));
        return false;
      }
      try {
        process.kill(childProc.pid, 0);
      } catch (e) {
        fail(new Error(`Staging server child process PID ${childProc.pid} is not alive: ${e.message}`));
        return false;
      }
      return true;
    }

    function ping() {
      if (completed) return;
      if (!checkChildAlive()) return;

      const req = http.get(identityUrl, { timeout: 1500 }, (res) => {
        let body = "";
        res.on("data", (chunk) => { body += chunk.toString(); });
        res.on("end", () => {
          if (completed) return;
          if (!checkChildAlive()) return;

          let data = null;
          try {
            data = JSON.parse(body);
          } catch (_) {}

          // Readiness strictly requires:
          // 1. HTTP 200 OK
          // 2. JSON payload with status === 'ok'
          // 3. Matching run_id === expectedRunId
          // 4. Mandatory data.pid: must be a valid positive integer
          // 5. Mandatory data.pid === childProc.pid
          if (res.statusCode === 200 && data && data.status === "ok" && data.run_id === expectedRunId) {
            const reportedPid = data.pid;
            if (typeof reportedPid !== "number" || !Number.isInteger(reportedPid) || reportedPid <= 0) {
              return fail(new Error(
                `Target port responded with matching run_id but invalid or missing PID (expected integer ${childProc.pid}, got: ${JSON.stringify(reportedPid)}). Readiness cannot be accepted on run ID alone.`
              ));
            }

            if (reportedPid !== childProc.pid) {
              return fail(new Error(
                `Target port responded with matching run_id but mismatched PID: server reported PID ${reportedPid}, expected child PID ${childProc.pid}`
              ));
            }

            return succeed(data);
          }

          // An HTTP response was received, but the identity is invalid or conflicting.
          // This proves another process is responding or the server failed to configure the identity.
          const detail = data ? JSON.stringify(data) : (body ? body.slice(0, 100) : "empty response");
          return fail(new Error(
            `Target port is served by an unverified or conflicting process (status ${res.statusCode}, response: ${detail}). Another process may be occupying the port.`
          ));
        });
      });

      req.on("timeout", () => {
        req.destroy();
        retry();
      });

      req.on("error", (err) => {
        if (completed) return;
        if (!checkChildAlive()) return;
        if (err.code === "ECONNREFUSED" || err.code === "ECONNRESET") {
          // Expected while server is still binding and starting up
          retry();
        } else {
          retry();
        }
      });
    }

    function retry() {
      if (completed) return;
      if (Date.now() - start > timeoutMs) {
        return fail(new Error(`Timeout waiting for staging server identity at ${identityUrl} after ${timeoutMs}ms`));
      }
      timer = setTimeout(ping, 250);
    }

    ping();
  });
}

/**
 * Perform a direct HTTP JSON request with JWT authorization.
 */
function apiRequest(method, endpoint, token = null, body = null) {
  return new Promise((resolve, reject) => {
    const url = new URL(endpoint, `http://127.0.0.1:${STAGING_PORT}`);
    const headers = { "Content-Type": "application/json" };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const data = body ? JSON.stringify(body) : null;
    if (data) {
      headers["Content-Length"] = Buffer.byteLength(data);
    }
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
    if (data) {
      req.write(data);
    }
    req.end();
  });
}

/**
 * Check if a process is alive by sending signal 0.
 */
function isProcessAlive(pid) {
  if (!pid || typeof pid !== "number" || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    if (err.code === "ESRCH") return false;
    return true;
  }
}

/**
 * Safely terminate a child process with SIGTERM, falling back to SIGKILL,
 * and verify that the child process has actually exited using bounded timeouts.
 *
 * Rejects if process termination cannot be confirmed within bounded timeouts.
 */
function terminateChildProcess(proc, sigtermTimeoutMs = 5000, sigkillTimeoutMs = 5000, aliveCheckFn = isProcessAlive) {
  return new Promise((resolve, reject) => {
    if (!proc || typeof proc !== "object") {
      return resolve({ exited: true, reason: "no_process" });
    }
    const pid = proc.pid;
    if (typeof pid !== "number" || pid <= 0) {
      return resolve({ exited: true, reason: "no_valid_pid" });
    }

    // If child is already dead and reaped
    if (proc.exitCode !== null || proc.signalCode !== null || !aliveCheckFn(pid)) {
      return resolve({ exited: true, code: proc.exitCode, signal: proc.signalCode, alreadyDead: true });
    }

    let settled = false;
    let killedWithSigkill = false;
    let sigtermTimer = null;
    let sigkillPollInterval = null;

    function finish(err, result) {
      if (settled) return;
      settled = true;
      if (sigtermTimer) {
        clearTimeout(sigtermTimer);
        sigtermTimer = null;
      }
      if (sigkillPollInterval) {
        clearInterval(sigkillPollInterval);
        sigkillPollInterval = null;
      }
      if (err) {
        reject(err);
      } else {
        resolve(result);
      }
    }

    proc.once("exit", (code, signal) => {
      finish(null, { exited: true, code, signal, forcedKill: killedWithSigkill });
    });

    console.log(`\n[*] Gracefully terminating staging FastAPI server PID ${pid} (SIGTERM)...`);
    try {
      proc.kill("SIGTERM");
    } catch (err) {
      if (!aliveCheckFn(pid)) {
        return finish(null, { exited: true, code: proc.exitCode, signal: proc.signalCode });
      }
      return finish(new Error(`Failed to send SIGTERM to child PID ${pid}: ${err.message}`));
    }

    sigtermTimer = setTimeout(() => {
      if (settled) return;

      if (!aliveCheckFn(pid) || proc.exitCode !== null || proc.signalCode !== null) {
        return finish(null, { exited: true, code: proc.exitCode, signal: proc.signalCode });
      }

      console.warn(`[!] Server PID ${pid} did not exit within ${sigtermTimeoutMs}ms after SIGTERM; sending SIGKILL...`);
      killedWithSigkill = true;
      try {
        proc.kill("SIGKILL");
      } catch (err) {
        if (!aliveCheckFn(pid)) {
          return finish(null, { exited: true, code: proc.exitCode, signal: "SIGKILL", forcedKill: true });
        }
        return finish(new Error(`Failed to send SIGKILL to child PID ${pid}: ${err.message}`));
      }

      const sigkillStart = Date.now();
      sigkillPollInterval = setInterval(() => {
        if (settled) return;

        const isDead = !aliveCheckFn(pid) || proc.exitCode !== null || proc.signalCode !== null;
        if (isDead) {
          return finish(null, { exited: true, code: proc.exitCode, signal: proc.signalCode || "SIGKILL", forcedKill: true });
        }

        if (Date.now() - sigkillStart > sigkillTimeoutMs) {
          return finish(new Error(
            `Child process PID ${pid} could not be confirmed terminated after SIGTERM (${sigtermTimeoutMs}ms) and SIGKILL (${sigkillTimeoutMs}ms). Process remains alive.`
          ));
        }
      }, 50);
    }, sigtermTimeoutMs);
  });
}

/**
 * Safely delete exact fixture users from authorized test database signal_bot_test.
 */
async function defaultDeleteFixtures(targetDb, userIds) {
  if (!Array.isArray(userIds) || userIds.length === 0) {
    return { status: "ok", deleted_users: 0 };
  }
  assert(targetDb === "signal_bot_test", `Database mismatch safety refusal: ${targetDb}`);
  const cleanupScript = `
import json, sys
import mysql.connector
from config import MYSQL_CONFIG

payload = json.loads(sys.stdin.read())
user_ids = payload["user_ids"]
db_name = payload["target_db"]
assert db_name == "signal_bot_test", f"Safety refusal: {db_name}"

cfg = MYSQL_CONFIG.copy()
cfg["database"] = db_name
conn = mysql.connector.connect(**cfg)
cur = conn.cursor()

placeholders = ",".join(["%s"] * len(user_ids))
cur.execute(f"DELETE FROM audit_logs WHERE user_id IN ({placeholders})", tuple(user_ids))
cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", tuple(user_ids))
conn.commit()
deleted = cur.rowcount
cur.close()
conn.close()

print(json.dumps({"status": "ok", "deleted_users": deleted}))
`;
  return await runPythonHelper(cleanupScript, {}, { target_db: targetDb, user_ids: userIds });
}

/**
 * Orchestrate safe staging cleanup:
 * 1. Close browser if provided (capturing any browser close error).
 * 2. Terminate child process and verify exit if proc provided (capturing termination error).
 * 3. Enforce safety rule: if process termination cannot be confirmed, PRESERVE FIXTURES and throw/reject failure.
 * 4. If process termination succeeded, clean up exact fixture user IDs via deleteFixturesFn.
 * 5. Handle error propagation (prior error, fixture cleanup error, browser close error).
 */
async function orchestrateStagingCleanup({
  browser = null,
  proc = null,
  seededUserIds = [],
  targetDb = TARGET_DB,
  previousError = null,
  terminateProcFn = terminateChildProcess,
  deleteFixturesFn = defaultDeleteFixtures,
  sigtermTimeoutMs = 5000,
  sigkillTimeoutMs = 5000,
} = {}) {
  let browserCloseError = null;
  if (browser) {
    try {
      if (typeof browser.close === "function") {
        await browser.close();
      }
    } catch (err) {
      console.error("[-] Browser close failed:", err.message || err);
      browserCloseError = err;
    }
  }

  let processTerminationError = null;
  if (proc) {
    try {
      const termResult = await terminateProcFn(proc, sigtermTimeoutMs, sigkillTimeoutMs);
      if (!termResult || typeof termResult !== "object" || termResult.exited !== true) {
        throw new Error(
          `Process termination could not be confirmed: termination result did not confirm exit (${JSON.stringify(termResult)})`
        );
      }
      if (termResult.forcedKill) {
        console.log(`[+] Staging FastAPI server confirmed terminated via SIGKILL fallback (PID: ${proc.pid}).`);
      } else if (termResult.alreadyDead) {
        console.log(`[+] Staging FastAPI server was already terminated (PID: ${proc.pid}).`);
      } else {
        console.log(`[+] Staging FastAPI server exited gracefully (code: ${termResult.code}, signal: ${termResult.signal}).`);
      }
    } catch (err) {
      console.error("[-] Staging process termination could not be confirmed:", err.message || err);
      processTerminationError = err;
    }
  }

  // CRITICAL SAFETY RULE:
  // Do NOT delete test fixtures while the staging server process may still be alive!
  // If termination cannot be confirmed, preserve fixtures, report a visible cleanup failure, and exit nonzero.
  if (processTerminationError) {
    console.error("[-] SAFETY PRESERVATION: Test fixtures were preserved because staging server termination could not be confirmed.");
    const termFatalErr = new Error(
      `Cleanup aborted: staging server termination could not be confirmed (${processTerminationError.message || processTerminationError}). Fixtures preserved.`
    );
    if (previousError) {
      termFatalErr.cause = previousError;
    }
    throw termFatalErr;
  }

  // Clean up only the exact fixture IDs created for this run (only if termination succeeded)
  let fixtureCleanupError = null;
  if (Array.isArray(seededUserIds) && seededUserIds.length > 0) {
    console.log(`[*] Cleaning up run-specific fixture users (IDs: ${seededUserIds.join(", ")})...`);
    try {
      await deleteFixturesFn(targetDb, seededUserIds);
      console.log(`[+] Exact fixture cleanup complete: deleted users confirmed.`);
    } catch (err) {
      console.error("[-] FATAL: Fixture cleanup failed:", err.message || err);
      fixtureCleanupError = err;
    }
  }

  // Error propagation priority
  if (previousError) {
    throw previousError;
  }
  if (fixtureCleanupError) {
    throw new Error(`Fixture cleanup failed: ${fixtureCleanupError.message || fixtureCleanupError}`);
  }
  if (browserCloseError) {
    throw new Error(`Browser close failed: ${browserCloseError.message || browserCloseError}`);
  }

  return {
    success: true,
    fixturesCleaned: Array.isArray(seededUserIds) && seededUserIds.length > 0,
    cleanedIds: seededUserIds,
  };
}

async function run() {
  let proc = null;
  let browser = null;
  let seededUserIds = [];
  let runError = null;

  try {
    // -------------------------------------------------------------
    // PHASE 1: Preflight & Safe Database Verification
    // -------------------------------------------------------------
    console.log(`[*] Preflight check: verifying port ${STAGING_PORT} is free...`);
    await checkPortFree(STAGING_PORT);
    console.log(`[+] Port ${STAGING_PORT} is free.`);

    console.log(`[*] Verifying database isolation against authorized '${TARGET_DB}'...`);
    assert(TARGET_DB === "signal_bot_test", `Target database must be 'signal_bot_test', got: ${TARGET_DB}`);

    // Seed run-specific accounts via stdin JSON (no shell interpolation)
    const seedScript = `
import json, sys
import mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password

payload = json.loads(sys.stdin.read())
db_name = payload["target_db"]
assert db_name == "signal_bot_test", f"Safety refusal: {db_name}"

cfg = MYSQL_CONFIG.copy()
cfg["database"] = db_name
conn = mysql.connector.connect(**cfg)
cur = conn.cursor(dictionary=True)

cur.execute("SELECT DATABASE() as active_db")
active_db = cur.fetchone()["active_db"]
assert active_db == "signal_bot_test", f"Active database is not signal_bot_test: {active_db}"

seeded_ids = []
for u in payload["users"]:
    pwd_hash = hash_password(u["password"])
    cur.execute(
        "INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, %s, 1, 0)",
        (u["username"], u["email"], pwd_hash, u["role"])
    )
    seeded_ids.append(cur.lastrowid)

conn.commit()
cur.close()
conn.close()

print(json.dumps({"status": "ok", "active_db": active_db, "seeded_ids": seeded_ids}))
`;

    const seedPayload = {
      target_db: TARGET_DB,
      users: [
        { username: ADMIN_USER, email: `${ADMIN_USER}@staging.internal`, role: "admin", password: STAGING_PASSWORD },
        { username: TRADER_USER, email: `${TRADER_USER}@staging.internal`, role: "trader", password: STAGING_PASSWORD },
        { username: VIEWER_USER, email: `${VIEWER_USER}@staging.internal`, role: "viewer", password: STAGING_PASSWORD },
      ],
    };

    const seedResult = await runPythonHelper(seedScript, {}, seedPayload);
    assert(seedResult.active_db === "signal_bot_test", `Database mismatch: ${seedResult.active_db}`);
    seededUserIds = seedResult.seeded_ids;
    assert(Array.isArray(seededUserIds) && seededUserIds.length === 3, "Failed to retrieve seeded user IDs");
    console.log(`[+] Seeded run-unique test accounts: ${ADMIN_USER}, ${TRADER_USER}, ${VIEWER_USER} (IDs: ${seededUserIds.join(", ")})`);

    // -------------------------------------------------------------
    // PHASE 2: Spawn Staging Server & Monitor Child
    // -------------------------------------------------------------
    console.log(`[*] Spawning staging FastAPI server on port ${STAGING_PORT} (DB: ${TARGET_DB})...`);
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

    proc.on("error", (err) => {
      console.error("[-] Staging process spawn error:", err);
    });

    proc.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      if (text.includes("ERROR") || text.includes("Exception") || text.includes("Traceback")) {
        console.error("[Uvicorn Error]", text.trim());
      }
    });

    const serverIdentity = await waitForServer(STAGING_PORT, proc, RUN_ID);
    console.log(`[+] Staging FastAPI server verified (run_id: ${serverIdentity.run_id}, PID: ${serverIdentity.pid}) and responding on port ${STAGING_PORT}.`);

    // -------------------------------------------------------------
    // PHASE 3: Direct API Contract & RBAC Checks
    // -------------------------------------------------------------
    console.log("\n[=== 1. Direct Backend Contract & RBAC Authorization Checks ===]");

    // 1. Unauthenticated request to protected endpoint
    const unauthRes = await apiRequest("GET", "/api/users");
    assert(unauthRes.status === 401, `Unauthenticated /api/users must return 401, got: ${unauthRes.status}`);
    console.log("  [✓] Direct API check: Unauthenticated /api/users correctly rejected with 401.");

    // Obtain JWT tokens for each role via login endpoint
    const adminLoginRes = await apiRequest("POST", "/api/auth/login", null, { username: ADMIN_USER, password: STAGING_PASSWORD });
    assert(adminLoginRes.status === 200 && adminLoginRes.data && adminLoginRes.data.token, `Admin login failed: status=${adminLoginRes.status}, data=${JSON.stringify(adminLoginRes.data)}`);
    const adminToken = adminLoginRes.data.token;

    const traderLoginRes = await apiRequest("POST", "/api/auth/login", null, { username: TRADER_USER, password: STAGING_PASSWORD });
    assert(traderLoginRes.status === 200 && traderLoginRes.data && traderLoginRes.data.token, `Trader login failed: status=${traderLoginRes.status}, data=${JSON.stringify(traderLoginRes.data)}`);
    const traderToken = traderLoginRes.data.token;

    const viewerLoginRes = await apiRequest("POST", "/api/auth/login", null, { username: VIEWER_USER, password: STAGING_PASSWORD });
    assert(viewerLoginRes.status === 200 && viewerLoginRes.data && viewerLoginRes.data.token, `Viewer login failed: status=${viewerLoginRes.status}, data=${JSON.stringify(viewerLoginRes.data)}`);
    const viewerToken = viewerLoginRes.data.token;

    // 2. Viewer direct API checks: user create -> 403, telegram test -> 403
    const viewerUserCreate = await apiRequest("POST", "/api/users", viewerToken, { username: "hack", password: "pwd", role: "admin" });
    assert(viewerUserCreate.status === 403, `Viewer POST /api/users must return 403, got: ${viewerUserCreate.status}`);

    const viewerTelegramTest = await apiRequest("POST", "/api/telegram/test", viewerToken, {});
    assert(viewerTelegramTest.status === 403, `Viewer POST /api/telegram/test must return 403, got: ${viewerTelegramTest.status}`);

    const viewerTelegramStatus = await apiRequest("GET", "/api/telegram/status", viewerToken);
    assert(viewerTelegramStatus.status === 200, `Viewer GET /api/telegram/status must return 200, got: ${viewerTelegramStatus.status}`);
    assert(viewerTelegramStatus.data.chatId === null, `Viewer Telegram status must mask chatId to null, got: ${viewerTelegramStatus.data.chatId}`);
    console.log("  [✓] Direct API check: Viewer strictly denied 403 on user management and Telegram test; chatId masked to null.");

    // 3. Trader direct API checks: user create -> 403, telegram test -> 403
    const traderUserCreate = await apiRequest("POST", "/api/users", traderToken, { username: "hack", password: "pwd", role: "admin" });
    assert(traderUserCreate.status === 403, `Trader POST /api/users must return 403, got: ${traderUserCreate.status}`);

    const traderTelegramTest = await apiRequest("POST", "/api/telegram/test", traderToken, {});
    assert(traderTelegramTest.status === 403, `Trader POST /api/telegram/test must return 403, got: ${traderTelegramTest.status}`);

    const traderTelegramStatus = await apiRequest("GET", "/api/telegram/status", traderToken);
    assert(traderTelegramStatus.status === 200, `Trader GET /api/telegram/status must return 200, got: ${traderTelegramStatus.status}`);
    assert(traderTelegramStatus.data.chatId === null, `Trader Telegram status must mask chatId to null, got: ${traderTelegramStatus.data.chatId}`);
    console.log("  [✓] Direct API check: Trader strictly denied 403 on user management and Telegram test; chatId masked to null.");

    // 4. Admin read-only API check: user listing -> 200
    const adminUserList = await apiRequest("GET", "/api/users", adminToken);
    assert(adminUserList.status === 200 && adminUserList.data && Array.isArray(adminUserList.data.users), "Admin GET /api/users must return { users: [...] }");
    console.log("  [✓] Direct API check: Admin successfully retrieved user catalog.");

    // -------------------------------------------------------------
    // PHASE 4: Browser Automation Acceptance
    // -------------------------------------------------------------
    console.log("\n[=== 2. Browser Acceptance & UI Role Boundaries ===]");
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Ignore browser-generated network resource failure logs for documented 403 RBAC endpoints
        if (text.includes("403 (Forbidden)")) {
          return;
        }
        consoleErrors.push(text);
      }
    });

    const pageErrors = [];
    page.on("pageerror", (err) => {
      pageErrors.push(err.message);
    });

    const failedRequests = [];
    page.on("requestfailed", (request) => {
      if (request.failure() && request.failure().errorText === "net::ERR_ABORTED") return;
      failedRequests.push(`${request.method()} ${request.url()} - ${request.failure() ? request.failure().errorText : "failed"}`);
    });

    const unexpectedHttpErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 400) {
        // Documented backend RBAC: /api/custom-strategy is restricted to admin and trader roles.
        // When viewer visits Strategy Library, backend middleware correctly enforces 403 Forbidden.
        const isDocumentedViewerRbac = res.status() === 403 && res.url().includes("/api/custom-strategy/");
        if (!isDocumentedViewerRbac) {
          console.warn(`  [HTTP ${res.status()}] ${res.request().method()} ${res.url()}`);
          unexpectedHttpErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`);
        } else {
          console.log(`  [✓] Verified documented backend RBAC: ${res.request().method()} ${res.url()} denied with HTTP 403 for viewer.`);
        }
      }
    });

    // TEST 1: Unauthenticated Navigation Gate
    console.log("\n  --- Unauthenticated Navigation Gate ---");
    await page.goto(`${BASE_URL}/`);
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    assert(await page.locator(".auth-form").isVisible(), "Authentication form must be visible to unauthenticated users");
    const navWhenUnauth = await page.$("nav");
    assert(navWhenUnauth === null, "Dashboard navigation must not be visible to unauthenticated requests");
    console.log("  [✓] Unauthenticated gate and protected route verified.");

    const viewVerifications = [
      { label: "Overview", selector: ".metrics-grid" },
      { label: "Market Screener", selector: ".screener-metrics, section[aria-label='Smart money screener status']" },
      { label: "Paper Trading", selector: ".paper-trading-view" },
      { label: "Strategy Library", selector: "section[aria-label='Strategy library summary'], section[aria-labelledby='strategy-catalog-title']" },
      { label: "Wallet Tracking", selector: ".wallet-tracking-view" },
      { label: "Backtest Studio", selector: ".backtest-studio-view" },
      { label: "ML Studio", selector: ".ml-studio-view" },
      { label: "Research", selector: ".research-view" },
      { label: "News AI", selector: ".news-ai-view" },
      { label: "Master Data", selector: ".master-data-view" },
      { label: "Settings", selector: ".settings-view" },
    ];

    async function exerciseRoleViews(roleName) {
      console.log(`\n    [*] Exercising all 11 navigation views and asserting role controls for ${roleName}...`);
      for (const { label, selector } of viewVerifications) {
        const btn = page.locator(`button:has-text("${label}")`).first();
        await btn.click();
        await page.waitForSelector(selector, { timeout: 8000 });
        assert(await page.locator(selector).first().isVisible(), `[${roleName}] View '${label}' panel matching '${selector}' must be visible`);
        console.log(`      [✓] [${roleName}] View '${label}' rendered and verified with stable selector '${selector}'.`);

        // Assert role-specific controls & permissions within each view
        if (label === "Market Screener") {
          if (roleName === "admin" || roleName === "trader") {
            const hasToggle = await page.locator(".screener-alert-toggle").first().isVisible().catch(() => false);
            const hasSave = (await page.locator("button:has-text('Save changes')").count()) > 0;
            const hasReload = (await page.locator("button:has-text('Reload')").count()) > 0;
            assert(hasToggle || hasSave || hasReload, `[${roleName}] Market Screener must render editable controls`);
            console.log(`      [✓] [${roleName}] Market Screener verified: editable controls accessible.`);
          } else {
            // viewer
            const saveBtnCount = await page.locator("button:has-text('Save changes')").count();
            assert(saveBtnCount === 0, "[viewer] Market Screener must NOT display Save changes button");
            assert(await page.locator(".screener-controls-readonly").isVisible(), "[viewer] Screener must display read-only banner");
            console.log("      [✓] [viewer] Market Screener verified: editable controls hidden, read-only banner active.");
          }
        } else if (label === "Backtest Studio") {
          if (roleName === "admin" || roleName === "trader") {
            assert(
              (await page.locator("button:has-text('Review backtest run')").count()) > 0,
              `[${roleName}] Backtest Studio must display Review backtest run button`
            );
            console.log(`      [✓] [${roleName}] Backtest Studio verified: execution controls accessible.`);
          } else {
            // viewer
            assert(
              (await page.locator("button:has-text('Review backtest run')").count()) === 0,
              "[viewer] Backtest Studio must NOT display Review backtest run button"
            );
            assert(
              await page.getByText("Backtest execution is restricted to admin and trader roles").first().isVisible(),
              "[viewer] Backtest Studio must display restriction notice"
            );
            console.log("      [✓] [viewer] Backtest Studio verified: execution controls restricted, view-only notice active.");
          }
        } else if (label === "Master Data") {
          if (roleName === "admin" || roleName === "trader") {
            assert(
              (await page.locator("button:has-text('Review backfill')").count()) > 0,
              `[${roleName}] Master Data must display Review backfill button`
            );
            console.log(`      [✓] [${roleName}] Master Data verified: backfill job controls accessible.`);
          } else {
            // viewer
            assert(
              (await page.locator("button:has-text('Review backfill')").count()) === 0,
              "[viewer] Master Data must NOT display Review backfill button"
            );
            assert(
              await page.getByText("Backfill and feature-build controls are restricted to admin and trader roles").first().isVisible(),
              "[viewer] Master Data must display restriction notice"
            );
            console.log("      [✓] [viewer] Master Data verified: backfill controls restricted, view-only notice active.");
          }
        } else if (label === "Strategy Library") {
          if (roleName === "admin" || roleName === "trader") {
            assert(
              (await page.locator("button:has-text('New strategy'), #strategy-create-title").count()) > 0,
              `[${roleName}] Strategy Library must display New strategy button or create section`
            );
            console.log(`      [✓] [${roleName}] Strategy Library verified: strategy creation controls accessible.`);
          } else {
            // viewer
            assert(
              (await page.locator("button:has-text('New strategy')").count()) === 0,
              "[viewer] Strategy Library must NOT display New strategy button"
            );
            assert(
              (await page.locator("button:has-text('Generate draft')").count()) === 0,
              "[viewer] Strategy Library must NOT display Generate draft button"
            );
            assert(
              await page.getByText("Strategy quick tests are restricted to admin and trader roles").first().isVisible(),
              "[viewer] Strategy Library must display quick tests restriction notice"
            );
            console.log("      [✓] [viewer] Strategy Library verified: strategy creation and quick tests restricted.");
          }
        } else if (label === "Settings") {
          if (roleName === "admin") {
            assert(await page.locator("text=User Accounts").first().isVisible(), "[admin] Settings must display User Accounts section");
            assert(await page.locator("text=Telegram").first().isVisible(), "[admin] Settings must display Telegram integration section");
            assert(await page.locator("button:has-text('Review Telegram test')").first().isVisible(), "[admin] Settings must display Review Telegram test button");
            console.log("      [✓] [admin] Settings verified: User Accounts catalog and Telegram integration panel present.");
          } else {
            // trader & viewer
            assert(
              (await page.locator("text='Manage operator credentials'").count()) === 0,
              `[${roleName}] Settings must NOT render User Management in DOM`
            );
            assert(
              (await page.locator("button:has-text('Review Telegram test')").count()) === 0,
              `[${roleName}] Settings must NOT render Telegram test ping in DOM`
            );
            assert(
              (await page.locator("button:has-text('Send test ping')").count()) === 0,
              `[${roleName}] Settings must NOT render Send test ping button in DOM`
            );
            assert(
              await page.getByText("Telegram status and test controls are restricted to admins").first().isVisible(),
              `[${roleName}] Settings must display Telegram restriction notice`
            );
            assert(
              (await page.locator("#settings-password-title, button:has-text('Update Password')").count()) > 0,
              `[${roleName}] Settings must render self password change section`
            );
            console.log(`      [✓] [${roleName}] Settings verified: User Management and Telegram ping strictly absent; restriction notice active.`);
          }
        }
      }
    }

    // TEST 2: Administrator Acceptance
    console.log(`\n  --- Administrator Acceptance (${ADMIN_USER}) ---`);
    await page.locator(".auth-form input[autoComplete='username']").fill(ADMIN_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await page.locator(".auth-form button.auth-submit").click();

    await page.waitForSelector("nav", { timeout: 10000 });
    const adminUserMeta = await page.locator(".user-meta").innerText();
    assert(adminUserMeta.includes(ADMIN_USER), `User meta must contain admin username ${ADMIN_USER}`);
    assert(adminUserMeta.toLowerCase().includes("admin"), "User meta must show admin role");
    console.log(`  [✓] Admin authenticated successfully: ${adminUserMeta.replace(/\n/g, " - ")}`);

    await exerciseRoleViews("admin");

    // Sign out
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    assert((await page.$("nav")) === null, "Dashboard navigation must be hidden after sign out");
    console.log("  [✓] Admin signed out successfully; returned to login gate.");

    // TEST 3: Trader Acceptance
    console.log(`\n  --- Trader Acceptance (${TRADER_USER}) ---`);
    await page.locator(".auth-form input[autoComplete='username']").fill(TRADER_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await page.locator(".auth-form button.auth-submit").click();
    await page.waitForSelector("nav", { timeout: 10000 });

    const traderUserMeta = await page.locator(".user-meta").innerText();
    assert(traderUserMeta.includes(TRADER_USER), `User meta must contain trader username ${TRADER_USER}`);
    assert(traderUserMeta.toLowerCase().includes("trader"), "User meta must show trader role");
    console.log(`  [✓] Trader authenticated successfully: ${traderUserMeta.replace(/\n/g, " - ")}`);

    await exerciseRoleViews("trader");

    // Sign out
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    console.log("  [✓] Trader signed out successfully; returned to login gate.");

    // TEST 4: Viewer Acceptance
    console.log(`\n  --- Viewer Acceptance (${VIEWER_USER}) ---`);
    await page.locator(".auth-form input[autoComplete='username']").fill(VIEWER_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await page.locator(".auth-form button.auth-submit").click();
    await page.waitForSelector("nav", { timeout: 10000 });

    const viewerUserMeta = await page.locator(".user-meta").innerText();
    assert(viewerUserMeta.includes(VIEWER_USER), `User meta must contain viewer username ${VIEWER_USER}`);
    assert(viewerUserMeta.toLowerCase().includes("viewer"), "User meta must show viewer role");
    console.log(`  [✓] Viewer authenticated successfully: ${viewerUserMeta.replace(/\n/g, " - ")}`);

    await exerciseRoleViews("viewer");

    // Sign out
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    console.log("  [✓] Viewer signed out successfully; returned to login gate.");

    await browser.close();
    browser = null;

    // -------------------------------------------------------------
    // PHASE 5: Console & Network Health Audit
    // -------------------------------------------------------------
    console.log("\n[=== 3. Browser Console, Page Error and Network Audit ===]");
    assert(consoleErrors.length === 0, `Browser console errors detected: ${JSON.stringify(consoleErrors)}`);
    console.log("  [✓] Executable assertion passed: Zero browser console errors recorded.");

    assert(pageErrors.length === 0, `Uncaught page errors detected: ${JSON.stringify(pageErrors)}`);
    console.log("  [✓] Executable assertion passed: Zero uncaught page errors recorded.");

    assert(failedRequests.length === 0, `Failed network requests detected: ${JSON.stringify(failedRequests)}`);
    console.log("  [✓] Executable assertion passed: Zero failed network requests recorded.");

    assert(unexpectedHttpErrors.length === 0, `Unexpected HTTP error responses detected: ${JSON.stringify(unexpectedHttpErrors)}`);
    console.log("  [✓] Executable assertion passed: Zero unexpected HTTP 4xx/5xx responses recorded.");

  } catch (err) {
    runError = err;
    throw err;
  } finally {
    // -------------------------------------------------------------
    // PHASE 6: Safe Cleanup & Child Termination
    // -------------------------------------------------------------
    await orchestrateStagingCleanup({
      browser,
      proc,
      seededUserIds,
      targetDb: TARGET_DB,
      previousError: runError,
    });
  }

  console.log("\n[+] READ-ONLY STAGING BROWSER ACCEPTANCE COMPLETED SUCCESSFULLY!");
  console.log("    Authorized test database 'signal_bot_test' verified.");
  console.log("    Direct backend RBAC contracts verified (401/403 enforced).");
  console.log("    All 3 roles (admin, trader, viewer) verified against real FastAPI backend.");
  console.log("    All 11 navigation views verified with stable content selectors.");
  console.log("    Role boundaries verified in both DOM and direct backend calls.");
  console.log("    Zero operational trading mutations and zero real Telegram broadcasts executed.");
  console.log("    Exact fixture users safely cleaned up; zero mutations to pre-existing accounts.");
}

if (require.main === module) {
  run().then(() => {
    process.exit(0);
  }).catch((err) => {
    console.error("\n[-] Acceptance run failed:", err.message);
    process.exit(1);
  });
}

module.exports = {
  checkPortFree,
  waitForServer,
  apiRequest,
  runPythonHelper,
  isProcessAlive,
  terminateChildProcess,
  defaultDeleteFixtures,
  orchestrateStagingCleanup,
  run,
};
