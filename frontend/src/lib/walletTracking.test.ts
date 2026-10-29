import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchWalletTrackingSnapshot } from "./walletTracking";

const responses: Record<string, unknown> = {
  "/api/wallets/meta": { periods_hours: [24, 72], chains: { eth: "Ethereum" }, min_net_usd: 1000, auto_track_net_usd: 5000 },
  "/api/wallets/settings": { enabled: true, networks: ["eth"], pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 150000, auto_track_net_usd: 500000, poll_interval_seconds: 300, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: [], has_birdeye_key: false },
  "/api/wallets/status": { enabled: true, service_healthy: true, last_scan: "2026-10-26T10:00:00Z", tracked_count: 1, networks: ["eth"], birdeye_fallback_active: false },
  "/api/wallets/tracked": { tracked: [{ id: 7, chain: "eth", wallet: "0x1234567890123456789012345678901234567890", label: "Test wallet", status: "ACTIVE", created_at: "2026-10-25T10:00:00Z" }] },
  "/api/wallets/positions?period_hours=24&limit=20": { positions: [{ token_address: "0xabc", token_symbol: "ABC", chain: "eth", net_usd: 2000, trade_count: 3 }] },
  "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [{ token_symbol: "ABC", chain: "eth", inflow_usd: 3000, outflow_usd: 1000, net_flow_usd: 2000 }] },
  "/api/wallets/events?limit=20": { events: [{ id: 1, tx_hash: "0xabcdef1234567890", chain: "eth", wallet: "0x1234567890123456789012345678901234567890", token_in: "ETH", token_out: "ABC", amount_usd: 2000, timestamp: "2026-10-26T10:00:00Z" }] },
};
function installFetch(overrides: Record<string, unknown> = {}) {
  const payloads = { ...responses, ...overrides };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method && init.method !== "GET") throw new Error("Wallet Tracking snapshot must use GET");
    const key = url.pathname + url.search;
    return Promise.resolve(new Response(JSON.stringify(payloads[key]), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
  }));
}
beforeEach(() => {
  window.localStorage.clear();
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("Wallet Tracking response contracts", () => {
  it("accepts a valid populated snapshot", async () => {
    const snapshot = await fetchWalletTrackingSnapshot();
    expect(snapshot.tracked[0].wallet).toContain("0x1234");
    expect(snapshot.positions[0].net_usd).toBe(2000);
    expect(snapshot.events).toHaveLength(1);
  });

  it("accepts valid empty result collections", async () => {
    installFetch({
      "/api/wallets/tracked": { tracked: [] },
      "/api/wallets/positions?period_hours=24&limit=20": { positions: [] },
      "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [] },
      "/api/wallets/events?limit=20": { events: [] },
    });
    const snapshot = await fetchWalletTrackingSnapshot();
    expect(snapshot.tracked).toEqual([]);
    expect(snapshot.positions).toEqual([]);
    expect(snapshot.flows).toEqual([]);
    expect(snapshot.events).toEqual([]);
  });

  it("rejects malformed tracked-wallet rows", async () => {
    installFetch({ "/api/wallets/tracked": { tracked: [{ id: "7", chain: "eth", wallet: "0xabc", status: "ACTIVE" }] } });
    await expect(fetchWalletTrackingSnapshot()).rejects.toThrow(/tracked wallets returned an invalid response/i);
  });

  it("rejects non-finite or missing numeric flow fields", async () => {
    installFetch({ "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [{ token_symbol: "ABC", chain: "eth", inflow_usd: null, outflow_usd: 1, net_flow_usd: 0 }] } });
    await expect(fetchWalletTrackingSnapshot()).rejects.toThrow(/token flow returned an invalid response/i);
  });

  it("rejects malformed settings rather than accepting unsafe configuration types", async () => {
    installFetch({ "/api/wallets/settings": { ...responses["/api/wallets/settings"] as object, enabled: "true" } });
    await expect(fetchWalletTrackingSnapshot()).rejects.toThrow(/settings returned an invalid response/i);
  });
});
