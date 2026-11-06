import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { WalletTrackingPanel } from "./WalletTrackingPanel";

const mockUseAuth = vi.fn();
vi.mock("./AuthGate", () => ({
  useAuth: () => mockUseAuth(),
}));

const responses: Record<string, unknown> = {
  "/api/wallets/meta": { periods_hours: [24, 72], chains: { eth: "Ethereum", solana: "Solana" }, min_net_usd: 1000, auto_track_net_usd: 5000 },
  "/api/wallets/settings": { enabled: true, networks: ["eth", "solana"], pools_per_network: 4, min_trade_usd: 10000, min_net_usd: 150000, auto_track_net_usd: 500000, poll_interval_seconds: 300, retention_days: 8, api_delay_seconds: 4, token_mode: "all", token_addresses: [], has_birdeye_key: false },
  "/api/wallets/status": { id: 1, last_cycle_at: "2026-10-26T10:00:00Z", last_cycle_seconds: 1.5, pools_scanned: 10, swaps_ingested: 3, candidates: 2, tracked_wallets: 1, swaps_24h: 5, enabled: true, networks: ["eth", "solana"], birdeye_fallback_active: false, pool_discovery_sources: ["GeckoTerminal"], trade_sources: ["GeckoTerminal"] },
  "/api/wallets/tracked": { tracked: [{ id: 7, chain: "eth", wallet: "0x1234567890123456789012345678901234567890", label: "Test wallet", status: "ACTIVE", source: "manual", first_flagged_at: "2026-10-25T10:00:00Z", last_seen_at: "2026-10-26T10:00:00Z", last_net_usd: 2000, chain_label: "Ethereum", explorer_url: "https://example.test/wallet" }] },
  "/api/wallets/positions?period_hours=24&limit=20": { positions: [{ id: 1, chain: "eth", wallet: "0x1234567890123456789012345678901234567890", pool_address: "0xpool", pair: "ETH/ABC", period_hours: 24, side: "BUY", bias: "ACCUMULATING", buy_usd: 3000, sell_usd: 1000, net_usd: 2000, total_usd: 4000, buy_tokens: 30, sell_tokens: 10, net_tokens: 20, avg_buy_price: 100, avg_sell_price: 100, avg_price: 100, trade_count: 3, buy_count: 2, sell_count: 1, first_trade_at: "2026-10-25T10:00:00Z", last_trade_at: "2026-10-26T10:00:00Z", updated_at: "2026-10-26T10:00:00Z", tracked_id: 7, tracked_label: "Test wallet", is_tracked: true, label: "Test wallet", chain_label: "Ethereum", explorer_url: "https://example.test/wallet" }] },
  "/api/wallets/token-flow?period_hours=24&limit=10": { flows: [{ chain: "eth", pool_address: "0xpool", pair: "ETH/ABC", accumulating_wallets: 3, distributing_wallets: 1, buy_usd: 3000, sell_usd: 1000, net_usd: 2000, signal: "ACCUMULATION", chain_label: "Ethereum" }] },
  "/api/wallets/events?limit=20": { events: [{ id: 1, tx_hash: "0xabcdef1234567890", chain: "eth", wallet: "0x1234567890123456789012345678901234567890", pair: "ETH/ABC", pool_address: "0xpool", side: "buy", usd_value: 2000, token_amount: 20, price_usd: 100, block_time: "2026-10-26T10:00:00Z", label: "Test wallet", chain_label: "Ethereum", explorer_url: "https://example.test/wallet", tx_url: "https://example.test/tx" }] },
};
function responseFor(url: string) {
  const path = new URL(url, "http://localhost").pathname + new URL(url, "http://localhost").search;
  return new Response(JSON.stringify(responses[path]), { status: 200, headers: { "content-type": "application/json" } });
}
describe("WalletTrackingPanel", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    mockUseAuth.mockReturnValue(null);
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      return Promise.resolve(responseFor(url));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("renders tracker health, tracked wallets, token flow, and recent events", async () => {
    render(<WalletTrackingPanel />);
    expect(await screen.findByText("Test wallet")).toBeInTheDocument();
    expect(screen.getByText("Cycle observed")).toBeInTheDocument();
    expect(screen.getAllByText("Ethereum").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ETH/ABC").length).toBeGreaterThan(0);
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
      "/api/wallets/status": { id: 1, last_cycle_at: null, last_cycle_seconds: null, pools_scanned: 0, swaps_ingested: 0, candidates: 0, tracked_wallets: 0, swaps_24h: 0, enabled: false, networks: [], birdeye_fallback_active: false, pool_discovery_sources: [], trade_sources: [] },
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

describe("Wallet Tracking Workflows (WT-04 & WT-05) with RBAC", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    mockUseAuth.mockReturnValue({
      user: { id: 1, username: "trader_alice", role: "trader", is_active: true },
      logout: vi.fn(),
      logoutError: null,
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("WT-04: tracks a new wallet address", async () => {
    let trackPayload: unknown = null;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, "http://localhost").pathname;
      const method = init?.method ?? "GET";

      if (path === "/api/wallets/tracked" && method === "POST") {
        trackPayload = JSON.parse(String(init?.body));
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: "ok",
              chain: "eth",
              wallet: "0x9876543210987654321098765432109876543210",
              label: "Alpha Whale",
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(responseFor(url));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WalletTrackingPanel />);
    const trackBtn = await screen.findByRole("button", { name: /Track wallet/i });
    fireEvent.click(trackBtn);

    const dialog = screen.getByRole("dialog", { name: "Track New Wallet" });
    expect(dialog).toBeInTheDocument();

    const chainSelect = within(dialog).getByLabelText("Chain / Network");
    fireEvent.change(chainSelect, { target: { value: "eth" } });

    const addressInput = within(dialog).getByLabelText("Wallet Address");
    fireEvent.change(addressInput, { target: { value: "0x9876543210987654321098765432109876543210" } });

    const labelInput = within(dialog).getByLabelText("Label (optional)");
    fireEvent.change(labelInput, { target: { value: "Alpha Whale" } });

    const submitBtn = within(dialog).getByRole("button", { name: "Start Tracking" });
    fireEvent.click(submitBtn);

    await waitFor(() => expect(trackPayload).not.toBeNull());
    expect(trackPayload).toMatchObject({
      chain: "eth",
      wallet: "0x9876543210987654321098765432109876543210",
      label: "Alpha Whale",
    });
    expect(await screen.findByText(/added to tracked wallets on eth\./i)).toBeInTheDocument();
  });

  it("WT-04: untracks a monitored wallet after confirmation", async () => {
    let untrackCalled = false;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, "http://localhost").pathname;
      const method = init?.method ?? "GET";

      if (path === "/api/wallets/tracked/7" && method === "DELETE") {
        untrackCalled = true;
        return Promise.resolve(
          new Response(
            JSON.stringify({ status: "ok", tracked_id: 7 }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(responseFor(url));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WalletTrackingPanel />);
    const untrackBtn = await screen.findByRole("button", { name: "Untrack" });
    fireEvent.click(untrackBtn);

    const dialog = screen.getByRole("dialog", { name: "Untrack Wallet" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/Test wallet/)).toBeInTheDocument();

    const confirmBtn = within(dialog).getByRole("button", { name: "Untrack Wallet" });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(untrackCalled).toBe(true));
    expect(await screen.findByText(/removed from tracked wallets\./i)).toBeInTheDocument();
  });

  it("WT-05: edits and saves wallet tracker settings", async () => {
    let savedSettings: unknown = null;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, "http://localhost").pathname;
      const method = init?.method ?? "GET";

      if (path === "/api/wallets/settings" && method === "PUT") {
        savedSettings = JSON.parse(String(init?.body));
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ...(responses["/api/wallets/settings"] as Record<string, unknown>),
              ...(savedSettings as Record<string, unknown>),
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(responseFor(url));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WalletTrackingPanel />);
    const editBtn = await screen.findByRole("button", { name: /Edit settings/i });
    fireEvent.click(editBtn);

    const dialog = screen.getByRole("dialog", { name: "Wallet Tracker Configuration" });
    expect(dialog).toBeInTheDocument();

    const minTradeInput = within(dialog).getByLabelText("Min Trade Size (USD)");
    fireEvent.change(minTradeInput, { target: { value: "25000" } });

    const submitBtn = within(dialog).getByRole("button", { name: "Save Settings" });
    const form = submitBtn.closest("form");
    if (form) fireEvent.submit(form);
    else fireEvent.click(submitBtn);

    await waitFor(() => expect(savedSettings).not.toBeNull());
    expect(savedSettings).toMatchObject({
      enabled: true,
      min_trade_usd: 25000,
    });
    expect(await screen.findByText(/Wallet tracker settings updated successfully\./i)).toBeInTheDocument();
  });

  it("RBAC: viewer role renders view-only badges and suppresses mutating buttons", async () => {
    mockUseAuth.mockReturnValue({
      user: { id: 3, username: "viewer_charlie", role: "viewer", is_active: true },
      logout: vi.fn(),
      logoutError: null,
    });

    const fetchMock = vi.fn((url: string) => Promise.resolve(responseFor(url)));
    vi.stubGlobal("fetch", fetchMock);

    render(<WalletTrackingPanel />);
    expect(await screen.findByRole("heading", { name: "Tracked wallets" })).toBeInTheDocument();

    // Mutating actions suppressed
    expect(screen.queryByRole("button", { name: /Track wallet/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit settings/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Untrack" })).not.toBeInTheDocument();

    // View only label present
    expect(screen.getByText("View only")).toBeInTheDocument();
  });
});
