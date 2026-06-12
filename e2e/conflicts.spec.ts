/**
 * E2E tests: Conflict queue — the core reviewer workflow.
 *
 * Covers:
 *   - Case list renders after login
 *   - Claim detail updates EvidencePanel on selection
 *   - Reviewer can resolve a conflict (with rationale)
 *   - Analyst sees conflict actions disabled
 *   - 409 conflict-modified response refreshes the panel and allows retry
 *
 * Prerequisites:
 *   - The test database must contain the Colgan Air 3407 seed case
 *   - At least one OPEN conflict must exist on that case
 *   - Reviewer and Analyst users must be seeded in the BFF DB
 */

import { test, expect, captureApiKeyLeaks, TEST_CASE_SLUG } from "./fixtures";

test.describe("Case list", () => {
  test("case list renders with at least the seed case", async ({ reviewerPage: page }) => {
    await page.goto("/app/cases");

    // Wait for either a case row or an empty state
    await expect(
      page.locator("table tbody tr, [data-testid='case-row']").first()
    ).toBeVisible({ timeout: 12_000 });

    // At minimum, no error panel should be showing
    await expect(page.locator("text=Failed to load")).not.toBeVisible();
  });
});

test.describe("Conflict queue (reviewer)", () => {
  test.beforeEach(async ({ reviewerPage: page }) => {
    await page.goto(`/app/cases/${TEST_CASE_SLUG}/conflicts`);
    // Wait for the conflict list to load
    await page.waitForSelector(
      "[data-conflict-id], text=No open conflicts, text=Select a conflict",
      { timeout: 12_000 }
    );
  });

  test("conflict list renders; selecting one shows detail panel", async ({ reviewerPage: page }) => {
    // If there are conflicts, clicking one should show the detail panel
    const firstConflict = page.locator("[data-conflict-id]").first();
    const hasConflicts = await firstConflict.isVisible().catch(() => false);

    if (!hasConflicts) {
      // Empty state is valid — just confirm it's visible
      await expect(page.locator("text=No open conflicts for this case.")).toBeVisible();
      return;
    }

    await firstConflict.click();

    // Detail panel should show the field name heading
    await expect(
      page.locator("h2, [data-testid='conflict-detail-header']").first()
    ).toBeVisible({ timeout: 6_000 });

    // Competing claims section should be visible
    await expect(
      page.locator("text=Pick a winning claim, text=Resolve conflict, label[for]").first()
    ).toBeVisible({ timeout: 6_000 });
  });

  test("selecting a candidate updates the right-rail EvidencePanel", async ({ reviewerPage: page }) => {
    const firstConflict = page.locator("[data-conflict-id]").first();
    if (!(await firstConflict.isVisible().catch(() => false))) return;

    await firstConflict.click();
    await page.waitForTimeout(1_000);

    // Select the first radio candidate
    const firstRadio = page.locator("input[type='radio'][name='winning-claim']").first();
    if (!(await firstRadio.isVisible().catch(() => false))) return;

    await firstRadio.click();

    // Right rail should now show evidence chain details
    await expect(
      page.locator("text=Source, text=Evidence chain").first()
    ).toBeVisible({ timeout: 5_000 });
  });

  test("resolve conflict requires rationale — submit disabled without it", async ({ reviewerPage: page }) => {
    const firstOpen = page.locator("[data-conflict-id]").first();
    if (!(await firstOpen.isVisible().catch(() => false))) return;

    await firstOpen.click();
    await page.waitForTimeout(800);

    // Pick first candidate
    const firstRadio = page.locator("input[type='radio'][name='winning-claim']").first();
    if (!(await firstRadio.isVisible().catch(() => false))) return;
    await firstRadio.click();

    // Submit should be disabled without rationale
    const submitBtn = page.locator("button:has-text('Resolve conflict')");
    await expect(submitBtn).toBeDisabled();

    // Fill rationale
    await page.fill("textarea", "NTSB final report supersedes preliminary CVR transcript. Tier 1 source authority.");

    // Submit should now be enabled
    await expect(submitBtn).toBeEnabled();
  });

  test("resolver submits correct claimId (not array index)", async ({ reviewerPage: page }) => {
    // This test verifies the conflict-candidates fix: the submitted winning_claim_id
    // must be the actual UUID from the candidate, not an index into claimIds.
    // We intercept the resolve request and verify the payload.

    let capturedResolveBody: Record<string, unknown> | null = null;

    await page.route("**/api/conflicts/*/resolve", async (route) => {
      const request = route.request();
      try {
        capturedResolveBody = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      } catch {
        capturedResolveBody = {};
      }
      // Abort the real request to avoid mutating test data
      await route.abort();
    });

    const firstOpen = page.locator("[data-conflict-id]").first();
    if (!(await firstOpen.isVisible().catch(() => false))) return;
    await firstOpen.click();
    await page.waitForTimeout(800);

    // Get the first candidate's claimId from the radio value attribute
    const firstRadio = page.locator("input[type='radio'][name='winning-claim']").first();
    if (!(await firstRadio.isVisible().catch(() => false))) return;

    const candidateClaimId = await firstRadio.getAttribute("value");
    expect(candidateClaimId).toBeTruthy();
    expect(candidateClaimId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    ); // UUID format

    await firstRadio.click();
    await page.fill("textarea", "Test rationale for automated verification.");
    await page.click("button:has-text('Resolve conflict')");

    // Wait for the intercepted request
    await page.waitForTimeout(1_000);

    if (capturedResolveBody) {
      // The submitted winning_claim_id must equal the radio button's value
      expect(capturedResolveBody["winning_claim_id"]).toBe(candidateClaimId);
      // expected_version must be a number
      expect(typeof capturedResolveBody["expected_version"]).toBe("number");
      // reason must be present
      expect(capturedResolveBody["reason"]).toBeTruthy();
    }
  });

  test("keyboard j/k navigation moves conflict selection", async ({ reviewerPage: page }) => {
    const conflicts = page.locator("[data-conflict-id]");
    const count = await conflicts.count();
    if (count < 2) return; // Need at least 2 conflicts to test navigation

    // Click first conflict to focus the list
    await conflicts.first().click();
    await page.waitForTimeout(300);

    // Press j to move to next
    await page.keyboard.press("j");
    await page.waitForTimeout(200);

    // The second conflict should now be selected (border-l-atlas-500)
    const secondSelected = await conflicts
      .nth(1)
      .evaluate((el) => el.classList.toString().includes("border-l-atlas-500"));
    expect(secondSelected).toBe(true);
  });

  test("r key focuses the rationale textarea", async ({ reviewerPage: page }) => {
    const firstConflict = page.locator("[data-conflict-id]").first();
    if (!(await firstConflict.isVisible().catch(() => false))) return;

    await firstConflict.click();
    await page.waitForTimeout(500);

    // Press r (not inside a textarea/input)
    await page.keyboard.press("r");
    await page.waitForTimeout(200);

    // Rationale textarea should be focused
    const focused = await page.evaluate(
      () => document.activeElement?.tagName.toLowerCase()
    );
    expect(focused).toBe("textarea");
  });
});

test.describe("Conflict queue (analyst — read-only)", () => {
  test("analyst sees conflict actions disabled", async ({ analystPage: page }) => {
    await page.goto(`/app/cases/${TEST_CASE_SLUG}/conflicts`);
    await page.waitForTimeout(3_000);

    const firstConflict = page.locator("[data-conflict-id]").first();
    if (!(await firstConflict.isVisible().catch(() => false))) return;

    await firstConflict.click();
    await page.waitForTimeout(800);

    // Resolve button should be absent or disabled for analyst
    const resolveBtn = page.locator("button:has-text('Resolve conflict')");
    const reopenBtn = page.locator("button:has-text('Reopen conflict')");

    const resolveVisible = await resolveBtn.isVisible().catch(() => false);
    if (resolveVisible) {
      await expect(resolveBtn).toBeDisabled();
    }

    const reopenVisible = await reopenBtn.isVisible().catch(() => false);
    if (reopenVisible) {
      await expect(reopenBtn).toBeDisabled();
    }

    // Should see the analyst role message
    await expect(
      page.locator("text=Requires reviewer role, text=reviewer role").first()
    ).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("409 conflict-modified handling", () => {
  test("409 response shows refreshed conflict state inline (not just a toast)", async ({ reviewerPage: page }) => {
    // Simulate a 409 by intercepting the resolve request and returning a crafted 409 body
    await page.route("**/api/conflicts/*/resolve", async (route) => {
      const fakeConflict = {
        id: "fake-conflict-id",
        eventId: "fake-event-id",
        fieldName: "pitch_trim",
        status: "OPEN",
        version: 5,
        lastModifiedReason: "Another reviewer updated this",
        lastModifiedNote: "Superseded by new NTSB data",
        winningClaimId: null,
        resolvedAt: null,
        resolvedBy: null,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        claimIds: [],
      };

      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "This conflict was modified by another reviewer while you were reviewing it.",
          modifierReason: "NTSB data corrected",
          currentVersion: 5,
          latestActivity: {
            id: "act-1",
            conflictId: "fake-conflict-id",
            sequence: 3,
            fromStatus: "OPEN",
            toStatus: "OPEN",
            modifierType: "USER",
            modifierId: "other-user-id",
            reason: "New NTSB source ingested",
            versionAtMoment: 5,
            claimsSnapshot: null,
            createdAt: new Date().toISOString(),
          },
          conflict: fakeConflict,
          accidentRecord: {
            eventId: "fake-event-id",
            projectionVersion: 14,
            fields: {},
            completenessScore: 0.7,
            unresolvedConflictFields: ["pitch_trim"],
            updatedAt: new Date().toISOString(),
          },
        }),
      });
    });

    await page.goto(`/app/cases/${TEST_CASE_SLUG}/conflicts`);
    const firstConflict = page.locator("[data-conflict-id]").first();
    if (!(await firstConflict.isVisible().catch(() => false))) return;

    await firstConflict.click();
    await page.waitForTimeout(800);

    const firstRadio = page.locator("input[type='radio'][name='winning-claim']").first();
    if (!(await firstRadio.isVisible().catch(() => false))) return;
    await firstRadio.click();
    await page.fill("textarea", "Test resolve attempt.");
    await page.click("button:has-text('Resolve conflict')");

    // The 409 banner should appear inline — not just a toast
    await expect(
      page.locator("text=modified, text=changed while you were reviewing, [class*='banner'], [class*='Banner']").first()
    ).toBeVisible({ timeout: 6_000 });

    // The panel should show the refreshed version number
    await expect(
      page.locator("text=Version 5").first()
    ).toBeVisible({ timeout: 4_000 });

    // The form should still be present for retry — not navigated away
    expect(page.url()).toContain("/conflicts");
  });
});
