import { expect, test } from '@playwright/test';

/**
 * Capture run, not an assertion suite: drives a full mock reconstruction and writes the
 * screenshots referenced in the README. Run with `npx playwright test screenshots`.
 */
const DIR = 'screenshots';

test.describe('capture', () => {
  test('walks a full reconstruction', async ({ page }, testInfo) => {
    const size = testInfo.project.use.viewport;
    const tag = `${size?.width ?? 0}x${size?.height ?? 0}`;
    const mobile = (size?.width ?? 0) < 700;

    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Reconstruct' }).first()).toBeVisible();
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${DIR}/01-landing-${tag}.png` });
    await page.screenshot({ path: `${DIR}/01-landing-${tag}-full.png`, fullPage: true });

    await page.getByRole('button', { name: 'Reconstruct' }).first().click();
    await expect(page).toHaveURL(/\/j\/mk_/);

    // Mid-run: catch the telemetry panel while frames are still streaming.
    if (mobile) {
      await page.getByRole('button', { name: /Telemetry/ }).click();
    }
    await page.waitForTimeout(4200);
    await page.screenshot({ path: `${DIR}/02-processing-${tag}.png` });
    if (mobile) await page.getByRole('button', { name: 'Close panel' }).click();

    await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 40_000 });
    // Let the fly-in land.
    await page.waitForTimeout(3400);
    await page.screenshot({ path: `${DIR}/03-viewer-orbit-${tag}.png` });

    // Height colouring reads well in a still, so capture that variant too.
    if (!mobile) {
      await page.getByRole('radio', { name: 'Height' }).click();
      await page.waitForTimeout(700);
      await page.screenshot({ path: `${DIR}/04-viewer-height-${tag}.png` });
      await page.getByRole('radio', { name: 'Obs' }).click();
      await page.waitForTimeout(700);
      await page.screenshot({ path: `${DIR}/05-viewer-observations-${tag}.png` });
      await page.getByRole('radio', { name: 'RGB' }).click();
    }

    // Follow cam mid-replay.
    const scrubber = page.getByRole('slider', { name: 'Scrub through the captured trajectory' });
    await scrubber.fill('120');
    await page.getByRole('button', { name: 'Toggle follow camera' }).click();
    await page.getByRole('button', { name: /Play replay/i }).click();
    await page.waitForTimeout(2200);
    await page.screenshot({ path: `${DIR}/06-followcam-${tag}.png` });
    await page.getByRole('button', { name: /Pause replay/i }).click();
    await page.getByRole('button', { name: 'Toggle follow camera' }).click();
    await page.waitForTimeout(900);

    // Metrics.
    if (mobile) {
      await page.getByRole('button', { name: /Metrics/ }).click();
      await page.waitForTimeout(700);
      await page.screenshot({ path: `${DIR}/07-metrics-${tag}.png` });
      await page.getByRole('button', { name: 'Close panel' }).click();
      await page.waitForTimeout(500);

      // The layer/colour controls are the other bottom sheet; both are the mobile story.
      await page.getByRole('button', { name: 'View' }).click();
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${DIR}/12-viewer-controls-${tag}.png` });
    } else {
      await page.screenshot({ path: `${DIR}/07-metrics-${tag}.png` });
      const panel = page.getByRole('dialog').or(page.locator('div').filter({ hasText: /^Metrics$/ })).first();
      void panel;
      await page.locator('text=Realtime factor').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${DIR}/07-metrics-${tag}-panel.png`, clip: { x: 1080, y: 120, width: 350, height: 760 } });
    }
  });

  test('top view and queued state', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop only');
    await page.goto('/j/mk_ok_topview');
    await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 40_000 });
    await page.waitForTimeout(3200);
    await page.getByRole('button', { name: 'top', exact: true }).click();
    await page.waitForTimeout(1400);
    await page.screenshot({ path: `${DIR}/08-viewer-top-1440x900.png` });

    await page.goto('/j/mk_queue_demo');
    await expect(page.getByText('Queued')).toBeVisible();
    await page.screenshot({ path: `${DIR}/09-queued-1440x900.png` });

    await page.goto('/j/mk_fail_demo');
    await expect(page.getByText('Reconstruction failed')).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: `${DIR}/10-failed-1440x900.png` });
  });

  test('measures frame rate with a 50 000 point cloud', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop only');
    await page.goto('/j/mk_ok_bench?points=50000');
    await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 45_000 });
    await page.waitForTimeout(3000);
    await expect(page.getByText('50,000 pts').first()).toBeVisible();

    // Continuous rendering: playback keeps the demand loop saturated.
    await page.getByRole('button', { name: /Play replay/i }).click();
    await page.waitForTimeout(1500);
    const samples: number[] = [];
    for (let i = 0; i < 12; i++) {
      await page.waitForTimeout(600);
      const text = await page.locator('span').filter({ hasText: /^\d+$/ }).first().textContent();
      const n = Number(text);
      if (Number.isFinite(n) && n > 0) samples.push(n);
    }
    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)] ?? 0;

    // A frame rate is only meaningful next to the renderer that produced it: headless
    // Chromium reports SwiftShader, a headed run (E2E_GPU=1) reports the real adapter.
    const renderer = await page.evaluate(() => {
      const gl = document.createElement('canvas').getContext('webgl2');
      const ext = gl?.getExtension('WEBGL_debug_renderer_info');
      return ext ? String(gl?.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : 'unknown';
    });
    testInfo.attach('fps', { body: JSON.stringify({ renderer, samples, median }), contentType: 'application/json' });
    console.log(`[fps] 50 000 points — median ${median} fps · samples ${samples.join(', ')} · renderer ${renderer}`);
    await page.screenshot({ path: `${DIR}/11-fps-50k-1440x900.png` });
    expect(median).toBeGreaterThan(0);
  });
});
