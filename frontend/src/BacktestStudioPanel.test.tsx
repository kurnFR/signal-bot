import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BacktestStudioPanel } from "./BacktestStudioPanel";

const strategies = { strategies: [{ id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend Following", validated: false, description: "Trend strategy", params: [], sharedParams: [] }] };
const run = { id: 42, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1", dataSegment: "holdout", totalTrades: 2, winRatePct: 50, expectancyR: 0.25, profitFactor: 1.5, maxDrawdownR: 1.2, createdAt: "2026-10-26T10:00:00Z" };
const history = { runs: [run] };
const details = { run: { ...run, params: { TREND_EMA_FAST: 20 } }, trades: [{ direction: "LONG", entryTime: "2026-10-26T09:00:00Z", entryTimeWib: "2026-10-26 16:00:00", entryPrice: 100, exitTime: "2026-10-26T10:00:00Z", exitTimeWib: "2026-10-26 17:00:00", exitPrice: 102, exitReason: "take_profit", stopLoss: 98, takeProfit: 102, netReturnPct: 1.8, rMultiple: 1, holdingBars: 1 }] };
function responseFor(url: string) {
  const parsed = new URL(url, "http://localhost");
  const payload = parsed.pathname === "/api/backtest/strategies" ? strategies : parsed.pathname === "/api/backtest/history" ? history : parsed.pathname === "/api/backtest/runs/42" ? details : {};
  return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
}
describe("BacktestStudioPanel", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      return Promise.resolve(responseFor(url));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("shows saved run metrics, strategy metadata, and closed trades using GET only", async () => {
    render(<BacktestStudioPanel />);
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(await screen.findByText("take_profit")).toBeInTheDocument();
    expect(screen.getByText("Trend EMA Crossover")).toBeInTheDocument();
    expect(screen.getByText("Stored run parameters")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method === "GET")).toBe(true);
  });
  it("filters saved runs without changing server state", async () => {
    render(<BacktestStudioPanel />);
    await screen.findByText("take_profit");
    fireEvent.change(screen.getByLabelText("Filter history"), { target: { value: "ETHUSDT" } });
    expect(screen.getByText("No saved runs match this filter.")).toBeInTheDocument();
  });
});
