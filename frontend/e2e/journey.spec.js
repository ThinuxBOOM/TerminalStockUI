import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

test.skip(!EMAIL || !PASSWORD, "set E2E_EMAIL and E2E_PASSWORD to an existing account");

async function signIn(page, next = "/app") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.waitForURL((url) => url.pathname === next);
}

test("terminal pages require a login", async ({ page }) => {
  await page.goto("/security/AAPL");
  await expect(page).toHaveURL(/\/login\?next=%2Fsecurity%2FAAPL/);
  await expect(page.getByRole("button", { name: /^sign in$/i })).toBeVisible();
});

test("wrong password shows an error and stays on login", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill("definitely-not-the-password");
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByRole("alert")).toContainText(/wrong email or password/i);
  await expect(page).toHaveURL(/\/login/);
});

test("sign in, research a stock, survive a reload, sign out", async ({ page }) => {
  await signIn(page, "/security/AAPL");

  // Live quote, chart, and a forecast that leads with its range.
  await expect(page.getByRole("heading", { name: /AAPL/ })).toBeVisible();
  await expect(page.getByText(/price at /)).toBeVisible();
  await expect(page.getByRole("tab", { name: "Forecast" })).toBeVisible();
  await expect(page.getByText(/Likely 21-day range \(80%\)/)).toBeVisible();
  await expect(page.getByText(/not investment advice/i).first()).toBeVisible();

  // The Forecast tab shows the measured record next to the numbers.
  await page.getByRole("tab", { name: "Forecast" }).click();
  await expect(page).toHaveURL(/tab=forecast/);
  await expect(page.getByText("How accurate is this?")).toBeVisible();

  // Risk metrics and the position sizer.
  await page.getByRole("tab", { name: "Risk" }).click();
  await expect(page.getByText("Value at risk")).toBeVisible();
  await expect(page.getByText("Position size")).toBeVisible();

  // A full reload restores the session from the httpOnly refresh cookie.
  await page.reload();
  await expect(page).toHaveURL(/\/security\/AAPL/);
  await expect(page.getByRole("heading", { name: /AAPL/ })).toBeVisible();

  // Signing out revokes the session: protected pages bounce to /login.
  await page.goto("/account");
  await page.getByRole("button", { name: "SIGN OUT" }).click();
  await page.waitForURL((url) => url.pathname === "/");
  await page.goto("/watchlist");
  await expect(page).toHaveURL(/\/login\?next=%2Fwatchlist/);
});

test("screener and model lab load measured data", async ({ page }) => {
  await signIn(page, "/screener");
  await expect(page.getByRole("heading", { name: "Screener" })).toBeVisible();
  await expect(page.getByText(/ranking's correlation with later returns/)).toBeVisible();
  await page.goto("/model");
  await expect(page.getByRole("heading", { name: "Model Lab" })).toBeVisible();
  await expect(page.getByText("Does the 80% range contain 80% of outcomes?")).toBeVisible();
});
