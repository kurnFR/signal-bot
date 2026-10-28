import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { MasterDataPanel } from "./MasterDataPanel";

const responses = {
  "/api/master-data/coverage": {
    summary: { totalCandles: 1200, distinctSymbols: 2, coverageEntries: 3 },
    coverage: [{ symbol: "BTCUSDT", market: "spot", timeframe: "1h", candleCount: 1000, featureCount: 950, hasFeatures: true, minOpenTime: null, maxCloseTime: null, startDate: "2026-10-01 00:00:00", endDate: "2026-10-26 12:00:00" }],
  },
  "/api/master-data/jobs": {
    jobs: [{ id: "job-1", type: "backfill", symbol: "BTCUSDT", market: "spot", timeframe: "1h", status: "completed", progress: 100, message: "Completed successfully", created_at: 1793000000 }],
  },
  "/api/crypto/popular": {
    market: "spot",
    items: [{ symbol: "SOLUSDT", baseAsset: "SOL", quoteAsset: "USDT", market: "spot", lastPrice: 150, priceChangePercent: 2.5, highPrice: 155, lowPrice: 145, volume: 10000, quoteVolume: 1500000 }],
  },
};

describe("MasterDataPanel", () => {
  beforeEach(() => {
    cleanup();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
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
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.map(([path]) => new URL(String(path), "http://localhost").pathname)).toEqual(expect.arrayContaining(Object.keys(responses)));
  });

  it("filters datasets by symbol, market, or timeframe", async () => {
    render(<MasterDataPanel />);
    await screen.findByText("BTCUSDT");
    fireEvent.change(screen.getByRole("textbox", { name: "Filter datasets" }), { target: { value: "ETH" } });
    expect(screen.getByText("No datasets match this filter.")).toBeInTheDocument();
  });

  it("searches Binance markets and quick-selects a symbol without starting a backfill", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const pepe = { symbol: "PEPEUSDT", baseAsset: "PEPE", quoteAsset: "USDT", market: "futures", lastPrice: 0.00001, priceChangePercent: 3.25, highPrice: 0.000011, lowPrice: 0.000009, volume: 5000000000, quoteVolume: 50000 };
    const calls: Array<{ path: string; url: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname;
      const method = init?.method ?? "GET";
      calls.push({ path, url: url.toString(), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/master-data/coverage") return json(responses["/api/master-data/coverage"]);
      if (path === "/api/master-data/jobs") return json(responses["/api/master-data/jobs"]);
      if (path === "/api/crypto/popular") return json(responses["/api/crypto/popular"]);
      if (path === "/api/crypto/search") return json({ query: url.searchParams.get("q"), market: url.searchParams.get("market"), count: 1, results: [pepe] });
      throw new Error("Unexpected endpoint: " + method + " " + url.toString());
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><MasterDataPanel /></AuthGate>);

    expect(await screen.findByRole("heading", { name: /Binance asset search/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "SOLUSDT" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use SOLUSDT in backfill form" }));
    expect(screen.getByRole("textbox", { name: "Backfill symbol" })).toHaveValue("SOLUSDT");
    expect(screen.getByRole("combobox", { name: "Backfill market" })).toHaveValue("spot");

    fireEvent.change(screen.getByRole("textbox", { name: "Search cryptocurrency symbols" }), { target: { value: "PEPE" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Search market" }), { target: { value: "futures" } });
    fireEvent.click(screen.getByRole("button", { name: "Search symbols" }));
    expect(await screen.findByText("PEPEUSDT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use PEPEUSDT in backfill form" }));
    expect(screen.getByRole("textbox", { name: "Backfill symbol" })).toHaveValue("PEPEUSDT");
    expect(screen.getByRole("combobox", { name: "Backfill market" })).toHaveValue("futures");

    const searchCall = calls.find((call) => call.path === "/api/crypto/search");
    expect(searchCall?.method).toBe("GET");
    const url = new URL(searchCall!.url);
    expect(url.searchParams.get("q")).toBe("PEPE");
    expect(url.searchParams.get("market")).toBe("futures");
    expect(url.searchParams.get("limit")).toBe("20");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("requires an explicit confirmation before a trader can launch a backfill", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/master-data/coverage") return json(responses["/api/master-data/coverage"]);
      if (path === "/api/master-data/jobs") return json(responses["/api/master-data/jobs"]);
      if (path === "/api/crypto/popular") return json(responses["/api/crypto/popular"]);
      if (path === "/api/master-data/backfill" && method === "POST") return json({ id: "job-new", type: "backfill", status: "queued", progress: 0, symbol: "ETHUSDT", market: "futures", timeframe: "4h" });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><MasterDataPanel /></AuthGate>);

    expect(await screen.findByRole("heading", { name: "Backfill historical data" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Backfill symbol" }), { target: { value: "ethusdt" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Backfill market" }), { target: { value: "futures" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Backfill timeframe" }), { target: { value: "4h" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Backfill years" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Review backfill" }));

    expect(await screen.findByRole("heading", { name: "Confirm backfill" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Start backfill" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/backfill job queued \(job-new\)/i);
    await waitFor(() => expect(calls.find((call) => call.path === "/api/master-data/backfill" && call.method === "POST")?.body).toEqual({
      symbol: "ETHUSDT", market: "futures", timeframe: "4h", years: 2, auto_compute_features: true,
    }));
  });

  it("requires confirmation before starting a feature-build job", async () => {
    window.localStorage.setItem("token", "trader-session");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      if (path === "/api/master-data/coverage") return json(responses["/api/master-data/coverage"]);
      if (path === "/api/master-data/jobs") return json(responses["/api/master-data/jobs"]);
      if (path === "/api/crypto/popular") return json(responses["/api/crypto/popular"]);
      if (path === "/api/master-data/build-features" && method === "POST") return json({ id: "features-new", type: "build_features", status: "queued", progress: 0, symbol: "BTCUSDT", market: "spot", timeframe: "1h" });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><MasterDataPanel /></AuthGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Build features" }));
    expect(await screen.findByRole("heading", { name: "Confirm feature build" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Start feature build" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/feature build job queued \(features-new\)/i);
    await waitFor(() => expect(calls.find((call) => call.path === "/api/master-data/build-features" && call.method === "POST")?.body).toEqual({
      symbol: "BTCUSDT", market: "spot", timeframe: "1h",
    }));
  });

  it("keeps job-launch controls hidden for viewers", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const payload = path === "/api/auth/me" ? profile : responses[path as keyof typeof responses];
      if (!payload) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><MasterDataPanel /></AuthGate>);

    expect(await screen.findByText(/restricted to admin and trader roles/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review backfill" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Build features" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "SOLUSDT" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use SOLUSDT in backfill form" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
});
