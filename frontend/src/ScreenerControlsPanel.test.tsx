import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "./AuthGate";
import { ScreenerControlsPanel } from "./ScreenerControlsPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

describe("screener control editor", () => {
  it("allows a trader to save alert-gate changes while preserving existing overrides", async () => {
    vi.stubGlobal("confirm", () => true);
    window.localStorage.setItem("token", "session-token");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const control = { enabled: true, overrides: { rvol_multiplier: 8, enable_velocity_detection: false }, updated_at: "2026-10-26 12:00:00", tunable_fields: ["rvol_multiplier", "enable_velocity_detection", "enable_divergence_detection", "require_confluence"] };
    let savedBody: { enabled: boolean; overrides: Record<string, number | boolean> } | null = null;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(response(profile));
      if (path === "/api/research/screener/control" && init?.method === "POST") { savedBody = JSON.parse(String(init.body)); return Promise.resolve(response({ status: "ok", note: "Takes effect within ~60s." })); }
      if (path === "/api/research/screener/control") return Promise.resolve(response(control));
      throw new Error("Unexpected endpoint: " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><ScreenerControlsPanel /></AuthGate>);
    expect(await screen.findByRole("heading", { name: "Screener configuration" })).toBeInTheDocument();
    fireEvent.click(await screen.findByLabelText(/Telegram alerting enabled/));
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    expect(await screen.findByRole("status")).toHaveTextContent(/takes effect within ~60s/i);
    await waitFor(() => expect(savedBody).toEqual({ enabled: false, overrides: { rvol_multiplier: 8, enable_velocity_detection: false } }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").every(([path]) => String(path) === "/api/research/screener/control")).toBe(true);
  });
  it("does not load or render editable settings for a viewer role", async () => {
    window.localStorage.setItem("token", "viewer-session");
    const profile = { id: 9, username: "viewer01", email: null, role: "viewer", must_change_password: false };
    const fetchMock = vi.fn().mockResolvedValue(response(profile));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><ScreenerControlsPanel /></AuthGate>);
    expect(await screen.findByText(/restricted to admin and trader roles/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save changes/ })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes("/api/research/screener/control"))).toBe(false);
  });
  it("cancels disabling the screener if confirmation is rejected", async () => {
    vi.stubGlobal("confirm", () => false);
    window.localStorage.setItem("token", "session-token");
    const profile = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
    const control = { enabled: true, overrides: {}, updated_at: "2026-10-26 12:00:00", tunable_fields: [] };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(response(profile));
      if (path === "/api/research/screener/control") return Promise.resolve(response(control));
      throw new Error("Unexpected endpoint: " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><ScreenerControlsPanel /></AuthGate>);
    const checkbox = (await screen.findByLabelText(/Telegram alerting enabled/)) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
  });
});
