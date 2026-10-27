import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MasterDataPanel } from "./MasterDataPanel";

const responses = {
  "/api/master-data/coverage": {
    summary: { totalCandles: 1200, distinctSymbols: 2, coverageEntries: 3 },
    coverage: [{ symbol: "BTCUSDT", market: "spot", timeframe: "1h", candleCount: 1000, featureCount: 950, hasFeatures: true, minOpenTime: null, maxCloseTime: null, startDate: "2026-10-01 00:00:00", endDate: "2026-10-26 12:00:00" }],
  },
  "/api/master-data/jobs": {
    jobs: [{ id: "job-1", type: "backfill", symbol: "BTCUSDT", market: "spot", timeframe: "1h", status: "completed", progress: 100, message: "Completed successfully", created_at: 1793000000 }],
  },
};

describe("MasterDataPanel", () => {
  beforeEach(() => {
    cleanup();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error("Master Data preview must be GET-only");
      const body = responses[path as keyof typeof responses];
      if (!body) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders coverage and job history using GET requests only", async () => {
    render(<MasterDataPanel />);
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("Completed successfully")).toBeInTheDocument();
    expect(screen.getByText("1,200")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining(Object.keys(responses)));
  });

  it("filters datasets by symbol, market, or timeframe", async () => {
    render(<MasterDataPanel />);
    await screen.findByText("BTCUSDT");
    fireEvent.change(screen.getByRole("textbox", { name: "Filter datasets" }), { target: { value: "ETH" } });
    expect(screen.getByText("No datasets match this filter.")).toBeInTheDocument();
  });
});
