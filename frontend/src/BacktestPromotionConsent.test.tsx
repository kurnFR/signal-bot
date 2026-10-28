import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { BacktestStudioPanel } from "./BacktestStudioPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("Backtest auto-promotion side-effect consent", () => {
  it("requires explicit acknowledgement when the saved policy may activate a strategy or send Telegram", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const strategies = { strategies: [{ id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend", validated: false, description: "Trend strategy", params: [], sharedParams: [] }] };
    const run = { id: 42, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1", dataSegment: "holdout", totalTrades: 2, winRatePct: 50, expectancyR: 0.25, profitFactor: 1.5, maxDrawdownR: 1.2, createdAt: "2026-10-26T10:00:00Z" };
    const policy = { enabled: true, min_test_trades: 30, min_profit_factor: 1.2, min_expectancy_r: 0.05, max_drawdown_r: 8, min_rank_score: 0.1, require_holdout: true, send_telegram: true };
    const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
    const calls: Array<{ path: string; method: string }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/backtest/strategies") return json(strategies);
      if (path === "/api/backtest/history?limit=100") return json({ runs: [run] });
      if (path === "/api/backtest/runs/42") return json({ run: { ...run, params: {} }, trades: [] });
      if (path === "/api/backtest/promotion-policy") return json({ policy });
      if (path === "/api/backtest/promotion-audit?limit=5") return json({ events: [] });
      if (path === "/api/backtest/run" && method === "POST") return json({ runId: 43, symbol: "BTCUSDT", market: "spot", timeframe: "1d", strategy: "trend_ema_v1", dataSegment: "holdout", totalCandles: 100, metrics: { total_trades: 10, win_rate_pct: 60, expectancy_r: 0.2, profit_factor: 1.4, max_drawdown_r: 1.5 }, trades: [] });
      return json({ detail: "Unexpected API path" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><BacktestStudioPanel /></AuthGate>);
    const reviewButton = await screen.findByRole("button", { name: "Review backtest run" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Backtest strategy" })).toHaveValue("trend_ema_v1"));
    await waitFor(() => expect(reviewButton).toBeEnabled());
    fireEvent.click(reviewButton);

    expect(await screen.findByText("Auto-promotion is enabled")).toBeInTheDocument();
    expect(screen.getByText(/This run may activate a paper strategy/)).toBeInTheDocument();
    const runButton = screen.getByRole("button", { name: "Run backtest" });
    expect(runButton).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Acknowledge auto-promotion side effects" })).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/backtest/run" && call.method === "POST")).toBe(false);

    fireEvent.click(screen.getByRole("checkbox", { name: "Acknowledge auto-promotion side effects" }));
    expect(runButton).toBeEnabled();
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });
});
