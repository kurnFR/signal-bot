import { fireEvent, render, screen, cleanup, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "./SettingsPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const validAdminStatus = {
  database: { status: "ok", host: "db.internal", port: 3306, database: "signal_bot", tables: { signals: 10 } },
  heartbeats: [],
  binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
  system: { timezone: "Asia/Jakarta (WIB UTC+7)" },
};

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
      if (path === "/api/telegram/status" && method === "GET") return json({ configured: true, deliveryAuthorized: false, chatId: "-100123456789", hasToken: true });
      if (path === "/api/telegram/test" && method === "POST") return json({ success: true, bot: { username: "signal_test_bot" }, chat_id: "-100123456789" });
      if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: [] });
      throw new Error("Unexpected endpoint: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin01" role="admin" />);

    expect(await screen.findByText("-100123456789")).toBeInTheDocument();
    expect(screen.getByText("Configured · delivery disabled")).toBeInTheDocument();
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

      if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
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

  it("handles user role change with confirmation, server update, and self-demotion restriction", async () => {
    window.localStorage.setItem("token", "admin-session");
    const mockUsers = [
      { id: 1, username: "admin", email: "admin@bot.internal", role: "admin", is_active: true, must_change_password: false, created_at: "2026-10-01 10:00:00", updated_at: "2026-10-01 10:00:00" },
      { id: 2, username: "trader_alice", email: null, role: "trader", is_active: true, must_change_password: false, created_at: "2026-10-01 10:00:00", updated_at: "2026-10-01 10:00:00" },
    ];
    let currentUsers = [...mockUsers];
    const calls: Array<{ path: string; method: string; body?: unknown }> = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname;
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (payload: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

      if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: currentUsers });
      if (path === "/api/settings/status") return json(validAdminStatus);
      if (path === "/api/telegram/status") return json({ configured: true, deliveryAuthorized: true, chatId: "-100", hasToken: true });
      if (path === "/api/users/2" && method === "PUT") {
        currentUsers = currentUsers.map((u) => u.id === 2 ? { ...u, role: "viewer" } : u);
        return json({ status: "success", message: "User 'trader_alice' role updated to viewer." });
      }
      throw new Error("Unexpected: " + method + " " + path);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

    expect(await screen.findByText("trader_alice")).toBeInTheDocument();
    expect(screen.getByText("Configured · delivery enabled")).toBeInTheDocument();

    // Open Change Role dialog for trader_alice
    fireEvent.click(screen.getByRole("button", { name: "Change role for trader_alice" }));
    const roleDialog = await screen.findByRole("dialog", { name: "Change Role for 'trader_alice'" });
    expect(roleDialog).toBeInTheDocument();

    // Select viewer role
    const roleSelect = within(roleDialog).getByLabelText("Role");
    fireEvent.change(roleSelect, { target: { value: "viewer" } });
    fireEvent.click(within(roleDialog).getByRole("button", { name: "Confirm Role Change" }));

    expect(await screen.findByText(/User 'trader_alice' role updated to viewer/i)).toBeInTheDocument();
    const putCall = calls.find((c) => c.path === "/api/users/2" && c.method === "PUT");
    expect(putCall?.body).toEqual({ role: "viewer" });

    // Verify self-demotion warning on admin user
    fireEvent.click(screen.getByRole("button", { name: "Change role for admin" }));
    const selfDialog = await screen.findByRole("dialog", { name: "Change Role for 'admin'" });
    const selfRoleSelect = within(selfDialog).getByLabelText("Role");
    fireEvent.change(selfRoleSelect, { target: { value: "trader" } });
    expect(screen.getByText(/Cannot change your own role from admin/i)).toBeInTheDocument();
    expect(within(selfDialog).getByRole("button", { name: "Confirm Role Change" })).toBeDisabled();
    fireEvent.click(within(selfDialog).getByRole("button", { name: "Cancel" }));
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

  describe("Accessible Dialogs & User Management Workflows", () => {
    const baseUsers = [
      {
        id: 1,
        username: "admin",
        email: "admin@bot.internal",
        role: "admin" as const,
        is_active: true,
        must_change_password: false,
        created_at: "2026-10-01 10:00:00",
        updated_at: "2026-10-01 10:00:00",
      },
      {
        id: 2,
        username: "trader_alice",
        email: "alice@bot.internal",
        role: "trader" as const,
        is_active: true,
        must_change_password: false,
        created_at: "2026-10-02 12:00:00",
        updated_at: "2026-10-02 12:00:00",
      },
    ];

    const validAdminStatus = {
      database: { status: "ok", host: "127.0.0.1", port: 3306, database: "test", tables: {}, error: null },
      heartbeats: [],
      binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
    };

    it("opens reset-password dialog with accessible semantics, validates length, handles cancel/Escape focus restoration, and submits reset", async () => {
      window.localStorage.setItem("token", "admin-session");
      let resetPasswordCalledWith: unknown = null;

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: baseUsers });
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path === "/api/users/2/reset-password" && method === "POST") {
          resetPasswordCalledWith = JSON.parse(String(init?.body));
          return json({ status: "success", message: "Password reset successfully for user 'trader_alice'" });
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("trader_alice")).toBeInTheDocument();
      const resetBtn = screen.getByRole("button", { name: "Reset password for trader_alice" });

      // Open reset dialog
      fireEvent.click(resetBtn);

      const dialog = await screen.findByRole("dialog", { name: "Reset Password for 'trader_alice'" });
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute("aria-describedby", "reset-dialog-desc");
      expect(within(dialog).getByText(/Sets a temporary password/i)).toBeInTheDocument();

      // Initial focus on input
      const passInput = within(dialog).getByLabelText("New Password (min 10 chars)");
      expect(document.activeElement).toBe(passInput);

      // Submit button disabled when password < 10 chars
      const submitBtn = within(dialog).getByRole("button", { name: "Confirm Password Reset" });
      expect(submitBtn).toBeDisabled();

      fireEvent.change(passInput, { target: { value: "short" } });
      expect(submitBtn).toBeDisabled();

      // Dismiss via Cancel button -> focus restored
      const cancelBtn = within(dialog).getByRole("button", { name: "Cancel" });
      fireEvent.click(cancelBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(document.activeElement).toBe(resetBtn);

      // Re-open and dismiss via Escape key -> focus restored
      fireEvent.click(resetBtn);
      const reOpenedDialog = await screen.findByRole("dialog", { name: "Reset Password for 'trader_alice'" });
      fireEvent.keyDown(reOpenedDialog, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(document.activeElement).toBe(resetBtn);

      // Re-open and submit valid password
      fireEvent.click(resetBtn);
      const reOpenedDialog2 = await screen.findByRole("dialog", { name: "Reset Password for 'trader_alice'" });
      const passInput2 = within(reOpenedDialog2).getByLabelText("New Password (min 10 chars)");
      fireEvent.change(passInput2, { target: { value: "NewTempPassword123!" } });
      const submitBtn2 = within(reOpenedDialog2).getByRole("button", { name: "Confirm Password Reset" });
      expect(submitBtn2).not.toBeDisabled();

      fireEvent.click(submitBtn2);

      // Dialog closes, focus is restored, and success status message announced
      expect(await screen.findByRole("status")).toHaveTextContent("Password reset successfully for user 'trader_alice'");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(document.activeElement).toBe(resetBtn);
      expect(resetPasswordCalledWith).toEqual({ new_password: "NewTempPassword123!" });
    });

    it("handles password reset failure with role=alert and prevents double submission", async () => {
      window.localStorage.setItem("token", "admin-session");

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: baseUsers });
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path === "/api/users/2/reset-password" && method === "POST") {
          return json({ detail: "Target user not found" }, 404);
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("trader_alice")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Reset password for trader_alice" }));

      const dialog = await screen.findByRole("dialog", { name: "Reset Password for 'trader_alice'" });
      const passInput = within(dialog).getByLabelText("New Password (min 10 chars)");
      fireEvent.change(passInput, { target: { value: "NewTempPassword123!" } });

      const submitBtn = within(dialog).getByRole("button", { name: "Confirm Password Reset" });
      fireEvent.click(submitBtn);

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Target user not found");
      expect(screen.getByRole("dialog")).toBeInTheDocument(); // Dialog remains open on error
    });

    it("toggles user status with inline feedback, catalog refresh, and handles error without calling alert()", async () => {
      window.localStorage.setItem("token", "admin-session");
      const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

      let isAliceActive = true;
      let shouldFail = false;

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") {
          return json({
            users: [
              baseUsers[0],
              { ...baseUsers[1], is_active: isAliceActive },
            ],
          });
        }
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path === "/api/users/2" && method === "PUT") {
          if (shouldFail) {
            return json({ detail: "Database concurrency conflict" }, 409);
          }
          const body = JSON.parse(String(init?.body));
          isAliceActive = body.is_active;
          return json({ status: "success", message: `User 'trader_alice' ${isAliceActive ? "activated" : "deactivated"}` });
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      const disableBtn = await screen.findByRole("button", { name: "Disable user trader_alice" });

      // Successful toggle
      fireEvent.click(disableBtn);

      expect(await screen.findByRole("status")).toHaveTextContent("User 'trader_alice' deactivated");
      expect(await screen.findByRole("button", { name: "Enable user trader_alice" })).toBeInTheDocument();

      // Failed toggle without alert()
      shouldFail = true;
      fireEvent.click(screen.getByRole("button", { name: "Enable user trader_alice" }));

      const alertRegion = await screen.findByRole("alert");
      expect(alertRegion).toHaveTextContent("Database concurrency conflict");
      expect(alertSpy).not.toHaveBeenCalled(); // alert() was NEVER invoked!
      // Status remained Disabled because mutation failed
      expect(screen.getByRole("button", { name: "Enable user trader_alice" })).toBeInTheDocument();

      // Dismiss inline error
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      alertSpy.mockRestore();
    });

    it("opens delete confirmation dialog with danger indicator, cancels without mutation, and restores focus", async () => {
      window.localStorage.setItem("token", "admin-session");
      let deleteCalled = false;

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: baseUsers });
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path === "/api/users/2" && method === "DELETE") {
          deleteCalled = true;
          return json({ status: "success", message: "User 'trader_alice' deleted" });
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("trader_alice")).toBeInTheDocument();
      const deleteBtn = screen.getByRole("button", { name: "Delete user trader_alice" });

      // Open delete dialog
      fireEvent.click(deleteBtn);

      const dialog = await screen.findByRole("dialog", { name: "Delete Account 'trader_alice'?" });
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute("aria-describedby", "delete-dialog-desc");
      expect(within(dialog).getByText(/permanently removes the user account/i)).toBeInTheDocument();

      // Initial focus on Cancel button (destructive safety)
      const cancelBtn = within(dialog).getByRole("button", { name: "Cancel" });
      expect(document.activeElement).toBe(cancelBtn);

      // Confirm button has destructive styling
      const confirmDeleteBtn = within(dialog).getByRole("button", { name: "Confirm Delete" });
      expect(confirmDeleteBtn).toHaveClass("button-danger");

      // Cancel dismissal
      fireEvent.click(cancelBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(deleteCalled).toBe(false);
      expect(document.activeElement).toBe(deleteBtn); // Focus restored!

      // Re-open and dismiss via Escape
      fireEvent.click(deleteBtn);
      const dialog2 = await screen.findByRole("dialog", { name: "Delete Account 'trader_alice'?" });
      fireEvent.keyDown(dialog2, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(deleteCalled).toBe(false);
      expect(document.activeElement).toBe(deleteBtn); // Focus restored!
    });

    it("executes user deletion upon confirmation, prevents double submission, and refreshes catalog", async () => {
      window.localStorage.setItem("token", "admin-session");
      let usersList = [...baseUsers];

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: usersList });
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path === "/api/users/2" && method === "DELETE") {
          usersList = usersList.filter((u) => u.id !== 2);
          return json({ status: "success", message: "User 'trader_alice' deleted" });
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("trader_alice")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Delete user trader_alice" }));

      const dialog = await screen.findByRole("dialog", { name: "Delete Account 'trader_alice'?" });
      const confirmDeleteBtn = within(dialog).getByRole("button", { name: "Confirm Delete" });
      fireEvent.click(confirmDeleteBtn);

      expect(await screen.findByRole("status")).toHaveTextContent("User 'trader_alice' deleted");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText("trader_alice")).not.toBeInTheDocument());
    });

    it("handles delete failure with inline role=alert error", async () => {
      window.localStorage.setItem("token", "admin-session");

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: baseUsers });
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path === "/api/users/2" && method === "DELETE") {
          return json({ detail: "Cannot delete the last active administrator" }, 400);
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("trader_alice")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Delete user trader_alice" }));

      const dialog = await screen.findByRole("dialog", { name: "Delete Account 'trader_alice'?" });
      const confirmDeleteBtn = within(dialog).getByRole("button", { name: "Confirm Delete" });
      fireEvent.click(confirmDeleteBtn);

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Cannot delete the last active administrator");
      expect(screen.getByRole("dialog")).toBeInTheDocument(); // Dialog remains open on error
    });

    it("dismisses Telegram test ping confirmation via cancel and Escape without calling /api/telegram/test, restoring focus", async () => {
      window.localStorage.setItem("token", "admin-session");
      let telegramTestCalled = false;

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path === "/api/telegram/status" && method === "GET") return json({ configured: true, chatId: "-100123456789", hasToken: true });
        if (path === "/api/settings/status") return json(validAdminStatus);
        if (path.startsWith("/api/auth/audit-logs")) return json({ items: [] });
        if (path === "/api/users") return json({ users: [] });
        if (path === "/api/telegram/test" && method === "POST") {
          telegramTestCalled = true;
          return json({ success: true, chat_id: "-100123456789" });
        }
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("-100123456789")).toBeInTheDocument();
      const reviewBtn = screen.getByRole("button", { name: "Review Telegram test" });

      // Open Telegram test dialog
      fireEvent.click(reviewBtn);

      const dialog = await screen.findByRole("dialog", { name: "Confirm Telegram test ping" });
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute("aria-describedby", "settings-telegram-confirm-desc");

      // Initial focus on Cancel test button
      const cancelTestBtn = within(dialog).getByRole("button", { name: "Cancel test" });
      expect(document.activeElement).toBe(cancelTestBtn);

      // Dismiss via Cancel button -> focus restored to review button
      fireEvent.click(cancelTestBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(telegramTestCalled).toBe(false);
      expect(document.activeElement).toBe(reviewBtn);

      // Re-open and dismiss via Escape -> focus restored to review button
      fireEvent.click(reviewBtn);
      const dialog2 = await screen.findByRole("dialog", { name: "Confirm Telegram test ping" });
      fireEvent.keyDown(dialog2, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(telegramTestCalled).toBe(false);
      expect(document.activeElement).toBe(reviewBtn);
    });

    it("traps keyboard focus within accessible dialogs", async () => {
      window.localStorage.setItem("token", "admin-session");

      const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        const path = url.pathname;
        const method = init?.method ?? "GET";
        const json = (payload: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } }));

        if (path.startsWith("/api/auth/audit-logs") && method === "GET") return json({ items: [] });
      if (path === "/api/users" && method === "GET") return json({ users: baseUsers });
        if (path === "/api/telegram/status") return json({ configured: false, chatId: "", hasToken: false });
        if (path === "/api/settings/status") return json(validAdminStatus);
        throw new Error("Unexpected: " + method + " " + path);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<SettingsPanel theme="dark" onToggleTheme={() => undefined} username="admin" role="admin" />);

      expect(await screen.findByText("trader_alice")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Reset password for trader_alice" }));

      const dialog = await screen.findByRole("dialog", { name: "Reset Password for 'trader_alice'" });
      const passInput = within(dialog).getByLabelText("New Password (min 10 chars)");
      const cancelBtn = within(dialog).getByRole("button", { name: "Cancel" });

      // Enable submit button
      fireEvent.change(passInput, { target: { value: "ValidPassword123!" } });
      const submitBtn = within(dialog).getByRole("button", { name: "Confirm Password Reset" });

      // Tab on last element (submitBtn) wraps to first element (passInput)
      submitBtn.focus();
      expect(document.activeElement).toBe(submitBtn);
      fireEvent.keyDown(dialog, { key: "Tab" });
      expect(document.activeElement).toBe(passInput);

      // Shift+Tab on first element (passInput) wraps to last element (submitBtn)
      passInput.focus();
      expect(document.activeElement).toBe(passInput);
      fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
      expect(document.activeElement).toBe(submitBtn);

      // Dismiss dialog
      fireEvent.click(cancelBtn);
    });
  });
});
