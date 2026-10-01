import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

test.skip(!EMAIL || !PASSWORD, "set E2E_EMAIL and E2E_PASSWORD to an existing account");

async function signIn(page, next = "/app") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByRole("button", { name: /SIGN IN/ }).click();
  await page.waitForURL((url) => url.pathname === next);
}

test("terminal pages require a login", async ({ page }) => {
  await page.goto("/security/AAPL");
  await expect(page).toHaveURL(/\/login\?next=%2Fsecurity%2FAAPL/);
  await expect(page.getByRole("button", { name: /SIGN IN/ })).toBeVisible();
});

test("wrong password shows an error and stays on login", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill("definitely-not-the-password");
  await page.getByRole("button", { name: /SIGN IN/ }).click();
  await expect(page.getByRole("alert")).toContainText(/wrong email or password/i);
  await expect(page).toHaveURL(/\/login/);
});

test("sign in, read a security brief, survive a reload, sign out", async ({ page }) => {
  await signIn(page, "/security/AAPL");

  // Live quote with provenance, chart, and an experimental forecast.
  await expect(page.getByRole("heading", { name: /AAPL/ })).toBeVisible();
  await expect(page.getByText(/price at /)).toBeVisible();
  await expect(page.getByText("PRICE CHART", { exact: false })).toBeVisible();
  await expect(page.getByText("EXPERIMENTAL", { exact: true }).first()).toBeVisible();
  // Range leads; the direction lean carries its measured track record.
  await expect(page.getByText(/-day range · 80% of comparable past periods/).first()).toBeVisible();
  await expect(page.getByText(/^Measured: /).first()).toBeVisible();
  await expect(page.getByText(/Not investment advice/i).first()).toBeVisible();

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
