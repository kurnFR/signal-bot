import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSystemStatus } from "./systemStatus";

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
