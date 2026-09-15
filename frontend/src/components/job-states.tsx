'use client';

import { motion } from 'framer-motion';
import { ArrowLeft, CircleSlash, Clock, RotateCcw, TriangleAlert } from 'lucide-react';
import Link from 'next/link';
import { int } from '@/lib/format';
import { Button, Panel } from './ui';

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1] }}
      className="mx-auto w-full max-w-xl px-4 py-16"
    >
      <Panel className="p-6">{children}</Panel>
      <Link
        href="/"
        className="mt-4 inline-flex items-center gap-1.5 text-xs text-ink-faint transition-colors hover:text-accent"
      >
        <ArrowLeft size={13} aria-hidden />
        Back to upload
      </Link>
    </motion.div>
  );
}

/** Queueing is the normal path: one reconstruction runs at a time so the benchmark stays honest. */
export function QueuedState({ position }: { position: number }) {
  return (
    <Shell>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent/10 text-accent">
          <Clock size={16} aria-hidden />
        </span>
        <div>
          <h1 className="text-base font-semibold text-ink">Queued</h1>
          <p className="num mt-2 text-3xl leading-none font-semibold text-accent" aria-live="polite">
            {position === 0 ? 'starting…' : `#${int(position)}`}
          </p>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            {position === 0
              ? 'A worker has picked this job up — decoding starts in a moment.'
              : `${int(position)} ${position === 1 ? 'job is' : 'jobs are'} ahead of this one.`}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-ink-faint">
            Only one reconstruction runs at a time, on purpose: a second concurrent job would share CPU and invalidate
            the published timing benchmark. Queue wait is measured separately from{' '}
            <span className="num">wall_ms</span>.
          </p>
        </div>
      </div>
    </Shell>
  );
}

export function FailedState({ error, onRetry }: { error: string | null; onRetry: (() => void) | null }) {
  return (
    <Shell>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-bad/40 bg-bad/10 text-bad">
          <TriangleAlert size={16} aria-hidden />
        </span>
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-ink">Reconstruction failed</h1>
          <p className="mt-2 text-sm leading-relaxed text-ink-muted">
            {error ?? 'The service did not say why. That is a bug on our side, not yours.'}
          </p>
          <p className="mt-3 text-xs leading-relaxed text-ink-faint">
            Monocular SLAM fails honestly rather than inventing geometry. The usual causes are too little parallax
            (panning on the spot), motion blur, or a textureless scene.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {onRetry ? (
              <Button variant="primary" size="sm" icon={RotateCcw} onClick={onRetry}>
                Try again
              </Button>
            ) : null}
            <Link href="/">
              <Button variant="subtle" size="sm">
                Upload a different clip
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </Shell>
  );
}

export function UnknownJobState({ jobId }: { jobId: string }) {
  return (
    <Shell>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-raised text-ink-faint">
          <CircleSlash size={16} aria-hidden />
        </span>
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-ink">No job with that id</h1>
          <p className="num mt-2 truncate text-xs text-ink-faint">{jobId}</p>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            Reconstructions and their exports are kept for 24 hours, then deleted. This one has either expired or never
            existed.
          </p>
        </div>
      </div>
    </Shell>
  );
}
