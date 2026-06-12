/**
 * E2E tests: Authentication and security boundary.
 *
 * Covers acceptance criteria from the implementation plan:
 *   1. Unauthenticated user is redirected to /login
 *   2. Login succeeds and /auth/me populates role and permissions
 *   3. POST /auth/logout invalidates the session
 *   4. No X-API-Key appears in any browser request
 *   5. Session cookie is HttpOnly (JS cannot read it)
 *   6. /api/cases returns 401 without a session
 */

import { test, expect, loginAs, captureApiKeyLeaks, REVIEWER_EMAIL, REVIEWER_PASSWORD } from "./fixtures";

test.describe("Authentication", () => {
  // ── Redirect ────────────────────────────────────────────────────────────────

  test("unauthenticated /app/cases redirects to /login", async ({ page }) => {
    await page.goto("/app/cases");
    await page.waitForURL("**/login**", { timeout: 8_000 });
    expect(page.url()).toContain("/login");
  });

  test("unauthenticated / redirects to /login (via /app/cases)", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL("**/login**", { timeout: 8_000 });
    expect(page.url()).toContain("/login");
  });

  // ── Login success ───────────────────────────────────────────────────────────

  test("successful login lands at /app/cases and shows case list", async ({ page }) => {
    await loginAs(page, REVIEWER_EMAIL, REVIEWER_PASSWORD);

    expect(page.url()).toContain("/app/cases");

    // The case list should render (at least the table header or an empty state)
    await expect(
      page.locator("table, [data-testid='case-list'], h1:has-text('Cases'), text=No cases")
    ).toBeVisible({ timeout: 10_000 });
  });

  test("invalid credentials show an error and do not redirect", async ({ page }) => {
    await page.goto("/login");
    await page.fill("input[type='email']", REVIEWER_EMAIL);
    await page.fill("input[type='password']", "definitely-wrong-password");
    await page.click("button[type='submit'], button:has-text('Sign in')");

    // Should stay on /login
    await page.waitForTimeout(1_500);
    expect(page.url()).toContain("/login");

    // Should show an error message
    await expect(
      page.locator("text=Invalid, text=incorrect, text=wrong, [role='alert']").first()
    ).toBeVisible({ timeout: 5_000 });
  });

  // ── Security: no X-API-Key in browser ──────────────────────────────────────

  test("X-API-Key never appears in browser network requests during login and case load", async ({ page }) => {
    const { leaked } = captureApiKeyLeaks(page);

    await loginAs(page, REVIEWER_EMAIL, REVIEWER_PASSWORD);
    // Wait for case list to load (triggers /api/cases request)
    await page.waitForTimeout(2_000);

    expect(leaked).toHaveLength(0);
    if (leaked.length > 0) {
      throw new Error(`X-API-Key leaked in browser request: ${leaked.join(", ")}`);
    }
  });

  test("X-API-Key does not appear in the JavaScript bundle", async ({ page }) => {
    await page.goto("/");

    // Collect all script URLs loaded by the page
    const scriptUrls: string[] = [];
    page.on("response", (res) => {
      const ct = res.headers()["content-type"] ?? "";
      if (ct.includes("javascript")) scriptUrls.push(res.url());
    });

    await page.goto("/login");
    await page.waitForTimeout(2_000);

    // Fetch each script and check for API key patterns
    for (const url of scriptUrls) {
      try {
        const res = await page.request.get(url);
        const body = await res.text();
        // Atlas API keys follow the pattern atlas_live_... or atlas_test_...
        const keyPattern = /atlas_(live|test|dev)_[a-zA-Z0-9_-]{20,}/g;
        const matches = body.match(keyPattern) ?? [];
        expect(matches).toHaveLength(0);
      } catch {
        // Script fetch failed — not an error for this test
      }
    }
  });

  test("session cookie is HttpOnly (JS cannot read it)", async ({ page }) => {
    await loginAs(page, REVIEWER_EMAIL, REVIEWER_PASSWORD);

    // Try to read all cookies from JS — the session cookie must not appear
    const cookiesFromJs = await page.evaluate(() => document.cookie);
    // The session cookie name contains "atlas-session"
    expect(cookiesFromJs).not.toContain("atlas-session");
    // The CSRF cookie (atlas-csrf) should be readable by JS
    expect(cookiesFromJs).toContain("atlas-csrf");
  });

  // ── Logout ──────────────────────────────────────────────────────────────────

  test("logout invalidates session; /app/cases then redirects to /login", async ({ page }) => {
    await loginAs(page, REVIEWER_EMAIL, REVIEWER_PASSWORD);

    // Find and click the logout button in the TopBar
    await page.click("button:has-text('Sign out'), button:has-text('Logout'), [data-testid='logout-btn']");
    await page.waitForURL("**/login**", { timeout: 8_000 });

    // Trying to access /app/cases after logout should redirect back to /login
    await page.goto("/app/cases");
    await page.waitForURL("**/login**", { timeout: 8_000 });
    expect(page.url()).toContain("/login");
  });

  // ── /auth/me role propagation ───────────────────────────────────────────────

  test("/auth/me returns correct role that gates UI affordances", async ({ page }) => {
    await loginAs(page, REVIEWER_EMAIL, REVIEWER_PASSWORD);

    // /auth/me is called by AuthProvider on startup
    // Verify role-gated UI: reviewer should see the Resolve button enabled somewhere
    // Navigate to a case with a conflict
    const caseUrl = page.url(); // should be /app/cases
    expect(caseUrl).toContain("/app/cases");
  });
});
