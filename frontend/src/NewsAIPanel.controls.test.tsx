import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NewsAIPanel } from "./NewsAIPanel";

const mockUseAuth = vi.fn();
vi.mock("./AuthGate", () => ({ useAuth: () => mockUseAuth() }));

const base = { market: "spot", timeframe: "1h", bias: "bullish", confidence: 90, reasoning: "r", invalidation_condition: null,
  time_horizon: "short", trade_timing: "immediate", paper_position_id: null, created_at: "2026-10-26T10:00:00", url: null,
  source: "Wire", category: "crypto", country: null, impact: "high", scheduled_at: null, actual_value: null, forecast_value: null, previous_value: null };
const signals = [
  { ...base, id: 11, symbol: "PENDUSDT", headline: "Pending one", acted_on: false, skip_reason: null },
  { ...base, id: 12, symbol: "DONEUSDT", headline: "Dismissed one", acted_on: true, skip_reason: "dismissed_by_operator" },
  { ...base, id: 13, symbol: "TRADEUSDT", headline: "Traded one", acted_on: true, skip_reason: null, paper_position_id: 3 },
];
const stats = { total_events_collected: 1, events_evaluated_by_ai: 1, high_confidence_signals: 1, avg_confidence: 80, trades_opened: 1,
  pending_signals: 1, performance: { closed_trades: 0, wins: 0, losses: 0, win_rate: 0, expectancy_r: 0, profit_factor: 0, total_r: 0, net_pnl: 0, max_drawdown_r: 0 }, heartbeats: [] };

let calls: Array<{ path: string; method: string }>;
let dismissResponse: () => Response;

beforeEach(() => {
  cleanup();
  calls = [];
  dismissResponse = () => new Response(JSON.stringify({ status: "dismissed", id: 11 }), { status: 200, headers: { "content-type": "application/json" } });
  vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method });
    const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "content-type": "application/json" } }));
    if (path === "/api/news/stats") return json(stats);
    if (path.startsWith("/api/news/signals?")) return json({ signals });
    if (path.startsWith("/api/news/events")) return json({ events: [] });
    if (path === "/api/news/settings") return json({ min_confidence: 75, max_trades_per_day: 3, source: "default", defaults: { min_confidence: 75, max_trades_per_day: 3 }, updated_by: null, updated_at: null });
    if (method === "POST" && path === "/api/news/signals/11/dismiss") return Promise.resolve(dismissResponse());
    throw new Error("Unexpected: " + method + " " + path);
  }));
});
afterEach(() => vi.unstubAllGlobals());

const user = (role: string) => mockUseAuth.mockReturnValue({ user: { id: 1, username: "u", role } });

describe("NewsAIPanel operator controls", () => {
  it("labels dismissed signals distinctly from traded ones", async () => {
    user("viewer");
    render(<NewsAIPanel />);
    expect(await screen.findByText("Dismissed")).toBeInTheDocument();
    expect(screen.getByText("Acted on")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("gives viewers no actions and no admin settings call", async () => {
    user("viewer");
    render(<NewsAIPanel />);
    await screen.findByText("PENDUSDT");
    expect(screen.queryByRole("button", { name: /Dismiss/ })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/news/settings")).toBe(false);
  });

  it("lets a trader dismiss only pending signals, after confirmation, without settings access", async () => {
    user("trader");
    render(<NewsAIPanel />);
    const buttons = await screen.findAllByRole("button", { name: /Dismiss .* signal/ });
    expect(buttons).toHaveLength(1);                          // only the pending row
    fireEvent.click(buttons[0]);
    expect(calls.some((c) => c.method === "POST")).toBe(false); // confirm first
    fireEvent.click(screen.getByRole("button", { name: "Confirm dismiss PENDUSDT" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/news/signals/11/dismiss")).toBe(true));
    expect(calls.some((c) => c.path === "/api/news/settings")).toBe(false);
  });

  it("shows the server's conflict message when the executor already picked the signal", async () => {
    user("trader");
    dismissResponse = () => new Response(JSON.stringify({ detail: "Signal is being processed right now. Try again shortly." }), { status: 409, headers: { "content-type": "application/json" } });
    render(<NewsAIPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /Dismiss PENDUSDT signal/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm dismiss PENDUSDT" }));
    expect(await screen.findByText(/being processed right now/)).toBeInTheDocument();
  });

  it("shows the guardrails card to admins only", async () => {
    user("admin");
    render(<NewsAIPanel />);
    expect(await screen.findByRole("heading", { name: /News AI guardrails/ })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/news/settings")).toBe(true);
  });
});
