import { defineConfig, devices } from "@playwright/test";

// Browser tests against a running deployment (docker compose or `npm run dev`
// with the backend). Run from frontend/:
//   E2E_BASE_URL=http://localhost:18080 E2E_EMAIL=... E2E_PASSWORD=... npm run e2e
// Without E2E_EMAIL/E2E_PASSWORD the specs skip (they need a real account).
export default defineConfig({
  testDir: ".",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
