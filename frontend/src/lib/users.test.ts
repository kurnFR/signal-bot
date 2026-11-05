import { afterEach, describe, expect, it, vi } from "vitest";
import { createUser, deleteUser, fetchUsers, isManagedUser, resetUserPassword, updateUser, UserActionError } from "./users";

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

  describe("user mutation response contracts", () => {
    const invalidPayloads = [
      null,
      "unexpected string",
      12345,
      [],
      {},
      { status: "error", message: "Error occurred" },
      { status: "ok", message: "Not canonical success" },
      { status: "success" },
      { status: "success", message: null },
      { status: "success", message: "" },
      { status: "success", message: "   " },
      { status: "success", message: 123 },
    ];

    it("rejects malformed resetUserPassword responses with UserActionError(502)", async () => {
      window.localStorage.setItem("token", "admin-jwt-token");
      for (const payload of invalidPayloads) {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
          JSON.stringify(payload),
          { status: 200, headers: { "content-type": "application/json" } },
        )));

        const errPromise = resetUserPassword(2, "SecurePass123!");
        await expect(errPromise).rejects.toThrow(UserActionError);
        await expect(errPromise).rejects.toMatchObject({ status: 502 });
      }
    });

    it("rejects malformed updateUser responses with UserActionError(502)", async () => {
      window.localStorage.setItem("token", "admin-jwt-token");
      for (const payload of invalidPayloads) {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
          JSON.stringify(payload),
          { status: 200, headers: { "content-type": "application/json" } },
        )));

        const errPromise = updateUser(2, { is_active: false });
        await expect(errPromise).rejects.toThrow(UserActionError);
        await expect(errPromise).rejects.toMatchObject({ status: 502 });
      }
    });

    it("rejects malformed deleteUser responses with UserActionError(502)", async () => {
      window.localStorage.setItem("token", "admin-jwt-token");
      for (const payload of invalidPayloads) {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
          JSON.stringify(payload),
          { status: 200, headers: { "content-type": "application/json" } },
        )));

        const errPromise = deleteUser(2);
        await expect(errPromise).rejects.toThrow(UserActionError);
        await expect(errPromise).rejects.toMatchObject({ status: 502 });
      }
    });
  });

  describe("createUser contract and validation", () => {
    it("accepts user creation with missing optional fields and safely defaults them", async () => {
      window.localStorage.setItem("token", "admin-jwt-token");
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          status: "success",
          user: {
            id: 10,
            username: "minimal_user",
            role: "trader",
          },
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const created = await createUser({
        username: "minimal_user",
        password: "Password123!",
        role: "trader",
      });

      expect(created.id).toBe(10);
      expect(created.username).toBe("minimal_user");
      expect(created.email).toBeNull();
      expect(created.role).toBe("trader");
      expect(created.is_active).toBe(true);
      expect(created.must_change_password).toBe(false);
      expect(created.created_at).toBe("");
      expect(created.updated_at).toBe("");
    });

    it("rejects malformed createUser responses", async () => {
      window.localStorage.setItem("token", "admin-jwt-token");

      const malformedCases = [
        { status: "error", user: { id: 1, username: "a", role: "admin" } },
        { status: "success", user: null },
        { status: "success", user: { id: -1, username: "a", role: "admin" } },
        { status: "success", user: { id: 1, username: "", role: "admin" } },
        { status: "success", user: { id: 1, username: "a", role: "invalid_role" } },
        { status: "success", user: { id: 1, username: "a", role: "admin", is_active: "not_a_bool" } },
      ];

      for (const payload of malformedCases) {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
          JSON.stringify(payload),
          { status: 201, headers: { "content-type": "application/json" } },
        )));

        await expect(createUser({
          username: "test",
          password: "Password123!",
          role: "admin",
        })).rejects.toThrow(UserActionError);
      }
    });

    it("validates objects with isManagedUser guard", () => {
      // Full valid object
      expect(isManagedUser({
        id: 1,
        username: "admin",
        email: "a@b.c",
        role: "admin",
        is_active: true,
        must_change_password: false,
        created_at: "2026-01-01",
        updated_at: "2026-01-01",
      })).toBe(true);

      // Minimal valid object with omitted optional fields
      expect(isManagedUser({
        id: 2,
        username: "trader",
        role: "trader",
      })).toBe(true);

      // Invalid cases
      expect(isManagedUser(null)).toBe(false);
      expect(isManagedUser({})).toBe(false);
      expect(isManagedUser({ id: 0, username: "u", role: "viewer" })).toBe(false);
      expect(isManagedUser({ id: 1, username: " ", role: "viewer" })).toBe(false);
      expect(isManagedUser({ id: 1, username: "u", role: "superadmin" })).toBe(false);
      expect(isManagedUser({ id: 1, username: "u", role: "viewer", email: 123 })).toBe(false);
      expect(isManagedUser({ id: 1, username: "u", role: "viewer", is_active: "yes" })).toBe(false);
      expect(isManagedUser({ id: 1, username: "u", role: "viewer", created_at: 12345 })).toBe(false);
    });
  });
});
