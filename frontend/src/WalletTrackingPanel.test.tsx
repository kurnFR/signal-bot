import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WalletTrackingPanel } from "./WalletTrackingPanel";

const responses: Record<string, unknown> = {
  "/api/wallets/meta": { periods_hours: [24, 72], chains: { eth: "Ethereum", solana: "Solana" }, min_net_usd: 1000, auto_track_net_usd: 5000 },
  "/api/wallets/settings": { enabled: true, networks: ["eth", "solana"], pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 150000, auto_track_net_usd: 500000, poll_interval_seconds: 300, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: [], has_birdeye_key: false },
  "/api/wallets/status": { enabled: true, service_healthy: true, last_scan: "2026-10-26T10:00:00Z", tracked_count: 1, networks: ["eth", "solana"], birdeye_fallback_active: false },
  "/api/wallets/tracked": { tracked: [{ id: 7, chain: "eth", wallet: "0x1234567890123456789012345678901234567890", label: "Test wallet", status: "ACTIVE", created_at: "2026-10-25T10:00:00Z" }] },
  "/api/wallets/positions?period_hours=24&limit=20": { positions: [{ token_address: "0xabc", token_symbol: "ABC", chain: "eth", net_usd: 2000, trade_count: 3 }] },
  "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [{ token_symbol: "ABC", chain: "eth", inflow_usd: 3000, outflow_usd: 1000, net_flow_usd: 2000 }] },
  "/api/wallets/events?limit=20": { events: [{ id: 1, tx_hash: "0xabcdef1234567890", chain: "eth", wallet: "0x1234567890123456789012345678901234567890", token_in: "ETH", token_out: "ABC", amount_usd: 2000, timestamp: "2026-10-26T10:00:00Z" }] },
};
function responseFor(url: string) {
  const path = new URL(url, "http://localhost").pathname + new URL(url, "http://localhost").search;
  return new Response(JSON.stringify(responses[path]), { status: 200, headers: { "content-type": "application/json" } });
}
describe("WalletTrackingPanel", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      return Promise.resolve(responseFor(url));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("renders tracker health, tracked wallets, token flow, and recent events", async () => {
    render(<WalletTrackingPanel />);
    expect(await screen.findByText("Test wallet")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getAllByText("Ethereum").length).toBeGreaterThan(0);
    expect(screen.getByText("ETH → ABC")).toBeInTheDocument();
    expect(screen.getByText(/Read-only Wallet Tracking monitor/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method === "GET")).toBe(true);
  });
  it("shows permission errors and offers retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 403, headers: { "content-type": "application/json" } })));
    render(<WalletTrackingPanel />);
    expect(await screen.findByText(/cannot view wallet tracking data/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(7));
  });

  it("renders successful empty wallet snapshots without an API error", async () => {
    const emptyResponses: Record<string, unknown> = {
      "/api/wallets/meta": { periods_hours: [24, 72], chains: {}, min_net_usd: 1000, auto_track_net_usd: 5000 },
      "/api/wallets/settings": { enabled: false, networks: [], pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 1000, auto_track_net_usd: 5000, poll_interval_seconds: 300, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: [] },
      "/api/wallets/status": { enabled: false, service_healthy: false, last_scan: null, tracked_count: 0, networks: [], birdeye_fallback_active: false },
      "/api/wallets/tracked": { tracked: [] },
      "/api/wallets/positions?period_hours=24&limit=20": { positions: [] },
      "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [] },
      "/api/wallets/events?limit=20": { events: [] },
    };
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      const path = new URL(url, "http://localhost").pathname + new URL(url, "http://localhost").search;
      return Promise.resolve(new Response(JSON.stringify(emptyResponses[path]), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<WalletTrackingPanel />);

    expect(await screen.findByText("No active tracked wallets were returned.")).toBeInTheDocument();
    expect(screen.getByText("No token flow data for the selected 24-hour window.")).toBeInTheDocument();
    expect(screen.getByText("No recent on-chain events were returned.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });

  it("recovers from a temporary wallet status API failure after Retry", async () => {
    let recovered = false;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      const path = new URL(url, "http://localhost").pathname;
      if (!recovered && path === "/api/wallets/status") {
        return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), { status: 503, headers: { "content-type": "application/json" } }));
      }
      return Promise.resolve(responseFor(url));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<WalletTrackingPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/check API and database health, then retry/i);
    recovered = true;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(await screen.findByText("Test wallet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });

});
