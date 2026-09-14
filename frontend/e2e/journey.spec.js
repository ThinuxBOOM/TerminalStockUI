import { test, expect } from "@playwright/test";
const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
async function backendOnline(request) {
  try {
    const resp = await request.get(`${API_BASE}/health`, { timeout: 5e3 });
    return resp.ok();
  } catch {
    return false;
  }
}
test.describe("Search \u2192 Security Brief \u2192 AI Insight", () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!await backendOnline(request), "backend offline \u2014 skipping E2E");
  });
  test("Search AAPL \u2192 Security Brief shows price, provenance, disclosure", async ({
    page
  }) => {
    await page.goto("/search?q=AAPL");
    await expect(page.getByLabel("Search instruments")).toBeVisible();
    const briefLink = page.getByRole("link", { name: /BRIEF →/ }).first();
    await expect(briefLink).toBeVisible({ timeout: 15e3 });
    await briefLink.click();
    await expect(page.getByText(/SECURITY BRIEF/, { exact: false })).toBeVisible({
      timeout: 15e3
    });
    await expect(page.getByText(/Forecast/, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(/Not investment advice/i).first()).toBeVisible();
    await expect(page.getByText(/Source|yfinance|instrument-registry/i).first()).toBeVisible();
  });
  test("AI Insight flow renders bounded opinion or explicit-request state", async ({
    page
  }) => {
    await page.goto("/security/AAPL");
    await expect(page.getByText(/SECURITY BRIEF/, { exact: false })).toBeVisible({
      timeout: 15e3
    });
    const requestBtn = page.getByRole("button", { name: /REQUEST AI OPINION/ });
    const opinionCard = page.getByText(/AI opinion/, { exact: false }).first();
    await expect(requestBtn.or(opinionCard)).toBeVisible({ timeout: 15e3 });
    if (await requestBtn.isVisible()) {
      await requestBtn.click();
      await expect(page.getByText(/AI opinion/, { exact: false }).first()).toBeVisible({
        timeout: 3e4
      });
    }
    await expect(page.getByText(/CAPPED 20%|bounded/i).first()).toBeVisible();
    await expect(page.getByText(/Not investment advice/i).first()).toBeVisible();
  });
});
