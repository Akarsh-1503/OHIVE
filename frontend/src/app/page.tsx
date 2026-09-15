import Link from 'next/link';
import { Aurora } from '@/components/aurora';
import { Ingest } from '@/components/ingest';
import { PipelineRail } from '@/components/pipeline-rail';
import { Badge } from '@/components/ui';

const IS_MOCK = process.env.NEXT_PUBLIC_MOCK === '1';

export default function Home() {
  return (
    <main className="relative min-h-dvh overflow-hidden">
      <Aurora state="idle" />

      <div className="relative z-10 mx-auto w-full max-w-6xl px-4 pt-6 pb-20 sm:px-6">
        <header className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="block h-6 w-6 rounded-md bg-linear-to-br from-accent to-accent-2 shadow-[0_0_22px_-4px_var(--color-accent)]"
            />
            <span className="text-sm font-semibold tracking-tight">Driftless</span>
          </div>
          <div className="flex items-center gap-2">
            {IS_MOCK ? <Badge tone="warn">mock data</Badge> : null}
            <Badge>monocular slam</Badge>
          </div>
        </header>

        <section className="pt-16 pb-12 sm:pt-24 sm:pb-16">
          <h1 className="max-w-3xl text-4xl leading-[1.05] font-semibold tracking-tight text-balance sm:text-6xl">
            Monocular video in.{' '}
            <span className="bg-linear-to-r from-accent to-accent-2 bg-clip-text text-transparent">
              Metric-consistent sparse map out.
            </span>
          </h1>
          <p className="mt-6 max-w-2xl text-[0.95rem] leading-relaxed text-ink-muted">
            Driftless takes a single hand-held clip and rebuilds where the camera went and what it saw. It extracts ORB
            features per frame, bootstraps geometry from a two-view initialisation, tracks the camera frame to frame with
            PnP, refines each neighbourhood with local bundle adjustment, recognises places it has already been using a
            bag-of-words index, and folds those loop constraints back through a Sim(3) pose-graph optimisation so the map
            closes on itself instead of spiralling away.
          </p>
          <p className="mt-4 max-w-2xl text-sm leading-relaxed text-ink-faint">
            One honest caveat, stated up front: a single camera cannot recover metric scale. Every distance here is{' '}
            <span className="text-ink-muted">up to scale</span> — internally consistent, and in arbitrary units until you
            anchor it to something of known size. &ldquo;Metric-consistent&rdquo; means the geometry stops drifting, not
            that it is in metres.
          </p>

          <div className="mt-10 max-w-3xl">
            <PipelineRail status="queued" />
          </div>
        </section>

        <Ingest />

        <footer className="mt-16 border-t border-line pt-6 text-xs text-ink-faint">
          <p>
            Dark mode only, by choice — this is a tool you stare at a 3D scene in. Reconstructions are kept for 24 hours.
          </p>
          {IS_MOCK ? (
            <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-ink-muted">Mock states:</span>
              <Link href="/j/mk_queue_demo" className="underline underline-offset-4 hover:text-accent">
                queued
              </Link>
              <Link href="/j/mk_fail_demo" className="underline underline-offset-4 hover:text-accent">
                failed
              </Link>
              <Link href="/j/mk_trunc_demo" className="underline underline-offset-4 hover:text-accent">
                truncated
              </Link>
              <Link href="/j/does-not-exist" className="underline underline-offset-4 hover:text-accent">
                unknown id
              </Link>
            </p>
          ) : null}
        </footer>
      </div>
    </main>
  );
}
