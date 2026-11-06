import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { PaperTradingPanel } from "./PaperTradingPanel";

const mockUseAuth = vi.fn();
vi.mock("./AuthGate", () => ({
  useAuth: () => mockUseAuth(),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  mockUseAuth.mockReturnValue(null);
});

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

const baseFixture = {
  "/api/paper/positions": {
    positions: [
      {
        id: 7,
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "demo_strategy",
        direction: "LONG",
        entry_time: 1793005200000,
        entry_price: 60000,
        current_price: 60500,
        stop_loss: 59000,
        take_profit: 62000,
        quantity: 0.1,
        unrealized_pnl_pct: 0.8,
        unrealized_r: 0.5,
        allocated_capital: 5000,
        status: "OPEN",
        last_update_time: 1793008800000,
      },
    ],
  },
  "/api/paper/configs": {
    configs: [
      {
        id: 2,
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "demo_strategy",
        is_active: true,
        allocated_capital: 5000,
        risk_per_trade_pct: 1,
        telegram_enabled: false,
        report_enabled: true,
        created_at: "2026-10-25T10:00:00Z",
      },
    ],
  },
  "/api/paper/orders?limit=20": {
    orders: [
      {
        id: 10,
        config_id: 2,
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "demo_strategy",
        direction: "LONG",
        signal_open_time: 1793005200,
        entry_time: 1793005200,
        requested_entry_price: 60000,
        stop_loss: 59000,
        take_profit: 62000,
        atr_at_signal: 500,
        ml_model_id: null,
        ml_probability: null,
        status: "PENDING",
        state_reason: null,
        fill_price: null,
        position_id: null,
        created_at: "2026-10-26T09:00:00Z",
        updated_at: "2026-10-26T09:00:00Z",
        filled_at: null,
        terminal_at: null,
      },
    ],
  },
  "/api/paper/trades?limit=20": {
    trades: [
      {
        id: 11,
        symbol: "ETHUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "demo_strategy",
        direction: "LONG",
        entry_price: 3000,
        exit_price: 3100,
        net_pnl: 10,
        net_return_pct: 2,
        r_multiple: 1.5,
        exit_reason: "TAKE_PROFIT",
        entry_time_wib: "2026-10-25 10:00",
        exit_time_wib: "2026-10-25 12:00",
      },
    ],
  },
  "/api/paper/metrics": {
    metrics: {
      totalTrades: 10,
      wins: 6,
      losses: 4,
      winRatePct: 60,
      cumulativeR: 4.5,
      expectancyR: 0.45,
      netPnl: 125.5,
      byStrategy: [
        {
          strategy: "trend_ema_v1",
          totalTrades: 3,
          wins: 2,
          losses: 1,
          winRatePct: 66.67,
          cumulativeR: 1.25,
          expectancyR: 0.4167,
          netPnl: 10.25,
        },
      ],
    },
  },
  "/api/paper/circuit-breakers": {
    status: "NORMAL",
    emergency_stop_active: false,
    emergency_stop_reason: null,
    active_positions: 1,
    max_concurrent_positions: 5,
    today_realized_pnl_usd: 25,
    max_daily_loss_usd: 250,
    open_notional_usd: 6000,
    max_total_notional_usd: 25000,
    can_open_new_position: true,
    message: "Risk checks passed",
  },
};

describe("read-only Paper Trading panel", () => {
  it("renders ledger, performance and risk data without issuing mutations", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname + new URL(String(input), "http://localhost").search;
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();
    expect((await screen.findAllByText("BTCUSDT")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("demo_strategy", { exact: false })).length).toBeGreaterThan(0);
    expect(screen.getByText("$125.50")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Strategy performance" })).toBeInTheDocument();
    expect(screen.getByText("trend_ema_v1")).toBeInTheDocument();
    expect(screen.getByText("66.67%")).toBeInTheDocument();
    expect(screen.getByText("1.25 R")).toBeInTheDocument();
    expect(screen.getByText("$10.25")).toBeInTheDocument();
    expect(screen.getByText("Risk checks passed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh snapshot/ })).toBeInTheDocument();
    expect(fetchMock.mock.calls).toHaveLength(6);
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes("/close") || String(path).includes("/cancel") || String(path).includes("/deploy") || String(path).includes("/sync"))).toBe(false);
  });

  it("offers retry when the server denies access", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ detail: "Forbidden" }, 403));
    vi.stubGlobal("fetch", fetchMock);
    render(<PaperTradingPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your account cannot view paper trading data.");
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(fetchMock).toHaveBeenCalledTimes(12);
  });
});

describe("Paper Trading Actions (PT-07 – PT-10) with RBAC", () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({
      user: { id: 1, username: "trader_alice", role: "trader", is_active: true },
      logout: vi.fn(),
      logoutError: null,
    });
  });

  it("PT-07: cancels pending paper order after confirmation", async () => {
    let cancelCalled = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname + url.search;
      const method = init?.method ?? "GET";

      if (path === "/api/paper/orders/10/cancel" && method === "POST") {
        cancelCalled = true;
        return Promise.resolve(json({ status: "cancelled", order_id: 10, message: "Order #10 cancelled" }));
      }
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();

    const cancelBtn = await screen.findByRole("button", { name: "Cancel pending order #10 for BTCUSDT" });
    fireEvent.click(cancelBtn);

    const dialog = screen.getByRole("dialog", { name: /Cancel Pending Paper Order #10/i });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/Confirm cancellation of this pending simulated order/i)).toBeInTheDocument();

    const confirmBtn = within(dialog).getByRole("button", { name: "Confirm Cancel Order" });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(cancelCalled).toBe(true));
    expect(await screen.findByText(/Paper order #10 was cancelled successfully/i)).toBeInTheDocument();
  });

  it("PT-08: closes open position after confirmation", async () => {
    let closeCalled = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname + url.search;
      const method = init?.method ?? "GET";

      if (path === "/api/paper/positions/7/close" && method === "POST") {
        closeCalled = true;
        return Promise.resolve(json({ status: "success", position_id: 7, message: "Position #7 closed at market" }));
      }
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();

    const closeBtn = await screen.findByRole("button", { name: "Close position #7 for BTCUSDT" });
    fireEvent.click(closeBtn);

    const dialog = screen.getByRole("dialog", { name: /Manually Exit Paper Position #7/i });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/This triggers a simulated market exit/i)).toBeInTheDocument();

    const confirmBtn = within(dialog).getByRole("button", { name: "Exit Position at Market" });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(closeCalled).toBe(true));
    expect(await screen.findByText(/Paper position #7 \(BTCUSDT\) was closed at market price/i)).toBeInTheDocument();
  });

  it("PT-09: deploys paper strategy through modal and displays success feedback", async () => {
    let deployPayload: unknown = null;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname + url.search;
      const method = init?.method ?? "GET";

      if (path === "/api/paper/deploy" && method === "POST") {
        deployPayload = JSON.parse(String(init?.body));
        return Promise.resolve(json({ status: "success", message: "Strategy trend_ema_v1 deployed" }));
      }
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();

    const deployBtn = screen.getByRole("button", { name: /Deploy strategy/i });
    fireEvent.click(deployBtn);

    const dialog = screen.getByRole("dialog", { name: "Deploy Strategy to Paper Trading" });
    expect(dialog).toBeInTheDocument();

    const submitBtn = within(dialog).getByRole("button", { name: "Deploy to Paper Trading" });
    fireEvent.click(submitBtn);

    await waitFor(() => expect(deployPayload).not.toBeNull());
    expect(deployPayload).toMatchObject({
      symbol: "BTCUSDT",
      market: "spot",
      timeframe: "1d",
      strategy_name: "confluence_ensemble_v1",
      allocated_capital: 5000,
      risk_per_trade_pct: 1.0,
      send_telegram: false,
    });
    const successMsg = await screen.findByText(/Strategy trend_ema_v1 deployed/i);
    expect(successMsg).toBeInTheDocument();
    expect(successMsg.closest(".control-success")).toBeInTheDocument();
    expect(screen.queryByText(/Configuration was saved, but immediate evaluation failed/i)).not.toBeInTheDocument();
  });

  it("PT-09: partial deployment displays warning, refreshes configuration, and avoids automatic retries", async () => {
    let deployCalls = 0;
    let getConfigsCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname + url.search;
      const method = init?.method ?? "GET";

      if (path === "/api/paper/configs" && method === "GET") {
        getConfigsCalls++;
        return Promise.resolve(json(baseFixture["/api/paper/configs"]));
      }
      if (path === "/api/paper/deploy" && method === "POST") {
        deployCalls++;
        return Promise.resolve(json({
          status: "partial",
          message: "Strategy 'confluence_ensemble_v1' deployed to Live Paper Trading on BTCUSDT (1d).",
          config: {
            id: 101,
            symbol: "BTCUSDT",
            market: "spot",
            timeframe: "1d",
            strategy_name: "confluence_ensemble_v1",
            is_active: true,
          },
          syncResult: { error: "Immediate evaluation failed" },
        }));
      }
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();
    const initialConfigCalls = getConfigsCalls;

    const deployBtn = screen.getByRole("button", { name: /Deploy strategy/i });
    fireEvent.click(deployBtn);

    const dialog = screen.getByRole("dialog", { name: "Deploy Strategy to Paper Trading" });
    const submitBtn = within(dialog).getByRole("button", { name: "Deploy to Paper Trading" });
    fireEvent.click(submitBtn);

    // Warning is displayed explaining configuration was saved but immediate evaluation failed
    const warningMsg = await screen.findByText(/Configuration was saved, but immediate evaluation failed/i);
    expect(warningMsg).toBeInTheDocument();
    expect(warningMsg.closest(".control-warning")).toBeInTheDocument();

    // Success feedback is NOT reported
    expect(screen.queryByText(/deployed to Paper Trading\.$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("status", { name: /CheckCircle/i })).not.toBeInTheDocument();

    // Exactly one deployment request occurred (no automatic second attempt)
    expect(deployCalls).toBe(1);

    // Paper configuration was refreshed so user can see persisted state
    await waitFor(() => expect(getConfigsCalls).toBeGreaterThan(initialConfigCalls));
  });

  it("PT-09: deployment API error displays error in dialog without reporting success", async () => {
    let deployCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname + url.search;
      const method = init?.method ?? "GET";

      if (path === "/api/paper/deploy" && method === "POST") {
        deployCalls++;
        return Promise.resolve(json({ detail: "Failed to persist paper config to database" }, 500));
      }
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();

    const deployBtn = screen.getByRole("button", { name: /Deploy strategy/i });
    fireEvent.click(deployBtn);

    const dialog = screen.getByRole("dialog", { name: "Deploy Strategy to Paper Trading" });
    const submitBtn = within(dialog).getByRole("button", { name: "Deploy to Paper Trading" });
    fireEvent.click(submitBtn);

    // Error is displayed inside modal dialog
    const errorAlert = await within(dialog).findByRole("alert");
    expect(errorAlert).toHaveTextContent(/Failed to persist paper config to database/i);

    // No success or warning feedback is reported on the main screen
    expect(screen.queryByText(/deployed to Live Paper Trading/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Configuration was saved/i)).not.toBeInTheDocument();
    expect(deployCalls).toBe(1);
  });

  it("PT-10: triggers emergency stop circuit breaker and resets", async () => {
    let stopCalled = false;
    let resetCalled = false;
    let emergencyActive = false;

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";

      if (url.pathname === "/api/paper/circuit-breakers/emergency-stop" && method === "POST") {
        stopCalled = true;
        emergencyActive = true;
        return Promise.resolve(
          json({
            status: "success",
            message: "Emergency stop activated",
            breaker: {
              status: "EMERGENCY_STOP",
              emergency_stop_active: true,
              emergency_stop_reason: "Manual emergency stop",
              active_positions: 1,
              max_concurrent_positions: 5,
              today_realized_pnl_usd: 25,
              max_daily_loss_usd: 250,
              open_notional_usd: 6000,
              max_total_notional_usd: 25000,
              can_open_new_position: false,
              message: "Emergency stop activated",
            },
          }),
        );
      }
      if (url.pathname === "/api/paper/circuit-breakers/reset" && method === "POST") {
        resetCalled = true;
        emergencyActive = false;
        return Promise.resolve(
          json({
            status: "success",
            message: "Circuit breaker reset",
            breaker: {
              status: "NORMAL",
              emergency_stop_active: false,
              emergency_stop_reason: null,
              active_positions: 1,
              max_concurrent_positions: 5,
              today_realized_pnl_usd: 25,
              max_daily_loss_usd: 250,
              open_notional_usd: 6000,
              max_total_notional_usd: 25000,
              can_open_new_position: true,
              message: "Risk checks passed",
            },
          }),
        );
      }
      if (url.pathname === "/api/paper/circuit-breakers" && method === "GET") {
        return Promise.resolve(
          json({
            ...baseFixture["/api/paper/circuit-breakers"],
            emergency_stop_active: emergencyActive,
            status: emergencyActive ? "EMERGENCY_STOP" : "NORMAL",
          }),
        );
      }
      const pathWithSearch = url.pathname + url.search;
      const payload = baseFixture[pathWithSearch as keyof typeof baseFixture] ?? baseFixture[url.pathname as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();

    // Trigger Emergency Stop
    const stopTrigger = screen.getByRole("button", { name: /Emergency Stop/i });
    fireEvent.click(stopTrigger);

    const stopDialog = screen.getByRole("dialog", { name: /Confirm Emergency Stop/i });
    expect(stopDialog).toBeInTheDocument();

    const confirmStopBtn = within(stopDialog).getByRole("button", { name: /Trigger Emergency Stop/i });
    fireEvent.click(confirmStopBtn);

    await waitFor(() => expect(stopCalled).toBe(true));
    expect(await screen.findByText(/Emergency stop activated/i)).toBeInTheDocument();

    // Reset circuit breaker
    const resetTrigger = await screen.findByRole("button", { name: /Reset Circuit Breaker/i });
    fireEvent.click(resetTrigger);

    const resetDialog = screen.getByRole("dialog", { name: /Confirm Circuit Breaker Reset/i });
    expect(resetDialog).toBeInTheDocument();

    const confirmResetBtn = within(resetDialog).getByRole("button", { name: "Reset Breaker" });
    fireEvent.click(confirmResetBtn);

    await waitFor(() => expect(resetCalled).toBe(true));
    expect(await screen.findByText(/Circuit breaker reset/i)).toBeInTheDocument();
  });

  it("RBAC: viewer role renders view-only badges and suppresses mutating buttons", async () => {
    mockUseAuth.mockReturnValue({
      user: { id: 3, username: "viewer_charlie", role: "viewer", is_active: true },
      logout: vi.fn(),
      logoutError: null,
    });

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname + new URL(String(input), "http://localhost").search;
      const payload = baseFixture[path as keyof typeof baseFixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();

    // Mutating buttons are NOT present for viewer
    expect(screen.queryByRole("button", { name: /Deploy strategy/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Emergency Stop/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel order/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Close position/i })).not.toBeInTheDocument();

    // View only indicators are present
    expect(screen.getByText(/Actions are view-only for this account/i)).toBeInTheDocument();
  });
});
