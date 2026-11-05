import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "./SettingsPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("SettingsPanel", () => {
  it("shows safe session metadata and toggles only local appearance preference", () => {
    const onToggleTheme = vi.fn();
    render(<SettingsPanel theme="dark" onToggleTheme={onToggleTheme} username="tester" role="viewer" />);
    expect(screen.getByText("tester")).toBeInTheDocument();
    expect(screen.getByText("viewer")).toBeInTheDocument();
    expect(screen.getByText(/server-side permissions are authoritative/i)).toBeInTheDocument();
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
      const json = (payload: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

      if (path === "/api/settings/status" && method === "GET") {
        return json({
          database: { status: "ok", host: "db.internal", port: 3306, database: "signal_bot", tables: { signals: 10 }, error: "Access denied for user secret-db-user" },
          heartbeats: [{ collector: "wallet-worker", status: "stale", detail: "token=do-not-leak", lastBeat: null }],
          binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: true },
          system: { timezone: "Asia/Jakarta (WIB UTC+7)" },
        });
      }
      if (path === "/api/telegram/status" && method === "GET") return json({ configured: true, chatId: "-100123456789", hasToken: true });
      if (path === "/api/telegram/test" && method === "POST") return json({ success: true, bot: { username: "signal_test_bot" }, chat_id: "-100123456789" });
      if (path === "/api/users" && method === "GET") return json({ users: [] });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin01" role="admin" />);

    expect(await screen.findByText("-100123456789")).toBeInTheDocument();
    expect(screen.getByText("Present (hidden)")).toBeInTheDocument();
    expect(screen.getByText("db.internal")).toBeInTheDocument();
    expect(screen.getByText("signal_bot")).toBeInTheDocument();
    expect(screen.getByText("3306")).toBeInTheDocument();
    expect(screen.queryByText(/Access denied for user secret-db-user/)).not.toBeInTheDocument();
    expect(screen.queryByText(/do-not-leak/)).not.toBeInTheDocument();
    expect(screen.queryByText("admin-session")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review Telegram test" }));
    expect(await screen.findByRole("heading", { name: "Confirm Telegram test ping" })).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/api/telegram/test" && call.method === "POST")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Send test ping" }));
    expect(await screen.findByText(/test ping sent to the configured telegram chat/i)).toBeInTheDocument();
    const testCall = calls.find((call) => call.path === "/api/telegram/test" && call.method === "POST");
    expect(testCall?.body).toEqual({});
  });

  it("does not fetch Telegram status or expose user management to non-admin roles", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="viewer01" role="viewer" />);
    expect(screen.getByText(/Telegram status and test controls are restricted to admins/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review Telegram test" })).not.toBeInTheDocument();
    expect(screen.queryByText("User Accounts & Access Control")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses safe fallback labels when no authenticated user is present", () => {
    render(<SettingsPanel theme="light" onToggleTheme={() => undefined} />);
    expect(screen.getByText("Preview workspace")).toBeInTheDocument();
    expect(screen.getByText("Unauthenticated preview")).toBeInTheDocument();
  });

  it("renders user management catalog for admin and handles create user", async () => {
    window.localStorage.setItem("token", "admin-session");
    const mockUsers = [
      {
        id: 1,
        username: "admin",
        email: "admin@bot.internal",
        role: "admin",
        is_active: true,
        must_change_password: false,
        created_at: "2026-10-01 10:00:00",
        updated_at: "2026-10-01 10:00:00",
      },
      {
        id: 2,
        username: "trader_alice",
        email: null,
        role: "trader",
        is_active: true,
        must_change_password: false,
        created_at: "2026-10-02 12:00:00",
        updated_at: "2026-10-02 12:00:00",
      },
    ];

    let currentUsers = [...mockUsers];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname;
      const method = init?.method ?? "GET";
      const json = (payload: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

      if (path === "/api/users" && method === "GET") return json({ users: currentUsers });
      if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
      if (path === "/api/settings/status") return json({ database: { status: "ok", host: "127.0.0.1", port: 3306, database: "test", tables: {} }, heartbeats: [], binance: { spotApi: "online", futuresApi: "online" } });
      if (path === "/api/users" && method === "POST") {
        const newUser = {
          id: 3,
          username: "new_trader",
          email: "new@bot.internal",
          role: "trader" as const,
          is_active: true,
          must_change_password: false,
          created_at: "2026-11-04 10:00:00",
          updated_at: "2026-11-04 10:00:00",
        };
        currentUsers.push(newUser);
        return json({
          status: "success",
          user: newUser,
        }, 201);
      }
      throw new Error("Unexpected: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

    expect(await screen.findByText("trader_alice")).toBeInTheDocument();
    expect(screen.getByText("admin@bot.internal")).toBeInTheDocument();

    // Create user
    const usernameInput = screen.getByLabelText("Username");
    const passwordInput = screen.getByLabelText("Password (10–1024 chars)");
    fireEvent.change(usernameInput, { target: { value: "new_trader" } });
    fireEvent.change(passwordInput, { target: { value: "SecurePassword123!" } });
    fireEvent.click(screen.getByRole("button", { name: /Add User Account/i }));

    expect(await screen.findByText(/User 'new_trader' created successfully/i)).toBeInTheDocument();
    // Inputs reset
    expect((usernameInput as HTMLInputElement).value).toBe("");
    expect((passwordInput as HTMLInputElement).value).toBe("");
    // Catalog refreshed with new user
    expect(await screen.findByText("new@bot.internal")).toBeInTheDocument();
  });

  it("handles self password change with validation", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname;
      const method = init?.method ?? "GET";
      const json = (payload: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

      if (path === "/api/auth/change-password" && method === "POST") {
        return json({ token: "renewed-jwt-token" });
      }
      throw new Error("Unexpected: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="trader_alice" role="trader" />);

    fireEvent.change(screen.getByLabelText("Current Password"), { target: { value: "OldPassword123!" } });
    fireEvent.change(screen.getByLabelText("New Password (min 10 chars)"), { target: { value: "NewPassword123!" } });
    fireEvent.change(screen.getByLabelText("Confirm New Password"), { target: { value: "NewPassword123!" } });

    fireEvent.click(screen.getByRole("button", { name: "Update Password" }));

    expect(await screen.findByText(/Password updated successfully/i)).toBeInTheDocument();
    expect(window.localStorage.getItem("token")).toBe("renewed-jwt-token");
  });
});
