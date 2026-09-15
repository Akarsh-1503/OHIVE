'use client';

import { motion } from 'framer-motion';
import { GitCompareArrows, Loader2, Play } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { createJobFromSample, explain, getSamples } from '@/lib/api';
import { recordRun } from '@/lib/runs';
import type { JobOptions, Sample } from '@/lib/types';
import { Badge, Button, cx } from './ui';

const HUES = ['195', '250', '285'];

/**
 * Plays the sample clip when the backend serves one. Until then — and in mock mode, where
 * there is no file to serve — it draws an optical-flow motif: tracked corners with their
 * motion vectors, drifting the way they would in the real clip.
 */
function FlowPoster({ seed, hue }: { seed: string; hue: string }) {
  const dots = useMemo(() => {
    let h = 2166136261;
    for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
    const rand = () => {
      h = Math.imul(h ^ (h >>> 15), 1 | h);
      h = (h + Math.imul(h ^ (h >>> 7), 61 | h)) ^ h;
      return ((h ^ (h >>> 14)) >>> 0) / 4294967296;
    };
    return Array.from({ length: 46 }, () => {
      const x = rand() * 100;
      const y = rand() * 100;
      const mag = 2 + rand() * 7;
      const ang = (x / 100 - 0.5) * 1.2 + rand() * 0.5;
      return { x, y, r: 0.5 + rand() * 1.1, dx: Math.cos(ang) * mag, dy: Math.sin(ang) * mag * 0.4, o: 0.25 + rand() * 0.6 };
    });
  }, [seed]);

  return (
    <svg viewBox="0 0 100 56" preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden>
      <defs>
        <linearGradient id={`bg-${seed}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={`oklch(0.30 0.09 ${hue})`} />
          <stop offset="55%" stopColor={`oklch(0.20 0.05 ${Number(hue) + 30})`} />
          <stop offset="100%" stopColor={`oklch(0.16 0.04 ${Number(hue) + 60})`} />
        </linearGradient>
      </defs>
      <rect width="100" height="56" fill={`url(#bg-${seed})`} />
      <g className="feature-drift">
        {dots.map((d, i) => (
          <g key={i} opacity={d.o}>
            <line
              x1={(d.x * 100) / 100}
              y1={(d.y * 56) / 100}
              x2={(d.x * 100) / 100 - d.dx}
              y2={(d.y * 56) / 100 - d.dy}
              stroke={`oklch(0.88 0.15 ${hue})`}
              strokeWidth="0.28"
              strokeLinecap="round"
            />
            <circle cx={(d.x * 100) / 100} cy={(d.y * 56) / 100} r={d.r * 0.5} fill={`oklch(0.94 0.13 ${hue})`} />
          </g>
        ))}
      </g>
    </svg>
  );
}

function Thumb({ sample, hue }: { sample: Sample; hue: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [playable, setPlayable] = useState(false);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    const ok = () => {
      setPlayable(true);
      void v.play().catch(() => setPlayable(false));
    };
    v.addEventListener('loadeddata', ok);
    return () => v.removeEventListener('loadeddata', ok);
  }, []);

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-line bg-void">
      <FlowPoster seed={sample.id} hue={hue} />
      <video
        ref={ref}
        src={sample.url}
        muted
        loop
        playsInline
        preload="metadata"
        aria-hidden
        className={cx(
          'absolute inset-0 h-full w-full object-cover transition-opacity duration-500',
          playable ? 'opacity-100' : 'opacity-0',
        )}
      />
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-linear-to-t from-void/90 to-transparent px-2.5 pt-6 pb-2">
        <span className="num text-[0.68rem] text-ink-muted">{sample.duration_s.toFixed(1)} s</span>
        {!playable ? <span className="text-[0.6rem] tracking-wide text-ink-faint uppercase">preview</span> : null}
      </div>
    </div>
  );
}

export function SampleRow({ options }: { options: JobOptions }) {
  const router = useRouter();
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // The samples list is served by the API behind a relative base URL (and by the in-browser
  // mock when NEXT_PUBLIC_MOCK=1), so there is no server-side origin to fetch it from.
  useEffect(() => {
    let alive = true;
    getSamples()
      .then((s) => alive && setSamples(s))
      .catch((e: unknown) => alive && setError(explain(e).title));
    return () => {
      alive = false;
    };
  }, []);

  const run = async (sample: Sample, loopClosure: boolean) => {
    setBusy(`${sample.id}:${loopClosure}`);
    try {
      const job = await createJobFromSample(sample.id, { ...options, enable_loop_closure: loopClosure });
      recordRun({
        job_id: job.job_id,
        label: sample.name,
        source: sample.id,
        loop_closure: loopClosure,
        started_at: Date.now(),
      });
      router.push(`/j/${job.job_id}`);
    } catch (e: unknown) {
      const { title, detail } = explain(e);
      toast.error(title, { description: detail ?? undefined });
      setBusy(null);
    }
  };

  if (error) {
    return (
      <p className="text-sm text-ink-faint">
        Sample clips are unavailable right now ({error}) — upload a clip instead.
      </p>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {(samples ?? new Array<null>(3).fill(null)).map((s, i) => {
        const hue = HUES[i % HUES.length]!;
        if (!s) {
          return (
            <div key={i} className="rounded-[var(--radius-card)] border border-line bg-surface/50 p-3">
              <div className="aspect-video w-full animate-pulse rounded-lg bg-raised/60" />
              <div className="mt-3 h-3 w-24 animate-pulse rounded bg-raised/60" />
              <div className="mt-2 h-3 w-full animate-pulse rounded bg-raised/40" />
            </div>
          );
        }
        return (
          <motion.article
            key={s.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1], delay: Math.min(0.4, i * 0.035) }}
            className="flex flex-col rounded-[var(--radius-card)] border border-line bg-surface/70 p-3 transition-colors hover:border-accent/40"
          >
            <Thumb sample={s} hue={hue} />
            <h3 className="mt-3 text-sm font-semibold text-ink">{s.name}</h3>
            <p className="mt-1 flex-1 text-xs leading-relaxed text-ink-faint">{s.description}</p>
            <div className="mt-3 flex items-center gap-2">
              <Button
                size="sm"
                variant="primary"
                icon={busy === `${s.id}:true` ? Loader2 : Play}
                onClick={() => void run(s, true)}
                disabled={busy !== null}
                className="flex-1"
              >
                Reconstruct
              </Button>
              <Button
                size="sm"
                variant="subtle"
                icon={busy === `${s.id}:false` ? Loader2 : GitCompareArrows}
                onClick={() => void run(s, false)}
                disabled={busy !== null}
                title="Run the same clip with loop closure disabled, so you can compare the drift side by side"
                aria-label={`Run ${s.name} with loop closure disabled`}
              >
                No closure
              </Button>
            </div>
          </motion.article>
        );
      })}
      {samples ? (
        <p className="col-span-full text-xs text-ink-faint">
          <Badge tone="accent" className="mr-2">
            A/B
          </Badge>
          Run a clip both ways — <span className="text-ink-muted">Reconstruct</span> closes the loop,{' '}
          <span className="text-ink-muted">No closure</span> leaves the drift in. Only one job runs at a time, so the
          second will queue.
        </p>
      ) : null}
    </div>
  );
}
