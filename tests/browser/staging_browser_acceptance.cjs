/**
 * Staging Browser Acceptance Test
 * 
 * Verifies the Modern UI (/modern) against a live FastAPI backend instance
 * connected to a dedicated disposable MariaDB database created specifically
 * for this acceptance run.
 * 
 * Run command:
 *   node tests/browser/staging_browser_acceptance.cjs
 * 
 * Requirements:
 *   - Local MariaDB service running.
 *   - Python 3 with requirements installed in active virtualenv/environment.
 *   - Node.js dependencies installed in frontend/node_modules.
 */
const { spawn, execSync } = require("node:child_process");
const crypto = require("node:crypto");
const http = require("node:http");
const path = require("node:path");

// Resolve Playwright from local frontend workspace
const playwrightPath = path.resolve(__dirname, "../../frontend/node_modules/@playwright/test");
const { chromium } = require(playwrightPath);

const STAGING_PORT = process.env.STAGING_PORT ? parseInt(process.env.STAGING_PORT, 10) : 8055;
const BASE_URL = `http://127.0.0.1:${STAGING_PORT}/modern`;
const REPO_ROOT = path.resolve(__dirname, "../..");

const RUN_ID = crypto.randomBytes(4).toString("hex");
const DISPOSABLE_DB = `signal_bot_ui_acceptance_${Date.now()}_${RUN_ID}`;
const STAGING_PASSWORD = process.env.STAGING_TEST_PASSWORD || `Stg_${crypto.randomBytes(8).toString("hex")}!Aa1`;

const ADMIN_USER = `adm_${RUN_ID}`;
const TRADER_USER = `trd_${RUN_ID}`;
const VIEWER_USER = `viw_${RUN_ID}`;

function assert(condition, message) {
  if (!condition) {
    throw new Error(`[ASSERTION FAILED] ${message}`);
  }
}

function waitForServer(url, timeoutMs = 25000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    function ping() {
      http.get(url, (res) => {
        if (res.statusCode === 200) {
          resolve(true);
        } else {
          retry();
        }
      }).on("error", () => {
        retry();
      });
    }
    function retry() {
      if (Date.now() - start > timeoutMs) {
        reject(new Error(`Timeout waiting for staging server at ${url}`));
      } else {
        setTimeout(ping, 400);
      }
    }
    ping();
  });
}

function setupDisposableDatabase() {
  console.log(`[*] Initializing dedicated disposable database '${DISPOSABLE_DB}'...`);

  // Hard safety boundary check: refuse production and legacy test databases
  assert(
    DISPOSABLE_DB.startsWith("signal_bot_ui_acceptance_"),
    `Invalid disposable database prefix: ${DISPOSABLE_DB}`
  );
  assert(
    DISPOSABLE_DB !== "crypto_signals" && DISPOSABLE_DB !== "signal_bot_test",
    `Prohibited database name: ${DISPOSABLE_DB}`
  );

  const pythonScript = `
import subprocess, sys
import mysql.connector
from config import MYSQL_CONFIG
from web.auth import hash_password

# 1. Connect using existing config and create the dedicated disposable DB
root_cfg = MYSQL_CONFIG.copy()
conn = mysql.connector.connect(**root_cfg)
cur = conn.cursor()
cur.execute("CREATE DATABASE ${DISPOSABLE_DB}")
cur.close()
conn.close()

# 2. Run migrations against the disposable DB
mig_env = {k: str(v) for k, v in MYSQL_CONFIG.items()}
mig_env.update({"MYSQL_DATABASE": "${DISPOSABLE_DB}", "PATH": "/bin:/usr/bin"})
res = subprocess.run([sys.executable, "-m", "db.migrate"], env=mig_env, capture_output=True, text=True, check=True)

# 3. Verify resolved database configuration
target_cfg = MYSQL_CONFIG.copy()
target_cfg["database"] = "${DISPOSABLE_DB}"
target_conn = mysql.connector.connect(**target_cfg)
target_cur = target_conn.cursor(dictionary=True)
target_cur.execute("SELECT DATABASE() as active_db")
active_db = target_cur.fetchone()["active_db"]
assert active_db == "${DISPOSABLE_DB}", f"Resolved DB mismatch: {active_db} != ${DISPOSABLE_DB}"

# 4. Seed run-specific staging test accounts
pwd_hash = hash_password("${STAGING_PASSWORD}")
users = [
    ('${ADMIN_USER}', '${ADMIN_USER}@staging.internal', 'admin'),
    ('${TRADER_USER}', '${TRADER_USER}@staging.internal', 'trader'),
    ('${VIEWER_USER}', '${VIEWER_USER}@staging.internal', 'viewer'),
]
for username, email, role in users:
    target_cur.execute(
        "INSERT INTO users (username, email, password_hash, role, is_active, must_change_password) VALUES (%s, %s, %s, %s, 1, 0)",
        (username, email, pwd_hash, role)
    )
target_conn.commit()
target_cur.close()
target_conn.close()
print("Dedicated disposable database and staging accounts successfully created.")
`;

  execSync(`python3 -c "${pythonScript.replace(/"/g, '\\"')}"`, { cwd: REPO_ROOT, stdio: "inherit" });
}

function tearDownDisposableDatabase() {
  console.log(`[*] Dropping dedicated disposable database '${DISPOSABLE_DB}'...`);
  if (!DISPOSABLE_DB.startsWith("signal_bot_ui_acceptance_") || DISPOSABLE_DB === "crypto_signals" || DISPOSABLE_DB === "signal_bot_test") {
    console.error(`[-] Safety check failed: refused to drop database '${DISPOSABLE_DB}'`);
    return;
  }

  const pythonScript = `
import mysql.connector
from config import MYSQL_CONFIG

root_cfg = MYSQL_CONFIG.copy()
conn = mysql.connector.connect(**root_cfg)
cur = conn.cursor()
cur.execute("DROP DATABASE IF EXISTS ${DISPOSABLE_DB}")
cur.close()
conn.close()
print("Dedicated disposable database '${DISPOSABLE_DB}' dropped cleanly.")
`;
  try {
    execSync(`python3 -c "${pythonScript.replace(/"/g, '\\"')}"`, { cwd: REPO_ROOT, stdio: "inherit" });
  } catch (err) {
    console.error("[-] Failed to drop disposable database:", err);
  }
}

async function run() {
  setupDisposableDatabase();

  console.log(`[*] Spawning staging FastAPI server on port ${STAGING_PORT} (DB: ${DISPOSABLE_DB})...`);
  const env = {
    ...process.env,
    MYSQL_DATABASE: DISPOSABLE_DB,
    WEB_PORT: String(STAGING_PORT),
    SIGNAL_BOT_HOST: "127.0.0.1",
  };

  const proc = spawn("python3", ["-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", String(STAGING_PORT)], {
    cwd: REPO_ROOT,
    env,
  });

  proc.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    if (text.includes("ERROR") || text.includes("Exception") || text.includes("Traceback")) {
      console.error("[Uvicorn Error]", text);
    }
  });

  let browser;

  try {
    await waitForServer(`http://127.0.0.1:${STAGING_PORT}/modern/`);
    console.log(`[+] Staging FastAPI server is healthy and responding on port ${STAGING_PORT}.`);

    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    const pageErrors = [];
    page.on("pageerror", (err) => {
      pageErrors.push(err.message);
    });

    const failedRequests = [];
    page.on("requestfailed", (request) => {
      // Don't report aborted requests caused by intentional navigation / page unmount
      if (request.failure() && request.failure().errorText === "net::ERR_ABORTED") return;
      failedRequests.push(`${request.method()} ${request.url()} - ${request.failure() ? request.failure().errorText : "failed"}`);
    });

    const unexpectedHttpErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 400) {
        console.warn(`  [HTTP ${res.status()}] ${res.request().method()} ${res.url()}`);
        unexpectedHttpErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`);
      }
    });

    // -------------------------------------------------------------
    // TEST 1: Unauthenticated Navigation & Protected Route Gate
    // -------------------------------------------------------------
    console.log("\n[=== 1. Testing Unauthenticated Gate & Protected Route Behavior ===]");
    await page.goto(`${BASE_URL}/`);
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    assert(await page.locator(".auth-form").isVisible(), "Authentication form must be visible to unauthenticated users");
    const navWhenUnauthenticated = await page.$("nav");
    assert(navWhenUnauthenticated === null, "Dashboard navigation must not be visible to unauthenticated requests");
    console.log("  [✓] Unauthenticated gate and protected route verified.");

    // -------------------------------------------------------------
    // TEST 2: Administrator Acceptance
    // -------------------------------------------------------------
    console.log(`\n[=== 2. Testing Administrator Acceptance (username: ${ADMIN_USER}) ===]`);
    await page.locator(".auth-form input[autoComplete='username']").fill(ADMIN_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await page.locator(".auth-form button.auth-submit").click();

    await page.waitForSelector("nav", { timeout: 10000 });
    const adminUserMeta = await page.locator(".user-meta").innerText();
    assert(adminUserMeta.includes(ADMIN_USER), `User meta must contain admin username ${ADMIN_USER}`);
    assert(adminUserMeta.toLowerCase().includes("admin"), "User meta must show admin role");
    console.log(`  [✓] Admin authenticated successfully: ${adminUserMeta.replace(/\n/g, " - ")}`);

    const viewVerifications = [
      { label: "Overview", selector: ".metrics-grid" },
      { label: "Master Data", selector: ".master-data-view" },
      { label: "Backtest Studio", selector: ".backtest-studio-view" },
      { label: "Paper Trading", selector: ".paper-trading-view" },
      { label: "ML Studio", selector: ".ml-studio-view" },
      { label: "Strategy Library", selector: "section[aria-label='Strategy library summary'], section[aria-labelledby='strategy-catalog-title']" },
      { label: "News AI", selector: ".news-ai-view" },
      { label: "Research", selector: ".research-view" },
      { label: "Market Screener", selector: ".screener-metrics, section[aria-label='Smart money screener status']" },
      { label: "Wallet Tracking", selector: ".wallet-tracking-view" },
      { label: "Settings", selector: ".settings-view" },
    ];

    for (const { label, selector } of viewVerifications) {
      const btn = page.locator(`button:has-text("${label}")`).first();
      await btn.click();
      await page.waitForSelector(selector, { timeout: 8000 });
      assert(await page.locator(selector).first().isVisible(), `View '${label}' panel matching '${selector}' must be visible`);
      console.log(`  [✓] View '${label}' rendered and verified with stable selector '${selector}'.`);
    }

    // Settings view admin inspection
    console.log("  [*] Verifying Admin Settings capabilities...");
    const adminSettingsContent = await page.content();
    assert(
      adminSettingsContent.includes("Manage operator credentials") || adminSettingsContent.includes("User Accounts"),
      "Admin Settings must display User Accounts catalog"
    );
    assert(
      adminSettingsContent.includes("Telegram Alerts") || adminSettingsContent.includes("Telegram status and test"),
      "Admin Settings must display Telegram integration panel"
    );
    console.log("  [✓] Admin Settings verified: User Accounts catalog and Telegram integration panel present.");

    // Sign out
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    assert((await page.$("nav")) === null, "Dashboard navigation must be hidden after sign out");
    console.log("  [✓] Admin signed out successfully; returned to login gate.");

    // -------------------------------------------------------------
    // TEST 3: Trader Acceptance
    // -------------------------------------------------------------
    console.log(`\n[=== 3. Testing Trader Acceptance (username: ${TRADER_USER}) ===]`);
    await page.locator(".auth-form input[autoComplete='username']").fill(TRADER_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await page.locator(".auth-form button.auth-submit").click();
    await page.waitForSelector("nav", { timeout: 10000 });
    const traderUserMeta = await page.locator(".user-meta").innerText();
    assert(traderUserMeta.includes(TRADER_USER), `User meta must contain trader username ${TRADER_USER}`);
    assert(traderUserMeta.toLowerCase().includes("trader"), "User meta must show trader role");
    console.log(`  [✓] Trader authenticated successfully: ${traderUserMeta.replace(/\n/g, " - ")}`);

    // Market Screener: Trader can access screener
    await page.locator('button:has-text("Market Screener")').first().click();
    await page.waitForSelector(".screener-metrics, section[aria-label='Smart money screener status']", { timeout: 8000 });
    console.log("  [✓] Trader accessed Market Screener; signals view rendered.");

    // Settings view: User administration and test Telegram must NOT be accessible
    await page.locator('button:has-text("Settings")').first().click();
    await page.waitForSelector(".settings-view", { timeout: 8000 });
    const traderSettingsContent = await page.content();
    assert(
      !traderSettingsContent.includes("Manage operator credentials"),
      "Trader Settings must NOT display User Management section"
    );
    assert(
      !traderSettingsContent.includes("Ping Bot") && !traderSettingsContent.includes("Confirm Telegram test ping"),
      "Trader Settings must NOT display Admin-only Telegram test button"
    );
    console.log("  [✓] Trader Settings verified: User Management and Telegram test ping are strictly hidden.");

    // Sign out
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    assert((await page.$("nav")) === null, "Dashboard navigation must be hidden after trader sign out");
    console.log("  [✓] Trader signed out successfully; returned to login gate.");

    // -------------------------------------------------------------
    // TEST 4: Viewer Acceptance
    // -------------------------------------------------------------
    console.log(`\n[=== 4. Testing Viewer Acceptance (username: ${VIEWER_USER}) ===]`);
    await page.locator(".auth-form input[autoComplete='username']").fill(VIEWER_USER);
    await page.locator(".auth-form input[type='password']").fill(STAGING_PASSWORD);
    await page.locator(".auth-form button.auth-submit").click();
    await page.waitForSelector("nav", { timeout: 10000 });
    const viewerUserMeta = await page.locator(".user-meta").innerText();
    assert(viewerUserMeta.includes(VIEWER_USER), `User meta must contain viewer username ${VIEWER_USER}`);
    assert(viewerUserMeta.toLowerCase().includes("viewer"), "User meta must show viewer role");
    console.log(`  [✓] Viewer authenticated successfully: ${viewerUserMeta.replace(/\n/g, " - ")}`);

    // Market Screener: editable controls form should be hidden
    await page.locator('button:has-text("Market Screener")').first().click();
    await page.waitForSelector(".screener-metrics, section[aria-label='Smart money screener status']", { timeout: 8000 });
    const viewerScreenerContent = await page.content();
    assert(
      !viewerScreenerContent.includes("Save changes"),
      "Viewer Market Screener must NOT display editable controls save button"
    );
    console.log("  [✓] Viewer Market Screener verified: editable controls hidden.");

    // Settings view: User Accounts hidden, Telegram test ping hidden
    await page.locator('button:has-text("Settings")').first().click();
    await page.waitForSelector(".settings-view", { timeout: 8000 });
    const viewerSettingsContent = await page.content();
    assert(
      !viewerSettingsContent.includes("Manage operator credentials"),
      "Viewer Settings must NOT display User Management section"
    );
    assert(
      !viewerSettingsContent.includes("Ping Bot") && !viewerSettingsContent.includes("Confirm Telegram test ping"),
      "Viewer Settings must NOT display Telegram test ping button"
    );
    console.log("  [✓] Viewer Settings verified: User Accounts and Telegram test ping are strictly hidden.");

    // Sign out
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector(".auth-form", { timeout: 10000 });
    assert((await page.$("nav")) === null, "Dashboard navigation must be hidden after viewer sign out");
    console.log("  [✓] Viewer signed out successfully; returned to login gate.");

    await browser.close();
    browser = null;

    // -------------------------------------------------------------
    // TEST 5: Browser Console, Page Error & Network Failure Audit
    // -------------------------------------------------------------
    console.log("\n[=== 5. Browser Console, Page Error and Network Audit ===]");
    assert(
      consoleErrors.length === 0,
      `Browser console errors detected: ${JSON.stringify(consoleErrors)}`
    );
    console.log("  [✓] Executable assertion passed: Zero browser console errors recorded.");

    assert(
      pageErrors.length === 0,
      `Uncaught page errors detected: ${JSON.stringify(pageErrors)}`
    );
    console.log("  [✓] Executable assertion passed: Zero uncaught page errors recorded.");

    assert(
      failedRequests.length === 0,
      `Failed network requests detected: ${JSON.stringify(failedRequests)}`
    );
    console.log("  [✓] Executable assertion passed: Zero failed network requests recorded.");

    assert(
      unexpectedHttpErrors.length === 0,
      `Unexpected HTTP error responses detected: ${JSON.stringify(unexpectedHttpErrors)}`
    );
    console.log("  [✓] Executable assertion passed: Zero unexpected HTTP 4xx/5xx responses recorded.");

    console.log("\n[+] READ-ONLY STAGING BROWSER ACCEPTANCE COMPLETED SUCCESSFULLY!");
    console.log("    All 3 roles (admin, trader, viewer) verified against real FastAPI backend.");
    console.log("    All 11 navigation views verified with stable content selectors.");
    console.log("    Role boundaries verified (admin user mgmt visible; trader/viewer user mgmt hidden).");
    console.log("    Zero operational trading mutations and zero real Telegram broadcasts executed.");

  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }

    console.log("[*] Gracefully terminating staging FastAPI server (SIGTERM)...");
    proc.kill("SIGTERM");
    const exitPromise = new Promise((resolve) => proc.on("exit", (code, signal) => resolve({ code, signal })));
    const timeoutPromise = new Promise((resolve) => setTimeout(() => resolve("timeout"), 5000));
    const termResult = await Promise.race([exitPromise, timeoutPromise]);

    if (termResult === "timeout") {
      console.warn("[!] Server did not exit in 5s; sending SIGKILL...");
      proc.kill("SIGKILL");
    } else {
      console.log(`[+] Staging FastAPI server exited gracefully (code: ${termResult.code}, signal: ${termResult.signal}).`);
    }

    // Dispose the dedicated database
    tearDownDisposableDatabase();
  }
}

run().then(() => {
  process.exit(0);
}).catch((err) => {
  console.error("[-] Fatal error during staging browser acceptance:", err);
  process.exit(1);
});
