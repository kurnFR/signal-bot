import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { StrategyLibraryPanel } from "./StrategyLibraryPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const payloads: Record<string, unknown> = {
  "/api/custom-strategy/list": { strategies: [{ name: "rsi_volume_v1", displayName: "RSI + Volume", description: "Mean reversion", rationale: "Confirm with volume", longConditions: [{ indicator: "rsi", operator: "<", value: 30 }], shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }], slAtrMult: 1.5, riskRewardRatio: 2.5, useTrailingStop: true }] },
  "/api/custom-strategy/indicators": { indicators: [{ name: "rsi", label: "RSI (14)", category: "Momentum", description: "Relative Strength Index", default: 30, type: "numeric" }], operators: ["<", "<=", ">", ">=", "=="] },
};
describe("read-only strategy library", () => {
  it("renders saved strategy rules and backend indicator metadata using GET only", async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => Promise.resolve(new Response(JSON.stringify(payloads[path]), { status: 200, headers: { "content-type": "application/json" } })));
    vi.stubGlobal("fetch", fetchMock);
    render(<StrategyLibraryPanel />);
    expect(await screen.findByRole("heading", { name: "RSI + Volume" })).toBeInTheDocument();
    expect(screen.getByText("Mean reversion")).toBeInTheDocument();
    expect(screen.getByText("rsi < 30")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Indicator catalog" })).toBeInTheDocument();
    expect(screen.getByText("RSI (14)")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });
  it("offers recoverable access errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Forbidden" }), { status: 403, headers: { "content-type": "application/json" } })));
    render(<StrategyLibraryPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your account cannot access the strategy library.");
  });

  it("runs a non-persisted quick test only after a trader clicks the action", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const quickResult = { status: "success", symbol: "BTCUSDT", market: "spot", timeframe: "1h", totalCandles: 1000, totalTrades: 12, winRate: 0.65, winRatePct: 65, netR: 2.4, profitFactor: 1.4, maxDrawdownR: 3.1, expectancyR: 0.2 };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/custom-strategy/quick-test" && method === "POST") return json(quickResult);
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    expect(await screen.findByRole("button", { name: "Quick test" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Quick test" }));
    expect(await screen.findByRole("heading", { name: "Quick-test result · BTCUSDT spot 1h" })).toBeInTheDocument();
    expect(screen.getByText("65.00%")).toBeInTheDocument();
    const request = calls.find((call) => call.path === "/api/custom-strategy/quick-test" && call.method === "POST");
    expect(request?.body).toMatchObject({
      symbol: "BTCUSDT", market: "spot", timeframe: "1h",
      strategy: { name: "rsi_volume_v1", displayName: "RSI + Volume", slAtrMult: 1.5, riskRewardRatio: 2.5 },
    });
  });

  it("keeps strategy quick tests unavailable to viewers", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      const payload = path === "/api/auth/me" ? profile : payloads[path];
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    expect(await screen.findByText(/restricted to admin and trader roles/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quick test" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
});
