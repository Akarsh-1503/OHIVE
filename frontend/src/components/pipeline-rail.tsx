'use client';

import { motion } from 'framer-motion';
import { Boxes, Compass, Route, Scan } from 'lucide-react';
import { cx } from './ui';
import type { JobStatus } from '@/lib/types';

const STAGES = [
  { key: 'decode', label: 'Decode', icon: Scan, note: 'Frames out of the container, downscaled' },
  { key: 'track', label: 'Track', icon: Route, note: 'ORB + two-view init + PnP + local BA' },
  { key: 'optimise', label: 'Optimise', icon: Boxes, note: 'BoW loop closure + Sim(3) pose graph' },
  { key: 'explore', label: 'Explore', icon: Compass, note: 'Map ready to inspect and export' },
] as const;

export function stageIndexFor(status: JobStatus): number {
  switch (status) {
    case 'queued':
      return -1;
    case 'decoding':
      return 0;
    case 'tracking':
      return 1;
    case 'optimizing':
      return 2;
    case 'completed':
      return 3;
    case 'failed':
      return -2;
  }
}

export function PipelineRail({ status, className }: { status: JobStatus; className?: string }) {
  const active = stageIndexFor(status);
  const failed = status === 'failed';
  return (
    <ol className={cx('flex items-stretch gap-1.5 sm:gap-3', className)} aria-label="Reconstruction pipeline">
      {STAGES.map((s, i) => {
        const done = active > i || status === 'completed';
        const isActive = active === i && status !== 'completed';
        const Icon = s.icon;
        return (
          <li key={s.key} className="min-w-0 flex-1" aria-current={isActive ? 'step' : undefined}>
            <div className="flex items-center gap-1.5">
              <Icon
                size={13}
                aria-hidden
                className={cx(
                  'shrink-0 transition-colors duration-300',
                  done ? 'text-accent' : isActive ? 'text-accent-2' : 'text-ink-faint',
                )}
              />
              <span
                className={cx(
                  'truncate text-[0.7rem] font-medium tracking-wide transition-colors duration-300 sm:text-xs',
                  done ? 'text-ink' : isActive ? 'text-ink' : 'text-ink-faint',
                )}
              >
                {s.label}
              </span>
            </div>
            <div className="relative mt-1.5 h-[3px] w-full overflow-hidden rounded-full bg-line/70">
              <motion.div
                initial={false}
                animate={{ scaleX: done ? 1 : isActive ? 0.55 : 0 }}
                transition={
                  done
                    ? { type: 'spring', stiffness: 320, damping: 22 }
                    : { duration: 0.4, ease: [0.16, 1, 0.3, 1] }
                }
                style={{ originX: 0 }}
                className={cx(
                  'absolute inset-0 rounded-full',
                  failed ? 'bg-bad' : 'bg-linear-to-r from-accent to-accent-2',
                )}
              />
              {isActive && !failed ? <span className="shimmer absolute inset-0 rounded-full" /> : null}
            </div>
            <p className="mt-1.5 hidden text-[0.68rem] leading-tight text-ink-faint lg:block">{s.note}</p>
          </li>
        );
      })}
    </ol>
  );
}
