// Drives the app in mock mode and captures the screenshot set in screenshots/.
// Usage: node scripts/shots.mjs [baseUrl]
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://localhost:3100';
const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'screenshots');
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();

async function capture(viewport, suffix) {
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 2,
    isMobile: viewport.width < 500,
    hasTouch: viewport.width < 500,
  });
  const page = await context.newPage();
  const problems = [];
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(message.text());
  });
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));

  const shot = async (name) => {
    await page.screenshot({ path: join(OUT, `${name}-${suffix}.jpg`), type: 'jpeg', quality: 90 });
    process.stdout.write(`  ${name}-${suffix}.jpg\n`);
  };

  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await shot('01-landing');

  await page.getByRole('button', { name: 'Load sample cards' }).click();
  await page.waitForSelector('li img[alt="card_10_glare.jpg"]');
  await page.waitForTimeout(900);
  await page.mouse.wheel(0, viewport.width < 500 ? 780 : 430);
  await page.waitForTimeout(700);
  await shot('02-landing-loaded');

  await page.getByRole('button', { name: /Extract 10 cards/ }).click();
  await page.waitForURL(/\/b\//, { timeout: 20000 });
  await page.waitForSelector('text=Reading card', { timeout: 20000 });
  await page.waitForTimeout(2600);
  await shot('03-processing');

  await page.waitForSelector('text=Stream closed', { timeout: 90000 });
  await page.waitForTimeout(1400);
  await shot('04-completed');

  // Open a card with a mix of strong and weak field reads so the low-confidence flagging
  // is actually visible in the shot.
  const flagged = page.locator('button[aria-label*="Farid Haddad"]');
  const target =
    (await flagged.count()) > 0 ? flagged.first() : page.locator('button[aria-label^="Open"]').first();
  await target.click();
  await page.waitForSelector('[role="dialog"][aria-modal="true"]');
  await page.waitForTimeout(1100);
  await shot('05-review-drawer');

  // Edit a field, optimistic update + PATCH.
  const jobTitle = page.locator('#field-job_title');
  await jobTitle.click();
  await jobTitle.fill('Regional Sales Director');
  await page.keyboard.press('Meta+Enter');
  await page.waitForSelector('text=Lead updated', { timeout: 10000 });
  await page.waitForTimeout(700);
  await shot('06-review-edited');

  await page.keyboard.press('Escape');
  await page.waitForTimeout(700);

  await page.getByRole('button', { name: 'Table view' }).click();
  await page.waitForTimeout(1100);
  await shot('07-table');

  const download = page.waitForEvent('download', { timeout: 15000 });
  await page.getByRole('button', { name: /^Export/ }).click();
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: 'Download .xlsx' }).click();
  const file = await download;
  process.stdout.write(`  downloaded ${file.suggestedFilename()}\n`);
  await page.waitForSelector('text=Export ready', { timeout: 10000 });
  await page.waitForTimeout(600);
  await shot('08-export');

  await page.goto(`${BASE}/b/0000000000000000000000000000dead`, { waitUntil: 'networkidle' });
  await page.waitForSelector('text=No batch with that id');
  await page.waitForTimeout(500);
  await shot('10-unknown-batch');

  await context.close();
  return problems;
}

/** Verifies the app is readable and non-nauseating with prefers-reduced-motion: reduce. */
async function captureReducedMotion() {
  const context = await browser.newContext({
    viewport: DESKTOP,
    deviceScaleFactor: 2,
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Load sample cards' }).click();
  await page.waitForSelector('li img[alt="card_10_glare.jpg"]');
  await page.getByRole('button', { name: /Extract 10 cards/ }).click();
  await page.waitForURL(/\/b\//, { timeout: 20000 });
  await page.waitForSelector('text=Reading card', { timeout: 20000 });
  await page.waitForTimeout(2000);
  await page.screenshot({
    path: join(OUT, '09-reduced-motion-1440x900.jpg'),
    type: 'jpeg',
    quality: 90,
  });
  process.stdout.write('  09-reduced-motion-1440x900.jpg\n');
  await context.close();
}

process.stdout.write('desktop 1440x900\n');
const desktopProblems = await capture(DESKTOP, '1440x900');
process.stdout.write('mobile 390x844\n');
const mobileProblems = await capture(MOBILE, '390x844');
process.stdout.write('reduced motion\n');
await captureReducedMotion();

await browser.close();

const all = [...desktopProblems, ...mobileProblems];
if (all.length > 0) {
  process.stdout.write(`\nconsole errors (${all.length}):\n${[...new Set(all)].join('\n')}\n`);
} else {
  process.stdout.write('\nno console errors\n');
}
