import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for Atlas web app E2E tests.
 *
 * Runs against the full stack:
 *   - This web app (Vite dev server on port 5173, auto-started via webServer below)
 *   - BFF (on port 3001)
 *   - Atlas FastAPI backend (on port 8000, must be running with test seed data)
 *
 * Before running:
 *   1. Start the Atlas FastAPI backend with a test database:
 *      cd ../../../atlas-argus && DATABASE_URL=... uvicorn atlas.presentation.api.app:app --port 8000
 *   2. Seed the BFF with a test user:
 *      cd ../../../bff-extracted/bff && BFF_DB_PATH=./bff-test.db ATLAS_RAW_API_KEY=... node --loader tsx src/scripts/seed-user.ts --email test@example.com --role reviewer
 *   3. Start the BFF:
 *      cd ../../../bff-extracted/bff && BFF_DB_PATH=./bff-test.db BFF_SESSION_SECRET=... npm run dev
 *   4. Run Playwright:
 *      cd web-extracted/apps/web && npx playwright test
 *
 * For CI, set E2E_BASE_URL, E2E_BFF_URL, E2E_TEST_EMAIL, E2E_TEST_PASSWORD,
 * E2E_REVIEWER_EMAIL, E2E_REVIEWER_PASSWORD, E2E_ANALYST_EMAIL, E2E_ANALYST_PASSWORD
 * as environment variables.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // Serial to avoid race conditions on shared test data
  forbidOnly: !!process.env["CI"],
  retries: process.env["CI"] ? 2 : 0,
  workers: 1, // Single worker — tests share server state
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "e2e/playwright-report" }],
  ],
  use: {
    baseURL: process.env["E2E_BASE_URL"] ?? "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // All requests should go through the SPA (which talks to BFF only)
    extraHTTPHeaders: {
      Accept: "application/json",
    },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Start the Vite dev server automatically if not already running
  webServer: {
    command: "npm run dev",
    port: 5173,
    reuseExistingServer: !process.env["CI"],
    timeout: 30_000,
  },
});
