import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAdminSystemStatus, fetchSystemStatus } from "./systemStatus";

afterEach(() => vi.unstubAllGlobals());

const validPayload = {
  database: { status: "ok", tables: { signals: 10, trades: 3, legacy: null }, error: null },
  heartbeats: [{ collector: "market-data", status: "ok", lastBeat: "2026-10-28 12:00:00" }],
  binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: true },
};

function stubPayload(payload: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  })));
}

describe("system status response contract", () => {
  it("accepts a valid status payload and an empty heartbeat collection", async () => {
    stubPayload({ ...validPayload, heartbeats: [] });
    await expect(fetchSystemStatus()).resolves.toMatchObject({
      database: { status: "ok", tables: { signals: 10, trades: 3, legacy: null }, error: null },
      heartbeats: [],
      binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: true },
    });
  });

  it.each([
    ["missing database", { ...validPayload, database: undefined }],
    ["invalid table counts", { ...validPayload, database: { ...validPayload.database, tables: { signals: "ten" } } }],
    ["invalid database error", { ...validPayload, database: { ...validPayload.database, error: 42 } }],
    ["invalid heartbeat row", { ...validPayload, heartbeats: [{ collector: "market-data", status: "ok", lastBeat: 42 }] }],
    ["invalid heartbeat collection", { ...validPayload, heartbeats: { collector: "market-data" } }],
    ["invalid Binance configuration flag", { ...validPayload, binance: { ...validPayload.binance, enableFuturesConfig: "yes" } }],
    ["missing Binance status", { ...validPayload, binance: { spotApi: "online", enableFuturesConfig: true } }],
  ])("rejects %s before the UI can render it", async (_label, payload) => {
    stubPayload(payload);
    await expect(fetchSystemStatus()).rejects.toThrow("System status returned an invalid response");
  });
});


const validAdminPayload = {
  database: { status: "ok", host: "db.internal", port: 3306, database: "signal_bot", tables: { signals: 10, trades: 3 }, error: null },
  heartbeats: [{ collector: "market-data", status: "ok", detail: "last poll completed", lastBeat: "2026-10-28 12:00:00" }],
  binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: true },
  system: { timezone: "Asia/Jakarta (WIB UTC+7)" },
};

describe("admin system status response contract", () => {
  it("accepts a complete admin diagnostic payload", async () => {
    stubPayload(validAdminPayload);
    await expect(fetchAdminSystemStatus()).resolves.toMatchObject({
      database: { status: "ok", host: "db.internal", port: 3306, database: "signal_bot" },
      heartbeats: [{ collector: "market-data", status: "ok", detail: "last poll completed" }],
      system: { timezone: "Asia/Jakarta (WIB UTC+7)" },
    });
  });

  it.each([
    ["invalid database port", { ...validAdminPayload, database: { ...validAdminPayload.database, port: null } }],
    ["invalid database table counts", { ...validAdminPayload, database: { ...validAdminPayload.database, tables: { signals: "ten" } } }],
    ["invalid heartbeat details", { ...validAdminPayload, heartbeats: [{ ...validAdminPayload.heartbeats[0], detail: 42 }] }],
    ["invalid heartbeat timestamp", { ...validAdminPayload, heartbeats: [{ ...validAdminPayload.heartbeats[0], lastBeat: false }] }],
    ["invalid Binance configuration", { ...validAdminPayload, binance: { ...validAdminPayload.binance, enableFuturesConfig: "true" } }],
    ["invalid system metadata", { ...validAdminPayload, system: { timezone: 7 } }],
  ])("rejects %s", async (_label, payload) => {
    stubPayload(payload);
    await expect(fetchAdminSystemStatus()).rejects.toThrow("Admin system status returned an invalid response");
  });
});
