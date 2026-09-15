# Shared Design System — LeadForge & Driftless

Both frontends ship the same visual language so the two submissions read as one body of work.
Goal: looks like a funded product, not a take-home. Motion is *purposeful* — it communicates
pipeline state, it is never decoration for its own sake.

## Stack (identical in both apps)
- **Next.js 15** App Router, TypeScript strict, `output: 'standalone'`.
- **Tailwind CSS v4** (CSS-first `@theme` config, no `tailwind.config.js` bloat).
- **Framer Motion 11** for all transitions.
- **lucide-react** icons. **sonner** toasts. **@tanstack/react-table** for the grid (Assignment 1).
- **@react-three/fiber + drei** for the 3D viewer (Assignment 2).
- No component library (no shadcn dump). Hand-roll the ~8 primitives each app needs.

## Tokens
```css
@theme {
  /* Surface — near-black, cool, with a lift ladder rather than flat #000 */
  --color-void:      oklch(0.145 0.012 265);   /* page background */
  --color-surface:   oklch(0.185 0.014 265);   /* cards */
  --color-raised:    oklch(0.225 0.016 265);   /* inputs, hovered rows */
  --color-line:      oklch(0.295 0.018 265);   /* hairlines */

  /* Ink */
  --color-ink:       oklch(0.97  0.005 265);
  --color-ink-muted: oklch(0.72  0.012 265);
  --color-ink-faint: oklch(0.55  0.014 265);

  /* Accent — Assignment 1 runs amber→magenta, Assignment 2 runs cyan→violet */
  --color-accent:     oklch(0.72 0.19 55);     /* LeadForge  : ember  */
  --color-accent-2:   oklch(0.66 0.24 330);    /* LeadForge  : magenta */
  /* Driftless overrides: --color-accent: oklch(0.78 0.15 195); --color-accent-2: oklch(0.62 0.22 285); */

  --color-ok:   oklch(0.76 0.17 155);
  --color-warn: oklch(0.80 0.16 85);
  --color-bad:  oklch(0.65 0.21 25);

  --radius-card: 14px;
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-spring:   cubic-bezier(0.34, 1.56, 0.64, 1);
}
```

Typography: `Geist` (or `Inter`) for UI, `Geist Mono` / `JetBrains Mono` for all numbers,
IDs, coordinates, and metrics. **Every number in the UI is tabular-nums monospace.**

## Motion rules (non-negotiable)
1. **Durations**: micro 120 ms, standard 260 ms, page/route 420 ms. Easing `--ease-out-expo`
   for entrances, linear for progress, `--ease-spring` only for success/pop moments.
2. **Stagger**: lists animate in with `delay: i * 0.035` capped at 0.4 s total.
3. **Layout animation**: use Framer `layout` + `layoutId` for card→detail expansion, never
   manual position math.
4. **Respect `prefers-reduced-motion`**: wrap in `<MotionConfig reducedMotion="user">`; all
   transforms collapse to opacity-only fades.
5. **Never animate on every render.** Progress values are driven by `useSpring`/`animate()` on
   a motion value, not by re-rendering React 30×/s. SSE updates write into motion values.
6. **60 fps or cut it.** No blur-heavy backdrop filters on scrolling containers.

## Signature moments (what makes it memorable)
Both apps share three:
- **Aurora field** — a single fixed, GPU-cheap gradient mesh behind the page that slowly
  drifts and shifts hue based on app state (idle → processing → done). One `<div>`, CSS
  `radial-gradient`s + `@keyframes`, `will-change: transform`. No canvas, no per-frame JS.
- **Pipeline rail** — a horizontal stage indicator (Upload → Extract → Review → Export /
  Decode → Track → Optimise → Explore) where the active segment has a travelling shimmer.
  Completed segments snap to accent with a spring.
- **Number roll** — metrics count up with `useSpring` when they settle; they never flash.

## Accessibility & polish baseline
- Full keyboard nav, visible `focus-visible` ring (2 px accent, 2 px offset).
- Contrast ≥ 4.5:1 for body text on every surface token above.
- Empty, loading, error, and partial-failure states are all designed — no bare spinners.
- Mobile: single column below 768 px, tables become stacked cards, 3D viewer keeps
  touch-orbit.

## Shared conventions
- Route structure: `/` (hero + upload), `/b/[id]` or `/j/[id]` (live run + results).
- SSE consumed through one `useEventSource(url, handlers)` hook per app. Reconnect with
  exponential backoff, and fall back to polling `GET /batches|jobs/{id}` after 3 failures.
- API base from `NEXT_PUBLIC_API_BASE` (default `/api/v1`, same-origin behind the reverse proxy).
- Dark mode only. It is a deliberate product decision; say so in the docs.
