import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAuthUser, login, AuthRequestError } from "./auth";

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
