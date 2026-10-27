import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { NewsAIPanel } from "./NewsAIPanel";

const responses = {
  "/api/news/stats": { bullish_count: 4, bearish_count: 2, neutral_count: 3, average_confidence: 0.82, active_overlay_multiplier: 1.1 },
  "/api/news/signals?limit=50": { signals: [{ id: 1, symbol: "BTCUSDT", headline: "Bitcoin market update", sentiment: "bullish", confidence: 0.9, impact_horizon: "short", published_at: "2026-10-26T10:00:00Z" }] },
  "/api/news/events?limit=30": { events: [{ id: 2, category: "macro", event_text: "Rate decision update", importance: "high", detected_at: "2026-10-26T11:00:00Z" }] },
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

  it("renders saved sentiment, signals, and events without mutation requests", async () => {
    render(<NewsAIPanel />);
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("Rate decision update")).toBeInTheDocument();
    expect(screen.getByText("Bitcoin market update")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining(Object.keys(responses)));
  });

  it("filters signals by headline and symbol", async () => {
    render(<NewsAIPanel />);
    await screen.findByText("Bitcoin market update");
    const input = screen.getByRole("textbox", { name: "Filter News AI signals" });
    await import("@testing-library/react").then(({ fireEvent }) => fireEvent.change(input, { target: { value: "ETH" } }));
    expect(screen.queryByText("Bitcoin market update")).not.toBeInTheDocument();
    expect(screen.getByText("No signals match this filter.")).toBeInTheDocument();
  });
});
