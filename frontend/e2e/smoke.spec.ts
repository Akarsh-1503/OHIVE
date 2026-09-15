import { expect, test } from '@playwright/test';

/**
 * End-to-end smoke against the mock backend: start a sample job, watch the stream, then
 * prove the 3D viewer actually drew something and its controls work.
 *
 * A blank canvas is the failure mode that matters here, so the check is on rendered pixels
 * rather than on the element existing. PNG is losslessly compressed: a real point cloud does
 * not compress anywhere near as well as a flat black rectangle, which makes byte size a
 * dependency-free proxy for "something was rendered".
 */
test.describe('Driftless', () => {
  test.beforeEach(({ browserName }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop layout only');
    void browserName;
  });

  test('reconstructs a sample clip and drives the 3D viewer', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Monocular video in');

    await page.getByRole('button', { name: 'Reconstruct' }).first().click();
    await expect(page).toHaveURL(/\/j\/mk_/);

    // Live telemetry while the mocked stream runs.
    await expect(page.getByText('Frames reconstructed', { exact: true })).toBeVisible();
    await expect(page.getByText('Reconstructing —')).toBeVisible();

    // Loop closure fires mid-run and must be announced.
    await expect(page.getByText('Loop closed').first()).toBeVisible({ timeout: 30_000 });

    // Completion, then the viewer.
    await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 40_000 });
    const canvas = page.locator('canvas');
    await expect(canvas).toBeVisible();

    // Give the fly-in a moment to settle before reading pixels.
    await page.waitForTimeout(2500);
    const withCloud = await canvas.screenshot();
    expect(withCloud.byteLength).toBeGreaterThan(25_000);

    // Hiding the cloud must visibly change the frame — proof the points are really drawn.
    await page.getByRole('switch', { name: 'Point cloud' }).click();
    await page.waitForTimeout(600);
    const withoutCloud = await canvas.screenshot();
    expect(withoutCloud.byteLength).toBeLessThan(withCloud.byteLength);
    await page.getByRole('switch', { name: 'Point cloud' }).click();

    // Colour modes.
    await page.getByRole('radio', { name: 'Obs' }).click();
    await expect(page.getByRole('radio', { name: 'Obs' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByText('Keyframes that observed each point', { exact: false })).toBeVisible();
    await page.getByRole('radio', { name: 'Height' }).click();
    await page.getByRole('radio', { name: 'RGB' }).click();

    // Scrubber moves the playhead.
    const scrubber = page.getByRole('slider', { name: 'Scrub through the captured trajectory' });
    await scrubber.fill('40');
    await expect(page.locator('text=/^1\\.3\\ds$/')).toBeVisible();

    // Playback along the trajectory, in follow-cam.
    await page.getByRole('button', { name: 'Toggle follow camera' }).click();
    await page.getByRole('button', { name: /Play replay along the trajectory/i }).click();
    await page.waitForTimeout(1500);
    const followFrame = await canvas.screenshot();
    expect(followFrame.byteLength).toBeGreaterThan(25_000);
    await page.getByRole('button', { name: /Pause replay/i }).click();

    // Metrics panel carries the benchmark headline.
    await expect(page.getByText('Realtime factor')).toBeVisible();
    await expect(page.getByText('wall_ms').first()).toBeVisible();
    await expect(page.getByText('excluded from wall_ms', { exact: true })).toBeVisible();
    await expect(page.getByText('scale_drift_ratio').first()).toBeVisible();
    await expect(page.getByText('reduction_pct').first()).toBeVisible();

    // Accessible text alternative to the canvas.
    await page.getByText('Trajectory and metrics as a table').click();
    await expect(page.getByRole('table').first()).toBeVisible();
    await expect(page.getByText('post_optimization_loop_error_m')).toBeVisible();
  });

  test('surfaces an unknown job id honestly', async ({ page }) => {
    await page.goto('/j/not-a-real-job');
    await expect(page.getByText('No job with that id')).toBeVisible();
  });

  test('keeps the view presets clickable next to the docked metrics panel', async ({ page }) => {
    await page.goto('/j/mk_ok_presets');
    await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 40_000 });
    await page.waitForTimeout(2500);
    const canvas = page.locator('canvas');
    const before = await canvas.screenshot();
    // Regression: the panel used to be docked over this cluster and ate the click.
    await page.getByRole('button', { name: 'top', exact: true }).click({ timeout: 5_000 });
    await page.waitForTimeout(1400);
    expect(Buffer.compare(before, await canvas.screenshot())).not.toBe(0);
  });
});

test.describe('Driftless on a phone', () => {
  test('puts both panels in bottom sheets and keeps touch orbit', async ({ page, context }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'phone layout only');
    await page.goto('/j/mk_ok_touch');
    await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 40_000 });

    // Metrics live in a sheet, not a docked column, and dismiss without leaving the page.
    await page.getByRole('button', { name: /Metrics/ }).click();
    const sheet = page.getByRole('dialog', { name: 'Metrics' });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText('Realtime factor')).toBeVisible();
    await page.getByRole('button', { name: 'Close panel' }).click();
    await expect(sheet).toBeHidden();

    await page.getByRole('button', { name: 'View' }).click();
    await expect(page.getByRole('dialog', { name: 'Viewer controls' })).toBeVisible();
    await page.getByRole('button', { name: 'Close controls' }).click();

    const canvas = page.locator('canvas');
    await page.waitForTimeout(2500);
    const before = await canvas.screenshot();

    // Real touch events, not a synthesised mouse drag: OrbitControls' touch path is the
    // thing being claimed, and a mouse drag would pass even if touch were broken.
    const box = (await canvas.boundingBox())!;
    const cdp = await context.newCDPSession(page);
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    for (let i = 1; i <= 8; i++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x - i * 12, y: y - i * 5 }] });
      await page.waitForTimeout(40);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await page.waitForTimeout(900);

    expect(Buffer.compare(before, await canvas.screenshot())).not.toBe(0);
  });
});
