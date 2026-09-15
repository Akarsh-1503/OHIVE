import { expect, test } from '@playwright/test';

/**
 * End-to-end path through the product: load the bundled corpus, upload it, watch the SSE
 * stream finish, edit a field in the review drawer, switch to the table, export.
 * Runs against NEXT_PUBLIC_MOCK=1 so it needs no backend.
 */
test('extracts a batch, reviews a lead and exports it', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));

  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Business cards in.');

  await page.getByRole('button', { name: 'Load sample cards' }).click();
  await expect(page.getByText('10/25 cards')).toBeVisible();

  await page.getByRole('button', { name: /Extract 10 cards/ }).click();
  await page.waitForURL(/\/b\/[a-f0-9]+/);

  // The pipeline rail and the live stream come up before any card finishes.
  await expect(page.getByText('Reading card…').first()).toBeVisible();

  // The mock finishes the batch (including a cold start) well inside this window.
  await expect(page.getByText('Stream closed')).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText('card_01_northwind.jpg')).toBeVisible();

  // One sample card is designed to fail on glare; retrying re-queues it and it settles
  // into needs_review on the second pass.
  const retry = page.getByRole('button', { name: /Retry 1 failed/ });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(retry).toBeHidden({ timeout: 60_000 });

  await page.locator('button[aria-label^="Open"]').first().click();
  const drawer = page.getByRole('dialog', { name: /Review/ });
  await expect(drawer).toBeVisible();

  const jobTitle = page.locator('#field-job_title');
  await jobTitle.fill('Head of Partnerships');
  await page.keyboard.press('Meta+Enter');
  await expect(page.getByText('Lead updated')).toBeVisible();

  // Arrow keys move between cards, Escape closes.
  await page.keyboard.press('ArrowRight');
  await expect(drawer).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();

  await page.getByRole('button', { name: 'Table view' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Head of Partnerships' })).toBeVisible();

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: /^Export/ }).click();
  await page.getByRole('button', { name: 'Download .xlsx' }).click();
  expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);
  await expect(page.getByText('Export ready')).toBeVisible();

  expect(consoleErrors).toEqual([]);
});

test('degrades to polling when the event stream cannot connect', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Load sample cards' }).click();
  await expect(page.getByText('10/25 cards')).toBeVisible();

  // Kill the SSE endpoint only; the snapshot endpoint stays up so polling can take over.
  await page.route('**/events', (route) => route.abort());

  await page.getByRole('button', { name: /Extract 10 cards/ }).click();
  await page.waitForURL(/\/b\/[a-f0-9]+/);

  await expect(page.getByText('Polling fallback')).toBeVisible({ timeout: 30_000 });
  // Progress still lands even with no stream at all.
  await expect(page.getByText('card_01_northwind.jpg')).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole('button', { name: /Retry 1 failed/ })).toBeVisible({
    timeout: 90_000,
  });
});
