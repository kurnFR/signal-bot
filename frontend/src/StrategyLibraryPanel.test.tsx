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

  it("creates a strategy only after the trader explicitly saves the form", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/custom-strategy/create" && method === "POST") return json({ status: "success", name: "new_test_v1", displayName: "New Test", message: "Strategy new_test_v1 saved." });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    fireEvent.click(await screen.findByRole("button", { name: "New strategy" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: "rsi_volume_v1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy display name" }), { target: { value: "New Test" } });
    fireEvent.click(screen.getByRole("button", { name: "Save strategy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
    expect(calls.filter((call) => call.method === "POST" || call.method === "DELETE")).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: "new_test_v1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save strategy" }));
    expect(await screen.findByText("Strategy new_test_v1 saved.")).toBeInTheDocument();
    const request = calls.find((call) => call.path === "/api/custom-strategy/create" && call.method === "POST");
    expect(request?.body).toMatchObject({
      name: "new_test_v1", displayName: "New Test",
      longConditions: [{ indicator: "rsi", operator: "<", value: 30 }],
      shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }],
      slAtrMult: 1.5, riskRewardRatio: 2.5, useTrailingStop: false,
    });
  });

  it("requires confirmation before deleting a saved strategy", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const calls: Array<{ path: string; method: string }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/custom-strategy/rsi_volume_v1" && method === "DELETE") return json({ status: "success", message: "Strategy deleted." });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    const deleteButtons = await screen.findAllByRole("button", { name: "Delete strategy" });
    fireEvent.click(deleteButtons[0]);
    expect(await screen.findByRole("heading", { name: "Delete strategy?" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "DELETE")).toEqual([]);
    const confirmButtons = screen.getAllByRole("button", { name: "Delete strategy" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    expect(await screen.findByText("Strategy deleted.")).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/custom-strategy/rsi_volume_v1" && call.method === "DELETE")).toBe(true);
  });

  it("generates an AI draft only on explicit request and does not save it automatically", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const generated = {
      status: "success",
      provider: "AI Quantitative Heuristic Engine",
      chatResponse: "Draft generated; review before saving.",
      strategy: {
        name: "ai_rsi_volume_v1",
        displayName: "AI RSI Volume",
        description: "Mean reversion with volume confirmation",
        rationale: "Wait for oversold conditions and stronger volume.",
        longConditions: [{ indicator: "rsi", operator: "<", value: 28 }],
        shortConditions: [{ indicator: "rsi", operator: ">", value: 72 }],
        slAtrMult: 2,
        riskRewardRatio: 3,
        useTrailingStop: true,
        trailActivationR: 1.5,
        trailDistanceAtrMult: 1.25,
      },
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/custom-strategy/generate-ai" && method === "POST") return json(generated);
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    const prompt = "Build an RSI mean-reversion strategy with volume confirmation.";
    fireEvent.change(await screen.findByRole("textbox", { name: "AI strategy prompt" }), { target: { value: prompt } });
    expect(calls.some((call) => call.path === "/api/custom-strategy/generate-ai")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Generate draft with AI" }));

    expect(await screen.findByText(/AI draft generated with AI Quantitative Heuristic Engine/i)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Strategy name" })).toHaveValue("ai_rsi_volume_v1");
    expect(screen.getByRole("textbox", { name: "Strategy display name" })).toHaveValue("AI RSI Volume");
    expect(screen.getByRole("textbox", { name: "Long condition 1 value" })).toHaveValue("28");
    expect(screen.getByRole("textbox", { name: "Short condition 1 value" })).toHaveValue("72");
    expect(screen.getByRole("spinbutton", { name: "Stop loss ATR multiple" })).toHaveValue(2);
    const generationCall = calls.find((call) => call.path === "/api/custom-strategy/generate-ai" && call.method === "POST");
    expect(generationCall?.body).toMatchObject({ prompt });
    expect(calls.some((call) => call.path === "/api/custom-strategy/create" && call.method === "POST")).toBe(false);
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
    expect(screen.queryByRole("button", { name: "New strategy" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete strategy" })).not.toBeInTheDocument();\n    expect(screen.queryByRole("button", { name: "Generate draft with AI" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST" || init?.method === "DELETE")).toBe(false);
  });
});
