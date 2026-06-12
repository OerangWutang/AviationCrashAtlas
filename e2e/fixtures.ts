/**
 * Shared Playwright fixtures for Atlas E2E tests.
 *
 * Provides:
 *   - loginAs(role)       — log in as reviewer, analyst, or admin
 *   - logout()            — log out and verify session cleared
 *   - waitForCase(slug)   — navigate to a case and wait for it to load
 *   - assertNoApiKey()    — verify X-API-Key never appears in browser requests
 *
 * All tests should import from this file, not from @playwright/test directly.
 */

import { test as base, expect, type Page } from "@playwright/test";

// ── Credentials from env (or safe defaults for local dev) ────────────────────

export const REVIEWER_EMAIL =
  process.env["E2E_REVIEWER_EMAIL"] ?? "reviewer@atlas-test.local";
export const REVIEWER_PASSWORD =
  process.env["E2E_REVIEWER_PASSWORD"] ?? "reviewer-test-password-123";
export const ANALYST_EMAIL =
  process.env["E2E_ANALYST_EMAIL"] ?? "analyst@atlas-test.local";
export const ANALYST_PASSWORD =
  process.env["E2E_ANALYST_PASSWORD"] ?? "analyst-test-password-123";

// The slug for the Colgan Air 3407 seed case — used across tests
export const TEST_CASE_SLUG =
  process.env["E2E_TEST_CASE_SLUG"] ?? "colgan-air-3407";

// ── Request interception helper ───────────────────────────────────────────────

/**
 * Capture any request that contains X-API-Key in its headers.
 * Called in security assertions to confirm the key never leaves the BFF.
 */
export function captureApiKeyLeaks(page: Page): { leaked: string[] } {
  const leaked: string[] = [];
  page.on("request", (req) => {
    const headers = req.headers();
    if ("x-api-key" in headers) {
      leaked.push(`${req.method()} ${req.url()}`);
    }
  });
  return { leaked };
}

// ── Custom fixture type ───────────────────────────────────────────────────────

type AtlasFixtures = {
  reviewerPage: Page;
  analystPage: Page;
};

// ── Login helper ──────────────────────────────────────────────────────────────

export async function loginAs(
  page: Page,
  email: string,
  password: string,
): Promise<void> {
  await page.goto("/login");
  await page.waitForSelector("input[type='email']");
  await page.fill("input[type='email']", email);
  await page.fill("input[type='password']", password);
  await page.click("button[type='submit'], button:has-text('Sign in'), button:has-text('Log in')");
  // Wait for redirect to /app/cases
  await page.waitForURL("**/app/cases**", { timeout: 10_000 });
}

export async function logout(page: Page): Promise<void> {
  // TopBar logout button
  await page.click("[data-testid='logout-btn'], button:has-text('Sign out'), button:has-text('Logout')");
  await page.waitForURL("**/login**", { timeout: 5_000 });
}

// ── Extended test fixture ─────────────────────────────────────────────────────

export const test = base.extend<AtlasFixtures>({
  reviewerPage: async ({ browser }, use) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await loginAs(page, REVIEWER_EMAIL, REVIEWER_PASSWORD);
    await use(page);
    await context.close();
  },
  analystPage: async ({ browser }, use) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await loginAs(page, ANALYST_EMAIL, ANALYST_PASSWORD);
    await use(page);
    await context.close();
  },
});

export { expect };
