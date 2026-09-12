/**
 * M8 Playwright E2E: Search → Security Brief → AI Insight.
 *
 * Run: `npx playwright test` (frontend `npm run dev` + backend on :8000).
 * Skip-friendly: every test first probes `${API}/health`; when the backend
 * is offline the test is skipped (not failed) so `npx playwright test`
 * stays green on a frontend-only checkout.
 *
 * Selector convention (accessible roles/text today; stable hooks next):
 * - Search input:       getByLabel('Search instruments')
 * - Search submit:      getByRole('button', { name: 'SEARCH' })
 * - Result links:       link 'BRIEF →'  (future: data-testid="search-result-<SYM>")
 * - Security Brief:     heading /SECURITY BRIEF/ (future: data-testid="security-brief")
 * - Provenance:         text /Source|source/ + /quality/i
 * - Disclosure:         text /Not investment advice/i
 * - AI Insight request: button 'REQUEST AI OPINION' (future: data-testid="ai-insight-request")
 * - AI opinion card:    text /AI opinion/ (future: data-testid="ai-opinion-card")
 */
import { test, expect } from '@playwright/test';

const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';

async function backendOnline(request: import('@playwright/test').APIRequestContext) {
  try {
    const resp = await request.get(`${API_BASE}/health`, { timeout: 5000 });
    return resp.ok();
  } catch {
    return false;
  }
}

test.describe('Search → Security Brief → AI Insight', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!(await backendOnline(request)), 'backend offline — skipping E2E');
  });

  test('Search AAPL → Security Brief shows price, provenance, disclosure', async ({
    page,
  }) => {
    await page.goto('/search?q=AAPL');
    await expect(page.getByLabel('Search instruments')).toBeVisible();

    // Results render from the live registry search.
    const briefLink = page.getByRole('link', { name: /BRIEF →/ }).first();
    await expect(briefLink).toBeVisible({ timeout: 15_000 });
    await briefLink.click();

    // Security Brief: symbol header + price + provenance + disclosure.
    await expect(page.getByText(/SECURITY BRIEF/, { exact: false })).toBeVisible({
      timeout: 15_000,
    });
    // Deterministic forecast header + disclosure always render (live or placeholder).
    await expect(page.getByText(/Forecast/, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(/Not investment advice/i).first()).toBeVisible();
    // Provenance badge carries source/freshness info.
    await expect(page.getByText(/Source|yfinance|instrument-registry/i).first()).toBeVisible();
  });

  test('AI Insight flow renders bounded opinion or explicit-request state', async ({
    page,
  }) => {
    await page.goto('/security/AAPL');

    await expect(page.getByText(/SECURITY BRIEF/, { exact: false })).toBeVisible({
      timeout: 15_000,
    });

    // The AI opinion card area: either an explicit-request button (no opinion
    // yet) or a bounded opinion with capped-20% policy + disclosure.
    const requestBtn = page.getByRole('button', { name: /REQUEST AI OPINION/ });
    const opinionCard = page.getByText(/AI opinion/, { exact: false }).first();
    await expect(requestBtn.or(opinionCard)).toBeVisible({ timeout: 15_000 });

    if (await requestBtn.isVisible()) {
      await requestBtn.click();
      await expect(page.getByText(/AI opinion/, { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });
    }

    // Bounded-policy markers: capped influence + evidence grounding.
    await expect(page.getByText(/CAPPED 20%|bounded/i).first()).toBeVisible();
    await expect(page.getByText(/Not investment advice/i).first()).toBeVisible();
  });
});
