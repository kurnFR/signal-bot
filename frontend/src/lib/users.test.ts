import { afterEach, describe, expect, it, vi } from "vitest";
import { createUser, deleteUser, fetchUsers, resetUserPassword, updateUser, UserActionError } from "./users";

describe("user management client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  const mockUsers = [
    {
      id: 1,
      username: "admin",
      email: "admin@example.com",
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
      must_change_password: true,
      created_at: "2026-10-02 12:00:00",
      updated_at: "2026-10-02 12:00:00",
    },
  ];

  it("fetches user list with authorization header", async () => {
    window.localStorage.setItem("token", "admin-jwt-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ users: mockUsers }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const users = await fetchUsers();
    expect(users).toHaveLength(2);
    expect(users[0].username).toBe("admin");
    expect(users[1].role).toBe("trader");
    expect(fetchMock).toHaveBeenCalledWith("/api/users", expect.objectContaining({
      method: "GET",
      headers: expect.objectContaining({ Authorization: "Bearer admin-jwt-token" }),
    }));
  });

  it("creates a new user and returns validated profile", async () => {
    window.localStorage.setItem("token", "admin-jwt-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        status: "success",
        user: {
          id: 3,
          username: "viewer_bob",
          email: "bob@example.com",
          role: "viewer",
          is_active: true,
          must_change_password: false,
          created_at: "2026-10-03 14:00:00",
          updated_at: "2026-10-03 14:00:00",
        },
      }),
      { status: 201, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const created = await createUser({
      username: "viewer_bob",
      email: "bob@example.com",
      password: "Password123456!",
      role: "viewer",
    });

    expect(created.id).toBe(3);
    expect(created.username).toBe("viewer_bob");
    expect(fetchMock).toHaveBeenCalledWith("/api/users", expect.objectContaining({
      method: "POST",
    }));
  });

  it("handles 401 session expiration and dispatches event", async () => {
    window.localStorage.setItem("token", "expired-token");
    const listener = vi.fn();
    window.addEventListener("signal-bot:session-expired", listener);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Not authenticated" }),
      { status: 401, headers: { "content-type": "application/json" } },
    )));

    await expect(fetchUsers()).rejects.toThrow(UserActionError);
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener("signal-bot:session-expired", listener);
  });

  it("resets a user password", async () => {
    window.localStorage.setItem("token", "admin-jwt-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: "success", message: "Password reset successfully" }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const message = await resetUserPassword(2, "NewPassword12345!");
    expect(message).toBe("Password reset successfully");
    expect(fetchMock).toHaveBeenCalledWith("/api/users/2/reset-password", expect.objectContaining({
      method: "POST",
    }));
  });

  it("updates user active status", async () => {
    window.localStorage.setItem("token", "admin-jwt-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: "success", message: "User updated" }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const message = await updateUser(2, { is_active: false });
    expect(message).toBe("User updated");
    expect(fetchMock).toHaveBeenCalledWith("/api/users/2", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ is_active: false }),
    }));
  });

  it("deletes a user account", async () => {
    window.localStorage.setItem("token", "admin-jwt-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: "success", message: "User deleted" }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const message = await deleteUser(2);
    expect(message).toBe("User deleted");
    expect(fetchMock).toHaveBeenCalledWith("/api/users/2", expect.objectContaining({
      method: "DELETE",
    }));
  });
});
