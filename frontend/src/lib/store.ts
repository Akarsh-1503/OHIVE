'use client';

import { createContext, useCallback, useContext, useSyncExternalStore } from 'react';
import type { Batch, BatchProgress, BatchStatus, CardFailed, CardStarted, Lead } from './types';

/**
 * A keyed store for one batch. SSE arrives faster than React should re-render a tree of
 * 25 tiles, so every tile subscribes only to its own card id; list order and aggregate
 * stats are separate subscriptions. Snapshots are cached objects so useSyncExternalStore
 * never loops.
 */

export interface BatchStats {
  status: BatchStatus;
  total: number;
  completed: number;
  failed: number;
  needsReview: number;
  processing: number;
  pending: number;
  /** Fraction 0..1 of cards that reached a terminal state. */
  progress: number;
  meanConfidence: number;
  elapsedMs: number;
  /** Cards per minute, computed over elapsed wall time. */
  throughput: number;
  startedAt: number;
  finishedAt: number | null;
}

const EMPTY_STATS: BatchStats = {
  status: 'queued',
  total: 0,
  completed: 0,
  failed: 0,
  needsReview: 0,
  processing: 0,
  pending: 0,
  progress: 0,
  meanConfidence: 0,
  elapsedMs: 0,
  throughput: 0,
  startedAt: 0,
  finishedAt: null,
};

export class BatchStore {
  private leads = new Map<string, Lead>();
  private order: string[] = [];
  private leadListeners = new Map<string, Set<() => void>>();
  private listListeners = new Set<() => void>();
  private statsListeners = new Set<() => void>();

  private idsSnapshot: string[] = [];
  private allSnapshot: Lead[] = [];
  private statsSnapshot: BatchStats = EMPTY_STATS;
  private serverElapsedMs = 0;

  batchId: string | null = null;

  // ---- reads -------------------------------------------------------------

  getLead = (cardId: string): Lead | undefined => this.leads.get(cardId);
  getIds = (): string[] => this.idsSnapshot;
  getAll = (): Lead[] => this.allSnapshot;
  getStats = (): BatchStats => this.statsSnapshot;

  subscribeLead = (cardId: string, listener: () => void): (() => void) => {
    let set = this.leadListeners.get(cardId);
    if (!set) {
      set = new Set();
      this.leadListeners.set(cardId, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
      if (set.size === 0) this.leadListeners.delete(cardId);
    };
  };

  subscribeList = (listener: () => void): (() => void) => {
    this.listListeners.add(listener);
    return () => this.listListeners.delete(listener);
  };

  subscribeStats = (listener: () => void): (() => void) => {
    this.statsListeners.add(listener);
    return () => this.statsListeners.delete(listener);
  };

  // ---- writes ------------------------------------------------------------

  applySnapshot(batch: Batch): void {
    this.batchId = batch.batch_id;
    const nextOrder = batch.leads.map((lead) => lead.card_id);
    const orderChanged =
      nextOrder.length !== this.order.length || nextOrder.some((id, i) => this.order[i] !== id);

    for (const lead of batch.leads) {
      const previous = this.leads.get(lead.card_id);
      this.leads.set(lead.card_id, lead);
      // A snapshot after a live update can be stale for a single card; only notify the
      // tiles whose payload actually moved.
      if (previous !== lead) this.notifyLead(lead.card_id);
    }
    this.order = nextOrder;
    this.serverElapsedMs = batch.elapsed_ms;
    this.statusOverride = batch.status;
    this.startedAt = Date.parse(batch.created_at);
    this.finishedAt = batch.finished_at ? Date.parse(batch.finished_at) : null;

    if (orderChanged) this.notifyList();
    this.recomputeStats();
  }

  cardStarted(event: CardStarted): void {
    const lead = this.leads.get(event.card_id);
    if (!lead || lead.status === 'processing') return;
    this.leads.set(event.card_id, { ...lead, status: 'processing', error: null });
    this.notifyLead(event.card_id);
    this.recomputeStats();
  }

  cardCompleted(lead: Lead): void {
    this.leads.set(lead.card_id, lead);
    this.notifyLead(lead.card_id);
    this.recomputeStats();
  }

  cardFailed(event: CardFailed): void {
    const lead = this.leads.get(event.card_id);
    if (!lead) return;
    this.leads.set(event.card_id, { ...lead, status: 'failed', error: event.error });
    this.notifyLead(event.card_id);
    this.recomputeStats();
  }

  progress(event: BatchProgress): void {
    this.serverElapsedMs = event.elapsed_ms;
    this.recomputeStats();
  }

  /** Optimistic local edit; the PATCH response replaces it via cardCompleted. */
  patchLead(cardId: string, patch: Partial<Lead>): void {
    const lead = this.leads.get(cardId);
    if (!lead) return;
    this.leads.set(cardId, { ...lead, ...patch });
    this.notifyLead(cardId);
    this.recomputeStats();
  }

  private statusOverride: BatchStatus = 'queued';
  private startedAt = 0;
  private finishedAt: number | null = null;

  private notifyLead(cardId: string): void {
    const set = this.leadListeners.get(cardId);
    if (set) for (const listener of set) listener();
  }

  private notifyList(): void {
    this.idsSnapshot = [...this.order];
    for (const listener of this.listListeners) listener();
  }

  private recomputeStats(): void {
    let completed = 0;
    let failed = 0;
    let needsReview = 0;
    let processing = 0;
    let pending = 0;
    let confidenceSum = 0;
    let confidenceCount = 0;

    for (const id of this.order) {
      const lead = this.leads.get(id);
      if (!lead) continue;
      switch (lead.status) {
        case 'completed':
          completed += 1;
          break;
        case 'needs_review':
          needsReview += 1;
          break;
        case 'failed':
          failed += 1;
          break;
        case 'processing':
          processing += 1;
          break;
        default:
          pending += 1;
      }
      if (lead.status === 'completed' || lead.status === 'needs_review') {
        confidenceSum += lead.overall_confidence;
        confidenceCount += 1;
      }
    }

    const total = this.order.length;
    const done = completed + needsReview + failed;
    const elapsedMs = this.serverElapsedMs;
    const minutes = elapsedMs / 60_000;

    this.statsSnapshot = {
      status: this.statusOverride,
      total,
      completed: completed + needsReview,
      failed,
      needsReview,
      processing,
      pending,
      progress: total === 0 ? 0 : done / total,
      meanConfidence: confidenceCount === 0 ? 0 : confidenceSum / confidenceCount,
      elapsedMs,
      throughput: minutes > 0 ? done / minutes : 0,
      startedAt: this.startedAt,
      finishedAt: this.finishedAt,
    };
    this.allSnapshot = this.order.map((id) => this.leads.get(id)).filter((l): l is Lead => !!l);
    for (const listener of this.statsListeners) listener();
  }
}

const StoreContext = createContext<BatchStore | null>(null);
export const StoreProvider = StoreContext.Provider;

export function useStore(): BatchStore {
  const store = useContext(StoreContext);
  if (!store) throw new Error('useStore must be used inside a StoreProvider');
  return store;
}

export function useLead(cardId: string): Lead | undefined {
  const store = useStore();
  const subscribe = useCallback(
    (listener: () => void) => store.subscribeLead(cardId, listener),
    [store, cardId],
  );
  const get = useCallback(() => store.getLead(cardId), [store, cardId]);
  return useSyncExternalStore(subscribe, get, get);
}

export function useCardIds(): string[] {
  const store = useStore();
  return useSyncExternalStore(store.subscribeList, store.getIds, store.getIds);
}

/** Full list — only mounted by the table view, which needs every row at once. */
export function useAllLeads(): Lead[] {
  const store = useStore();
  return useSyncExternalStore(store.subscribeStats, store.getAll, store.getAll);
}

export function useBatchStats(): BatchStats {
  const store = useStore();
  return useSyncExternalStore(store.subscribeStats, store.getStats, store.getStats);
}
