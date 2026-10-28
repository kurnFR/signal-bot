import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });
import { SettingsPanel } from "./SettingsPanel";

describe("SettingsPanel", () => {
  it("shows safe session metadata and toggles only local appearance preference", () => {
    const onToggleTheme = vi.fn();
    render(<SettingsPanel theme="dark" onToggleTheme={onToggleTheme} username="tester" role="viewer" />);
    expect(screen.getByText("tester")).toBeInTheDocument();
    expect(screen.getByText("viewer")).toBeInTheDocument();
    expect(screen.getByText(/API credentials remain hidden/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(onToggleTheme).toHaveBeenCalledTimes(1);
  });

  it("loads Telegram status for admins and requires confirmation before a real test ping", async () => {
    window.localStorage.setItem("token", "admin-session");
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname;
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));
      if (path === "/api/telegram/status" && method === "GET") return json({ configured: true, chatId: "-100123456789", hasToken: true });
      if (path === "/api/telegram/test" && method === "POST") return json({ success: true, bot: { username: "signal_test_bot" }, chat_id: "-100123456789" });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin01" role="admin" />);

    expect(await screen.findByText("-100123456789")).toBeInTheDocument();
    expect(screen.getByText("Present (hidden)")).toBeInTheDocument();
    expect(screen.queryByText("admin-session")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review Telegram test" }));
    expect(await screen.findByRole("heading", { name: "Confirm Telegram test ping" })).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/api/telegram/test" && call.method === "POST")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Send test ping" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/test ping sent to the configured telegram chat/i);
    const testCall = calls.find((call) => call.path === "/api/telegram/test" && call.method === "POST");
    expect(testCall?.body).toEqual({});
  });

  it("does not fetch Telegram status or expose controls to non-admin roles", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="viewer01" role="viewer" />);
    expect(screen.getByText(/restricted to admins/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review Telegram test" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses safe fallback labels when no authenticated user is present", () => {
    render(<SettingsPanel theme="light" onToggleTheme={() => undefined} />);
    expect(screen.getByText("Preview workspace")).toBeInTheDocument();
    expect(screen.getByText("Unauthenticated preview")).toBeInTheDocument();
  });
});
