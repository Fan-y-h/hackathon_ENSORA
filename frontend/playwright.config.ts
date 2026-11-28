import { defineConfig, devices } from "@playwright/test";

// End-to-end tests of the jury journey on the demo build (production code + demo data).
// Browser: the one installed on the machine (PW_CHANNEL=msedge or chrome), or Playwright's
// own Chromium after `npx playwright install chromium` (leave PW_CHANNEL empty).
const channel = process.env.PW_CHANNEL || undefined;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4175",
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1366, height: 900 } }, testIgnore: /responsive.spec.ts/ },
    { name: "phone", use: { ...devices["Pixel 7"], channel }, testMatch: /responsive\.spec\.ts/ },
  ],
  webServer: {
    // The tests need the demo data: build in demo mode (.env.demo), not the production mode.
    command: "npm run build:demo && npx vite preview --port 4175 --strictPort",
    url: "http://localhost:4175",
    // Reuse a server already started on 4175 (faster local reruns).
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
