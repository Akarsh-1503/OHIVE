import type { ApiErrorCode, Job, JobOptions, Reconstruction, Sample } from './types';

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? '/api/v1';
export const IS_MOCK = process.env.NEXT_PUBLIC_MOCK === '1';

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  constructor(code: ApiErrorCode, detail: string, status: number) {
    super(detail);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

/**
 * The service emits a fixed code list; dumping `detail` verbatim is how you end up showing a
 * reviewer a stack trace. Codes get a written explanation, `detail` becomes the sub-line.
 */
const CODE_COPY: Record<ApiErrorCode, string> = {
  UNSUPPORTED_MEDIA_TYPE: 'That file type is not supported. Use MP4, MOV, MKV, WebM or AVI.',
  FILE_TOO_LARGE: 'That file is over the 200 MB limit. Trim the clip or re-encode it smaller.',
  VIDEO_TOO_LONG: 'Clips are capped at 60 seconds. Trim it, or raise the frame cap and accept a truncated run.',
  UNDECODABLE_VIDEO: 'The video could not be decoded. It may be corrupt or use an unsupported codec.',
  EMPTY_UPLOAD: 'No file arrived. Pick a clip and try again.',
  INVALID_PARAMETER: 'One of the advanced options is out of range.',
  JOB_NOT_FOUND: 'No job with that id. Jobs are kept for 24 hours, so this one may have expired.',
  JOB_NOT_COMPLETED: 'That job has not finished reconstructing yet.',
  SAMPLE_NOT_FOUND: 'That sample clip is no longer available.',
  FRAME_NOT_FOUND: 'That frame is outside the processed range.',
  ARTIFACT_NOT_FOUND: 'That export has not been written yet.',
  INTERNAL_ERROR: 'The reconstruction service hit an unexpected error.',
};

export function explain(err: unknown): { title: string; detail: string | null } {
  if (err instanceof ApiError) {
    const title = CODE_COPY[err.code] ?? 'The request failed.';
    return { title, detail: err.message && err.message !== title ? err.message : null };
  }
  if (err instanceof Error) return { title: 'The request failed.', detail: err.message };
  return { title: 'The request failed.', detail: null };
}

async function toApiError(res: Response): Promise<ApiError> {
  let code: ApiErrorCode = 'INTERNAL_ERROR';
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === 'object') {
      const b = body as { detail?: unknown; code?: unknown };
      if (typeof b.detail === 'string') detail = b.detail;
      if (typeof b.code === 'string' && b.code in CODE_COPY) code = b.code as ApiErrorCode;
    }
  } catch {
    // Non-JSON error body (a proxy 502, say) — keep the status line as the detail.
  }
  return new ApiError(code, detail, res.status);
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { accept: 'application/json' } });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export async function getSamples(): Promise<Sample[]> {
  if (IS_MOCK) return (await import('./mock')).mockSamples();
  return getJson<Sample[]>('/samples');
}

export async function getJob(jobId: string): Promise<Job> {
  if (IS_MOCK) {
    const job = (await import('./mock')).mockGetJob(jobId);
    if (!job) throw new ApiError('JOB_NOT_FOUND', `No job ${jobId}`, 404);
    return job;
  }
  return getJson<Job>(`/jobs/${encodeURIComponent(jobId)}`);
}

export async function createJobFromSample(sampleId: string, options: JobOptions): Promise<Job> {
  if (IS_MOCK) {
    const mock = await import('./mock');
    const sample = mock.mockSamples().find((s) => s.id === sampleId);
    if (!sample) throw new ApiError('SAMPLE_NOT_FOUND', `No sample ${sampleId}`, 404);
    return mock.mockCreateJob(`${sampleId}.mp4`, options);
  }
  const res = await fetch(`${API_BASE}/jobs/from-sample/${encodeURIComponent(sampleId)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(options),
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as Job;
}

/** XHR rather than fetch because multi-megabyte uploads need a real progress signal. */
export function createJobFromUpload(
  file: File,
  options: JobOptions,
  onProgress: (fraction: number) => void,
): Promise<Job> {
  if (IS_MOCK) {
    return new Promise((resolve) => {
      let p = 0;
      const timer = setInterval(() => {
        p = Math.min(1, p + 0.11);
        onProgress(p);
        if (p >= 1) {
          clearInterval(timer);
          void import('./mock').then((m) => resolve(m.mockCreateJob(file.name, options)));
        }
      }, 90);
    });
  }
  const form = new FormData();
  form.append('video', file);
  if (options.target_width !== undefined) form.append('target_width', String(options.target_width));
  if (options.max_frames !== undefined) form.append('max_frames', String(options.max_frames));
  if (options.enable_loop_closure !== undefined) form.append('enable_loop_closure', String(options.enable_loop_closure));

  return new Promise<Job>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/jobs`);
    xhr.responseType = 'text';
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Job);
        return;
      }
      let code: ApiErrorCode = 'INTERNAL_ERROR';
      let detail = `${xhr.status} ${xhr.statusText}`;
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: string; code?: string };
        if (body.detail) detail = body.detail;
        if (body.code && body.code in CODE_COPY) code = body.code as ApiErrorCode;
      } catch {
        // keep status line
      }
      reject(new ApiError(code, detail, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('INTERNAL_ERROR', 'Upload failed — the server is unreachable.', 0));
    xhr.send(form);
  });
}

export async function getReconstruction(jobId: string): Promise<Reconstruction> {
  if (IS_MOCK) {
    const recon = (await import('./mock')).mockReconstruction(jobId);
    if (!recon) throw new ApiError('JOB_NOT_COMPLETED', `Job ${jobId} has no reconstruction yet`, 409);
    return recon;
  }
  // gzip is handled by the browser; we parse once and immediately build typed arrays.
  const res = await fetch(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/reconstruction`, {
    headers: { accept: 'application/json' },
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as Reconstruction;
}

export function eventsUrl(jobId: string): string {
  return `${API_BASE}/jobs/${encodeURIComponent(jobId)}/events`;
}

export type ExportKind = 'ply' | 'tum' | 'report.json';

export function exportUrl(jobId: string, kind: ExportKind): string {
  return `${API_BASE}/jobs/${encodeURIComponent(jobId)}/export/${kind}`;
}

export async function downloadExport(jobId: string, kind: ExportKind): Promise<void> {
  let href: string;
  let revoke = false;
  if (IS_MOCK) {
    const mock = await import('./mock');
    const recon = mock.mockReconstruction(jobId);
    if (!recon) throw new ApiError('JOB_NOT_COMPLETED', `Job ${jobId} has no reconstruction yet`, 409);
    const blob = kind === 'ply' ? mock.mockPly(recon) : kind === 'tum' ? mock.mockTum(recon) : mock.mockReport(recon);
    href = URL.createObjectURL(blob);
    revoke = true;
  } else {
    href = exportUrl(jobId, kind);
  }
  const a = document.createElement('a');
  a.href = href;
  a.download = kind === 'ply' ? `${jobId}.ply` : kind === 'tum' ? `${jobId}-trajectory.txt` : `${jobId}-report.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  if (revoke) setTimeout(() => URL.revokeObjectURL(href), 4000);
}
