'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Flame, Loader2, Snowflake, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { getHealth, warmup } from '@/lib/api';
import type { Health } from '@/lib/types';
import { Button, cx } from './ui';

const IDLE_POLL_MS = 10_000;
const WAKING_POLL_MS = 3_000;
/** Measured cold boot on the deployed L4: 187-237 s, median ~210 s. */
const COLD_START_S = 210;

/**
 * Live VLM status. A scale-to-zero GPU is the biggest UX weakness of this deployment, so
 * the cold state is stated plainly and paired with the action that fixes it.
 */
export function HealthPill({ compact = false }: { compact?: boolean }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [failed, setFailed] = useState(false);
  const [waking, setWaking] = useState(false);
  const [wakeStartedAt, setWakeStartedAt] = useState<number | null>(null);
  const wakingRef = useRef(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await getHealth(signal);
      setHealth(next);
      setFailed(false);
      if (next.vlm.warm && wakingRef.current) {
        wakingRef.current = false;
        setWaking(false);
        setWakeStartedAt(null);
        toast.success('Model is warm', { description: 'Extraction will start immediately.' });
      }
    } catch {
      if (!signal?.aborted) setFailed(true);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const interval = setInterval(
      () => void refresh(controller.signal),
      waking ? WAKING_POLL_MS : IDLE_POLL_MS,
    );
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [refresh, waking]);

  const onWarmup = async (): Promise<void> => {
    wakingRef.current = true;
    setWaking(true);
    setWakeStartedAt(Date.now());
    try {
      await warmup();
      toast('Waking the GPU', {
        description: 'About 3½ minutes from cold. You can queue your cards meanwhile.',
        duration: 8000,
      });
    } catch {
      wakingRef.current = false;
      setWaking(false);
      toast.error('Could not reach the warm-up endpoint');
    }
  };

  const state = failed ? 'down' : !health ? 'loading' : health.vlm.warm ? 'warm' : 'cold';
  const tone = {
    down: 'border-bad/40 text-bad',
    loading: 'border-line text-ink-muted',
    warm: 'border-ok/35 text-ok',
    cold: 'border-warn/40 text-warn',
  }[state];

  return (
    <div className="flex flex-col items-start gap-2 sm:items-end">
      <div
        className={cx(
          'flex items-center gap-2.5 rounded-full border bg-surface/70 py-1.5 pl-2.5 pr-3 backdrop-blur-sm',
          tone,
        )}
        aria-live="polite"
      >
        <span className="relative flex h-2 w-2 shrink-0">
          <span
            className={cx(
              'absolute inline-flex h-full w-full rounded-full',
              state === 'warm' && 'bg-ok',
              state === 'cold' && 'bg-warn',
              state === 'down' && 'bg-bad',
              state === 'loading' && 'bg-ink-faint',
            )}
          />
          {state === 'warm' ? (
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-60" />
          ) : null}
        </span>

        {state === 'down' ? (
          <span className="flex items-center gap-1.5 text-xs font-medium">
            <TriangleAlert className="h-3.5 w-3.5" aria-hidden />
            API unreachable
          </span>
        ) : state === 'loading' ? (
          <span className="text-xs text-ink-muted">Checking model…</span>
        ) : (
          <span className="flex items-center gap-2 text-xs">
            {health?.vlm.warm ? (
              <Flame className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <Snowflake className="h-3.5 w-3.5" aria-hidden />
            )}
            <span className="font-medium">{health?.vlm.warm ? 'Warm' : 'Cold'}</span>
            <span className="h-3 w-px bg-line" aria-hidden />
            <span className="tnum text-ink-muted">{modelShortName(health)}</span>
            {compact ? null : (
              <>
                <span className="h-3 w-px bg-line" aria-hidden />
                <span className="tnum text-ink-muted">{health?.vlm.provider}</span>
              </>
            )}
          </span>
        )}
      </div>

      {/* The batch screen already shows a cold start as elapsed time on the tiles, so the
          explanatory note only belongs on the landing page. */}
      <AnimatePresence initial={false}>
        {state === 'cold' && !compact ? (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2"
          >
            <p className="max-w-[21rem] text-left text-[12.5px] leading-5 text-ink-muted sm:text-right">
              <span className="font-medium text-warn">The GPU is asleep.</span> It scales to zero
              when idle, so the first card takes about 3½ minutes to wake it — every card after
              that takes 6–11 s. Warm it up now and it will be ready by the time you have picked
              your files.
            </p>
            <Button size="sm" variant="outline" onClick={() => void onWarmup()} disabled={waking}>
              {waking ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Waking…
                </>
              ) : (
                <>
                  <Flame className="h-3.5 w-3.5" aria-hidden /> Warm up the GPU
                </>
              )}
            </Button>
            {waking && wakeStartedAt !== null ? (
              <WakeProgress startedAt={wakeStartedAt} />
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

/**
 * Elapsed time against the measured median cold start.
 *
 * Deliberately not a percentage: the real boot varies 187-237 s and can overrun, so a bar
 * that fills to 100% and then sits there is a lie. This one eases to 95% and holds, and
 * the label switches to "taking longer than usual" rather than pretending to be finished.
 */
function WakeProgress({ startedAt }: { startedAt: number }): React.ReactElement {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const tick = () => setElapsed(Math.round((Date.now() - startedAt) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  const overrun = elapsed > COLD_START_S;
  const pct = Math.min(95, (elapsed / COLD_START_S) * 95);
  const remaining = Math.max(0, COLD_START_S - elapsed);

  return (
    <div className="flex w-full flex-col items-end gap-1" aria-live="polite">
      <div className="h-1 w-full max-w-[24rem] overflow-hidden rounded-full bg-raised">
        <motion.div
          className="h-full rounded-full bg-gradient-to-r from-accent to-accent-2"
          animate={{ width: `${pct}%` }}
          transition={{ ease: 'linear', duration: 1 }}
        />
      </div>
      <p className="tnum text-[11.5px] text-ink-faint">
        {overrun
          ? `Waking — ${elapsed}s elapsed, taking longer than usual`
          : `Waking — about ${remaining}s left`}
      </p>
    </div>
  );
}

function modelShortName(health: Health | null): string {
  if (!health) return '—';
  const parts = health.vlm.model.split('/');
  return parts[parts.length - 1] ?? health.vlm.model;
}
