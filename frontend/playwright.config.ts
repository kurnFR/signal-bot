import { defineConfig, devices } from "@playwright/test";

const realBase = process.env.SIGNAL_BOT_REAL_E2E_URL;
if (realBase && !["127.0.0.1", "localhost"].includes(new URL(realBase).hostname)) {
  throw new Error("Real-backend UI tests require a loopback server.");
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: realBase ? "real-backend.spec.ts" : "dashboard.spec.ts",
  projects: [{ name: realBase ? "real-backend" : "mocked-dashboard" }],
  fullyParallel: !realBase,
  workers: realBase ? 1 : undefined,
  forbidOnly: Boolean(process.env.CI),
  retries: realBase ? 0 : process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: realBase || "http://127.0.0.1:5173",
    trace: realBase ? "off" : "retain-on-failure",
    actionTimeout: realBase ? 10_000 : undefined,
    ...devices["Desktop Chrome"],
    colorScheme: "dark",
  },
  webServer: realBase ? undefined : {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
