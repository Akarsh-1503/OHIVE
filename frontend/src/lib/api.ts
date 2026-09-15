import type { ApiErrorBody, Batch, Health, Lead } from './types';

export const MOCK = process.env.NEXT_PUBLIC_MOCK === '1';

// In mock mode everything is served by the in-process route handler under /mock/v1, which
// speaks the exact contract shapes. Otherwise we talk to the real backend, same-origin
// behind the reverse proxy by default.
export const API_BASE = MOCK ? '/mock/v1' : (process.env.NEXT_PUBLIC_API_BASE ?? '/api/v1');

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;

  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    let code: string | undefined;
    try {
      const body = (await res.json()) as ApiErrorBody;
      if (typeof body.detail === 'string') detail = body.detail;
      code = body.code;
    } catch {
      // Non-JSON error bodies (proxy HTML pages) keep the generic message.
    }
    throw new ApiError(res.status, detail, code);
  }
  return (await res.json()) as T;
}

/**
 * Uploads via XHR rather than fetch: we want a real upload-progress signal to drive the
 * "Upload" stage of the pipeline rail, and fetch still has no upload progress event.
 */
export function createBatch(files: File[], onProgress?: (fraction: number) => void): Promise<Batch> {
  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);

  return new Promise<Batch>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/batches`);
    xhr.responseType = 'text';

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    });
    xhr.addEventListener('error', () => reject(new ApiError(0, 'Network error while uploading')));
    xhr.addEventListener('abort', () => reject(new ApiError(0, 'Upload cancelled')));
    xhr.addEventListener('load', () => {
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(xhr.responseText) as unknown;
      } catch {
        parsed = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && parsed) {
        onProgress?.(1);
        resolve(parsed as Batch);
        return;
      }
      const body = (parsed ?? {}) as ApiErrorBody;
      reject(new ApiError(xhr.status, body.detail ?? `Upload failed (${xhr.status})`, body.code));
    });

    xhr.send(form);
  });
}

export async function getBatch(batchId: string, signal?: AbortSignal): Promise<Batch> {
  return unwrap<Batch>(await fetch(`${API_BASE}/batches/${batchId}`, { signal, cache: 'no-store' }));
}

export async function patchLead(cardId: string, patch: Partial<Record<string, string | null>>): Promise<Lead> {
  return unwrap<Lead>(
    await fetch(`${API_BASE}/leads/${cardId}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  );
}

export async function retryBatch(batchId: string, cardIds?: string[]): Promise<Batch> {
  return unwrap<Batch>(
    await fetch(`${API_BASE}/batches/${batchId}/retry`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(cardIds ? { card_ids: cardIds } : {}),
    }),
  );
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  return unwrap<Health>(await fetch(`${API_BASE}/health`, { signal, cache: 'no-store' }));
}

export async function warmup(): Promise<{ warming: boolean }> {
  return unwrap<{ warming: boolean }>(await fetch(`${API_BASE}/vlm/warmup`, { method: 'POST' }));
}

export interface ExportOptions {
  includeLowConfidence: boolean;
  includeDuplicates: boolean;
}

export function exportUrl(batchId: string, format: 'xlsx' | 'csv', options: ExportOptions): string {
  const query = new URLSearchParams({
    include_low_confidence: String(options.includeLowConfidence),
    include_duplicates: String(options.includeDuplicates),
  });
  return `${API_BASE}/batches/${batchId}/export.${format}?${query.toString()}`;
}

/** Fetches the export as a blob so a failed export surfaces as an error instead of an HTML page. */
export async function downloadExport(
  batchId: string,
  format: 'xlsx' | 'csv',
  options: ExportOptions,
): Promise<string> {
  const res = await fetch(exportUrl(batchId, format, options));
  if (!res.ok) {
    throw new ApiError(res.status, `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const filename = `leadforge-${batchId.slice(0, 8)}.${format}`;
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Give the browser a tick to start the download before releasing the blob.
  setTimeout(() => URL.revokeObjectURL(href), 4000);
  return filename;
}

export function cardImageUrl(cardId: string, thumb = false): string {
  return `${API_BASE}/cards/${cardId}/image${thumb ? '?thumb=1' : ''}`;
}

export function eventsUrl(batchId: string): string {
  return `${API_BASE}/batches/${batchId}/events`;
}
