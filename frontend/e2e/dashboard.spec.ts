import { expect, test, type Page } from "@playwright/test";

const user = {
  id: 4,
  username: "browser-trader",
  email: null,
  role: "trader",
  must_change_password: false,
};

async function mockApi(page: Page, options: { expiredScreener?: boolean; forcePasswordChange?: boolean; sessionValidationUnavailable?: boolean; deniedPath?: string; loginRejected?: boolean; role?: string } = {}) {
  const activeUser = { ...user, role: options.role ?? user.role };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/auth/login" && request.method() === "POST") {
      if (options.loginRejected) {
        await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Invalid username or password" }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ token: "playwright-session-token", user: { ...activeUser, must_change_password: Boolean(options.forcePasswordChange) } }) });
      return;
    }
    if (path === "/api/auth/me") {
      if (options.sessionValidationUnavailable) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Service unavailable" }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...activeUser, must_change_password: Boolean(options.forcePasswordChange) }) });
      return;
    }
    if (options.deniedPath === path) {
      await route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "Insufficient privileges for this operation" }) });
      return;
    }
    if (options.expiredScreener && path.startsWith("/api/research/screener/")) {
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not authenticated" }) });
      return;
    }
    if (path === "/api/settings/status") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        database: { status: "ok", host: "db.internal", port: 3306, database: "signal_bot", tables: { signals: 42 }, error: null },
        heartbeats: [{ collector: "wallet-worker", status: "ok", detail: "internal-only-detail", lastBeat: "2026-10-28 05:00:00" }],
        binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
        system: { timezone: "Asia/Jakarta (WIB UTC+7)" },
      }) });
      return;
    }
    if (path === "/api/telegram/status") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ configured: true, chatId: "-100123456789", hasToken: true }) });
      return;
    }
    if (path === "/api/telegram/test" && request.method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ success: true, chat_id: "-100123456789" }) });
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
    if (path === "/api/research/screener/control" && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ enabled: true, overrides: {}, updated_at: null, tunable_fields: ["rvol_multiplier", "enable_divergence_detection"] }) });
      return;
    }

    // Domain views receive realistic empty payloads so this browser test checks
    // navigation and read-only behavior, rather than crashing on malformed mocks.
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/paper/positions") return json({ positions: [] });
    if (path === "/api/paper/configs") return json({ configs: [] });
    if (path === "/api/paper/orders") return json({ orders: [] });
    if (path === "/api/paper/trades") return json({ trades: [] });
    if (path === "/api/paper/metrics") return json({ metrics: { totalTrades: 0, wins: 0, losses: 0, winRatePct: 0, cumulativeR: 0, expectancyR: 0, netPnl: 0, byStrategy: [] } });
    if (path === "/api/paper/circuit-breakers") return json({ status: "ok", emergency_stop_active: false, emergency_stop_reason: null, active_positions: 0, max_concurrent_positions: 10, today_realized_pnl_usd: 0, max_daily_loss_usd: 100, open_notional_usd: 0, max_total_notional_usd: 1000, can_open_new_position: true, message: "ok" });

    if (path === "/api/custom-strategy/list") return json({ strategies: [] });
    if (path === "/api/custom-strategy/indicators") return json({ indicators: [], operators: [] });

    if (path === "/api/wallets/meta") return json({ periods_hours: [], chains: {}, min_net_usd: 0, auto_track_net_usd: 0 });
    if (path === "/api/wallets/settings") return json({ enabled: false, networks: [], pools_per_network: 0, min_trade_usd: 0, min_net_usd: 0, auto_track_net_usd: 0, poll_interval_seconds: 0, retention_days: 0, api_delay_seconds: 0, token_mode: "all", token_addresses: [] });
    if (path === "/api/wallets/status") return json({});
    if (path === "/api/wallets/tracked") return json({ tracked: [] });
    if (path === "/api/wallets/positions") return json({ positions: [] });
    if (path === "/api/wallets/token-flow") return json({ flows: [] });
    if (path === "/api/wallets/events") return json({ events: [] });

    if (path === "/api/backtest/strategies") return json({ strategies: [] });
    if (path === "/api/backtest/history") return json({ runs: [] });
    if (path === "/api/ml/experiments") return json({ experiments: [] });
    if (path === "/api/ml/models") return json({ models: [] });

    if (path === "/api/research/retailbot2/positions") return json({ positions: [] });
    if (path === "/api/research/retailbot2/trades") return json({ trades: [] });
    if (path === "/api/research/retailbot2/stats") return json({ by_strategy: [], by_mode: {} });
    if (path === "/api/research/retailbot2/strategies") return json({ strategies: [] });

    if (path === "/api/news/stats") return json({ total_events_collected: 0, events_evaluated_by_ai: 0, high_confidence_signals: 0, avg_confidence: null, trades_opened: 0, pending_signals: 0, performance: { closed_trades: 0, wins: 0, losses: 0, win_rate: null, expectancy_r: null, profit_factor: null, total_r: 0, net_pnl: 0, max_drawdown_r: 0 }, heartbeats: [] });
    if (path === "/api/news/signals") return json({ signals: [] });
    if (path === "/api/news/events") return json({ events: [] });

    if (path === "/api/master-data/coverage") return json({ summary: { totalCandles: 0, distinctSymbols: 0, coverageEntries: 0 }, coverage: [] });
    if (path === "/api/master-data/jobs") return json({ jobs: [] });

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
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) {
      apiRequests.push(request.method() + " " + url.pathname);
    }
  });

  await page.addInitScript(() => window.localStorage.setItem("token", "temporary-browser-token"));
  await mockApi(page, { forcePasswordChange: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Change your password" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);

  // Password-reset-required sessions may validate identity, but must not load
  // protected dashboard data before the user completes the required change.
  expect(apiRequests.length).toBeGreaterThan(0);
  expect(apiRequests.every((request) => request === "GET /api/auth/me")).toBe(true);
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


test("Market Screener exposes role-gated control settings to an authorized trader", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await page.getByRole("button", { name: /Market Screener/ }).click();
  await expect(page.getByRole("heading", { name: "Screener configuration" })).toBeVisible();
  await expect(page.getByLabel(/Telegram alerting enabled/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible();
});

test("Market Screener hides editable controls from viewer sessions", async ({ page }) => {
  await mockApi(page, { role: "viewer" });
  await signIn(page);
  await page.getByRole("button", { name: /Market Screener/ }).click();
  await expect(page.getByRole("heading", { name: "Screener configuration" })).toBeVisible();
  await expect(page.getByText(/restricted to admin and trader roles/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0);
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
  await expect(page.locator("#dashboard-sidebar")).not.toHaveClass(/sidebar-open/);
  await expect(page.locator(".mobile-scrim")).toHaveCount(0);
});


test("password confirmation mismatch is rejected in the browser without sending a mutation", async ({ page }) => {
  const passwordChangeRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/auth/change-password" && request.method() !== "GET") {
      passwordChangeRequests.push(request.method());
    }
  });

  await page.addInitScript(() => window.localStorage.setItem("token", "temporary-browser-token"));
  await mockApi(page, { forcePasswordChange: true });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Change your password" })).toBeVisible();
  await page.getByLabel("Current password").fill("current-password");
  await page.getByLabel("New password", { exact: true }).fill("valid-new-password");
  await page.getByLabel("Confirm new password").fill("different-new-password");
  await page.getByRole("button", { name: "Change password" }).click();

  await expect(page.getByRole("alert")).toHaveText("New password and confirmation do not match.");
  expect(passwordChangeRequests).toEqual([]);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
});


test("all domain preview views remain read-only during browser navigation", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  const mutations: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") && request.method() !== "GET") {
      mutations.push(request.method() + " " + url.pathname);
    }
  });

  const views = [
    "Paper Trading",
    "Strategy Library",
    "Wallet Tracking",
    "Backtest Studio",
    "ML Studio",
    "Research",
    "News AI",
    "Master Data",
    "Settings",
  ];

  for (const view of views) {
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: view, exact: true }).click();
    await expect(page.locator("h1")).toHaveText(view);
  }

  expect(mutations).toEqual([]);
});


test("all read-only domain views fit a narrow mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await mockApi(page);
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  const views = [
    "Paper Trading",
    "Strategy Library",
    "Wallet Tracking",
    "Backtest Studio",
    "ML Studio",
    "Research",
    "News AI",
    "Master Data",
    "Settings",
  ];

  for (const view of views) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: view, exact: true }).click();
    await expect(page.locator("h1")).toHaveText(view);

    const dimensions = await page.evaluate(() => ({
      viewportWidth: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
    }));
    expect(
      dimensions.documentWidth,
      `${view} must not create horizontal page overflow at 320px`,
    ).toBeLessThanOrEqual(dimensions.viewportWidth);
  }
});


test("a denied read endpoint shows a recoverable permission message without crashing the preview", async ({ page }) => {
  await mockApi(page, { deniedPath: "/api/paper/positions" });
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  await page.getByRole("button", { name: "Paper Trading", exact: true }).click();

  await expect(page.locator("h1")).toHaveText("Paper Trading");
  await expect(page.getByRole("alert")).toContainText("Your account cannot view paper trading data.");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Paper Trading", exact: true })).toHaveAttribute("aria-current", "page");
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBe("playwright-session-token");
});


test("explicit sign-out revokes the session and returns to the sign-in screen", async ({ page }) => {
  const logoutRequests: Array<{ method: string; authorization: string | undefined }> = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/auth/logout") {
      logoutRequests.push({
        method: request.method(),
        authorization: request.headers()["authorization"],
      });
    }
  });

  await mockApi(page);
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBe("playwright-session-token");

  await page.getByRole("button", { name: "Sign out" }).click();

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBeNull();
  expect(logoutRequests).toEqual([{
    method: "POST",
    authorization: "Bearer playwright-session-token",
  }]);
});


test("admin Settings shows sanitized connection diagnostics and requires confirmation before Telegram test", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/settings/status" || url.pathname === "/api/telegram/status" || url.pathname === "/api/telegram/test") {
      requests.push(request.method() + " " + url.pathname);
    }
  });
  await mockApi(page, { role: "admin" });
  await signIn(page);
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Detailed system diagnostics" })).toBeVisible();
  await expect(page.getByText("db.internal")).toBeVisible();
  await expect(page.getByText("signal_bot")).toBeVisible();
  await expect(page.getByText("online").first()).toBeVisible();
  await expect(page.getByText("internal-only-detail")).toHaveCount(0);
  await expect(page.getByText("Present (hidden)")).toBeVisible();

  await page.getByRole("button", { name: "Review Telegram test" }).click();
  await expect(page.getByRole("heading", { name: "Confirm Telegram test ping" })).toBeVisible();
  expect(requests.filter((item) => item === "POST /api/telegram/test")).toEqual([]);
  await page.getByRole("button", { name: "Cancel test" }).click();
  expect(requests.filter((item) => item === "POST /api/telegram/test")).toEqual([]);
});

test("viewer Settings does not request admin diagnostics or Telegram configuration", async ({ page }) => {
  const restrictedReads: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (["/api/settings/status", "/api/telegram/status"].includes(url.pathname)) {
      restrictedReads.push(request.method() + " " + url.pathname);
    }
  });
  await mockApi(page, { role: "viewer" });
  await signIn(page);
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.locator("h1")).toHaveText("Settings");
  await expect(page.getByText(/Telegram status and test controls are restricted to admins/i)).toBeVisible();
  expect(restrictedReads).toEqual([]);
});

test("an expired Settings read clears the stale token and returns to sign-in", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("token", "stale-settings-token"));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/auth/me") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...user, role: "admin" }) });
      return;
    }
    if (path === "/api/settings/status") {
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not authenticated" }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveText("Your session has expired. Sign in again to continue.");
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBeNull();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
});

test("a rejected sign-in keeps protected content hidden and does not persist a token", async ({ page }) => {
  await mockApi(page, { loginRejected: true });
  await signIn(page);

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveText("Invalid username or password");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
  expect(await page.evaluate(() => window.localStorage.getItem("token"))).toBeNull();
});

test("an unauthenticated browser load does not request protected dashboard APIs", async ({ page }) => {
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) {
      apiRequests.push(request.method() + " " + url.pathname);
    }
  });

  await mockApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
  expect(apiRequests).toEqual([]);
});
