import { expect, test, type Page } from "@playwright/test";

const username = process.env.SIGNAL_BOT_REAL_E2E_USER ?? "browser_e2e";
const password = process.env.SIGNAL_BOT_REAL_E2E_PASSWORD;
const runId = process.env.SIGNAL_BOT_TEST_RUN_ID;

async function signIn(page: Page, view: string) {
  if (!password || !runId) throw new Error("Set browser fixture credentials and test-run identity.");
  const identity = await page.request.get("/api/system/test-identity");
  expect(identity.ok()).toBe(true);
  expect((await identity.json()).run_id).toBe(runId);
  const readiness = await page.request.get("/readyz");
  expect(readiness.ok()).toBe(true);
  await page.goto(`/modern/${view}`);
  await page.getByLabel("Username").fill(username);
  const field = page.getByLabel("Password");
  await expect(field).toBeVisible();
  await field.evaluate((input, value) => {
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    nativeSetter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("navigation", { name: "Primary navigation" }).waitFor();
  const terms = page.getByRole("checkbox", { name: /I accept the platform terms/ });
  if (await terms.isVisible()) {
    await terms.check();
    const acknowledge = page.getByRole("button", { name: "Acknowledge & Enter Workstation" });
    await acknowledge.click();
    await expect(acknowledge).toBeHidden();
  }
}

async function confirmDeploymentDelete(page: Page, symbol: string) {
  await page.getByRole("row").filter({ hasText: symbol }).getByRole("button", { name: "Delete permanently" }).click();
  await page.getByRole("button", { name: "Confirm permanent delete" }).click();
}

test("deploy, pause, edit parameters, archive and delete paper deployments", async ({ page }) => {
  await signIn(page, "paper-trading");
  const review = page.getByRole("button", { name: "Confirm review" });
  await review.click();
  await review.click();
  await expect(review).toBeDisabled();
  const nav = page.getByRole("navigation", { name: "Primary navigation" });
  await nav.getByRole("button", { name: "Backtest Studio", exact: true }).click();
  await page.getByRole("button", { name: "Use run for paper readiness" }).click();
  await review.click();
  await review.click();
  await page.getByRole("button", { name: "Return to selected evidence" }).click();
  await page.getByRole("button", { name: "Deploy to Paper Trading" }).click();
  const deploy = page.getByRole("dialog", { name: "Deploy Tested Strategy to Paper Trading" });
  await expect(deploy).toContainText("E2EPAPERUSDT");
  await deploy.getByRole("button", { name: "Deploy to Paper Trading" }).click();
  await expect(deploy).toBeHidden();
  await nav.getByRole("button", { name: "Paper Trading", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: "E2EPAPERUSDT" });
  await expect(row).toContainText("Active");
  await expect(row.getByRole("button", { name: "Delete permanently" })).toBeDisabled();
  await row.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(row).toContainText("Paused");
  await row.getByRole("button", { name: "Edit", exact: true }).click();
  let editor = page.getByRole("region", { name: "Edit paper deployment" });
  await editor.getByLabel("Allocated capital").fill("7000");
  await editor.getByLabel("Risk per trade (%)").fill("1.5");
  const param = editor.getByRole("group", { name: "Strategy parameters" }).getByRole("spinbutton").first();
  const min = Number(await param.getAttribute("min"));
  const max = Number(await param.getAttribute("max"));
  const step = Number(await param.getAttribute("step"));
  const override = String(Number(Math.min(max, min + step).toFixed(6)));
  await param.fill(override);
  await editor.getByRole("button", { name: "Save deployment" }).click();
  await expect(editor).toHaveCount(0);
  await expect(row).toContainText("7,000");
  await row.getByRole("button", { name: "Edit", exact: true }).click();
  editor = page.getByRole("region", { name: "Edit paper deployment" });
  await expect(editor.getByLabel("Risk per trade (%)")).toHaveValue("1.5");
  await expect(editor.getByRole("group", { name: "Strategy parameters" }).getByRole("spinbutton").first()).toHaveValue(override);
  await editor.getByRole("button", { name: "Reset parameters to defaults" }).click();
  await editor.getByRole("button", { name: "Save deployment" }).click();
  await expect(editor).toHaveCount(0);
  await row.getByRole("button", { name: "Archive", exact: true }).click();
  await page.getByRole("button", { name: "Confirm archive" }).click();
  await expect(row).toContainText("Archived");
  await confirmDeploymentDelete(page, "E2EPAPERUSDT");
  await expect(row).toHaveCount(0);
  await confirmDeploymentDelete(page, "HISTORYUSDT");
  await expect(page.getByText(/has positions, trades or orders/)).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "HISTORYUSDT" })).toBeVisible();
});

test("pause and resume background services with launch and stale heartbeat messages", async ({ page }) => {
  await signIn(page, "settings");
  const services = page.getByRole("region", { name: "Background Services" });
  const engine = services.getByRole("row").filter({ hasText: "Paper Trading Engine" });
  await expect(engine).toContainText("PAPER_ENGINE_ENABLED=1");
  const collectors = services.getByRole("row").filter({ hasText: "Market Data Collectors" });
  await expect(collectors).toContainText("Stale heartbeat");
  for (const service of [
    "Paper Trading Engine", "Paper Performance Reports", "News + Economic Calendar AI",
    "Wallet Tracker", "Market Data Collectors", "Screener", "Telegram Outbox",
  ]) {
    await services.getByRole("button", { name: `Pause ${service}`, exact: true }).click();
    await services.getByRole("button", { name: `Confirm pause ${service}`, exact: true }).click();
    await services.getByRole("button", { name: `Resume ${service}`, exact: true }).click();
    await expect(services.getByRole("button", { name: `Pause ${service}`, exact: true })).toBeVisible();
  }
});

test("save and reset a Screener override, then toggle scanning", async ({ page }) => {
  await signIn(page, "market-screener");
  const control = page.getByRole("region", { name: "Screener configuration" });
  const rvol = control.getByLabel(/Minimum signal RVOL/);
  await rvol.fill("3.5");
  await control.getByRole("button", { name: "Save changes" }).click();
  await expect(control.getByText(/Saved override/).first()).toBeVisible();
  await rvol.fill("");
  await control.getByRole("button", { name: "Save changes" }).click();
  await expect(rvol).toHaveValue("");
  await expect(control.getByText(/Saved override/)).toHaveCount(0);
  page.once("dialog", (dialog) => dialog.accept());
  const enabled = control.getByRole("checkbox", { name: /Smart Money Screener enabled/ });
  await enabled.uncheck();
  await control.getByRole("button", { name: "Save changes" }).click();
  await control.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(enabled).not.toBeChecked();
  await enabled.check();
  await control.getByRole("button", { name: "Save changes" }).click();
  await expect(enabled).toBeChecked();
});

test("track a wallet, edit its label and untrack it", async ({ page }) => {
  await signIn(page, "wallet-tracking");
  const wallets = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "Tracked wallets", exact: true }) });
  await wallets.getByRole("button", { name: "Track wallet", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Chain / Network").selectOption("eth");
  await dialog.getByLabel("Wallet Address").fill("0x1234567890123456789012345678901234567890");
  await dialog.getByLabel("Label (optional)").fill("Browser fixture wallet");
  await dialog.getByRole("button", { name: "Start Tracking" }).click();
  const row = page.getByRole("row").filter({ hasText: "Browser fixture wallet" });
  await row.getByRole("button", { name: "Edit label" }).click();
  await wallets.getByRole("textbox", { name: "Wallet label" }).fill("Renamed browser wallet");
  await wallets.getByRole("button", { name: "Save label" }).click();
  const renamed = page.getByRole("row").filter({ hasText: "Renamed browser wallet" });
  await renamed.getByRole("button", { name: "Untrack", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Untrack Wallet" }).click();
  await expect(renamed).toHaveCount(0);
});

test("create, edit, disable, and delete a custom strategy", async ({ page }) => {
  await signIn(page, "strategy-library");
  await page.getByRole("button", { name: "New strategy" }).click();
  await page.getByRole("textbox", { name: "Strategy name", exact: true }).fill("browser_fixture_rule");
  await page.getByRole("textbox", { name: "Strategy display name" }).fill("Browser fixture rule");
  await page.getByRole("button", { name: "Add condition" }).first().click();
  await page.getByRole("button", { name: "Save strategy" }).click();
  const card = page.getByRole("article").filter({ hasText: "Browser fixture rule" });
  await card.getByRole("button", { name: "Edit strategy" }).click();
  await page.getByRole("textbox", { name: "Strategy display name" }).fill("Edited browser rule");
  await page.getByRole("button", { name: "Review update" }).click();
  await page.getByRole("button", { name: "Confirm update" }).click();
  const edited = page.getByRole("article").filter({ hasText: "Edited browser rule" });
  await edited.getByRole("button", { name: "Disable strategy" }).click();
  await expect(edited.getByRole("button", { name: "Enable strategy" })).toBeVisible();
  await edited.getByRole("button", { name: "Delete strategy" }).click();
  await page.getByRole("region", { name: "Delete strategy?" }).getByRole("button", { name: "Delete strategy" }).click();
  await expect(edited).toHaveCount(0);
});

test("change News AI sizing overrides and reset server defaults", async ({ page }) => {
  await signIn(page, "news-ai");
  const controls = page.getByRole("region", { name: "News AI guardrails" });
  const stop = controls.getByRole("textbox", { name: /Stop loss \(ATR multiple\)/ });
  const target = controls.getByRole("textbox", { name: /Scalp take profit/ });
  const originalStop = await stop.inputValue();
  const originalTarget = await target.inputValue();
  await stop.fill("2.25");
  await target.fill("4.5");
  await controls.getByRole("button", { name: "Save guardrails" }).click();
  await expect(controls.getByRole("status")).toContainText("Settings saved");
  await page.reload();
  await expect(stop).toHaveValue("2.25");
  await expect(target).toHaveValue("4.5");
  await controls.getByRole("button", { name: "Reset to defaults" }).click();
  await expect(stop).toHaveValue(originalStop);
  await expect(target).toHaveValue(originalTarget);
});

test("Modern and Legacy show the same seeded coverage KPIs", async ({ page }) => {
  await signIn(page, "overview");
  const coverage = page.getByRole("region", { name: "Dataset coverage" });
  const kpis = { Candles: "3", Datasets: "2", Symbols: "2" };
  for (const [label, value] of Object.entries(kpis)) {
    await expect(coverage.getByRole("article").filter({ hasText: label }).locator(".metric-value")).toHaveText(value);
  }
  await page.goto("/", { waitUntil: "domcontentloaded" });
  for (const [label, value] of Object.entries(kpis)) {
    await expect(page.locator(`#home-kpi-${label.toLowerCase()}`)).toHaveText(value);
  }
});
