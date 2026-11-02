import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAuthUser, login, logout, changePassword, AuthRequestError } from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const validUser = { id: 4, username: "trader01", email: null, role: "trader", must_change_password: false };
function stubPayload(payload: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("authentication response contracts", () => {
  it("accepts the documented authenticated profile", async () => {
    stubPayload(validUser);
    await expect(fetchAuthUser()).resolves.toEqual(validUser);
  });

  it.each([
    ["non-positive user ID", { ...validUser, id: 0 }],
    ["string user ID", { ...validUser, id: "4" }],
    ["empty username", { ...validUser, username: " " }],
    ["unknown role", { ...validUser, role: "superuser" }],
    ["invalid email", { ...validUser, email: 7 }],
    ["invalid password-change flag", { ...validUser, must_change_password: "false" }],
  ])("rejects %s in the authenticated profile", async (_label, profile) => {
    stubPayload(profile);
    await expect(fetchAuthUser()).rejects.toThrow("Authentication service returned an invalid user profile");
  });

  it("accepts a valid login session and stores its non-empty token", async () => {
    stubPayload({ token: "session-token", user: validUser });
    await expect(login("trader01", "secret")).resolves.toEqual({ token: "session-token", user: validUser });
    expect(window.localStorage.getItem("token")).toBe("session-token");
  });

  it("stores the renewed token returned by a successful password change", async () => {
    window.localStorage.setItem("token", "temporary-session-token");
    const fetchMock = stubPayload({ status: "success", token: "renewed-session-token" });
    await expect(changePassword("temporary-password", "new-strong-password")).resolves.toBeUndefined();
    expect(window.localStorage.getItem("token")).toBe("renewed-session-token");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/change-password", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ Authorization: "Bearer temporary-session-token" }),
    }));
  });

  it("clears the invalidated token when password change omits the renewed token", async () => {
    window.localStorage.setItem("token", "temporary-session-token");
    stubPayload({ status: "success" });
    await expect(changePassword("temporary-password", "new-strong-password")).rejects.toBeInstanceOf(AuthRequestError);
    expect(window.localStorage.getItem("token")).toBeNull();
  });

  it("retains the token when server-side logout fails and clears it after a successful retry", async () => {
    window.localStorage.setItem("token", "active-session-token");
    const failedFetch = stubPayload({ detail: "Service unavailable" }, 503);
    await expect(logout()).rejects.toBeInstanceOf(AuthRequestError);
    expect(window.localStorage.getItem("token")).toBe("active-session-token");
    expect(failedFetch).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ Authorization: "Bearer active-session-token" }),
    }));

    stubPayload({ status: "success" }, 200);
    await expect(logout()).resolves.toBeUndefined();
    expect(window.localStorage.getItem("token")).toBeNull();
  });

  it("clears an already-invalid token when logout receives 401", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    stubPayload({ detail: "Session expired" }, 401);
    await expect(logout()).resolves.toBeUndefined();
    expect(window.localStorage.getItem("token")).toBeNull();
  });

  it.each([
    ["non-string token", { token: 123, user: validUser }],
    ["empty token", { token: " ", user: validUser }],
    ["malformed user", { token: "session-token", user: { ...validUser, role: "root" } }],
  ])("rejects login responses with %s", async (_label, payload) => {
    stubPayload(payload);
    await expect(login("trader01", "secret")).rejects.toBeInstanceOf(AuthRequestError);
    expect(window.localStorage.getItem("token")).toBeNull();
  });
});
