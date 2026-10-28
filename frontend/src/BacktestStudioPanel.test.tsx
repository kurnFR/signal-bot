import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { BacktestStudioPanel } from "./BacktestStudioPanel";

const strategies = { strategies: [{ id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend Following", validated: false, description: "Trend strategy", params: [], sharedParams: [] }, { id: "rsi_mean_reversion", baseName: "rsi_mean_reversion", isInverse: false, displayName: "RSI Mean Reversion", category: "Mean Reversion", validated: false, description: "RSI reversal strategy", params: [], sharedParams: [] }] };
const run = { id: 42, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1", dataSegment: "holdout", totalTrades: 2, winRatePct: 50, expectancyR: 0.25, profitFactor: 1.5, maxDrawdownR: 1.2, createdAt: "2026-10-26T10:00:00Z" };
const history = { runs: [run] };
const promotionPolicy = { enabled: false, min_test_trades: 30, min_profit_factor: 1.2, min_expectancy_r: 0.05, max_drawdown_r: 8, min_rank_score: 0.1, require_holdout: true, send_telegram: true };
const details = { run: { ...run, params: { TREND_EMA_FAST: 20 } }, trades: [{ direction: "LONG", entryTime: "2026-10-26T09:00:00Z", entryTimeWib: "2026-10-26 16:00:00", entryPrice: 100, exitTime: "2026-10-26T10:00:00Z", exitTimeWib: "2026-10-26 17:00:00", exitPrice: 102, exitReason: "take_profit", stopLoss: 98, takeProfit: 102, netReturnPct: 1.8, rMultiple: 1, holdingBars: 1 }] };
function responseFor(url: string) {
  const parsed = new URL(url, "http://localhost");
  const payload = parsed.pathname === "/api/backtest/strategies" ? strategies : parsed.pathname === "/api/backtest/history" ? history : parsed.pathname === "/api/backtest/runs/42" ? details : parsed.pathname === "/api/backtest/promotion-policy" ? { policy: promotionPolicy } : parsed.pathname === "/api/backtest/promotion-audit" ? { events: [] } : {};
  return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
}
describe("BacktestStudioPanel", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method ?? "GET").toBe("GET");
      return Promise.resolve(responseFor(url));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("shows saved run metrics, strategy metadata, and closed trades using GET only", async () => {
    render(<BacktestStudioPanel />);
    expect((await screen.findAllByText("BTCUSDT")).length).toBeGreaterThan(0);
    expect(await screen.findByText("take_profit")).toBeInTheDocument();
    expect(screen.getAllByText("Trend EMA Crossover").length).toBeGreaterThan(0);
    expect(screen.getByText("Stored run parameters")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method === "GET")).toBe(true);
  });
  it("preselects a catalog strategy without executing a backtest", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(new Response(JSON.stringify(profile), { status: 200, headers: { "content-type": "application/json" } }));
      return Promise.resolve(responseFor(path));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onInitialStrategyApplied = vi.fn();
    render(<AuthGate><BacktestStudioPanel initialStrategyId="rsi_mean_reversion" onInitialStrategyApplied={onInitialStrategyApplied} /></AuthGate>);
    const strategySelect = await screen.findByRole("combobox", { name: "Backtest strategy" });
    await waitFor(() => expect(strategySelect).toHaveValue("rsi_mean_reversion"));
    expect(onInitialStrategyApplied).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.every((call) => call[1]?.method == null || call[1]?.method === "GET")).toBe(true);
  });

  it("filters saved runs without changing server state", async () => {
    render(<BacktestStudioPanel />);
    await screen.findByText("take_profit");
    fireEvent.change(screen.getByLabelText("Filter history"), { target: { value: "ETHUSDT" } });
    expect(screen.getByText("No saved runs match this filter.")).toBeInTheDocument();
  });

  it("requires confirmation before an admin/trader can run a saved backtest", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const newRun = { ...run, id: 43, symbol: "ETHUSDT", timeframe: "1h" };
    const execution = {
      runId: 43, symbol: "ETHUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1",
      dataSegment: "holdout", totalCandles: 100,
      metrics: { total_trades: 10, win_rate_pct: 60, expectancy_r: 0.2, profit_factor: 1.4, max_drawdown_r: 1.5 },
      trades: Array.from({ length: 10 }, () => ({})),
    };
    let runCreated = false;
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/backtest/strategies") return json(strategies);
      if (path === "/api/backtest/history?limit=100") return json({ runs: runCreated ? [newRun, run] : [run] });
      if (path === "/api/backtest/runs/42") return json(details);
      if (path === "/api/backtest/runs/43") return json({ run: { ...newRun, params: {} }, trades: [] });
      if (path === "/api/backtest/run" && method === "POST") { runCreated = true; return json(execution); }
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><BacktestStudioPanel /></AuthGate>);

    expect(await screen.findByRole("heading", { name: "Run a backtest" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Backtest symbol" }), { target: { value: "ethusdt" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Backtest timeframe" }), { target: { value: "1h" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Initial capital" }), { target: { value: "7500" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Risk per trade" }), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Review backtest run" }));

    expect(await screen.findByRole("heading", { name: "Confirm backtest run" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));
    expect(await screen.findByText("Backtest completed and saved as run #43.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Latest execution result" })).toBeInTheDocument();
    await waitFor(() => expect(calls.find((call) => call.path === "/api/backtest/run" && call.method === "POST")?.body).toEqual({
      symbol: "ETHUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1",
      data_segment: "holdout", initial_capital: 7500, risk_per_trade_pct: 1.5, save_to_db: true, params: {},
    }));
  });

  it("does not claim a backtest was saved when the API returns a null run ID", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const execution = {
      runId: null, symbol: "BTCUSDT", market: "spot", timeframe: "1d", strategy: "trend_ema_v1",
      dataSegment: "holdout", totalCandles: 80,
      metrics: { total_trades: 3, win_rate_pct: 33.3, expectancy_r: -0.1, profit_factor: 0.8, max_drawdown_r: 2.1 },
      trades: [{}, {}, {}],
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/backtest/strategies") return json(strategies);
      if (path === "/api/backtest/history?limit=100") return json(history);
      if (path === "/api/backtest/runs/42") return json(details);
      if (path === "/api/backtest/run" && method === "POST") return json(execution);
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><BacktestStudioPanel /></AuthGate>);

    expect(await screen.findByRole("heading", { name: "Strategy parameters" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review backtest run" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run backtest" }));
    expect(await screen.findByText(/persistence was not confirmed/i)).toBeInTheDocument();
    expect(screen.getByText("NOT CONFIRMED SAVED")).toBeInTheDocument();
  });

  it("keeps backtest execution unavailable to viewers", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      const payload = path === "/api/auth/me" ? profile : path === "/api/backtest/strategies" ? strategies : path === "/api/backtest/history?limit=100" ? history : path === "/api/backtest/runs/42" ? details : null;
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><BacktestStudioPanel /></AuthGate>);

    expect(await screen.findByText(/restricted to admin and trader roles/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review backtest run" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("requires confirmation before an admin/trader can run a saved backtest", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const newRun = { ...run, id: 43, symbol: "ETHUSDT", timeframe: "1h" };
    const execution = {
      runId: 43, symbol: "ETHUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1",
      dataSegment: "holdout", totalCandles: 100,
      metrics: { total_trades: 10, win_rate_pct: 60, expectancy_r: 0.2, profit_factor: 1.4, max_drawdown_r: 1.5 },
      trades: Array.from({ length: 10 }, () => ({})),
    };
    let runCreated = false;
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/backtest/strategies") return json(strategies);
      if (path === "/api/backtest/history?limit=100") return json({ runs: runCreated ? [newRun, run] : [run] });
      if (path === "/api/backtest/runs/42") return json(details);
      if (path === "/api/backtest/runs/43") return json({ run: { ...newRun, params: {} }, trades: [] });
      if (path === "/api/backtest/run" && method === "POST") { runCreated = true; return json(execution); }
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><BacktestStudioPanel /></AuthGate>);

    expect(await screen.findByRole("heading", { name: "Run a backtest" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Backtest symbol" }), { target: { value: "ethusdt" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Backtest timeframe" }), { target: { value: "1h" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Initial capital" }), { target: { value: "7500" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Risk per trade" }), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Review backtest run" }));

    expect(await screen.findByRole("heading", { name: "Confirm backtest run" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));
    expect(await screen.findByText("Backtest completed and saved as run #43.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Latest execution result" })).toBeInTheDocument();
    await waitFor(() => expect(calls.find((call) => call.path === "/api/backtest/run" && call.method === "POST")?.body).toEqual({
      symbol: "ETHUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1",
      data_segment: "holdout", initial_capital: 7500, risk_per_trade_pct: 1.5, save_to_db: true, params: {},
    }));
  });

  it("keeps backtest execution unavailable to viewers", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      const payload = path === "/api/auth/me" ? profile : path === "/api/backtest/strategies" ? strategies : path === "/api/backtest/history?limit=100" ? history : path === "/api/backtest/runs/42" ? details : null;
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><BacktestStudioPanel /></AuthGate>);

    expect(await screen.findByText(/restricted to admin and trader roles/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review backtest run" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("recovers from a temporary history API failure after Retry using GET-only requests", async () => {
    let recovered = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method ?? "GET").toBe("GET");
      if (!recovered && String(input).includes("/api/backtest/history")) {
        return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), {
          status: 503, headers: { "content-type": "application/json" },
        }));
      }
      return Promise.resolve(responseFor(String(input)));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BacktestStudioPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/check API\/database health, then retry/i);
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();

    recovered = true;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));

    expect(await screen.findByText("take_profit")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(5);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
});
