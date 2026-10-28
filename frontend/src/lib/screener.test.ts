import { afterEach, describe, expect, it, vi } from "vitest";
import { saveScreenerControl } from "./screener";

describe("Screener control session handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.removeItem("token");
  });

  it("clears an expired token and announces session expiry when saving is unauthorized", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    const listener = vi.fn();
    window.addEventListener("signal-bot:session-expired", listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not authenticated" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
    ));

    await expect(saveScreenerControl(false, {})).rejects.toMatchObject({ status: 401 });
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener("signal-bot:session-expired", listener);
  });
});
