import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { PromotionStatusPanel } from "./PromotionStatusPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const basePolicy = {
  enabled: false,
  min_test_trades: 30,
  min_profit_factor: 1.2,
  min_expectancy_r: 0.05,
  max_drawdown_r: 8,
  min_rank_score: 0.1,
  require_holdout: true,
  send_telegram: false,
};
const profile = (role: string) => ({ id: 4, username: "trader01", email: null, role, must_change_password: false });
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json" },
}));

describe("promotion policy editor", () => {
  it("allows admin/trader policy editing only after separate auto-promotion and Telegram acknowledgements", async () => {
    window.localStorage.setItem("token", "trader-session");
    let savedPolicy = { ...basePolicy };
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (path === "/api/auth/me") return json(profile("trader"));
      if (path === "/api/backtest/promotion-policy" && method === "GET") return json({ policy: savedPolicy });
      if (path === "/api/backtest/promotion-audit?limit=5") return json({ events: [] });
      if (path === "/api/backtest/promotion-policy" && method === "PUT") {
        savedPolicy = JSON.parse(String(init?.body));
        return json({ status: "ok", policy: savedPolicy });
      }
      return json({ detail: "Unexpected API request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><PromotionStatusPanel /></AuthGate>);
    await screen.findByText("DISABLED");
    fireEvent.click(screen.getByRole("button", { name: "Edit policy" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable automatic promotion after qualifying backtests" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Allow Telegram alerts for qualifying promotions" }));
    fireEvent.click(screen.getByRole("button", { name: "Review policy changes" }));

    const saveButton = screen.getByRole("button", { name: "Save promotion policy" });
    expect(saveButton).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Acknowledge automatic promotion behavior" }));
    expect(saveButton).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Acknowledge Telegram alert side effects" }));
    expect(saveButton).toBeEnabled();
    expect(calls.some((call) => call.method === "PUT")).toBe(false);

    fireEvent.click(saveButton);
    expect(await screen.findByRole("status")).toHaveTextContent("Promotion policy saved and verified by readback.");
    await waitFor(() => expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1));
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ ...basePolicy, enabled: true, send_telegram: true });
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
  });

  it("does not expose policy writes to viewers", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/auth/me") return json(profile("viewer"));
      if (path === "/api/backtest/promotion-policy") return json({ policy: basePolicy });
      if (path === "/api/backtest/promotion-audit?limit=5") return json({ events: [] });
      return json({ detail: "Unexpected API request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><PromotionStatusPanel /></AuthGate>);
    await screen.findByText("DISABLED");
    expect(screen.queryByRole("button", { name: "Edit policy" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
  });

  it("does not claim a policy was verified when the server reports readback unavailable", async () => {
    window.localStorage.setItem("token", "admin-session");
    const warning = "The policy save completed, but reading it back failed. The submitted values have not been verified by readback.";
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/api/auth/me") return json(profile("admin"));
      if (path === "/api/backtest/promotion-policy" && method === "GET") return json({ policy: basePolicy });
      if (path === "/api/backtest/promotion-audit?limit=5") return json({ events: [] });
      if (path === "/api/backtest/promotion-policy" && method === "PUT") return json({ status: "saved_readback_unavailable", policy: JSON.parse(String(init?.body)), warning });
      return json({ detail: "Unexpected API request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><PromotionStatusPanel /></AuthGate>);
    await screen.findByText("DISABLED");
    fireEvent.click(screen.getByRole("button", { name: "Edit policy" }));
    fireEvent.click(screen.getByRole("button", { name: "Review policy changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Save promotion policy" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(warning);
    expect(screen.queryByText("Promotion policy saved and verified by readback.")).not.toBeInTheDocument();
    expect(screen.getByText(/Editing and backtest review stay disabled/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit policy" })).not.toBeInTheDocument();
  });
  
  it("clears an expired session when saving the policy returns 401", async () => {
    window.localStorage.setItem("token", "expired-admin-session");
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/api/auth/me") return json(profile("admin"));
      if (path === "/api/backtest/promotion-policy" && method === "GET") return json({ policy: basePolicy });
      if (path === "/api/backtest/promotion-audit?limit=5") return json({ events: [] });
      if (path === "/api/backtest/promotion-policy" && method === "PUT") return json({ detail: "Not authenticated" }, 401);
      return json({ detail: "Unexpected API request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><PromotionStatusPanel /></AuthGate>);
    await screen.findByText("DISABLED");
    fireEvent.click(screen.getByRole("button", { name: "Edit policy" }));
    fireEvent.click(screen.getByRole("button", { name: "Review policy changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Save promotion policy" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(screen.queryByText("Promotion policy saved and verified by readback.")).not.toBeInTheDocument();
  });

});
