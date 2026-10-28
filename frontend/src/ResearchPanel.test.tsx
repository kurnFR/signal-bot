import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ResearchPanel } from "./ResearchPanel";

const snapshot = {
  "/api/research/retailbot2/positions?limit=100": { positions: [{ id: 1, symbol: "BTCUSDT", mode: "inverse", entry_price: 100, current_price: 110, unrealized_pnl: 10, opened_at: "2026-10-26T10:00:00Z" }] },
  "/api/research/retailbot2/trades?limit=50": { trades: [{ id: 2, symbol: "ETHUSDT", side: "LONG", mode: "shadow", entry_price: 50, exit_price: 55, realized_pnl: 5, closed_at: "2026-10-26T11:00:00Z" }] },
  "/api/research/retailbot2/stats": { by_strategy: [{ strategy: "BB_REV_INV", mode: "inverse", total_trades: 12, wins: 7, losses: 5, total_pnl: 125.5, last_updated: "2026-10-26T12:00:00Z" }], by_mode: { inverse: { realized_pnl: 125.5, closed_count: 12, wins: 7, win_rate: 58.3, open_count: 1 }, shadow: { realized_pnl: -25, closed_count: 4, wins: 1, win_rate: 25, open_count: 0 } } },
  "/api/research/retailbot2/strategies": { strategies: [{ strategy_id: "BB_REV_INV", mode: "inverse", evaluation_enabled: true, paper_enabled: true, telegram_enabled: false, report_enabled: true, auto_promote_enabled: false }] },
};

describe("ResearchPanel", () => {
  beforeEach(() => {
    cleanup();
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
    expect(screen.getAllByText("BB_REV_INV")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Performance by mode" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Performance by strategy" })).toBeInTheDocument();
    expect(screen.getByText("58.3%")).toBeInTheDocument();
    expect(screen.getByText("-25")).toBeInTheDocument();
    expect(screen.getAllByText("125.5")).toHaveLength(2);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining(Object.keys(snapshot)));
  });
});
