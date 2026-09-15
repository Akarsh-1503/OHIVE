# LeadForge — frontend

Business cards in. Pipeline-ready leads out.

Next.js 15 (App Router) UI for the bulk business-card extraction pipeline. It consumes the
frozen contract in `_contracts/assignment1-api.md`: upload a batch, watch extraction stream
in over SSE, review and correct leads against the original image, export to Excel.

## Routes

| Route | Rendering | What it does |
|---|---|---|
| `/` | Server component + client dropzone | Hero, live `GET /health` model pill with cold-start warm-up, drag/drop/paste ingest, sample corpus loader, client-side validation |
| `/b/[batchId]` | Server shell + client view | Pipeline rail, live card grid with the scanline state, review drawer, table view, batch stats, export, retry |
| `/mock/v1/*` | Route handler | In-process fake backend, only mounted when `NEXT_PUBLIC_MOCK=1` |

## Running it

```bash
npm install

# Against the bundled mock — no backend required. Real SSE, real .xlsx download.
NEXT_PUBLIC_MOCK=1 npm run dev

# Against the real backend (same origin behind the reverse proxy)
NEXT_PUBLIC_API_BASE=/api/v1 npm run dev
```

| Env var | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `/api/v1` | Base URL of the LeadForge API |
| `NEXT_PUBLIC_MOCK` | `0` | `1` routes every call to the in-process mock at `/mock/v1` |

Both are inlined at build time, so Docker builds take them as `--build-arg`.

## Mock mode

`src/lib/mock/` is a small simulation of the backend: it holds uploads in memory, runs a
concurrency-3 worker pool with staggered per-card timings, emits the exact SSE events from
the contract, and serves a genuinely valid styled `.xlsx` (a ~150-line OOXML/ZIP writer,
so no spreadsheet dependency leaks into the frontend).

The ten cards in `public/samples/` are rendered by `scripts/gen-samples.mjs`, and the mock's
extractor returns the same people that are printed on them — so the side-by-side review
screen shows fields that actually match the pixels. The corpus deliberately contains one
blurry card (lands in `needs_review`), one glare-damaged card (fails, then succeeds on
retry) and one duplicate.

The real deployment pays ~200 s to wake a scale-to-zero GPU (measured 203.8 s on
2026-09-15); the mock scales that to 6 s so the cold-start UI is demoable without a
three-minute wait.

## Tests

```bash
# Builds, starts, and drives the app in mock mode
npm run e2e

# Or against an already-running instance
E2E_BASE_URL=http://localhost:3000 npx playwright test
```

`e2e/smoke.spec.ts` covers upload → stream → retry-failed → drawer edit (optimistic PATCH)
→ table view → xlsx download, plus a second test that aborts the SSE endpoint and asserts
the UI degrades to the polling fallback.

`node scripts/shots.mjs http://localhost:3000` regenerates `screenshots/`.

## Architecture notes

- **One SSE hook.** `src/lib/use-event-source.ts` is the only place `EventSource` is
  touched. Exponential backoff with jitter, and after 3 consecutive failures it stops
  reconnecting and polls `GET /batches/{id}` instead.
- **Keyed store, not context-wide state.** `src/lib/store.ts` keeps leads in a `Map` with
  per-card listener sets. Each tile subscribes to its own card id through
  `useSyncExternalStore`, so a 25-card batch streaming updates never re-renders the grid.
  List order and aggregate stats are separate subscriptions.
- **Continuous values are motion values.** Progress, elapsed time and every metric are
  driven by `useSpring` / `useAnimationFrame` writing into motion values. The progress bar
  and the elapsed counter do not re-render React at all.
- **Server components by default.** Only ingest, the batch view and the primitives that
  need events are `'use client'`.
- **Dark mode only.** A deliberate product decision: the product is used in a scanning /
  review context where a dark surface keeps attention on the card images.

## Accessibility

`<MotionConfig reducedMotion="user">` at the root, plus CSS fallbacks: under
`prefers-reduced-motion` the aurora stops drifting and the scanline collapses to a static
wash — state is still legible, nothing moves. The drawer is a real dialog with a focus
trap, `Esc` / `←` / `→` / `⌘↵` shortcuts and focus restoration. The progress region is
`aria-live`. Body text uses `ink` / `ink-muted` on `void` / `surface`, both ≥ 7:1.

## Docker

```bash
docker build -t leadforge-frontend .
docker run --rm -p 3000:3000 leadforge-frontend

# mock-mode image, useful for demoing without a backend
docker build --build-arg NEXT_PUBLIC_MOCK=1 -t leadforge-frontend:mock .
```

Multi-stage, `output: 'standalone'`, runs as the non-root `nextjs` user, exposes 3000.

## Known gaps

- `GET /cards/{id}/image?thumb=1` is served by the mock at full size — the real backend does
  the 480 px webp resize.
  (see the report/contract note).
