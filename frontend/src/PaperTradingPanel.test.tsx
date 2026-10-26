import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PaperTradingPanel } from "./PaperTradingPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function json(payload: unknown, status = 200) { return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }); }
const fixture = {
  "/api/paper/positions": { positions: [{ id: 7, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "demo_strategy", direction: "LONG", entry_price: 60000, current_price: 60500, stop_loss: 59000, take_profit: 62000, quantity: 0.1, unrealized_pnl_pct: 0.8, unrealized_r: 0.5, allocated_capital: 5000, status: "OPEN", last_update_time: "2026-10-26T10:00:00Z" }] },
  "/api/paper/configs": { configs: [{ id: 2, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "demo_strategy", is_active: true, allocated_capital: 5000, risk_per_trade_pct: 1, telegram_enabled: false, report_enabled: true, created_at: "2026-10-25T10:00:00Z" }] },
  "/api/paper/orders?limit=20": { orders: [{ id: 10, symbol: "BTCUSDT", market: "spot", timeframe: "1h", side: "BUY", order_type: "MARKET", status: "FILLED", quantity: 0.1, price: 60000, created_at: "2026-10-26T09:00:00Z", state_reason: null }] },
  "/api/paper/trades?limit=20": { trades: [{ id: 11, symbol: "ETHUSDT", market: "spot", timeframe: "1h", strategy_name: "demo_strategy", direction: "LONG", entry_price: 3000, exit_price: 3100, net_pnl: 10, net_return_pct: 2, r_multiple: 1.5, exit_reason: "TAKE_PROFIT", entry_time_wib: "2026-10-25 10:00", exit_time_wib: "2026-10-25 12:00" }] },
  "/api/paper/metrics": { metrics: { totalTrades: 10, wins: 6, losses: 4, winRatePct: 60, cumulativeR: 4.5, expectancyR: 0.45, netPnl: 125.5, byStrategy: [] } },
  "/api/paper/circuit-breakers": { status: "NORMAL", emergency_stop_active: false, emergency_stop_reason: null, active_positions: 1, max_concurrent_positions: 5, today_realized_pnl_usd: 25, max_daily_loss_usd: 250, open_notional_usd: 6000, max_total_notional_usd: 25000, can_open_new_position: true, message: "Risk checks passed" },
};

describe("read-only Paper Trading panel", () => {
  it("renders ledger, performance and risk data without issuing mutations", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname + new URL(String(input), "http://localhost").search;
      const payload = fixture[path as keyof typeof fixture];
      return Promise.resolve(payload ? json(payload) : json({ detail: "Unexpected route" }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PaperTradingPanel />);
    expect(await screen.findByRole("heading", { name: "Open positions" })).toBeInTheDocument();
    expect((await screen.findAllByText("BTCUSDT")).length).toBeGreaterThan(0);
    expect(screen.getByText("demo_strategy", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("$125.50")).toBeInTheDocument();
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
