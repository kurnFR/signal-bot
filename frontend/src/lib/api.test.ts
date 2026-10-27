import { afterEach, describe, expect, it, vi } from "vitest";
import { assertApiPath, getJson } from "./api";

describe("read-only same-origin API transport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("accepts only API-relative same-origin paths", () => {
    expect(() => assertApiPath("/api/health")).not.toThrow();
    for (const path of ["https://example.com/api/health", "//example.com/api/health", "/static/app.js", "/api\\health", "/api/../static/app.js", "/api/%2e%2e/static/app.js", "/api/%2E./static/app.js", "javascript:alert(1)"]) {
      expect(() => assertApiPath(path)).toThrow(TypeError);
    }
  });

  it("uses GET, same-origin credentials, and an explicit JSON accept header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"status":"ok"}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    window.localStorage.setItem("token", "test-session-token");
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJson<{ status: string }>("/api/health")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json", Authorization: "Bearer test-session-token" },
    }));
  });

  it("surfaces HTTP failures without leaking response bodies", async () => {
    window.localStorage.removeItem("token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("secret server detail", { status: 403 })));
    await expect(getJson("/api/health")).rejects.toMatchObject({
      name: "ApiError", status: 403, code: "http",
    });
    await expect(getJson("/api/health")).rejects.not.toThrow("secret server detail");
  });

  it("clears expired tokens and dispatches a session-expired event for protected API reads", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    const listener = vi.fn();
    window.addEventListener("signal-bot:session-expired", listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not authenticated" }), { status: 401 })));

    await expect(getJson("/api/paper/positions")).rejects.toMatchObject({ status: 401, code: "http" });

    expect(window.localStorage.getItem("token")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener("signal-bot:session-expired", listener);
  });

  it("rejects HTML and malformed JSON responses", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }))
      .mockResolvedValueOnce(new Response("{", { status: 200, headers: { "content-type": "application/json" } })));
    await expect(getJson("/api/health")).rejects.toMatchObject({ code: "invalid-content-type" });
    await expect(getJson("/api/health")).rejects.toMatchObject({ code: "invalid-json" });
  });
});
