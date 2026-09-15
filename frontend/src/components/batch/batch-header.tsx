'use client';

import { AnimatePresence, motion, useSpring } from 'framer-motion';
import { LayoutGrid, RotateCcw, Snowflake, Table2, TriangleAlert, Wifi, WifiOff } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useBatchStats } from '@/lib/store';
import type { ConnectionState } from '@/lib/use-event-source';
import { HealthPill } from '../health-pill';
import { LiveElapsed, NumberRoll, ProgressBar } from '../metrics';
import { PipelineRail, type StageIndex } from '../pipeline-rail';
import { Button, cx } from '../ui';
import { Wordmark } from '../wordmark';
import { ExportMenu } from './export-menu';

export function BatchHeader({
  batchId,
  connection,
  finished,
  exported,
  view,
  reviewOnly,
  onView,
  onReviewOnly,
  onRetryFailed,
  onExported,
}: {
  batchId: string;
  connection: ConnectionState;
  finished: boolean;
  exported: boolean;
  view: 'grid' | 'table';
  reviewOnly: boolean;
  onView: (next: 'grid' | 'table') => void;
  onReviewOnly: (next: boolean) => void;
  onRetryFailed: () => void;
  onExported: () => void;
}) {
  const stats = useBatchStats();
  // The bar is a motion value so the 3 px strip never re-renders with the stream.
  const progress = useSpring(0, { stiffness: 120, damping: 26 });

  useEffect(() => {
    progress.set(stats.progress);
  }, [progress, stats.progress]);

  const stage: StageIndex = !finished ? 1 : exported ? 3 : 2;

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-void/80 backdrop-blur-xl">
      <div className="mx-auto w-full max-w-[1600px] px-5 sm:px-8">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3.5">
          <Wordmark />
          <span className="tnum hidden rounded-md border border-line bg-surface/60 px-2 py-0.5 text-[11px] text-ink-muted sm:inline">
            batch {batchId.slice(0, 8)}
          </span>
          <div className="ml-auto flex items-center gap-3">
            <ConnectionIndicator state={connection} finished={finished} />
            <span className="hidden sm:block">
              <HealthPill compact />
            </span>
          </div>
        </div>

        <PipelineRail active={stage} className="max-w-2xl pb-4" />

        <div className="flex flex-wrap items-end justify-between gap-4 pb-4">
          <div
            className="flex flex-wrap items-center gap-x-5 gap-y-3 sm:gap-x-6"
            aria-live="polite"
            aria-atomic="true"
          >
            <Stat label="Cards" value={stats.total} className="hidden sm:block" />
            <Stat label="Extracted" value={stats.completed} tone="ok" />
            <Stat label="Review" value={stats.needsReview} tone="warn" />
            <Stat label="Failed" value={stats.failed} tone="bad" />
            <Stat
              label="Mean conf."
              value={stats.meanConfidence * 100}
              format={(v) => `${Math.round(v)}%`}
              className="hidden sm:block"
            />
            <div>
              <div className="text-[10.5px] uppercase tracking-[0.16em] text-ink-muted">Elapsed</div>
              <LiveElapsed
                startedAt={stats.startedAt}
                frozenMs={stats.elapsedMs}
                running={!finished}
                className="text-[17px] font-medium text-ink sm:text-[19px]"
              />
            </div>
            <Stat
              label="Throughput"
              value={stats.throughput}
              format={(v) => `${v.toFixed(1)}/min`}
              className="hidden sm:block"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {stats.failed > 0 ? (
              <Button size="sm" variant="danger" onClick={onRetryFailed}>
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                Retry {stats.failed} failed
              </Button>
            ) : null}

            {view === 'grid' ? (
              <Button
                size="sm"
                variant={reviewOnly ? 'primary' : 'outline'}
                onClick={() => onReviewOnly(!reviewOnly)}
                aria-pressed={reviewOnly}
              >
                <TriangleAlert className="h-3.5 w-3.5" aria-hidden />
                Needs review
                {stats.needsReview > 0 ? (
                  <span className="tnum ml-0.5">{stats.needsReview}</span>
                ) : null}
              </Button>
            ) : null}

            <div className="flex items-center rounded-xl border border-line bg-surface/60 p-0.5">
              <ViewToggle active={view === 'grid'} onClick={() => onView('grid')} label="Grid view">
                <LayoutGrid className="h-3.5 w-3.5" aria-hidden />
              </ViewToggle>
              <ViewToggle active={view === 'table'} onClick={() => onView('table')} label="Table view">
                <Table2 className="h-3.5 w-3.5" aria-hidden />
              </ViewToggle>
            </div>

            <ExportMenu batchId={batchId} disabled={stats.completed === 0} onExported={onExported} />
          </div>

          <ColdStartBanner
            firstResultPending={!finished && stats.completed === 0 && stats.failed === 0}
          />
        </div>
      </div>

      <ProgressBar progress={progress} className="rounded-none" />
      <span className="sr-only" aria-live="polite">
        {`${stats.completed} of ${stats.total} cards extracted, ${stats.failed} failed.`}
      </span>
    </header>
  );
}

function Stat({
  label,
  value,
  tone,
  format,
  className,
}: {
  label: string;
  value: number;
  tone?: 'ok' | 'warn' | 'bad';
  format?: (value: number) => string;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="text-[10.5px] uppercase tracking-[0.16em] text-ink-muted">{label}</div>
      <NumberRoll
        value={value}
        format={format}
        className={cx(
          'text-[17px] font-medium sm:text-[19px]',
          tone === 'ok' && value > 0 && 'text-ok',
          tone === 'warn' && value > 0 && 'text-warn',
          tone === 'bad' && value > 0 && 'text-bad',
          !tone && 'text-ink',
          tone && value === 0 && 'text-ink-muted',
        )}
      />
    </div>
  );
}

function ViewToggle({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={cx(
        'flex h-8 w-9 items-center justify-center rounded-[10px] transition-colors duration-150',
        active ? 'bg-raised text-ink' : 'text-ink-muted hover:text-ink',
      )}
    >
      {children}
    </button>
  );
}

function ConnectionIndicator({
  state,
  finished,
}: {
  state: ConnectionState;
  finished: boolean;
}) {
  if (finished && state === 'closed') {
    return (
      <span className="flex items-center gap-1.5 text-[12px] text-ink-muted">
        <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />
        Stream closed
      </span>
    );
  }
  const copy: Record<ConnectionState, { text: string; tone: string; icon: 'on' | 'off' }> = {
    connecting: { text: 'Connecting…', tone: 'text-ink-muted', icon: 'on' },
    live: { text: 'Live', tone: 'text-ok', icon: 'on' },
    reconnecting: { text: 'Reconnecting…', tone: 'text-warn', icon: 'off' },
    polling: { text: 'Polling fallback', tone: 'text-warn', icon: 'off' },
    closed: { text: 'Idle', tone: 'text-ink-muted', icon: 'off' },
  };
  const { text, tone, icon } = copy[state];
  return (
    <span className={cx('flex items-center gap-1.5 text-[12px]', tone)} aria-live="polite">
      {icon === 'on' ? (
        <Wifi className="h-3.5 w-3.5" aria-hidden />
      ) : (
        <WifiOff className="h-3.5 w-3.5" aria-hidden />
      )}
      {text}
    </span>
  );
}

/**
 * Shown while a batch is running and the first card has not come back yet.
 *
 * A scale-to-zero GPU means the first result can be three and a half minutes away. Without
 * this, the honest read of the screen is "it has hung". It disappears the moment any card
 * completes, so a warm batch never sees it.
 */
function ColdStartBanner({ firstResultPending }: { firstResultPending: boolean }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!firstResultPending) {
      setElapsed(0);
      return;
    }
    const started = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, [firstResultPending]);

  // Below ~20 s an ordinary warm extraction is still in flight; saying "cold start" then
  // would be wrong most of the time.
  const show = firstResultPending && elapsed > 20;

  return (
    <AnimatePresence initial={false}>
      {show ? (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="overflow-hidden"
        >
          <div className="mb-3 flex items-start gap-2.5 rounded-xl border border-warn/30 bg-warn/[0.06] px-3.5 py-2.5">
            <Snowflake className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
            <p className="text-[12.5px] leading-5 text-ink-muted">
              <span className="font-medium text-ink">Waking the GPU — this is expected.</span> The
              model scales to zero when idle, so the first card takes about 3½ minutes to load it. The remaining cards follow at 6–11 s each. 
              <span className="tnum text-ink-faint">({elapsed}s elapsed)</span>
            </p>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
