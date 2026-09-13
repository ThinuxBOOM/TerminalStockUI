import { defineConfig } from 'vitest/config';

// Phase 4b: pure-logic specs only — Node environment, no browser APIs,
// no jsdom. Specs live in src/**/*.test.ts and must stay deterministic
// (no network, no DOM, pinned timestamps).
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
