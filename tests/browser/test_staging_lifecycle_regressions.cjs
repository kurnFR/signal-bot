/**
 * Staging Lifecycle and Server Identity Regression Tests
 *
 * Verifies that the staging server readiness and port safety mechanisms
 * deterministically prove the intended child process is serving the application.
 *
 * Covers:
 *   1. Unrelated server already occupying the port (fails port check and waitForServer).
 *   2. Child process starts but serves wrong / unverified identity (fails fast).
 *   3. Child process exits prematurely before readiness (fails fast on process exit).
 *   4. Unexpected socket error handled strictly (propagated, never assumed free).
 *   5. Successful readiness verified against the intended child process (matching run_id & PID).
 *
 * Run command:
 *   node tests/browser/test_staging_lifecycle_regressions.cjs
 */
const http = require("node:http");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { checkPortFree, waitForServer } = require("./staging_browser_acceptance.cjs");

function assert(condition, message) {
  if (!condition) {
    throw new Error(`[ASSERTION FAILED] ${message}`);
  }
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

async function runTests() {
  console.log("[*] Starting Staging Server Lifecycle & Identity Regression Tests...\n");
  let passed = 0;

  // --------------------------------------------------------------------------
  // TEST 1: Unrelated server already occupying the port
  // --------------------------------------------------------------------------
  console.log("=== Test 1: Unrelated server already occupying target port ===");
  {
    const port = await getAvailablePort();
    const unrelatedServer = http.createServer((req, res) => {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end("<html><body>Unrelated Service</body></html>");
    });

    await new Promise((resolve) => unrelatedServer.listen(port, "127.0.0.1", resolve));

    // 1A. checkPortFree must reject
    let portCheckFailed = false;
    try {
      await checkPortFree(port);
    } catch (err) {
      portCheckFailed = true;
      assert(err.message.includes("already in use"), `Unexpected error message: ${err.message}`);
      console.log("  [✓] checkPortFree correctly rejected occupied port:", err.message);
    }
    assert(portCheckFailed, "checkPortFree should have rejected an occupied port");

    // 1B. waitForServer must reject even if child process is alive
    const dummyChild = spawn("node", ["-e", "setInterval(() => {}, 1000)"]);
    let waitFailed = false;
    try {
      await waitForServer(port, dummyChild, "expected-run-id-111", 2000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("unverified or conflicting process") || err.message.includes("occupying"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer correctly rejected unrelated server on port:", err.message);
    } finally {
      dummyChild.kill("SIGKILL");
      await new Promise((r) => dummyChild.on("close", r));
      await new Promise((r) => unrelatedServer.close(r));
    }
    assert(waitFailed, "waitForServer should have rejected unrelated server");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 2: Child process that starts but never serves the expected identity
  // --------------------------------------------------------------------------
  console.log("\n=== Test 2: Child process serves wrong or unverified identity ===");
  {
    const port = await getAvailablePort();
    const childScript = `
      const http = require('http');
      const server = http.createServer((req, res) => {
        if (req.url === '/api/system/test-identity') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'ok', run_id: 'alien_run_id_999', pid: process.pid }));
        } else {
          res.writeHead(404);
          res.end();
        }
      });
      server.listen(${port}, '127.0.0.1');
    `;
    const child = spawn("node", ["-e", childScript]);

    let waitFailed = false;
    try {
      await waitForServer(port, child, "expected_correct_run_id", 3000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("unverified or conflicting process") || err.message.includes("alien_run_id_999"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer rejected child with mismatched identity:", err.message);
    } finally {
      child.kill("SIGKILL");
      await new Promise((r) => child.on("close", r));
    }
    assert(waitFailed, "waitForServer should have rejected child with mismatched run_id");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 3: Child process that exits prematurely before readiness
  // --------------------------------------------------------------------------
  console.log("\n=== Test 3: Child process exits before readiness ===");
  {
    const port = await getAvailablePort();
    // Child exits with non-zero code after short delay
    const child = spawn("node", ["-e", "setTimeout(() => process.exit(42), 200)"]);

    let waitFailed = false;
    try {
      await waitForServer(port, child, "any-run-id", 3000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("died prematurely") && err.message.includes("42"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer detected premature child death:", err.message);
    }
    assert(waitFailed, "waitForServer should have failed on premature process exit");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 4: Unexpected socket error is propagated
  // --------------------------------------------------------------------------
  console.log("\n=== Test 4: Unexpected socket error handling ===");
  {
    // Test checkPortFree behavior with unexpected socket error (mocking socket error emission)
    const netOrigConnect = net.Socket.prototype.connect;
    let customErrorEmitted = false;
    try {
      net.Socket.prototype.connect = function (...args) {
        process.nextTick(() => {
          const err = new Error("Custom network interface permission error");
          err.code = "EPERM";
          this.emit("error", err);
        });
        return this;
      };

      try {
        await checkPortFree(9999);
      } catch (err) {
        customErrorEmitted = true;
        assert(err.code === "EPERM", `Expected EPERM error code, got: ${err.code}`);
        assert(
          err.message.includes("Custom network interface"),
          `Expected custom message, got: ${err.message}`
        );
        console.log("  [✓] checkPortFree correctly propagated unexpected socket error:", err.message);
      }
      assert(customErrorEmitted, "checkPortFree must fail on unexpected socket error");
    } finally {
      net.Socket.prototype.connect = netOrigConnect;
    }

    // Also test EADDRINUSE handling
    let eaddrinuseEmitted = false;
    try {
      net.Socket.prototype.connect = function (...args) {
        process.nextTick(() => {
          const err = new Error("Port in use");
          err.code = "EADDRINUSE";
          this.emit("error", err);
        });
        return this;
      };

      try {
        await checkPortFree(9999);
      } catch (err) {
        eaddrinuseEmitted = true;
        assert(err.message.includes("EADDRINUSE"), `Expected EADDRINUSE error, got: ${err.message}`);
        console.log("  [✓] checkPortFree correctly treated EADDRINUSE as hard failure:", err.message);
      }
      assert(eaddrinuseEmitted, "checkPortFree must fail on EADDRINUSE");
    } finally {
      net.Socket.prototype.connect = netOrigConnect;
    }

    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 5: Successful readiness from the intended child
  // --------------------------------------------------------------------------
  console.log("\n=== Test 5: Successful readiness from intended child process ===");
  {
    const port = await getAvailablePort();
    const expectedRunId = `run_${Date.now().toString(36)}`;

    // Verify port is initially free
    const isFree = await checkPortFree(port);
    assert(isFree === true, "Target port should be free");

    // Spawn a child process that simulates the backend serving the exact identity
    const childScript = `
      const http = require('http');
      setTimeout(() => {
        const server = http.createServer((req, res) => {
          if (req.url === '/api/system/test-identity') {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ status: 'ok', run_id: '${expectedRunId}', pid: process.pid }));
          } else {
            res.writeHead(404);
            res.end();
          }
        });
        server.listen(${port}, '127.0.0.1');
      }, 300);
    `;
    const child = spawn("node", ["-e", childScript]);

    let identity = null;
    try {
      identity = await waitForServer(port, child, expectedRunId, 5000);
      assert(identity.status === "ok", "Expected status ok");
      assert(identity.run_id === expectedRunId, `Expected run_id ${expectedRunId}, got ${identity.run_id}`);
      assert(identity.pid === child.pid, `Expected pid ${child.pid}, got ${identity.pid}`);
      console.log(`  [✓] waitForServer successfully verified intended child (PID: ${identity.pid}, run_id: ${identity.run_id}).`);
    } finally {
      child.kill("SIGTERM");
      await new Promise((r) => child.on("close", r));
    }
    assert(identity !== null, "Readiness verification must succeed");
    passed++;
  }

  console.log(`\n[+] ALL ${passed} STAGING LIFECYCLE REGRESSION TESTS PASSED SUCCESSFULLY!`);
}

runTests().then(() => {
  process.exit(0);
}).catch((err) => {
  console.error("\n[-] Regression test failure:", err);
  process.exit(1);
});
