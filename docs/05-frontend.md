# 05 — Frontend

Next.js 15 App Router, TypeScript `strict`, Tailwind CSS v4, Framer Motion 11,
`output: 'standalone'`. No component library: the ~8 primitives this product needs are
hand-rolled in `src/components/ui.tsx`, which is smaller than the config file a component
library would have needed.

Screenshots of every state — landing, processing, completed, review drawer, table, export,
reduced-motion, unknown-batch — at desktop and mobile widths are in
[`../frontend/screenshots/`](../frontend/screenshots/).

## Routes

| Route | Rendering | What it does |
|---|---|---|
| `/` | Server component + client dropzone | Hero, live `GET /health` model pill with cold-start warm-up, drag/drop/paste ingest, sample-corpus loader, client-side validation |
| `/b/[batchId]` | Server shell + client view | Pipeline rail, live card grid, review drawer, table view, batch stats, export, retry |
| `/mock/v1/*` | Route handler | An in-process fake backend, mounted only when `NEXT_PUBLIC_MOCK=1` |

Server components by default. `'use client'` appears only on ingest, the batch view, and the
primitives that genuinely need events. No `useEffect` fetches data a server component could
have fetched.

## The three things that make this fast

### 1. One SSE hook, and it is the only place `EventSource` is touched

`src/lib/use-event-source.ts` owns connection state (`connecting` → `live` → `reconnecting` →
`polling` → `closed`), exponential backoff with jitter capped at 15 s, and — after **three**
consecutive failures — a graceful degradation to polling `GET /batches/{id}` every 2.5 s.
Handlers are read through a ref, so re-rendering the consumer never re-opens the stream.

A frozen UI is a worse failure than a slow one. The connection state is rendered in the
header (*Live* / *Reconnecting* / *Polling* / *Stream closed*), so the user is told which mode
they are in rather than left guessing why numbers stopped moving. The Playwright smoke suite
aborts the SSE endpoint and asserts the polling fallback takes over.

### 2. A keyed store, not context-wide state

`src/lib/store.ts` keeps leads in a `Map` with **per-card listener sets**. Each tile
subscribes to its own card id through `useSyncExternalStore`; list order and aggregate stats
are separate subscriptions. A `card.completed` event for one card therefore re-renders exactly
one tile, not a grid of 25. Snapshots are cached objects, so `useSyncExternalStore` cannot
loop.

At 6-way concurrency the backend emits card events in bursts. Putting that into a single React
state object would re-render the whole tree several times a second for no visual benefit.

### 3. Continuous values live in motion values, not in React state

Progress, elapsed time and every metric are driven by `useSpring` / `useAnimationFrame`
writing into Framer motion values. **The progress bar and the elapsed counter do not re-render
React at all** — they mutate a style property directly. The `batch.progress` event writes a
target into a spring; the spring animates. Numbers never flash; they roll.

## Review, which is where the product actually earns its keep

The review drawer is the answer to "what do I do with a `needs_review` lead?". It puts the
original card image beside the extracted fields, with per-field confidence visible, so the
correction loop is: look at the image, fix the one wrong field, move on. It is a real dialog
with a focus trap, `Esc` / `←` / `→` / `⌘↵` shortcuts and focus restoration, because a
reviewer working through a queue of 25 should never have to reach for the mouse.

Edits are optimistic: the field updates immediately and the `PATCH` reconciles behind it, with
a rollback and a toast if the request fails. The backend pins an edited field to confidence
1.0 and re-scores the record, so a card can climb out of the queue as it is corrected — and
correctly *stay* in the queue when only some of it has been fixed.

## Design system

Shared with the sibling assignment so the two submissions read as one body of work: the same
tokens, motion rules and primitives, with only the accent ramp differing (LeadForge runs
ember → magenta, the other cyan → violet).

```css
--color-void:    oklch(0.145 0.012 265);   /* page */
--color-surface: oklch(0.185 0.014 265);   /* cards */
--color-raised:  oklch(0.225 0.016 265);   /* inputs, hovered rows */
--color-accent:  oklch(0.72 0.19 55);      /* ember */
--color-accent-2:oklch(0.66 0.24 330);     /* magenta */
--ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
```

**Dark mode only, and that is a product decision rather than a shortcut.** The job is
comparing a photograph of a card against extracted text; a dark surface keeps attention on the
card image and stops a grid of bright thumbnails competing with a bright page. Saying so here
is more honest than shipping a half-considered light theme.

**Every number in the UI is tabular-nums monospace** (`.tnum`). Confidence scores, card counts
and elapsed times all sit in columns that do not jitter as digits change — which matters a
great deal when the digits are changing 30 times a second.

### Motion rules

Motion communicates pipeline state; it is never decoration.

- **Durations**: micro 120 ms, standard 260 ms, route 420 ms. `--ease-out-expo` for
  entrances, linear for progress, spring only for success moments.
- **Stagger**: lists animate in at `delay: i * 0.035`, capped at 0.4 s total, so a 25-card
  grid lands in under half a second instead of trickling.
- **Layout animation** uses Framer `layout` / `layoutId` for the card → detail expansion, never
  manual position maths.
- **60 fps or cut it.** No blur-heavy backdrop filters on scrolling containers.

Three signature moments carry the state machine:

| | |
|---|---|
| **Aurora field** | One fixed `<div>` of CSS radial gradients that drifts slowly and shifts hue with app state (idle → processing → done). No canvas, no per-frame JS. |
| **Pipeline rail** | Upload → Extract → Review → Export. The active segment carries a travelling shimmer; completed segments snap to accent with a spring. It is the clearest possible answer to "what is happening right now". |
| **Number roll** | Metrics count up on a spring when they settle. They never flash. |
| **Scanline** | A card tile being read shows a travelling scanline. It is the per-card equivalent of the rail, and it is why a 40-second batch feels like progress rather than a freeze. |

## Accessibility

- `<MotionConfig reducedMotion="user">` at the root, plus CSS fallbacks: under
  `prefers-reduced-motion` the aurora stops drifting and the scanline collapses to a static
  wash. State stays legible; nothing moves. There is a screenshot of exactly this state.
- Full keyboard navigation with a visible `focus-visible` ring (2 px accent, 2 px offset).
- The progress region is `aria-live`; the drawer traps focus and restores it on close.
- Body text uses `ink` / `ink-muted` on `void` / `surface` — both ≥ 7:1, comfortably past the
  4.5:1 requirement.
- Empty, loading, error and partial-failure states are all designed. There are no bare
  spinners, and an unknown batch id gets a real screen rather than a crash.
- Mobile: single column below 768 px, the table becomes stacked cards.

## The mock backend, and why it exists

`src/lib/mock/` is a small simulation of the API: it holds uploads in memory, runs a
concurrency-3 worker pool with staggered per-card timings, emits the exact SSE events from the
contract, and serves a genuinely valid styled `.xlsx` through a ~150-line OOXML/ZIP writer —
so no spreadsheet dependency leaks into the frontend bundle.

It earns its place three times over:

1. **The frontend is developable and demoable with no backend at all** (`NEXT_PUBLIC_MOCK=1`).
2. **The Playwright smoke test is hermetic** — no Python, no model, no network.
3. **The cold-start UI is testable.** The real deployment takes ~210 s to wake the GPU; the
   mock scales that to 6 s, so the *Cold* pill, the warm-up call and the waiting state can all
   be exercised without a three-minute wait.

The ten cards in `public/samples/` are rendered by `scripts/gen-samples.mjs`, and the mock's
extractor returns the same people printed on them — so the side-by-side review screen shows
fields that actually match the pixels. The corpus deliberately contains one blurry card (lands
in `needs_review`), one glare-damaged card (fails, then succeeds on retry) and one duplicate.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `/api/v1` | Base URL of the API. `http://localhost:8000/api/v1` in the dev stack, `/api/v1` behind the reverse proxy. |
| `NEXT_PUBLIC_MOCK` | `0` | `1` routes every call to the in-process mock at `/mock/v1`. |

Both are inlined at build time, so the Docker build takes them as `--build-arg` — which is why
`docker-compose.yml` passes `NEXT_PUBLIC_API_BASE` as a build argument rather than an
environment variable. Behind Caddy the browser and the API share an origin, so the relative
default is correct and the bundle stays IP-agnostic.

## Tests

```bash
make e2e                      # builds, starts and drives the app in mock mode
E2E_BASE_URL=http://localhost:3000 npx playwright test    # against a running instance
```

`e2e/smoke.spec.ts` covers upload → stream → retry-failed → drawer edit (optimistic PATCH) →
table view → `.xlsx` download, plus a second test that aborts the SSE endpoint and asserts the
UI degrades to polling. `node scripts/shots.mjs http://localhost:3000` regenerates the
screenshot set.

## Known gaps

- `GET /cards/{id}/image?thumb=1` is served at full size **by the mock**; the real backend
  does the 480 px WebP resize.
- The frontend's own `README.md` notes `include_duplicates` as not yet in the contract. It is
  now — the contract was updated and the backend implements it — so that note is stale.
