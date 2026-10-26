import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { SystemStatusPanel } from "./SystemStatusPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("read-only Overview status panel", () => {
  it("renders sanitized system status and heartbeat metadata", async () => {
    window.localStorage.setItem("token", "session-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      database: { status: "ok", tables: { signals: 10, trades: 3 }, error: null },
      heartbeats: [{ collector: "market-data", status: "ok", detail: "not rendered", lastBeat: "2026-10-26 12:00:00" }],
      binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: true },
    }), { status: 200, headers: { "content-type": "application/json" } })));
    render(<SystemStatusPanel />);
    expect(await screen.findByText("market-data")).toBeInTheDocument();
    expect(screen.getByText("2 tables")).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
    expect(screen.queryByText("not rendered")).not.toBeInTheDocument();
  });
  it("shows a recoverable authentication error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401, headers: { "content-type": "application/json" } })));
    render(<SystemStatusPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/sign in through the existing dashboard/i);
  });
});
