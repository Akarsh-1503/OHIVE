# Driftless — frontend (`slam-web`)

Monocular video in. Metric-consistent sparse map out.

Next.js 15 App Router UI for the Driftless API. Upload a clip (or run a built-in sample),
watch the reconstruction stream in over SSE, then explore the resulting point cloud and
camera trajectory in a WebGL viewer.

Live: **https://slam-34-47-153-95.nip.io**

## Quickstart

```bash
make dev          # http://localhost:3000, in-browser mock backend, no API needed
make test         # Playwright smoke suite, desktop + phone
make screenshots  # regenerate screenshots/ at 1440x900 and 390x844
make bench        # viewer frame rate at 50 000 points, on the host GPU
make lint         # eslint + tsc --noEmit
make build        # docker build -t slam-web .
```

As a container:

```bash
docker build -t slam-web .
docker run -p 3000:3000 slam-web
```

It serves on `:3000` as an unprivileged user (uid 10001) from Next's `standalone` output, and
expects a reverse proxy to hand it `/api/*` on the same origin. The deployed stack is
`infra/docker-compose.yml` at the repo root, fronted by Caddy.

## Environment

| variable | default | meaning |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `/api/v1` | Base URL of the Driftless API. Relative by default, because the reverse proxy serves API and UI from one origin. |
| `NEXT_PUBLIC_MOCK` | `0` | `1` runs the whole UI — including the 3D viewer, the SSE stream and the exports — against an in-browser mock. |

Both are `NEXT_PUBLIC_*`, so they are inlined at **build** time and are `--build-arg`s on the
image, not runtime environment.

## Structure

| path | responsibility |
|---|---|
| `src/app` | routes: `/` (hero + ingest), `/j/[jobId]` (live run + results) |
| `src/lib/api.ts` | the only place that talks to the API; maps the contract's error codes to human copy |
| `src/lib/recon.ts` | `Reconstruction` JSON → GPU-ready typed arrays, OpenCV → three.js basis change |
| `src/lib/use-event-source.ts` | one SSE hook: backoff, then polling fallback after three failures |
| `src/lib/mock.ts` | the mock backend, shaped exactly like the contract |
| `src/components/viewer` | `<Canvas>`, point cloud, trajectory, camera rig, overlay |
| `src/components/*` | ingest, telemetry, metrics, exports, pipeline rail, primitives |

Two rules hold throughout: the render loop never re-renders React (playback state lives in a
plain mutable object and is written straight into DOM nodes), and every number in the UI is
tabular monospace.

## What the viewer draws

One `THREE.Points` for the whole cloud, one `LineSegments` for every keyframe frustum, one
line for the trajectory, one per loop-closure arc. `frameloop="demand"`, so a still scene
costs nothing. Colour modes are precomputed attribute buffers (source RGB, height ramp,
viridis by observation count), and the density slider subsamples with `setDrawRange` over a
seeded shuffle, which keeps a thinned cloud spatially uniform instead of lopping off a corner.

## Measured performance

Viewer frame rate with a 50 000-point cloud, replaying the trajectory (the worst case: the
demand loop is saturated and the playhead camera moves every frame).

| renderer | 50 000 points | 60 000 points (the contract's web cap) |
|---|---|---|
| Apple M4, ANGLE Metal, vsync on | **85 fps**, pinned to the display refresh | 85 fps |
| Apple M4, ANGLE Metal, vsync off | **607 fps** median — 1.65 ms/frame | 589 fps — 1.70 ms/frame |
| SwiftShader, headless CI | 8 fps | — |

`make bench` reports the first row and records the renderer string alongside the number, so a
frame rate is never quoted without saying what drew it. The vsync-on row is the honest
user-facing figure — the viewer never misses a frame — and the vsync-off row is the headroom
behind it. The SwiftShader row is what the headless harness sees; it is a correctness check
(a blank canvas would fail the smoke test), not a performance claim.

Bundle: 177 kB first load on `/j/[jobId]`, of which three.js is code-split and fetched while
the job is still reconstructing rather than blocking first paint.

## Tests

```bash
make test
```

`e2e/smoke.spec.ts` drives a full mock reconstruction end to end: it waits for the loop
closure to be announced, proves the canvas actually rendered geometry (a blank canvas is the
failure mode that matters, so the assertion is on rendered pixels), toggles the cloud off and
checks the frame changed, exercises the colour modes, scrubber and follow camera, and reads
the benchmark numbers back out of the metrics panel. A second project runs the same app at
390×844 and drives the viewer with real touch events through CDP, because a synthesised mouse
drag would pass even if touch orbit were broken.

Headless Chromium has no GPU, so the harness forces ANGLE + SwiftShader. Without those flags
the canvas is a black rectangle and every viewer assertion passes for the wrong reason.

The harness builds and serves the standalone output rather than running `next dev`, so the
suite exercises the same server the container does. `E2E_BASE_URL=https://…` points it at a
running deployment instead; `E2E_GPU=1` runs headed on the host GPU.

## Screenshots

`screenshots/` holds the captures referenced above, at 1440×900 and 390×844, regenerated by
`make screenshots`.

## Limitations

- **Dark mode only.** A deliberate product decision: this is a tool for staring at a 3D scene.
- **No server-side data fetching.** The API base is relative and same-origin behind the proxy,
  so there is no server origin for a server component to fetch from; the job page is a client
  component driven by SSE.
- **Run history is per tab.** The loop-closure A/B comparison remembers which runs had
  correction enabled in `sessionStorage`, because the contract's `Job` deliberately does not
  echo back the options a run was started with.
- **The point cloud is capped at 60 000 points** by the API's web payload budget. The full
  cloud is always available through the PLY export.
- **Distances are up to scale.** The UI says so on the landing page, in the metrics panel and
  under the viewer; nothing is labelled in metres.
