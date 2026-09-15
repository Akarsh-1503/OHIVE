import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT ?? 3217);
// E2E_BASE_URL points the suite at an already-running deployment instead of a local dev
// server. The mock-only routes the capture run uses do not exist there, so it is for the
// smoke suite and for ad-hoc checks against the live URL.
const REMOTE = process.env.E2E_BASE_URL;
const BASE_URL = REMOTE ?? `http://127.0.0.1:${PORT}`;

/**
 * WebGL in headless Chromium needs an explicit software rasteriser, otherwise the canvas is
 * a black rectangle and every viewer assertion passes for the wrong reason.
 *
 * SwiftShader renders correctly but at software speed, so a frame rate measured under it says
 * nothing about a real client. `E2E_GPU=1` runs headed on the host's actual GPU instead; that
 * is the mode `make bench` uses, and the test records which renderer produced the number.
 */
const USE_GPU = process.env.E2E_GPU === '1';
const GL_ARGS = USE_GPU
  ? []
  : ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--enable-webgl'];

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    headless: !USE_GPU,
    launchOptions: { args: GL_ARGS },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    {
      // Chromium with touch emulation rather than a WebKit iPhone profile: the swiftshader
      // flags above are Chromium-only, and WebKit headless has no software WebGL at all, so
      // an iPhone profile would screenshot an empty canvas. Scale factor stays 1 so the PNGs
      // are literally 390x844.
      name: 'mobile',
      use: {
        ...devices['Pixel 7'],
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
      },
    },
  ],
  // The standalone server off a production build, not `next dev`. Two reasons: the dev
  // server recompiles routes mid-suite and occasionally hands the client a truncated RSC
  // payload (a bogus "Unexpected end of JSON input" on whichever navigation lands on it),
  // and this is byte-for-byte what the container runs.
  webServer: REMOTE
    ? undefined
    : {
        command: 'npm run build && npm run start:standalone',
        url: BASE_URL,
        reuseExistingServer: false,
        timeout: 300_000,
        env: { NEXT_PUBLIC_MOCK: '1', PORT: String(PORT), HOSTNAME: '127.0.0.1' },
      },
});
