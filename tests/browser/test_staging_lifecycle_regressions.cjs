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
const { checkPortFree, waitForServer, isProcessAlive, terminateChildProcess, orchestrateStagingCleanup } = require("./staging_browser_acceptance.cjs");

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
  // TEST 5: Successful readiness from intended child (matching run_id & PID)
  // --------------------------------------------------------------------------
  console.log("\n=== Test 5: Correct run ID and exact matching PID ===");
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

  // --------------------------------------------------------------------------
  // TEST 6: Correct run ID but missing PID (mandatory PID enforcement)
  // --------------------------------------------------------------------------
  console.log("\n=== Test 6: Correct run ID but missing PID ===");
  {
    const port = await getAvailablePort();
    const expectedRunId = `run_${Date.now().toString(36)}`;
    const childScript = `
      const http = require('http');
      const server = http.createServer((req, res) => {
        if (req.url === '/api/system/test-identity') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'ok', run_id: '${expectedRunId}' }));
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
      await waitForServer(port, child, expectedRunId, 3000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("missing PID") || err.message.includes("invalid or missing"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer rejected response with missing PID:", err.message);
    } finally {
      child.kill("SIGKILL");
      await new Promise((r) => child.on("close", r));
    }
    assert(waitFailed, "waitForServer must reject response with missing PID");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 7: Correct run ID but null PID
  // --------------------------------------------------------------------------
  console.log("\n=== Test 7: Correct run ID but null PID ===");
  {
    const port = await getAvailablePort();
    const expectedRunId = `run_${Date.now().toString(36)}`;
    const childScript = `
      const http = require('http');
      const server = http.createServer((req, res) => {
        if (req.url === '/api/system/test-identity') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'ok', run_id: '${expectedRunId}', pid: null }));
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
      await waitForServer(port, child, expectedRunId, 3000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("missing PID") || err.message.includes("invalid or missing"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer rejected response with null PID:", err.message);
    } finally {
      child.kill("SIGKILL");
      await new Promise((r) => child.on("close", r));
    }
    assert(waitFailed, "waitForServer must reject response with null PID");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 8: Correct run ID but incorrect PID
  // --------------------------------------------------------------------------
  console.log("\n=== Test 8: Correct run ID but incorrect PID ===");
  {
    const port = await getAvailablePort();
    const expectedRunId = `run_${Date.now().toString(36)}`;
    const childScript = `
      const http = require('http');
      const server = http.createServer((req, res) => {
        if (req.url === '/api/system/test-identity') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'ok', run_id: '${expectedRunId}', pid: 999999 }));
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
      await waitForServer(port, child, expectedRunId, 3000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("mismatched PID"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer rejected response with incorrect PID:", err.message);
    } finally {
      child.kill("SIGKILL");
      await new Promise((r) => child.on("close", r));
    }
    assert(waitFailed, "waitForServer must reject response with mismatched PID");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 9: Child process exits during identity verification
  // --------------------------------------------------------------------------
  console.log("\n=== Test 9: Child process exits during identity verification ===");
  {
    const port = await getAvailablePort();
    const expectedRunId = `run_${Date.now().toString(36)}`;
    const childScript = `
      setTimeout(() => {
        process.exit(13);
      }, 250);
    `;
    const child = spawn("node", ["-e", childScript]);

    let waitFailed = false;
    try {
      await waitForServer(port, child, expectedRunId, 3000);
    } catch (err) {
      waitFailed = true;
      assert(
        err.message.includes("died prematurely") && err.message.includes("13"),
        `Unexpected error message: ${err.message}`
      );
      console.log("  [✓] waitForServer detected premature exit during identity verification:", err.message);
    }
    assert(waitFailed, "waitForServer must fail when child exits during identity verification");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 10: Process ignores SIGTERM; SIGKILL fallback terminates and confirms
  // --------------------------------------------------------------------------
  console.log("\n=== Test 10: Process ignores SIGTERM; SIGKILL fallback terminates and confirms ===");
  {
    const childScript = `
      process.on('SIGTERM', () => {
        // Intentionally ignore SIGTERM
      });
      setInterval(() => {}, 1000);
    `;
    const child = spawn("node", ["-e", childScript]);
    await new Promise((r) => setTimeout(r, 200));
    assert(isProcessAlive(child.pid), "Spawned child should be alive initially");

    const result = await terminateChildProcess(child, 300, 2500);
    assert(result.exited === true, "Child should be confirmed exited");
    assert(result.forcedKill === true, "Child should have required SIGKILL fallback");
    assert(!isProcessAlive(child.pid), "Child PID must not be alive after termination");
    console.log(`  [✓] terminateChildProcess verified SIGKILL fallback for PID ${child.pid}.`);
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 11: Cleanup orchestration - termination failure prevents fixture deletion
  // --------------------------------------------------------------------------
  console.log("\n=== Test 11: Cleanup orchestration - termination failure prevents fixture deletion ===");
  {
    // Subtest 11A: terminateProcFn throws an error
    const mockProc = { pid: 888888, exitCode: null, signalCode: null };
    let deleteFixturesCalled = false;
    let cleanupReportedFailure = false;

    try {
      await orchestrateStagingCleanup({
        proc: mockProc,
        seededUserIds: [8001, 8002],
        targetDb: "signal_bot_test",
        terminateProcFn: async () => {
          throw new Error("Process PID 888888 remains alive after SIGTERM and SIGKILL");
        },
        deleteFixturesFn: async () => {
          deleteFixturesCalled = true;
          return { status: "ok" };
        },
      });
    } catch (err) {
      cleanupReportedFailure = true;
      assert(
        err.message.includes("staging server termination could not be confirmed") && err.message.includes("Fixtures preserved"),
        `Unexpected cleanup error message: ${err.message}`
      );
      console.log("  [✓] 11A: orchestrateStagingCleanup reported visible termination failure on throw:", err.message);
    }

    assert(cleanupReportedFailure, "orchestrateStagingCleanup must report failure when process termination fails");
    assert(!deleteFixturesCalled, "Fixtures must NOT be deleted when process termination fails");
    console.log("  [✓] 11A Verified: termination failure strictly prevented fixture deletion.");

    // Subtest 11B: terminateProcFn returns { exited: false }
    const unconfirmedResults = [
      { exited: false },
      {},
      null,
    ];

    for (const [idx, result] of unconfirmedResults.entries()) {
      let unconfirmedDeleteCalled = false;
      let unconfirmedReportedFailure = false;

      try {
        await orchestrateStagingCleanup({
          proc: mockProc,
          seededUserIds: [8001, 8002],
          targetDb: "signal_bot_test",
          terminateProcFn: async () => result,
          deleteFixturesFn: async () => {
            unconfirmedDeleteCalled = true;
            return { status: "ok" };
          },
        });
      } catch (err) {
        unconfirmedReportedFailure = true;
        assert(
          err.message.includes("staging server termination could not be confirmed") && err.message.includes("Fixtures preserved"),
          `Unexpected cleanup error message for result ${JSON.stringify(result)}: ${err.message}`
        );
        console.log(`  [✓] 11B.${idx + 1}: orchestrateStagingCleanup rejected unconfirmed result ${JSON.stringify(result)}:`, err.message);
      }

      assert(unconfirmedReportedFailure, `orchestrateStagingCleanup must report failure for unconfirmed termination result ${JSON.stringify(result)}`);
      assert(!unconfirmedDeleteCalled, `Fixtures must NOT be deleted when termination result is unconfirmed: ${JSON.stringify(result)}`);
    }
    console.log("  [✓] 11B Verified: unconfirmed termination results ({ exited: false }, {}, null) strictly prevented fixture deletion.");
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 12: Cleanup orchestration - successful termination permits exact-ID fixture cleanup
  // --------------------------------------------------------------------------
  console.log("\n=== Test 12: Cleanup orchestration - successful termination permits exact-ID fixture cleanup ===");
  {
    const mockProc = { pid: 888889, exitCode: null, signalCode: null };
    let recordedTargetDb = null;
    let recordedUserIds = null;

    const cleanupResult = await orchestrateStagingCleanup({
      proc: mockProc,
      seededUserIds: [8003, 8004],
      targetDb: "signal_bot_test",
      terminateProcFn: async (proc) => {
        return { exited: true, forcedKill: false, code: 0, signal: null };
      },
      deleteFixturesFn: async (targetDb, userIds) => {
        recordedTargetDb = targetDb;
        recordedUserIds = [...userIds];
        return { status: "ok", deleted_users: userIds.length };
      },
    });

    assert(cleanupResult.success === true, "orchestrateStagingCleanup must return success on clean termination and fixture cleanup");
    assert(recordedTargetDb === "signal_bot_test", `Expected targetDb 'signal_bot_test', got: ${recordedTargetDb}`);
    assert(
      Array.isArray(recordedUserIds) && recordedUserIds.length === 2 && recordedUserIds[0] === 8003 && recordedUserIds[1] === 8004,
      `Exact fixture user IDs not cleaned up: ${JSON.stringify(recordedUserIds)}`
    );
    console.log(`  [✓] orchestrateStagingCleanup successfully executed exact-ID cleanup for users ${JSON.stringify(recordedUserIds)} on ${recordedTargetDb}.`);
    passed++;
  }

  // --------------------------------------------------------------------------
  // TEST 13: Cleanup orchestration - browser closure and error propagation
  // --------------------------------------------------------------------------
  console.log("\n=== Test 13: Cleanup orchestration - browser closure and error propagation ===");
  {
    // Subtest 13A: Browser closure failure is captured and reported
    let browserCloseFailed = false;
    const failingBrowser = {
      close: async () => {
        throw new Error("Chromium CDP socket disconnected unexpectedly");
      },
    };
    try {
      await orchestrateStagingCleanup({
        browser: failingBrowser,
        terminateProcFn: async () => ({ exited: true }),
        deleteFixturesFn: async () => ({ status: "ok" }),
      });
    } catch (err) {
      browserCloseFailed = true;
      assert(err.message.includes("Browser close failed: Chromium CDP socket disconnected unexpectedly"), `Unexpected browser error: ${err.message}`);
      console.log("  [✓] orchestrateStagingCleanup reported browser close failure:", err.message);
    }
    assert(browserCloseFailed, "orchestrateStagingCleanup must report browser close failure");

    // Subtest 13B: Prior test error propagation preserved while still cleaning fixtures
    let fixturesDeletedOnPriorError = false;
    let priorErrorPropagated = false;
    const priorTestError = new Error("Assertion failed: DOM selector .screener-metrics not found");
    try {
      await orchestrateStagingCleanup({
        previousError: priorTestError,
        seededUserIds: [8005],
        targetDb: "signal_bot_test",
        terminateProcFn: async () => ({ exited: true }),
        deleteFixturesFn: async (targetDb, userIds) => {
          fixturesDeletedOnPriorError = true;
          return { status: "ok" };
        },
      });
    } catch (err) {
      priorErrorPropagated = true;
      assert(err === priorTestError, "orchestrateStagingCleanup must propagate prior test error");
      console.log("  [✓] orchestrateStagingCleanup propagated prior test error:", err.message);
    }
    assert(priorErrorPropagated, "Prior error must be propagated");
    assert(fixturesDeletedOnPriorError, "Fixtures must still be safely cleaned up when prior test error occurs but termination succeeded");

    // Subtest 13C: Fixture cleanup failure reporting
    let fixtureCleanupReported = false;
    try {
      await orchestrateStagingCleanup({
        seededUserIds: [8006],
        targetDb: "signal_bot_test",
        terminateProcFn: async () => ({ exited: true }),
        deleteFixturesFn: async () => {
          throw new Error("Deadlock detected during audit_logs cleanup");
        },
      });
    } catch (err) {
      fixtureCleanupReported = true;
      assert(err.message.includes("Fixture cleanup failed: Deadlock detected during audit_logs cleanup"), `Unexpected fixture error: ${err.message}`);
      console.log("  [✓] orchestrateStagingCleanup reported fixture cleanup failure:", err.message);
    }
    assert(fixtureCleanupReported, "orchestrateStagingCleanup must report fixture cleanup failure");
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
