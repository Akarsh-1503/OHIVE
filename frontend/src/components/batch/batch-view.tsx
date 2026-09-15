'use client';

import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { FileQuestion, Inbox, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, eventsUrl, getBatch, retryBatch } from '@/lib/api';
import { BatchStore, StoreProvider, useBatchStats, useCardIds } from '@/lib/store';
import type { Batch, BatchProgress, CardFailed, CardStarted, Lead } from '@/lib/types';
import { useEventSource, type SseHandlers } from '@/lib/use-event-source';
import { useAuroraPhase, type AuroraPhase } from '../use-phase';
import { Wordmark } from '../wordmark';
import { BatchHeader } from './batch-header';
import { CardTile } from './card-tile';
import { LeadTable } from './lead-table';
import { ReviewDrawer } from './review-drawer';

const TERMINAL = new Set(['completed', 'partial', 'failed']);

export function BatchView({ batchId }: { batchId: string }) {
  const store = useMemo(() => new BatchStore(), []);
  const [load, setLoad] = useState<'loading' | 'missing' | 'error' | 'ready'>('loading');
  const [finished, setFinished] = useState(false);
  const [phase, setPhase] = useState<AuroraPhase>('processing');
  const [view, setView] = useState<'grid' | 'table'>('grid');
  const [reviewOnly, setReviewOnly] = useState(false);
  const [exported, setExported] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const lastFocused = useRef<HTMLElement | null>(null);

  const settle = useCallback(
    (batch: Batch) => {
      store.applySnapshot(batch);
      if (TERMINAL.has(batch.status)) {
        setFinished(true);
        setPhase(batch.failed > 0 ? 'error' : 'done');
      }
    },
    [store],
  );

  useEffect(() => {
    const controller = new AbortController();
    getBatch(batchId, controller.signal)
      .then((batch) => {
        settle(batch);
        setLoad('ready');
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoad(error instanceof ApiError && error.status === 404 ? 'missing' : 'error');
      });
    return () => controller.abort();
  }, [batchId, settle]);

  const handlers = useMemo<SseHandlers>(
    () => ({
      'batch.snapshot': (data) => store.applySnapshot(data as Batch),
      'card.started': (data) => store.cardStarted(data as CardStarted),
      'card.completed': (data) => store.cardCompleted(data as Lead),
      'card.failed': (data) => store.cardFailed(data as CardFailed),
      'batch.progress': (data) => store.progress(data as BatchProgress),
      'batch.completed': (data) => settle(data as Batch),
      ping: () => undefined,
    }),
    [store, settle],
  );

  const onPoll = useCallback(async () => {
    const batch = await getBatch(batchId);
    settle(batch);
    return TERMINAL.has(batch.status);
  }, [batchId, settle]);

  const { state: connection } = useEventSource(eventsUrl(batchId), handlers, {
    enabled: load === 'ready' && !finished,
    onPoll,
  });

  useAuroraPhase(load === 'ready' ? phase : 'idle');

  const open = useCallback((cardId: string) => {
    lastFocused.current = document.activeElement as HTMLElement | null;
    setSelected(cardId);
  }, []);

  const close = useCallback(() => {
    setSelected(null);
    lastFocused.current?.focus();
  }, []);

  const step = useCallback(
    (delta: 1 | -1) => {
      const ids = store.getIds();
      if (ids.length === 0) return;
      setSelected((current) => {
        const index = current ? ids.indexOf(current) : -1;
        const next = (index + delta + ids.length) % ids.length;
        return ids[next] ?? current;
      });
    },
    [store],
  );

  const retry = useCallback(
    async (cardIds?: string[]) => {
      try {
        const batch = await retryBatch(batchId, cardIds);
        store.applySnapshot(batch);
        setFinished(false);
        setPhase('processing');
        toast('Re-queued for extraction', {
          description: cardIds ? '1 card' : 'All failed cards',
        });
      } catch {
        toast.error('Retry failed', { description: 'The batch could not be re-queued.' });
      }
    },
    [batchId, store],
  );

  const retryOne = useCallback((cardId: string) => void retry([cardId]), [retry]);
  const retryAll = useCallback(() => void retry(), [retry]);

  if (load === 'loading') return <Splash>Loading batch…</Splash>;
  if (load === 'missing') {
    return (
      <Fallback
        icon={<FileQuestion className="h-6 w-6 text-ink-muted" aria-hidden />}
        title="No batch with that id"
        body="It may have expired, or the link was mistyped. Uploaded batches are kept for the life of the server process."
      />
    );
  }
  if (load === 'error') {
    return (
      <Fallback
        icon={<FileQuestion className="h-6 w-6 text-bad" aria-hidden />}
        title="Could not reach the API"
        body="The extraction service did not respond. Check that the backend is running, then reload."
      />
    );
  }

  const ids = store.getIds();
  const selectedIndex = selected ? ids.indexOf(selected) : -1;

  return (
    <StoreProvider value={store}>
      <div className="min-h-dvh">
        <BatchHeader
          batchId={batchId}
          connection={connection}
          finished={finished}
          exported={exported}
          view={view}
          reviewOnly={reviewOnly}
          onView={setView}
          onReviewOnly={setReviewOnly}
          onRetryFailed={retryAll}
          onExported={() => setExported(true)}
        />

        <main className="mx-auto w-full max-w-[1600px] px-5 pb-24 pt-6 sm:px-8">
          <LayoutGroup>
            <AnimatePresence mode="popLayout" initial={false}>
              {view === 'grid' ? (
                <motion.div
                  key="grid"
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10, transition: { duration: 0.16 } }}
                >
                  <CardGrid reviewOnly={reviewOnly} onOpen={open} onRetry={retryOne} />
                </motion.div>
              ) : (
                <motion.div
                  key="table"
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10, transition: { duration: 0.16 } }}
                >
                  <LeadTable onOpen={open} />
                </motion.div>
              )}
            </AnimatePresence>
          </LayoutGroup>
        </main>

        <AnimatePresence>
          {selected ? (
            <ReviewDrawer
              key={selected}
              cardId={selected}
              onClose={close}
              onStep={step}
              position={{ index: selectedIndex, total: ids.length }}
            />
          ) : null}
        </AnimatePresence>
      </div>
    </StoreProvider>
  );
}

function CardGrid({
  reviewOnly,
  onOpen,
  onRetry,
}: {
  reviewOnly: boolean;
  onOpen: (cardId: string) => void;
  onRetry: (cardId: string) => void;
}) {
  const ids = useCardIds();

  if (ids.length === 0) {
    return (
      <Fallback
        icon={<Inbox className="h-6 w-6 text-ink-muted" aria-hidden />}
        title="This batch is empty"
        body="No cards were accepted for extraction. Upload a new batch to try again."
        bare
      />
    );
  }

  return (
    <>
      <ReviewOnlyNotice active={reviewOnly} />
      <motion.div
        layout
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      >
        {ids.map((cardId, index) => (
          <CardTile
            key={cardId}
            cardId={cardId}
            index={index}
            reviewOnly={reviewOnly}
            onOpen={onOpen}
            onRetry={onRetry}
          />
        ))}
      </motion.div>
    </>
  );
}

/** Subscribes to stats on its own so the grid itself does not re-render per event. */
function ReviewOnlyNotice({ active }: { active: boolean }) {
  const stats = useBatchStats();
  if (!active || stats.needsReview > 0) return null;
  return (
    <p className="mb-4 rounded-xl border border-line bg-surface/50 px-4 py-3 text-[13px] text-ink-muted">
      Nothing needs review — every extracted card cleared the confidence threshold.
    </p>
  );
}

function Splash({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center gap-3 text-ink-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span className="text-[14px]">{children}</span>
    </div>
  );
}

function Fallback({
  icon,
  title,
  body,
  bare = false,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  bare?: boolean;
}) {
  const card = (
    <div className="mx-auto max-w-md rounded-card border border-line bg-surface/60 p-8 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-raised/60">
        {icon}
      </div>
      <h1 className="text-[17px] font-medium tracking-tight text-ink">{title}</h1>
      <p className="mt-2 text-[13.5px] leading-6 text-ink-muted">{body}</p>
      <Link
        href="/"
        className="mt-6 inline-flex h-9 items-center justify-center rounded-xl bg-[linear-gradient(100deg,var(--color-accent),var(--color-accent-2))] px-4 text-[13px] font-semibold text-void transition-[filter] hover:brightness-110"
      >
        Start a new batch
      </Link>
    </div>
  );

  if (bare) return card;

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-5 sm:px-8">
      <div className="py-6">
        <Wordmark />
      </div>
      <div className="flex flex-1 items-center justify-center pb-24">{card}</div>
    </div>
  );
}
