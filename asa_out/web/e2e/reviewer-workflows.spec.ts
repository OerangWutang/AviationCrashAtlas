import { test, expect } from '@playwright/test';

const reviewerKey = process.env.ATLAS_REVIEWER_API_KEY ?? 'atlas_test_reviewer_key';

test('reviewer/operator console exposes core workflow pages', async ({ page }) => {
  await page.goto('/operator');
  await expect(page.getByRole('heading', { name: /operator console/i })).toBeVisible();
  await page.getByLabel(/reviewer api key/i).fill(reviewerKey);
  await page.getByRole('button', { name: /save/i }).click();
  await expect(page.getByText(/reviewer key active/i)).toBeVisible();

  await page.getByRole('link', { name: /duplicate candidates/i }).click();
  await expect(page.getByRole('heading', { name: /duplicate review/i })).toBeVisible();

  await page.goto('/data-quality');
  await expect(page.getByRole('heading', { name: /data-quality issues/i })).toBeVisible();

  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: /admin console/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /sources/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /audit/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /keys/i })).toBeVisible();
});
