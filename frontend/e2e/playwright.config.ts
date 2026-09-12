import { defineConfig } from '@playwright/test';

/**
 * Minimal M8 Playwright config.
 * - Frontend dev server must be running: `npm run dev` (http://localhost:5173).
 * - Backend API expected at http://localhost:8000 (override with API_BASE_URL).
 * - Specs skip gracefully when the backend is offline (see journey.spec.ts).
 *
 * Run: `npx playwright test` (first install: `npm i -D @playwright/test`).
 */
export default defineConfig({
  // NOTE: this config lives in frontend/e2e/ (per M8 spec), so the test
  // dir is the config's own directory. Run from frontend/ with:
  //   npx playwright test -c e2e/playwright.config.ts
  testDir: '.',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.FRONTEND_BASE_URL ?? 'http://localhost:5173',
  },
});
