import { expect, test, type Page } from "@playwright/test";

const user = {
  id: 4,
  username: "browser-trader",
  email: null,
  role: "trader",
  must_change_password: false,
};

async function mockApi(page: Page, options: { expiredScreener?: boolean; forcePasswordChange?: boolean; sessionValidationUnavailable?: boolean } = {}) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/auth/login" && request.method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ token: "playwright-session-token", user: { ...user, must_change_password: Boolean(options.forcePasswordChange) } }) });
      return;
    }
    if (path === "/api/auth/me") {
      if (options.sessionValidationUnavailable) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Service unavailable" }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...user, must_change_password: Boolean(options.forcePasswordChange) }) });
      return;
    }
    if (options.expiredScreener && path.startsWith("/api/research/screener/")) {
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not authenticated" }) });
      return;
    }
    if (path === "/api/system/status") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        database: { status: "ok", tables: { signals: 42 }, error: null },
        heartbeats: [{ collector: "market-data", status: "ok", lastBeat: "2026-10-27 04:00:00" }],
        binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
      }) });
      return;
    }
    if (path === "/api/research/screener/status") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ running: true, database_ok: true, enabled: true, timeframe: "5m", symbols_monitored: 25, last_beat_at: null, last_signal_at: null, last_universe_refresh: null }) });
      return;
    }
    if (path === "/api/research/screener/signals") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ signals: [] }) });
      return;
    }
    if (path === "/api/research/screener/stats") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total_signals: 0, signals_last_24h: 0, by_type_last_24h: {} }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Username").fill("browser-trader");
  await page.getByLabel("Password").fill("not-a-real-password");
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("sign-in, navigation, and theme preference work in a real browser", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  await page.getByRole("button", { name: /Market Screener/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Market Screener" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent smart-money signals" })).toBeVisible();

  await page.locator(".topbar").getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  // The active view is intentionally session-local; theme preference persists across reloads.
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("a forced password change blocks the dashboard until completed", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("token", "temporary-browser-token"));
  await mockApi(page, { forcePasswordChange: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Change your password" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
});

test("an API 401 after sign-in returns the user to the sign-in screen", async ({ page }) => {
  await mockApi(page, { expiredScreener: true });
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await page.getByRole("button", { name: /Market Screener/ }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveText("Your session has expired. Sign in again to continue.");
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBeNull();
});


test("mobile viewport keeps navigation usable and exposes the selected view", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await mockApi(page);
  await signIn(page);

  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  const menuButton = page.getByRole("button", { name: "Open navigation" });
  await expect(menuButton).toBeVisible();
  await menuButton.click();
  await expect(menuButton).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();

  await page.getByRole("button", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
  await expect(menuButton).toHaveAttribute("aria-expanded", "false");
  await expect(menuButton).toBeFocused();

  const dimensions = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth);
});


test("Market Screener preview does not issue mutation requests", async ({ page }) => {
  const mutations: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/research/screener/") && request.method() !== "GET") {
      mutations.push(request.method() + " " + url.pathname);
    }
  });

  await mockApi(page);
  await signIn(page);
  await page.getByRole("button", { name: /Market Screener/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Market Screener" })).toBeVisible();
  await expect(page.getByText("No recent smart-money signals were returned.")).toBeVisible();
  expect(mutations).toEqual([]);
});


test("a temporary session-validation outage preserves the stored token without opening the dashboard", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("token", "possibly-valid-browser-token"));
  await mockApi(page, { sessionValidationUnavailable: true });
  await page.goto("/");

  await expect(page.getByRole("alert")).toHaveText("Unable to verify your session. The server may be temporarily unavailable. Please try signing in again.");
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBe("possibly-valid-browser-token");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
});

test("Escape closes mobile navigation and restores focus in the browser", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await mockApi(page);
  await signIn(page);

  const menuButton = page.getByRole("button", { name: "Open navigation" });
  await menuButton.click();
  await expect(menuButton).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Close navigation" }).first()).toBeVisible();

  await page.keyboard.press("Escape");

  await expect(menuButton).toHaveAttribute("aria-expanded", "false");
  await expect(menuButton).toBeFocused();
  await expect(page.getByRole("button", { name: "Close navigation" }).first()).toBeHidden();
  await expect(page.locator(".mobile-scrim")).toHaveCount(0);
});
