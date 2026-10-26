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
});
