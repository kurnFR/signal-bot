import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { StrategyLibraryPanel } from "./StrategyLibraryPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const payloads: Record<string, unknown> = {
  "/api/custom-strategy/list": { strategies: [{ name: "rsi_volume_v1", displayName: "RSI + Volume", description: "Mean reversion", rationale: "Confirm with volume", longConditions: [{ indicator: "rsi", operator: "<", value: 30 }], shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }], slAtrMult: 1.5, riskRewardRatio: 2.5, useTrailingStop: true }] },
  "/api/custom-strategy/indicators": { indicators: [{ name: "rsi", label: "RSI (14)", category: "Momentum", description: "Relative Strength Index", default: 30, type: "numeric" }], operators: ["<", "<=", ">", ">=", "=="] },
  "/api/backtest/strategies": { strategies: [] },
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
    expect(await screen.findByRole("heading", { name: "Strategy catalog" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });
  it("offers recoverable access errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Forbidden" }), { status: 403, headers: { "content-type": "application/json" } })));
    render(<StrategyLibraryPanel />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((el) => el.textContent?.includes("Your account cannot access the strategy library."))).toBe(true);
  });

  it("searches and filters the backtest strategy catalog and navigates without running a simulation", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const catalog = { strategies: [
      { id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend Following", validated: true, description: "Trend-following EMA cross", params: [{ key: "fast" }], sharedParams: [] },
      { id: "rsi_mean_reversion", baseName: "rsi_mean_reversion", isInverse: false, displayName: "RSI Mean Reversion", category: "Mean Reversion", validated: false, description: "RSI reversal with a wider stop", params: [], sharedParams: [] },
      { id: "custom_momentum", baseName: "custom_momentum", isInverse: false, displayName: "Custom Momentum", category: "Custom Strategy", validated: false, description: "Custom rule strategy", params: [], sharedParams: [] },
    ] };
    const calls: Array<{ path: string; method: string }> = [];
    const onSelectForBacktest = vi.fn();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/backtest/strategies") return json(catalog);
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel onSelectForBacktest={onSelectForBacktest} /></AuthGate>);

    expect(await screen.findByRole("heading", { name: "Strategy catalog" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Trend EMA Crossover" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Strategy catalog category" }), { target: { value: "builtin" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Search strategy catalog" }), { target: { value: "rsi" } });
    expect(await screen.findByRole("heading", { name: "RSI Mean Reversion" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Custom Momentum" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Use in Backtest Studio" }));
    expect(onSelectForBacktest).toHaveBeenCalledWith("rsi_mean_reversion");
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
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
      if (path === "/api/backtest/strategies") return json({ strategies: [] });
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

  it("requires explicit confirmation before replacing a saved strategy definition", async () => {
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
      if (path === "/api/backtest/strategies") return json({ strategies: [] });
      if (path === "/api/custom-strategy/create" && method === "POST") return json({ status: "success", name: "rsi_volume_v1", displayName: "RSI + Volume", message: "Strategy rsi_volume_v1 updated." });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Edit strategy" }));
    expect(await screen.findByRole("heading", { name: "Edit saved strategy" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Strategy name" })).toHaveValue("rsi_volume_v1");
    expect(screen.getByRole("textbox", { name: "Strategy display name" })).toHaveValue("RSI + Volume");
    fireEvent.click(screen.getByRole("button", { name: "Review update" }));
    expect(await screen.findByRole("heading", { name: "Confirm strategy update" })).toBeInTheDocument();
    expect(screen.getByText("rsi_volume_v1", { selector: "strong" })).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/api/custom-strategy/create" && call.method === "POST")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Confirm update" }));
    expect(await screen.findByText("Strategy rsi_volume_v1 updated.")).toBeInTheDocument();
    const request = calls.find((call) => call.path === "/api/custom-strategy/create" && call.method === "POST");
    expect(request?.body).toMatchObject({
      name: "rsi_volume_v1", displayName: "RSI + Volume",
      longConditions: [{ indicator: "rsi", operator: "<", value: 30 }],
      shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }],
      slAtrMult: 1.5, riskRewardRatio: 2.5,
    });
  });

  it("keeps AI-generated strategies as drafts until the user explicitly saves them", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const draft = {
      status: "success", provider: "Offline Quantitative Engine", chatResponse: "Draft ready.",
      strategy: {
        name: "generated_v1", displayName: "Generated Strategy", description: "RSI reversal",
        rationale: "Wait for momentum extremes.", longConditions: [{ indicator: "rsi", operator: "<", value: 25 }],
        shortConditions: [{ indicator: "rsi", operator: ">", value: 75 }], slAtrMult: 2,
        riskRewardRatio: 3, useTrailingStop: true, trailActivationR: 1.5, trailDistanceAtrMult: 1.2,
      },
    };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/backtest/strategies") return json({ strategies: [] });
      if (path === "/api/custom-strategy/generate-ai" && method === "POST") return json(draft);
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    fireEvent.change(await screen.findByRole("textbox", { name: "AI strategy prompt" }), { target: { value: "Use RSI extremes with a wider stop" } });
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Generate draft" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Generate draft" }));
    expect((await screen.findAllByText("Draft ready.")).length).toBeGreaterThan(0);
    expect(screen.getByText("Offline Quantitative Engine")).toBeInTheDocument();
    expect(calls.find((call) => call.path === "/api/custom-strategy/generate-ai" && call.method === "POST")?.body).toMatchObject({
      prompt: "Use RSI extremes with a wider stop",
    });
    fireEvent.click(screen.getByRole("button", { name: "Use generated draft" }));
    expect(screen.getByRole("textbox", { name: "Strategy name" })).toHaveValue("generated_v1");
    expect(screen.getByRole("textbox", { name: "Strategy display name" })).toHaveValue("Generated Strategy");
    expect(await screen.findByText(/nothing has been saved yet/i)).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/custom-strategy/create" && call.method === "POST")).toBe(false);
  });

  it("sends successful AI conversation history and the prior draft on follow-up prompts", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const firstDraft = {
      status: "success", provider: "Offline Quantitative Engine", chatResponse: "First response.",
      strategy: {
        name: "generated_v1", displayName: "Generated Strategy", description: "RSI reversal",
        rationale: "Initial draft.", longConditions: [{ indicator: "rsi", operator: "<", value: 25 }],
        shortConditions: [{ indicator: "rsi", operator: ">", value: 75 }], slAtrMult: 2,
        riskRewardRatio: 3, useTrailingStop: false, trailActivationR: 1, trailDistanceAtrMult: 1.5,
      },
    };
    const secondDraft = {
      ...firstDraft,
      chatResponse: "Refined response.",
      strategy: { ...firstDraft.strategy, slAtrMult: 2.5, rationale: "Wider stop after follow-up." },
    };
    const calls: Array<{ path: string; method: string; body?: any }> = [];
    let generationCount = 0;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/custom-strategy/list") return json(payloads["/api/custom-strategy/list"]);
      if (path === "/api/custom-strategy/indicators") return json(payloads["/api/custom-strategy/indicators"]);
      if (path === "/api/backtest/strategies") return json({ strategies: [] });
      if (path === "/api/custom-strategy/generate-ai" && method === "POST") return json(generationCount++ === 0 ? firstDraft : secondDraft);
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><StrategyLibraryPanel /></AuthGate>);

    const prompt = await screen.findByRole("textbox", { name: "AI strategy prompt" });
    fireEvent.change(prompt, { target: { value: "Create an RSI reversal strategy" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Generate draft" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Generate draft" }));
    expect((await screen.findAllByText("First response.")).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByRole("textbox", { name: "AI strategy prompt" }), { target: { value: "Make the stop loss wider" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Generate draft" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Generate draft" }));
    expect((await screen.findAllByText("Refined response.")).length).toBeGreaterThan(0);
    expect(screen.getByText("Create an RSI reversal strategy")).toBeInTheDocument();

    const generationCalls = calls.filter((call) => call.path === "/api/custom-strategy/generate-ai" && call.method === "POST");
    expect(generationCalls).toHaveLength(2);
    expect(generationCalls[1].body).toMatchObject({
      prompt: "Make the stop loss wider",
      history: [
        { role: "user", content: "Create an RSI reversal strategy" },
        { role: "assistant", content: "First response." },
      ],
      currentConfig: { name: "generated_v1", slAtrMult: 2 },
    });
    expect(calls.some((call) => call.path === "/api/custom-strategy/create" && call.method === "POST")).toBe(false);
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
      if (path === "/api/backtest/strategies") return json({ strategies: [] });
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
      if (path === "/api/backtest/strategies") return json({ strategies: [] });
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
    expect(screen.queryByRole("button", { name: "Generate draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete strategy" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST" || init?.method === "DELETE")).toBe(false);
  });

  it("makes the strategy catalog readable for authenticated viewers without mutation controls", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const catalog = { strategies: [
      { id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend Following", validated: true, description: "Trend-following EMA cross", params: [{ key: "fast" }], sharedParams: [] },
    ] };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(new Response(JSON.stringify(profile), { status: 200, headers: { "content-type": "application/json" } }));
      if (path === "/api/backtest/strategies") return Promise.resolve(new Response(JSON.stringify(catalog), { status: 200, headers: { "content-type": "application/json" } }));
      const payload = payloads[path];
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AuthGate>
        <StrategyLibraryPanel onSelectForBacktest={vi.fn()} onSelectForPaper={vi.fn()} />
      </AuthGate>
    );

    expect(await screen.findByRole("heading", { name: "Strategy catalog" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Trend EMA Crossover" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use in Backtest Studio" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deploy to Paper" })).not.toBeInTheDocument();
  });

  it("loads and allows deployment when mounted with only onSelectForPaper callback", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const onSelectPaper = vi.fn();
    const catalog = { strategies: [
      { id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend Following", validated: true, description: "Trend-following EMA cross", params: [{ key: "fast" }], sharedParams: [] },
    ] };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(new Response(JSON.stringify(profile), { status: 200, headers: { "content-type": "application/json" } }));
      if (path === "/api/backtest/strategies") return Promise.resolve(new Response(JSON.stringify(catalog), { status: 200, headers: { "content-type": "application/json" } }));
      const payload = payloads[path];
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AuthGate>
        <StrategyLibraryPanel onSelectForPaper={onSelectPaper} />
      </AuthGate>
    );

    expect(await screen.findByRole("heading", { name: "Strategy catalog" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Trend EMA Crossover" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use in Backtest Studio" })).not.toBeInTheDocument();
    const deployButtons = screen.getAllByRole("button", { name: "Deploy to Paper" });
    expect(deployButtons.length).toBeGreaterThan(0);
    fireEvent.click(deployButtons[0]);
    expect(onSelectPaper).toHaveBeenCalledWith("trend_ema_v1");
  });

  it("fails closed when saved strategy rows are malformed", async () => {
    const malformed: Record<string, unknown> = {
      ...payloads,
      "/api/custom-strategy/list": { strategies: [{ name: 42, longConditions: "not-an-array" }] },
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      return Promise.resolve(new Response(JSON.stringify(malformed[path]), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    }));
    render(<StrategyLibraryPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByRole("heading", { name: "RSI + Volume" })).not.toBeInTheDocument();
  });

  it("fails closed when indicator metadata contains malformed rows", async () => {
    const malformed: Record<string, unknown> = {
      ...payloads,
      "/api/custom-strategy/indicators": { indicators: [{ name: "", label: "Must not render", default: Number.NaN }], operators: ["<"] },
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      return Promise.resolve(new Response(JSON.stringify(malformed[path]), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    }));
    render(<StrategyLibraryPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByText("Must not render")).not.toBeInTheDocument();
  });

  it("renders catalog even when mounted without callback props", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const catalog = { strategies: [
      { id: "trend_ema_v1", baseName: "trend_ema_v1", isInverse: false, displayName: "Trend EMA Crossover", category: "Trend Following", validated: true, description: "Trend-following EMA cross", params: [{ key: "fast" }], sharedParams: [] },
    ] };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(new Response(JSON.stringify(profile), { status: 200, headers: { "content-type": "application/json" } }));
      if (path === "/api/backtest/strategies") return Promise.resolve(new Response(JSON.stringify(catalog), { status: 200, headers: { "content-type": "application/json" } }));
      const payload = payloads[path];
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AuthGate>
        <StrategyLibraryPanel />
      </AuthGate>
    );

    expect(await screen.findByRole("heading", { name: "Strategy catalog" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Trend EMA Crossover" })).toBeInTheDocument();
  });
});
