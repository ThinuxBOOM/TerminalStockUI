import { defineConfig } from "@playwright/test";
var stdin_default = defineConfig({
  // NOTE: this config lives in frontend/e2e/ (per M8 spec), so the test
  // dir is the config's own directory. Run from frontend/ with:
  //   npx playwright test -c e2e/playwright.config.ts
  testDir: ".",
  timeout: 6e4,
  retries: 0,
  use: {
    baseURL: process.env.FRONTEND_BASE_URL ?? "http://localhost:5173"
  }
});
export { stdin_default as default };
