'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Copy, RotateCcw, TriangleAlert } from 'lucide-react';
import { memo, useEffect, useRef, useState } from 'react';
import { cardImageUrl } from '@/lib/api';
import { useLead } from '@/lib/store';
import type { Lead } from '@/lib/types';
import { ConfidenceRing, LiveElapsed } from '../metrics';
import { Badge, cx } from '../ui';

const SHELL_BY_STATUS: Record<Lead['status'], string> = {
  queued: 'border-line/70 bg-surface/40',
  processing: 'border-accent/45 bg-surface/70 shadow-[0_0_0_1px_color-mix(in_oklch,var(--color-accent)_18%,transparent),0_18px_50px_-28px_color-mix(in_oklch,var(--color-accent)_60%,transparent)]',
  completed: 'border-line bg-surface/70',
  needs_review: 'border-warn/55 bg-surface/70',
  failed: 'border-bad/55 bg-bad/[0.05]',
};

/**
 * One tile per uploaded image. Subscribes only to its own card, so a 25-card batch
 * streaming updates never re-renders the grid.
 */
export const CardTile = memo(function CardTile({
  cardId,
  index,
  reviewOnly,
  onOpen,
  onRetry,
}: {
  cardId: string;
  index: number;
  reviewOnly: boolean;
  onOpen: (cardId: string) => void;
  onRetry: (cardId: string) => void;
}) {
  const lead = useLead(cardId);
  const [startedAt, setStartedAt] = useState(0);
  const wasProcessing = useRef(false);

  useEffect(() => {
    if (lead?.status === 'processing' && !wasProcessing.current) {
      wasProcessing.current = true;
      setStartedAt(Date.now());
    }
    if (lead?.status !== 'processing') wasProcessing.current = false;
  }, [lead?.status]);

  if (!lead) return null;
  // Filtering lives on the tile so the grid container stays subscribed to ids alone.
  if (reviewOnly && lead.status !== 'needs_review') return null;

  const status = lead.status;
  const name = [lead.first_name, lead.last_name].filter(Boolean).join(' ');
  const settled = status === 'completed' || status === 'needs_review';

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        duration: 0.4,
        ease: [0.16, 1, 0.3, 1],
        delay: Math.min(index * 0.035, 0.4),
      }}
      className={cx(
        'group relative flex flex-col overflow-hidden rounded-card border transition-colors duration-300',
        SHELL_BY_STATUS[status],
      )}
    >
      <button
        type="button"
        onClick={() => onOpen(cardId)}
        className="flex flex-1 flex-col text-left"
        aria-label={`Open ${name || lead.filename} for review`}
      >
        <div className="relative aspect-[1.75/1] w-full overflow-hidden border-b border-line/70 bg-void/60">
          <motion.div layoutId={`card-image-${cardId}`} className="absolute inset-0">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={cardImageUrl(cardId, true)}
              alt=""
              loading="lazy"
              draggable={false}
              className={cx(
                'h-full w-full object-cover transition-[opacity,filter] duration-500',
                status === 'queued' && 'opacity-20 saturate-0',
                status === 'processing' && 'opacity-70',
                settled && 'opacity-95 group-hover:opacity-100',
                status === 'failed' && 'opacity-30 saturate-0',
              )}
            />
          </motion.div>

          <AnimatePresence>
            {status === 'processing' ? (
              <motion.div
                key="scan"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="scanfield"
                aria-hidden
              >
                <div className="scanband" />
              </motion.div>
            ) : null}
          </AnimatePresence>

          {status === 'queued' ? (
            <div className="pulse-soft absolute inset-0 bg-void/45" aria-hidden />
          ) : null}

          {status === 'failed' ? (
            <div className="absolute inset-0 flex items-center justify-center bg-void/55">
              <TriangleAlert className="h-6 w-6 text-bad" aria-hidden />
            </div>
          ) : null}

          <div className="absolute left-2 top-2 flex gap-1.5">
            {lead.duplicate_of ? (
              <Badge tone="neutral" className="backdrop-blur-sm">
                <Copy className="h-3 w-3" aria-hidden />
                Duplicate
              </Badge>
            ) : null}
            {lead.edited ? <Badge tone="accent">Edited</Badge> : null}
          </div>

          <span className="absolute bottom-2 left-2 max-w-[75%] truncate rounded bg-void/75 px-1.5 py-0.5 text-[10.5px] text-ink-muted backdrop-blur-sm">
            {lead.filename}
          </span>
        </div>

        <div className="flex flex-1 flex-col gap-2.5 p-3.5">
          {status === 'queued' ? (
            <div className="space-y-2 py-1.5" aria-hidden>
              <div className="h-3.5 w-2/3 rounded bg-line/70" />
              <div className="h-3 w-1/2 rounded bg-line/50" />
              <div className="h-3 w-5/6 rounded bg-line/40" />
            </div>
          ) : null}

          {status === 'processing' ? (
            <div className="flex items-center gap-2 py-2 text-[13px] text-accent">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
              </span>
              Reading card…
              <LiveElapsed
                startedAt={startedAt}
                frozenMs={0}
                running
                className="ml-auto text-ink-muted"
              />
            </div>
          ) : null}

          {status === 'failed' ? (
            <p className="line-clamp-2 py-1 text-[12.5px] leading-5 text-bad">{lead.error}</p>
          ) : null}

          {settled ? <SettledFields lead={lead} /> : null}
        </div>
      </button>

      {settled ? (
        <div className="flex items-center gap-3 border-t border-line/70 px-3.5 py-2.5">
          <ConfidenceRing value={lead.overall_confidence} size={34} stroke={2.5} />
          <div className="min-w-0 flex-1">
            {status === 'needs_review' ? (
              <span className="text-[12px] font-medium text-warn">Needs review</span>
            ) : (
              <span className="text-[12px] text-ink-muted">Extracted</span>
            )}
            <div className="tnum truncate text-[11px] text-ink-muted">
              {lead.processing_ms ? `${(lead.processing_ms / 1000).toFixed(1)}s` : '—'}
              {lead.quality_flags.length > 0 ? ` · ${lead.quality_flags.length} flag${lead.quality_flags.length === 1 ? '' : 's'}` : ''}
            </div>
          </div>
        </div>
      ) : null}

      {status === 'failed' ? (
        <div className="flex items-center justify-between gap-2 border-t border-bad/30 px-3.5 py-2.5">
          <span className="text-[11.5px] uppercase tracking-wider text-bad">Failed</span>
          <button
            type="button"
            onClick={() => onRetry(cardId)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-bad/40 px-2.5 py-1 text-[12px] text-bad transition-colors hover:bg-bad/10"
          >
            <RotateCcw className="h-3 w-3" aria-hidden />
            Retry
          </button>
        </div>
      ) : null}
    </motion.div>
  );
});

/** Fields materialise in a stagger the moment the card settles. */
function SettledFields({ lead }: { lead: Lead }) {
  const rows: { value: string | null; className: string }[] = [
    {
      value: [lead.first_name, lead.last_name].filter(Boolean).join(' ') || null,
      className: 'text-[14.5px] font-medium tracking-tight text-ink',
    },
    { value: lead.job_title, className: 'text-[12.5px] text-ink-muted' },
    { value: lead.company, className: 'text-[12.5px] text-ink' },
    { value: lead.email ?? lead.phone, className: 'tnum text-[11.5px] text-ink-muted' },
  ];

  return (
    <div className="space-y-1">
      {rows.map((row, index) => (
        <motion.div
          key={index}
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: index * 0.045, ease: [0.16, 1, 0.3, 1] }}
          className={cx('truncate', row.className)}
        >
          {row.value ?? <span className="text-ink-faint">—</span>}
        </motion.div>
      ))}
    </div>
  );
}
