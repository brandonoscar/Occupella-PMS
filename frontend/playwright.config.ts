// Browser tests of the portal. They need the API serving the built portal on E2E_BASE_URL
// (default http://127.0.0.1:8123) and a database seeded by e2e/seed.py into e2e/.seed.json;
// .github/workflows/e2e.yml sets both up. PW_CHROMIUM points at a Chromium already installed
// (for a machine where `playwright install` can't run). PW_PROXY sends every request through a
// proxy, loopback too (zap.yml's ZAP, which reads what the tests' sessions get).
// scripts/simulate.ps1 runs them in other browsers and places: PW_CHANNEL a branded browser
// already on the machine (msedge, chrome), PW_TIMEZONE the browser's time zone and PW_LOCALE its
// language; unset, the machine's own.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8123",
    trace: "retain-on-failure",
    channel: process.env.PW_CHANNEL || undefined,
    timezoneId: process.env.PW_TIMEZONE || undefined,
    locale: process.env.PW_LOCALE || undefined,
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
    proxy: process.env.PW_PROXY
      ? { server: process.env.PW_PROXY, bypass: "<-loopback>" }
      : undefined,
  },
});
