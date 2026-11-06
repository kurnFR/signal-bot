import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ResearchPanel } from "./ResearchPanel";

const mockUseAuth = vi.fn();
vi.mock("./AuthGate", () => ({
  useAuth: () => mockUseAuth(),
}));

const snapshot = {
  "/api/research/retailbot2/positions?limit=100": {
    positions: [
      {
        id: 1,
        symbol: "BTCUSDT",
        side: "LONG",
        mode: "inverse",
        entry_price: 100,
        stop_price: 95,
        target_price: 110,
        atr: 2,
        rr: 2,
        units: 0.1,
        strategy: "BB_REV_INV",
        strategies_json: null,
        confluence_score: 4,
        reason: "test",
        entry_time: "2026-10-26T10:00:00Z",
      },
    ],
  },
  "/api/research/retailbot2/trades?limit=50": {
    trades: [
      {
        id: 2,
        symbol: "ETHUSDT",
        side: "LONG",
        mode: "shadow",
        entry_price: 50,
        stop_price: 48,
        target_price: 55,
        units: 1,
        strategy: "BB_REV",
        confluence_score: 3,
        status: "CLOSED",
        exit_price: 55,
        exit_reason: "target",
        net_pnl: 5,
        pnl_percent: 10,
        entry_time: "2026-10-26T10:00:00Z",
        exit_time: "2026-10-26T11:00:00Z",
      },
    ],
  },
  "/api/research/retailbot2/stats": {
    by_strategy: [
      {
        strategy: "BB_REV_INV",
        mode: "inverse",
        total_trades: 12,
        wins: 7,
        losses: 5,
        total_pnl: 125.5,
        last_updated: "2026-10-26T12:00:00Z",
      },
    ],
    by_mode: {
      inverse: { realized_pnl: 125.5, closed_count: 12, wins: 7, win_rate: 58.3, open_count: 1 },
      shadow: { realized_pnl: -25, closed_count: 4, wins: 1, win_rate: 25, open_count: 0 },
    },
  },
  "/api/research/retailbot2/strategies": {
    strategies: [
      {
        strategy_id: "BB_REV_INV",
        mode: "inverse",
        evaluation_enabled: true,
        paper_enabled: true,
        telegram_enabled: false,
        report_enabled: true,
        auto_promote_enabled: false,
        historical_only: false,
      },
      {
        strategy_id: "HIST_ONLY_STRAT",
        mode: "shadow",
        evaluation_enabled: false,
        paper_enabled: false,
        telegram_enabled: false,
        report_enabled: true,
        auto_promote_enabled: false,
        historical_only: true,
      },
    ],
  },
};

describe("ResearchPanel (Read-only baseline)", () => {
  beforeEach(() => {
    cleanup();
    mockUseAuth.mockReturnValue(null);
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error("Research preview must be GET-only");
      const body = snapshot[path as keyof typeof snapshot];
      if (!body) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders positions, trades, and persisted lifecycle flags using GET requests only", async () => {
    render(<ResearchPanel />);
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    expect(screen.getAllByText("BB_REV_INV").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("heading", { name: "Performance by mode" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Performance by strategy" })).toBeInTheDocument();
    expect(screen.getByText("58.3%")).toBeInTheDocument();
    expect(screen.getByText("-25")).toBeInTheDocument();
    expect(screen.getAllByText("125.5")).toHaveLength(2);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining(Object.keys(snapshot)));
  });
});

describe("Research Strategy Lifecycle Workflows (RB-03 & RB-04) with RBAC", () => {
  beforeEach(() => {
    cleanup();
    mockUseAuth.mockReturnValue({
      user: { id: 1, username: "trader_alice", role: "trader", is_active: true },
      logout: vi.fn(),
      logoutError: null,
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("RB-03: saves updated lifecycle settings for a strategy row", async () => {
    let lifecyclePayload: unknown = null;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";

      if (path === "/api/research/retailbot2/strategies" && method === "POST") {
        lifecyclePayload = JSON.parse(String(init?.body));
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: "ok",
              strategy_id: "BB_REV_INV",
              mode: "inverse",
              settings: lifecyclePayload,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      const body = snapshot[path as keyof typeof snapshot];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ResearchPanel />);
    expect((await screen.findAllByText("BB_REV_INV")).length).toBeGreaterThan(0);

    // Toggle telegram checkbox for BB_REV_INV (inverse)
    const telegramCheckbox = screen.getByLabelText("Telegram alerts for BB_REV_INV (inverse)");
    expect(telegramCheckbox).not.toBeChecked();
    fireEvent.click(telegramCheckbox);
    expect(telegramCheckbox).toBeChecked();

    // Save row
    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => expect(lifecyclePayload).not.toBeNull());
    expect(lifecyclePayload).toMatchObject({
      strategy_id: "BB_REV_INV",
      mode: "inverse",
      evaluation_enabled: true,
      paper_enabled: true,
      telegram_enabled: true,
      report_enabled: true,
      auto_promote_enabled: false,
    });
    expect(await screen.findByText(/Lifecycle settings saved for 'BB_REV_INV' \(inverse\)\./i)).toBeInTheDocument();
  });

  it("RB-03: disables evaluation, paper, and telegram for historical_only strategies", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      const body = snapshot[path as keyof typeof snapshot];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ResearchPanel />);
    expect(await screen.findByText("HIST_ONLY_STRAT")).toBeInTheDocument();

    const evalCheck = screen.getByLabelText("Evaluation for HIST_ONLY_STRAT (shadow)");
    const paperCheck = screen.getByLabelText("Paper tracking for HIST_ONLY_STRAT (shadow)");
    const teleCheck = screen.getByLabelText("Telegram alerts for HIST_ONLY_STRAT (shadow)");
    const reportCheck = screen.getByLabelText("Report visibility for HIST_ONLY_STRAT (shadow)");

    expect(evalCheck).toBeDisabled();
    expect(paperCheck).toBeDisabled();
    expect(teleCheck).toBeDisabled();
    expect(reportCheck).not.toBeDisabled();
  });

  it("RB-04: stops and hides strategy via confirmation dialog", async () => {
    let stopPayload: unknown = null;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";

      if (path === "/api/research/retailbot2/strategies" && method === "POST") {
        stopPayload = JSON.parse(String(init?.body));
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: "ok",
              strategy_id: "BB_REV_INV",
              mode: "inverse",
              settings: stopPayload,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      const body = snapshot[path as keyof typeof snapshot];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ResearchPanel />);
    expect((await screen.findAllByText("BB_REV_INV")).length).toBeGreaterThan(0);

    // Click Stop & Hide
    const stopBtn = screen.getByRole("button", { name: "Stop & Hide" });
    fireEvent.click(stopBtn);

    const dialog = screen.getByRole("dialog", { name: "Stop & Hide Strategy" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/This will disable live evaluation, paper tracking, and Telegram alerts/i)).toBeInTheDocument();

    const confirmBtn = within(dialog).getByRole("button", { name: "Stop & Hide Strategy" });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(stopPayload).not.toBeNull());
    expect(stopPayload).toMatchObject({
      strategy_id: "BB_REV_INV",
      mode: "inverse",
      evaluation_enabled: false,
      paper_enabled: false,
      telegram_enabled: false,
      report_enabled: false,
      auto_promote_enabled: false,
    });
    expect(await screen.findByText(/Strategy 'BB_REV_INV' \(inverse\) stopped and hidden\./i)).toBeInTheDocument();
  });

  it("RBAC: viewer role renders view-only badges and suppresses action buttons", async () => {
    mockUseAuth.mockReturnValue({
      user: { id: 3, username: "viewer_charlie", role: "viewer", is_active: true },
      logout: vi.fn(),
      logoutError: null,
    });

    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      const body = snapshot[path as keyof typeof snapshot];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ResearchPanel />);
    expect((await screen.findAllByText("BB_REV_INV")).length).toBeGreaterThan(0);

    // No Save or Stop & Hide buttons
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop & Hide" })).not.toBeInTheDocument();

    // View only label present
    expect(screen.getAllByText("View only").length).toBeGreaterThan(0);
  });
});
