import { afterEach, describe, expect, it, vi } from "vitest";
import { assertApiPath, getJson } from "./api";

describe("read-only same-origin API transport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("accepts only API-relative same-origin paths", () => {
    expect(() => assertApiPath("/api/health")).not.toThrow();
    for (const path of ["https://example.com/api/health", "//example.com/api/health", "/static/app.js", "/api\\health", "javascript:alert(1)"]) {
      expect(() => assertApiPath(path)).toThrow(TypeError);
    }
  });

  it("uses GET, same-origin credentials, and an explicit JSON accept header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"status":"ok"}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJson<{ status: string }>("/api/health")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }));
  });

  it("surfaces HTTP failures without leaking response bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("secret server detail", { status: 403 })));
    await expect(getJson("/api/health")).rejects.toMatchObject({
      name: "ApiError", status: 403, code: "http",
    });
    await expect(getJson("/api/health")).rejects.not.toThrow("secret server detail");
  });

  it("rejects HTML and malformed JSON responses", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }))
      .mockResolvedValueOnce(new Response("{", { status: 200, headers: { "content-type": "application/json" } })));
    await expect(getJson("/api/health")).rejects.toMatchObject({ code: "invalid-content-type" });
    await expect(getJson("/api/health")).rejects.toMatchObject({ code: "invalid-json" });
  });
});
