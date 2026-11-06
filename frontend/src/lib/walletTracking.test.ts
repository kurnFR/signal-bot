import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchWalletTrackingSnapshot,
  saveWalletSettings,
  trackWallet,
  untrackWallet,
  WalletActionError,
} from "./walletTracking";

const responses: Record<string, unknown> = {
  "/api/wallets/meta": { periods_hours: [24, 72], chains: { eth: "Ethereum" }, min_net_usd: 1000, auto_track_net_usd: 5000 },
  "/api/wallets/settings": { enabled: true, networks: ["eth"], pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 150000, auto_track_net_usd: 500000, poll_interval_seconds: 300, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: [], has_birdeye_key: false },
  "/api/wallets/status": { id: 1, last_cycle_at: "2026-10-26T10:00:00Z", last_cycle_seconds: 1.5, pools_scanned: 10, swaps_ingested: 3, candidates: 2, tracked_wallets: 1, swaps_24h: 5, enabled: true, networks: ["eth"], birdeye_fallback_active: false, pool_discovery_sources: ["GeckoTerminal"], trade_sources: ["GeckoTerminal"] },
  "/api/wallets/tracked": { tracked: [{ id: 7, chain: "eth", wallet: "0x1234567890123456789012345678901234567890", label: "Test wallet", status: "ACTIVE", source: "manual", first_flagged_at: "2026-10-25T10:00:00Z", last_seen_at: "2026-10-26T10:00:00Z", last_net_usd: 2000, chain_label: "Ethereum", explorer_url: "https://example.test/wallet" }] },
  "/api/wallets/positions?period_hours=24&limit=20": { positions: [{ id: 1, chain: "eth", wallet: "0x1234567890123456789012345678901234567890", pool_address: "0xpool", pair: "ETH/ABC", period_hours: 24, side: "BUY", bias: "ACCUMULATING", buy_usd: 3000, sell_usd: 1000, net_usd: 2000, total_usd: 4000, buy_tokens: 30, sell_tokens: 10, net_tokens: 20, avg_buy_price: 100, avg_sell_price: 100, avg_price: 100, trade_count: 3, buy_count: 2, sell_count: 1, first_trade_at: "2026-10-25T10:00:00Z", last_trade_at: "2026-10-26T10:00:00Z", updated_at: "2026-10-26T10:00:00Z", tracked_id: 7, tracked_label: "Test wallet", is_tracked: true, label: "Test wallet", chain_label: "Ethereum", explorer_url: "https://example.test/wallet" }] },
  "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [{ chain: "eth", pool_address: "0xpool", pair: "ETH/ABC", accumulating_wallets: 3, distributing_wallets: 1, buy_usd: 3000, sell_usd: 1000, net_usd: 2000, signal: "ACCUMULATION", chain_label: "Ethereum" }] },
  "/api/wallets/events?limit=20": { events: [{ id: 1, tx_hash: "0xabcdef1234567890", chain: "eth", wallet: "0x1234567890123456789012345678901234567890", pair: "ETH/ABC", pool_address: "0xpool", side: "buy", usd_value: 2000, token_amount: 20, price_usd: 100, block_time: "2026-10-26T10:00:00Z", label: "Test wallet", chain_label: "Ethereum", explorer_url: "https://example.test/wallet", tx_url: "https://example.test/tx" }] },
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
    installFetch({ "/api/wallets/tracked": { tracked: [{ id: "7", chain: "eth", wallet: "0xabc", status: "ACTIVE", first_flagged_at: null }] } });
    await expect(fetchWalletTrackingSnapshot()).rejects.toThrow(/tracked wallets returned an invalid response/i);
  });

  it("rejects non-finite or missing numeric flow fields", async () => {
    installFetch({ "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [{ chain: "eth", pool_address: "0xpool", pair: "ETH/ABC", accumulating_wallets: 2, distributing_wallets: 0, buy_usd: null, sell_usd: 1, net_usd: 0, signal: "MIXED" }] } });
    await expect(fetchWalletTrackingSnapshot()).rejects.toThrow(/token flow returned an invalid response/i);
  });

  it("rejects malformed settings rather than accepting unsafe configuration types", async () => {
    installFetch({ "/api/wallets/settings": { ...responses["/api/wallets/settings"] as object, enabled: "true" } });
    await expect(fetchWalletTrackingSnapshot()).rejects.toThrow(/settings returned an invalid response/i);
  });
});

describe("Wallet Tracking mutation contracts", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  describe("trackWallet", () => {
    it("submits track request and returns normalized response", async () => {
      window.localStorage.setItem("token", "trader-jwt");
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ status: "ok", chain: "eth", wallet: "0x1234567890123456789012345678901234567890" }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await trackWallet("eth", "0x1234567890123456789012345678901234567890", "Whale 1");
      expect(res.status).toBe("ok");
      expect(res.wallet).toBe("0x1234567890123456789012345678901234567890");
      expect(fetchMock).toHaveBeenCalledWith("/api/wallets/tracked", expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer trader-jwt" }),
      }));
    });

    it("rejects empty wallet without making network call", async () => {
      await expect(trackWallet("eth", "")).rejects.toThrow(WalletActionError);
    });
  });

  describe("untrackWallet", () => {
    it("handles actual backend response shape {status: 'ok'} without tracked_id in payload", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ status: "ok" }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await untrackWallet(15);
      expect(res.status).toBe("ok");
      expect(res.tracked_id).toBe(15);
      expect(fetchMock).toHaveBeenCalledWith("/api/wallets/tracked/15", expect.objectContaining({
        method: "DELETE",
      }));
    });

    it("handles backend response with explicit tracked_id in payload", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ status: "ok", tracked_id: 15 }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await untrackWallet(15);
      expect(res.status).toBe("ok");
      expect(res.tracked_id).toBe(15);
    });

    it("throws 502 if backend returns unexpected status", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ status: "failed" }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      await expect(untrackWallet(15)).rejects.toThrow("Server returned an invalid wallet untrack response");
    });

    it("handles 404 when wallet is not found", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ detail: "Monitored wallet not found" }),
        { status: 404, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      await expect(untrackWallet(999)).rejects.toThrow("Monitored wallet not found");
    });
  });

  describe("saveWalletSettings", () => {
    it("saves valid settings and returns updated config", async () => {
      const updatedSettings = {
        enabled: true,
        networks: ["eth", "solana"],
        pools_per_network: 5,
        min_trade_usd: 15000,
        min_net_usd: 200000,
        auto_track_net_usd: 600000,
        poll_interval_seconds: 120,
        retention_days: 14,
        api_delay_seconds: 2.0,
        token_mode: "all" as const,
        token_addresses: [],
        has_birdeye_key: false,
      };
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify(updatedSettings),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await saveWalletSettings(updatedSettings);
      expect(res.min_trade_usd).toBe(15000);
      expect(res.networks).toEqual(["eth", "solana"]);
      expect(fetchMock).toHaveBeenCalledWith("/api/wallets/settings", expect.objectContaining({
        method: "PUT",
      }));
    });

    it("accepts up to 8 supported networks and includes them in payload", async () => {
      const eightNetworks = ["eth", "base", "bsc", "arbitrum", "polygon_pos", "optimism", "avalanche", "solana"];
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          enabled: true,
          networks: eightNetworks,
          pools_per_network: 4,
          min_trade_usd: 10000,
          min_net_usd: 150000,
          auto_track_net_usd: 500000,
          poll_interval_seconds: 300,
          retention_days: 8,
          api_delay_seconds: 4.0,
          token_mode: "all",
          token_addresses: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await saveWalletSettings({
        enabled: true,
        networks: eightNetworks,
        pools_per_network: 4,
        min_trade_usd: 10000,
        min_net_usd: 150000,
        auto_track_net_usd: 500000,
        poll_interval_seconds: 300,
        retention_days: 8,
        api_delay_seconds: 4.0,
        token_mode: "all",
        token_addresses: [],
      });
      expect(res.networks).toHaveLength(8);
    });

    it("rejects more than 8 networks before making network call", async () => {
      const nineNetworks = ["eth", "base", "bsc", "arbitrum", "polygon_pos", "optimism", "avalanche", "solana", "extra"];
      await expect(saveWalletSettings({
        enabled: true,
        networks: nineNetworks,
        pools_per_network: 4,
        min_trade_usd: 10000,
        min_net_usd: 150000,
        auto_track_net_usd: 500000,
        poll_interval_seconds: 300,
        retention_days: 8,
        api_delay_seconds: 4.0,
        token_mode: "all",
        token_addresses: [],
      })).rejects.toThrow("Cannot select more than 8 supported networks");
    });

    it("passes clear_birdeye_api_key in payload when requested", async () => {
      let sentBody: unknown = null;
      const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
        sentBody = JSON.parse(String(init?.body));
        return Promise.resolve(new Response(
          JSON.stringify({
            enabled: true,
            networks: ["eth"],
            pools_per_network: 4,
            min_trade_usd: 10000,
            min_net_usd: 150000,
            auto_track_net_usd: 500000,
            poll_interval_seconds: 300,
            retention_days: 8,
            api_delay_seconds: 4.0,
            token_mode: "all",
            token_addresses: [],
            has_birdeye_key: false,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ));
      });
      vi.stubGlobal("fetch", fetchMock);

      await saveWalletSettings({
        enabled: true,
        networks: ["eth"],
        pools_per_network: 4,
        min_trade_usd: 10000,
        min_net_usd: 150000,
        auto_track_net_usd: 500000,
        poll_interval_seconds: 300,
        retention_days: 8,
        api_delay_seconds: 4.0,
        token_mode: "all",
        token_addresses: [],
        clear_birdeye_api_key: true,
      });

      expect(sentBody).toMatchObject({ clear_birdeye_api_key: true });
    });

    it("rejects auto_track threshold lower than min_net_usd without network call", async () => {
      await expect(saveWalletSettings({
        enabled: true,
        networks: ["eth"],
        pools_per_network: 4,
        min_trade_usd: 10000,
        min_net_usd: 500000,
        auto_track_net_usd: 100000, // < 500000
        poll_interval_seconds: 300,
        retention_days: 8,
        api_delay_seconds: 4.0,
        token_mode: "all",
        token_addresses: [],
      })).rejects.toThrow(/Auto-track threshold must be greater than or equal to minimum net flow/);
    });
  });
});
