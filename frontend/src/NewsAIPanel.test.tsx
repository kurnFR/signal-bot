import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NewsAIPanel } from "./NewsAIPanel";

const responses = {
  "/api/news/stats": {
    total_events_collected: 120, events_evaluated_by_ai: 90, high_confidence_signals: 12,
    avg_confidence: 82.5, trades_opened: 4, pending_signals: 2,
    performance: { closed_trades: 3, wins: 2, losses: 1, win_rate: 66.7, expectancy_r: 0.4, profit_factor: 1.8, total_r: 1.2, net_pnl: 125, max_drawdown_r: 0.7 },
    heartbeats: [{ collector_name: "news_poller", status: "ok", detail: null, last_beat_time: "2026-10-26T12:00:00" }],
  },
  "/api/news/signals?limit=50": { signals: [{ id: 1, symbol: "BTCUSDT", market: "spot", timeframe: "1h", bias: "bullish", confidence: 90, reasoning: "Positive catalyst", invalidation_condition: null, time_horizon: "short", trade_timing: "immediate", acted_on: true, paper_position_id: 7, skip_reason: null, created_at: "2026-10-26T10:00:00", headline: "Bitcoin market update", url: null, source: "Test Wire", category: "crypto", country: null, impact: "high", scheduled_at: null, actual_value: null, forecast_value: null, previous_value: null }] },
  "/api/news/events?limit=30": { events: [{ id: 2, source: "Calendar", category: "macro", headline: "Rate decision update", url: null, symbols: "BTCUSDT", country: "US", impact: "high", published_at: "2026-10-26T11:00:00", scheduled_at: null, actual_value: null, forecast_value: null, previous_value: null, processed_at: null, fetched_at: "2026-10-26T11:05:00" }] },
};

describe("NewsAIPanel", () => {
  beforeEach(() => {
    cleanup();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error("News AI preview must be GET-only");
      const body = responses[path as keyof typeof responses];
      if (!body) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders actual backend-shaped stats, signals, events, and heartbeats without mutation requests", async () => {
    render(<NewsAIPanel />);
    expect((await screen.findAllByText("BTCUSDT")).length).toBe(2);
    expect(screen.getByText("Rate decision update")).toBeInTheDocument();
    expect(screen.getByText("Bitcoin market update")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("news_poller")).toBeInTheDocument();
    expect(screen.getByText("66.7%")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining(Object.keys(responses)));
  });

  it("filters signals by headline and symbol", async () => {
    render(<NewsAIPanel />);
    await screen.findByText("Bitcoin market update");
    const input = screen.getByRole("textbox", { name: "Filter News AI signals" });
    fireEvent.change(input, { target: { value: "ETH" } });
    expect(screen.queryByText("Bitcoin market update")).not.toBeInTheDocument();
    expect(screen.getByText("No signals match this filter.")).toBeInTheDocument();
  });

  it("renders nullable metrics safely when no realized trades or confidence exist", async () => {
    const emptyStats = { ...responses["/api/news/stats"], avg_confidence: null, performance: { ...responses["/api/news/stats"].performance, win_rate: null, expectancy_r: null, profit_factor: null, closed_trades: 0, total_r: 0 } };
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error("News AI preview must be GET-only");
      const body = path === "/api/news/stats" ? emptyStats : responses[path as keyof typeof responses];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    render(<NewsAIPanel />);
    expect(await screen.findByText("Collected events")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows empty states when stats, signals, and events load successfully with no rows", async () => {
    const emptyResponses = {
      "/api/news/stats": {
        total_events_collected: 0, events_evaluated_by_ai: 0, high_confidence_signals: 0,
        avg_confidence: null, trades_opened: 0, pending_signals: 0,
        performance: { closed_trades: 0, wins: 0, losses: 0, win_rate: null, expectancy_r: null, profit_factor: null, total_r: 0, net_pnl: 0, max_drawdown_r: 0 },
        heartbeats: [],
      },
      "/api/news/signals?limit=50": { signals: [] },
      "/api/news/events?limit=30": { events: [] },
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const body = emptyResponses[String(input) as keyof typeof emptyResponses];
      if (!body) throw new Error("Unexpected endpoint: " + String(input));
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    }));
    render(<NewsAIPanel />);

    expect(await screen.findByText("No signals match this filter.")).toBeInTheDocument();
    expect(screen.getByText("No recent events returned.")).toBeInTheDocument();
    expect(screen.getByText("No collector heartbeat records returned.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("recovers from a temporary API failure after Retry without issuing mutations", async () => {
    let recovered = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error("News AI preview must be GET-only");
      if (!recovered) return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), {
        status: 503, headers: { "content-type": "application/json" },
      }));
      const body = responses[path as keyof typeof responses];
      if (!body) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NewsAIPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/check API\/database health, then retry/i);
    recovered = true;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));

    expect(await screen.findByText("Bitcoin market update")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(6);
  });
});
