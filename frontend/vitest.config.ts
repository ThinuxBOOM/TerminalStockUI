import { defineConfig } from 'vitest/config';

// Phase 4b: pure-logic specs only — Node environment, no browser APIs,
// no jsdom. Specs live in src/**/*.{test,spec}.{ts,tsx} and must stay deterministic
// (no network, no DOM, pinned timestamps).
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // Playwright E2E lives in e2e/ (run: `npx playwright test`) and must
    // never be collected by vitest, even if invoked from the repo root
    // where this config's `include` may not apply. `**/e2e/**` covers
    // repo-root invocation (`frontend/e2e/...`); `**/dist/**` and
    // `**/coverage/**` prevent collecting build/coverage output on reruns.
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      'e2e/**',
      '**/e2e/**',
      '**/coverage/**',
      '**/.vite/**',
    ],
  },
});
