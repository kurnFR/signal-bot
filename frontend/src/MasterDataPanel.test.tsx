import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { MasterDataPanel } from "./MasterDataPanel";
import { fetchMasterDataSnapshot } from "./lib/masterData";

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
    await waitFor(() => expect(screen.getByRole("button", { name: "Review backfill" })).toBeEnabled());
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

  const harness = (role: string, onDelete?: (path: string) => Response) => {
    window.localStorage.setItem("token", role + "-session");
    const profile = { id: 4, username: role + "01", email: null, role, must_change_password: false };
    const calls: Array<{ path: string; method: string }> = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/auth/me") return json(profile);
      const base = new URL(path, "http://localhost").pathname;
      if (method === "DELETE" && base === "/api/master-data/dataset") return Promise.resolve(onDelete ? onDelete(path) : new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
      const body = responses[base as keyof typeof responses];
      if (!body) throw new Error("Unexpected endpoint: " + method + " " + path);
      return json(body);
    }));
    return calls;
  };

  it("lets an admin delete a dataset only after explicit confirmation", async () => {
    const calls = harness("admin", () => new Response(JSON.stringify({ features_deleted: 950, candles_deleted: 1000 }), { status: 200, headers: { "content-type": "application/json" } }));
    render(<AuthGate><MasterDataPanel /></AuthGate>);
    fireEvent.click(await screen.findByRole("button", { name: /Delete dataset BTCUSDT spot 1h/ }));
    expect(await screen.findByRole("heading", { name: /Confirm delete \(candles \+ features\)/ })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/Deleted 950 feature row\(s\) and 1,000 candle\(s\)/);
    const del = calls.find((c) => c.method === "DELETE")!;
    expect(del.path).toBe("/api/master-data/dataset?symbol=BTCUSDT&market=spot&timeframe=1h&scope=all");
  });

  it("surfaces the server refusal when an active deployment uses the dataset", async () => {
    harness("admin", () => new Response(JSON.stringify({ detail: "1 active paper deployment(s) use this dataset. Archive them first." }), { status: 409, headers: { "content-type": "application/json" } }));
    render(<AuthGate><MasterDataPanel /></AuthGate>);
    fireEvent.click(await screen.findByRole("button", { name: /Delete features BTCUSDT spot 1h/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByText(/Archive them first/)).toBeInTheDocument();
  });

  it("hides delete controls from traders (admin-only)", async () => {
    harness("trader");
    render(<AuthGate><MasterDataPanel /></AuthGate>);
    expect(await screen.findByRole("button", { name: "Build features" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete dataset/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete features/ })).not.toBeInTheDocument();
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

  it("renders successful empty coverage and job responses without showing an API error", async () => {
    const emptyResponses = {
      "/api/master-data/coverage": { summary: { totalCandles: 0, distinctSymbols: 0, coverageEntries: 0 }, coverage: [] },
      "/api/master-data/jobs": { jobs: [] },
      "/api/crypto/popular": { market: "spot", items: [] },
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (init?.method && init.method !== "GET") throw new Error("Master Data preview must be GET-only");
      const body = emptyResponses[path as keyof typeof emptyResponses];
      if (!body) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MasterDataPanel />);

    expect(await screen.findByText("No datasets match this filter.")).toBeInTheDocument();
    expect(screen.getByText("No recent background jobs returned.")).toBeInTheDocument();
    expect(screen.getByText("No popular markets are available right now.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getAllByText("0", { selector: ".metric-value" })).toHaveLength(4);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("recovers from a temporary coverage API failure after Retry", async () => {
    let coverageAttempts = 0;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (init?.method && init.method !== "GET") throw new Error("Master Data preview must be GET-only");
      if (path === "/api/master-data/coverage" && coverageAttempts++ === 0) {
        return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), { status: 503, headers: { "content-type": "application/json" } }));
      }
      const body = responses[path as keyof typeof responses];
      if (!body) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MasterDataPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/check API\/database health, then retry/i);
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(coverageAttempts).toBeGreaterThanOrEqual(2);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });


  it("rejects malformed coverage summary and rows instead of silently rendering partial data", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const body = path === "/api/master-data/coverage"
        ? { summary: { totalCandles: "1200", distinctSymbols: 2, coverageEntries: 1 }, coverage: [] }
        : responses["/api/master-data/jobs"];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchMasterDataSnapshot()).rejects.toThrow(/coverage returned an invalid response/i);
  });

  it("rejects malformed job rows instead of treating them as valid status", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const body = path === "/api/master-data/jobs"
        ? { jobs: [{ id: "job-1", type: "backfill", symbol: "BTCUSDT", market: "spot", timeframe: "1h", status: "completed", progress: "100" }] }
        : responses["/api/master-data/coverage"];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchMasterDataSnapshot()).rejects.toThrow(/jobs returned an invalid response/i);
  });

  it("accepts numeric epoch timestamps for minOpenTime and maxCloseTime", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const body = path === "/api/master-data/coverage"
        ? {
            summary: { totalCandles: 181047, distinctSymbols: 1, coverageEntries: 1 },
            coverage: [{
              symbol: "BNBUSDT", market: "spot", timeframe: "15m", candleCount: 181047,
              featureCount: 177937, hasFeatures: true,
              minOpenTime: 1628607600000, maxCloseTime: 1795184099999,
              startDate: "2021-08-10 22:00:00", endDate: "2026-11-20 21:14:59",
            }],
          }
        : responses["/api/master-data/jobs"];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const snapshot = await fetchMasterDataSnapshot();
    expect(snapshot.coverage).toHaveLength(1);
    expect(snapshot.coverage[0].minOpenTime).toBe(1628607600000);
    expect(snapshot.coverage[0].maxCloseTime).toBe(1795184099999);
  });
});
