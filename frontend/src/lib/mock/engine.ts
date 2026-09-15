import type { Batch, BatchStatus, Confidence, Lead } from '../types';
import { confidenceFor, pickPersona, type Persona } from './personas';

/**
 * In-process fake of the LeadForge backend. It holds uploads in memory, simulates a
 * concurrency-limited VLM worker pool with realistic staggered timings, and pushes the
 * same SSE events the real service does. Only used when NEXT_PUBLIC_MOCK=1.
 */

export interface MockCard {
  lead: Lead;
  bytes: Uint8Array;
  mime: string;
  attempt: number;
  persona: Persona;
  seed: number;
}

type Listener = (event: string, data: unknown) => void;

interface MockBatch {
  id: string;
  createdAt: number;
  finishedAt: number | null;
  status: BatchStatus;
  order: string[];
  cards: Map<string, MockCard>;
  listeners: Set<Listener>;
  running: boolean;
  seenKeys: Map<string, string>;
}

interface MockState {
  batches: Map<string, MockBatch>;
  bootedAt: number;
  warm: boolean;
  warming: boolean;
  lastLatencyMs: number | null;
}

const CONCURRENCY = 3;
const MIN_CARD_MS = 1100;
const MAX_CARD_MS = 3200;
/** The real deployment pays ~90 s to wake a scale-to-zero GPU; the mock scales that down. */
const COLD_START_MS = 6000;
const MAX_FILES = 25;
const MAX_BYTES = 12 * 1024 * 1024;
const ALLOWED_MIME = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/heic',
  'image/heif',
  'application/pdf',
]);
/** Uploads are held in memory; keep the demo from growing without bound. */
const MAX_RETAINED_BATCHES = 5;

const GLOBAL_KEY = Symbol.for('leadforge.mock.state');

function state(): MockState {
  const globals = globalThis as unknown as Record<symbol, MockState | undefined>;
  let current = globals[GLOBAL_KEY];
  if (!current) {
    current = { batches: new Map(), bootedAt: Date.now(), warm: false, warming: false, lastLatencyMs: null };
    globals[GLOBAL_KEY] = current;
  }
  return current;
}

export class MockError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, detail: string, code: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

function uuid(): string {
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

function iso(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function emptyConfidence(): Confidence {
  return {
    first_name: 0,
    last_name: 0,
    job_title: 0,
    company: 0,
    location: 0,
    phone: 0,
    email: 0,
    website: 0,
  };
}

function weightedOverall(confidence: Confidence): number {
  // Name / company / contact carry the weight; website is a bonus field.
  const weights: Record<keyof Confidence, number> = {
    first_name: 1.2,
    last_name: 1.2,
    job_title: 1,
    company: 1.3,
    location: 0.8,
    phone: 1.2,
    email: 1.3,
    website: 0.5,
  };
  let sum = 0;
  let total = 0;
  for (const key of Object.keys(weights) as (keyof Confidence)[]) {
    if (confidence[key] <= 0) continue;
    sum += confidence[key] * weights[key];
    total += weights[key];
  }
  return total === 0 ? 0 : Math.round((sum / total) * 1000) / 1000;
}

function rawTextFor(persona: Persona): string {
  return [
    persona.company.toUpperCase(),
    `${persona.first_name} ${persona.last_name}`,
    persona.job_title,
    persona.phone,
    persona.email,
    persona.website,
    persona.location,
  ].join('\n');
}

function toBatch(batch: MockBatch): Batch {
  const leads = batch.order.map((id) => batch.cards.get(id)?.lead).filter((l): l is Lead => !!l);
  const completed = leads.filter((l) => l.status === 'completed' || l.status === 'needs_review').length;
  const failed = leads.filter((l) => l.status === 'failed').length;
  return {
    batch_id: batch.id,
    status: batch.status,
    total: leads.length,
    completed,
    failed,
    pending: leads.length - completed - failed,
    created_at: iso(batch.createdAt),
    finished_at: batch.finishedAt ? iso(batch.finishedAt) : null,
    elapsed_ms: (batch.finishedAt ?? Date.now()) - batch.createdAt,
    leads,
  };
}

function emit(batch: MockBatch, event: string, data: unknown): void {
  for (const listener of batch.listeners) listener(event, data);
}

function emitProgress(batch: MockBatch): void {
  const snapshot = toBatch(batch);
  emit(batch, 'batch.progress', {
    completed: snapshot.completed,
    failed: snapshot.failed,
    total: snapshot.total,
    elapsed_ms: snapshot.elapsed_ms,
  });
}

export interface Upload {
  name: string;
  type: string;
  bytes: Uint8Array;
}

export function createBatch(uploads: Upload[]): Batch {
  if (uploads.length === 0) throw new MockError(422, 'No files were uploaded.', 'NO_FILES');
  if (uploads.length > MAX_FILES) {
    throw new MockError(422, `A batch is limited to ${MAX_FILES} cards.`, 'TOO_MANY_FILES');
  }
  for (const upload of uploads) {
    if (upload.bytes.byteLength > MAX_BYTES) {
      throw new MockError(422, `${upload.name} is larger than 12 MB.`, 'FILE_TOO_LARGE');
    }
    if (!ALLOWED_MIME.has(upload.type)) {
      throw new MockError(422, `${upload.name} is not an accepted image type.`, 'UNSUPPORTED_MEDIA_TYPE');
    }
  }

  const now = Date.now();
  const batch: MockBatch = {
    id: uuid(),
    createdAt: now,
    finishedAt: null,
    status: 'queued',
    order: [],
    cards: new Map(),
    listeners: new Set(),
    running: false,
    seenKeys: new Map(),
  };

  for (const upload of uploads) {
    const cardId = uuid();
    const { persona, seed } = pickPersona(upload.name);
    batch.order.push(cardId);
    batch.cards.set(cardId, {
      persona,
      seed,
      attempt: 0,
      bytes: upload.bytes,
      mime: upload.type,
      lead: {
        card_id: cardId,
        batch_id: batch.id,
        filename: upload.name,
        status: 'queued',
        first_name: null,
        last_name: null,
        job_title: null,
        company: null,
        location: null,
        phone: null,
        phone_e164: null,
        email: null,
        website: null,
        confidence: emptyConfidence(),
        overall_confidence: 0,
        quality_flags: [],
        duplicate_of: null,
        raw_text: null,
        edited: false,
        processing_ms: null,
        error: null,
        created_at: iso(now),
      },
    });
  }

  const store = state();
  store.batches.set(batch.id, batch);
  while (store.batches.size > MAX_RETAINED_BATCHES) {
    const oldest = store.batches.keys().next();
    if (oldest.done) break;
    store.batches.delete(oldest.value);
  }

  void run(batch);
  return toBatch(batch);
}

function extract(card: MockCard, batch: MockBatch, elapsedMs: number): Lead {
  const { persona, seed } = card;
  // A retried card is re-read at a higher resolution, so it lands in review rather than
  // failing again — this is what the real pipeline does on a second pass.
  const profile = persona.profile === 'fail' && card.attempt > 0 ? 'low' : persona.profile;
  const confidence = confidenceFor(profile, seed);

  const flags: string[] = [];
  if (profile === 'low') flags.push('blurry', 'low_resolution');
  if (profile === 'soft') flags.push('partial_crop');

  const weak = (value: string, key: keyof Confidence): string | null =>
    confidence[key] < 0.4 ? null : value;

  const website = weak(persona.website, 'website');
  const location = weak(persona.location, 'location');
  if (!website) flags.push('no_website');

  const resolved: Confidence = {
    ...confidence,
    website: website ? confidence.website : 0,
    location: location ? confidence.location : 0,
  };
  const overall = weightedOverall(resolved);
  const duplicateOf = batch.seenKeys.get(persona.email) ?? null;
  if (duplicateOf && duplicateOf !== card.lead.card_id) flags.push('duplicate');
  else batch.seenKeys.set(persona.email, card.lead.card_id);

  return {
    ...card.lead,
    status: overall < 0.72 ? 'needs_review' : 'completed',
    first_name: persona.first_name,
    last_name: persona.last_name,
    job_title: persona.job_title,
    company: persona.company,
    location,
    phone: persona.phone,
    phone_e164: persona.phone_e164,
    email: persona.email,
    website,
    confidence: resolved,
    overall_confidence: overall,
    quality_flags: flags,
    duplicate_of: duplicateOf === card.lead.card_id ? null : duplicateOf,
    raw_text: rawTextFor(persona),
    processing_ms: Math.round(elapsedMs),
    error: null,
  };
}

async function run(batch: MockBatch): Promise<void> {
  if (batch.running) return;
  batch.running = true;
  batch.status = 'processing';

  const queue = batch.order.filter((id) => batch.cards.get(id)?.lead.status === 'queued');
  const store = state();

  // Every in-flight request waits on the same GPU wake-up, so the first few tiles sit in
  // "processing" together rather than one card being mysteriously slow.
  let coldStart: Promise<void> | null = null;
  if (!store.warm && !store.warming) {
    store.warming = true;
    coldStart = sleep(COLD_START_MS).then(() => {
      store.warm = true;
      store.warming = false;
    });
  }

  const worker = async (): Promise<void> => {
    for (;;) {
      const cardId = queue.shift();
      if (!cardId) return;
      const card = batch.cards.get(cardId);
      if (!card) continue;

      card.lead = { ...card.lead, status: 'processing', error: null };
      emit(batch, 'card.started', { card_id: cardId, filename: card.lead.filename });

      const started = Date.now();
      if (coldStart) await coldStart;
      await sleep(MIN_CARD_MS + ((card.seed >>> 5) % (MAX_CARD_MS - MIN_CARD_MS)));
      const duration = Date.now() - started;
      store.lastLatencyMs = Math.round(duration);

      if (card.persona.profile === 'fail' && card.attempt === 0) {
        card.attempt += 1;
        card.lead = {
          ...card.lead,
          status: 'failed',
          processing_ms: Math.round(duration),
          quality_flags: ['glare', 'low_resolution'],
          error: 'Glare occludes most of the card — the model returned no parseable fields.',
        };
        emit(batch, 'card.failed', { card_id: cardId, error: card.lead.error });
      } else {
        card.attempt += 1;
        card.lead = extract(card, batch, duration);
        emit(batch, 'card.completed', card.lead);
      }
      emitProgress(batch);
    }
  };

  await Promise.all(Array.from({ length: CONCURRENCY }, worker));

  const snapshot = toBatch(batch);
  batch.finishedAt = Date.now();
  batch.status = snapshot.failed === 0 ? 'completed' : snapshot.completed === 0 ? 'failed' : 'partial';
  batch.running = false;
  emit(batch, 'batch.completed', toBatch(batch));
}

export function getBatch(batchId: string): Batch | null {
  const batch = state().batches.get(batchId);
  return batch ? toBatch(batch) : null;
}

export function subscribe(batchId: string, listener: Listener): (() => void) | null {
  const batch = state().batches.get(batchId);
  if (!batch) return null;
  batch.listeners.add(listener);
  listener('batch.snapshot', toBatch(batch));
  if (!batch.running && batch.finishedAt) {
    // Late subscriber to a finished batch: close the stream immediately.
    listener('batch.completed', toBatch(batch));
  }
  return () => batch.listeners.delete(listener);
}

export function patchLead(cardId: string, patch: Record<string, unknown>): Lead | null {
  for (const batch of state().batches.values()) {
    const card = batch.cards.get(cardId);
    if (!card) continue;
    const confidence = { ...card.lead.confidence };
    const next: Record<string, unknown> = { ...card.lead };
    for (const [key, value] of Object.entries(patch)) {
      if (!(key in card.lead)) continue;
      next[key] = value;
      if (key in confidence) confidence[key as keyof Confidence] = value ? 1 : 0;
    }
    const merged = next as unknown as Lead;
    card.lead = {
      ...merged,
      confidence,
      overall_confidence: weightedOverall(confidence),
      edited: true,
      status: merged.status === 'failed' ? 'needs_review' : merged.status,
      error: null,
    };
    emit(batch, 'card.completed', card.lead);
    emitProgress(batch);
    return card.lead;
  }
  return null;
}

export function retry(batchId: string, cardIds?: string[]): Batch | null {
  const batch = state().batches.get(batchId);
  if (!batch) return null;
  const targets = cardIds ?? batch.order.filter((id) => batch.cards.get(id)?.lead.status === 'failed');
  for (const id of targets) {
    const card = batch.cards.get(id);
    if (!card) continue;
    card.lead = { ...card.lead, status: 'queued', error: null };
  }
  batch.finishedAt = null;
  emit(batch, 'batch.snapshot', toBatch(batch));
  void run(batch);
  return toBatch(batch);
}

export function getImage(cardId: string): { bytes: Uint8Array; mime: string } | null {
  for (const batch of state().batches.values()) {
    const card = batch.cards.get(cardId);
    if (card) return { bytes: card.bytes, mime: card.mime };
  }
  return null;
}

export function health(): {
  status: string;
  version: string;
  vlm: {
    provider: 'stub';
    model: string;
    endpoint_reachable: boolean;
    warm: boolean;
    last_latency_ms: number | null;
  };
  uptime_s: number;
} {
  const store = state();
  return {
    status: 'ok',
    version: '1.0.0-mock',
    vlm: {
      provider: 'stub',
      model: 'Qwen/Qwen2.5-VL-3B-Instruct',
      endpoint_reachable: true,
      warm: store.warm,
      last_latency_ms: store.lastLatencyMs,
    },
    uptime_s: Math.round((Date.now() - store.bootedAt) / 1000),
  };
}

export function warmup(): { warming: boolean } {
  const store = state();
  if (!store.warm) {
    store.warming = true;
    setTimeout(() => {
      store.warm = true;
      store.warming = false;
      store.lastLatencyMs = 1830;
    }, COLD_START_MS);
  }
  return { warming: true };
}

export const EXPORT_COLUMNS = [
  'filename',
  'first_name',
  'last_name',
  'job_title',
  'company',
  'location',
  'phone',
  'phone_e164',
  'email',
  'website',
  'overall_confidence',
  'status',
  'quality_flags',
  'duplicate_of',
  'edited',
  'processing_ms',
  'created_at',
] as const;

export function exportRows(
  batchId: string,
  options: { includeLowConfidence: boolean; includeDuplicates: boolean },
): string[][] | null {
  const batch = getBatch(batchId);
  if (!batch) return null;
  const rows: string[][] = [[...EXPORT_COLUMNS]];
  for (const lead of batch.leads) {
    if (!options.includeLowConfidence && lead.overall_confidence < 0.72) continue;
    if (!options.includeDuplicates && lead.duplicate_of) continue;
    rows.push([
      lead.filename,
      lead.first_name ?? '',
      lead.last_name ?? '',
      lead.job_title ?? '',
      lead.company ?? '',
      lead.location ?? '',
      lead.phone ?? '',
      lead.phone_e164 ?? '',
      lead.email ?? '',
      lead.website ?? '',
      lead.overall_confidence.toFixed(3),
      lead.status,
      lead.quality_flags.join('|'),
      lead.duplicate_of ?? '',
      String(lead.edited),
      lead.processing_ms === null ? '' : String(lead.processing_ms),
      lead.created_at,
    ]);
  }
  return rows;
}
