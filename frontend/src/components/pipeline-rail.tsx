'use client';

import { motion } from 'framer-motion';
import { Check, Download, ScanLine, SlidersHorizontal, Upload } from 'lucide-react';
import { cx } from './ui';

const STAGES = [
  { key: 'upload', label: 'Upload', Icon: Upload },
  { key: 'extract', label: 'Extract', Icon: ScanLine },
  { key: 'review', label: 'Review', Icon: SlidersHorizontal },
  { key: 'export', label: 'Export', Icon: Download },
] as const;

export type StageIndex = 0 | 1 | 2 | 3;

/**
 * Upload → Extract → Review → Export. The active connector carries a travelling shimmer;
 * connectors snap to accent with a spring as each stage completes.
 */
export function PipelineRail({
  active,
  className,
  muted = false,
}: {
  active: StageIndex;
  className?: string;
  muted?: boolean;
}) {
  return (
    <ol className={cx('flex w-full items-center', className)} aria-label="Extraction pipeline">
      {STAGES.map(({ key, label, Icon }, index) => {
        const done = index < active;
        const current = index === active && !muted;
        return (
          <li key={key} className={cx('flex items-center', index < STAGES.length - 1 && 'flex-1')}>
            <div className="flex shrink-0 items-center gap-2.5">
              <motion.span
                animate={
                  done
                    ? { scale: [1, 1.18, 1], borderColor: 'transparent' }
                    : { scale: 1 }
                }
                transition={{ type: 'spring', stiffness: 420, damping: 14 }}
                className={cx(
                  'relative flex h-7 w-7 items-center justify-center rounded-full border',
                  done && 'border-transparent bg-[linear-gradient(120deg,var(--color-accent),var(--color-accent-2))] text-void',
                  current && 'border-accent/60 bg-accent/10 text-accent',
                  !done && !current && 'border-line bg-surface/60 text-ink-faint',
                )}
                aria-hidden
              >
                {done ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                ) : (
                  <Icon className="h-3.5 w-3.5" />
                )}
                {current ? (
                  <span className="absolute inset-0 animate-ping rounded-full border border-accent/50" />
                ) : null}
              </motion.span>
              {/* Below sm only the active stage keeps its label, so the rail never overflows. */}
              <span
                className={cx(
                  'text-[13px] font-medium tracking-tight transition-colors duration-300',
                  current ? 'inline' : 'hidden sm:inline',
                  done && 'text-ink-muted',
                  current && 'text-ink',
                  !done && !current && 'text-ink-faint',
                )}
              >
                {label}
              </span>
            </div>

            {index < STAGES.length - 1 ? (
              <div className="relative mx-2.5 h-[3px] min-w-6 flex-1 overflow-hidden rounded-full bg-line/70 sm:mx-3">
                <motion.div
                  className="h-full origin-left rounded-full bg-[linear-gradient(90deg,var(--color-accent),var(--color-accent-2))]"
                  initial={false}
                  animate={{ scaleX: done ? 1 : 0 }}
                  transition={{ type: 'spring', stiffness: 180, damping: 22 }}
                />
                {current ? <span className="shimmer absolute inset-0 opacity-40" aria-hidden /> : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
