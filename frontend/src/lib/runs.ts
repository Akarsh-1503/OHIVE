'use client';

/**
 * A small session-scoped history of runs started in this tab. The API's `Job` deliberately
 * does not echo back the options a run was started with, so the loop-closure A/B comparison
 * (same clip, correction on vs off) is remembered here instead of inventing a contract field.
 */

const KEY = 'driftless.runs';
const LIMIT = 8;

export interface RunRecord {
  job_id: string;
  label: string;
  source: string;
  loop_closure: boolean;
  started_at: number;
}

export function recordRun(run: RunRecord): void {
  if (typeof window === 'undefined') return;
  const all = [run, ...listRuns().filter((r) => r.job_id !== run.job_id)].slice(0, LIMIT);
  window.sessionStorage.setItem(KEY, JSON.stringify(all));
}

export function listRuns(): RunRecord[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as RunRecord[]) : [];
  } catch {
    return [];
  }
}

export function findRun(jobId: string): RunRecord | null {
  return listRuns().find((r) => r.job_id === jobId) ?? null;
}
