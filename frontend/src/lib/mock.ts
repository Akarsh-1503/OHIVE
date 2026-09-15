/**
 * In-browser mock backend (`NEXT_PUBLIC_MOCK=1`).
 *
 * Serves the exact shapes of `_contracts/assignment2-api.md` and fakes the SSE stream with
 * realistic pacing, so the whole UI — including the 3D viewer — can be developed, demoed and
 * screenshotted with no backend running. Only one job "runs" at a time, mirroring
 * `SLAM_MAX_CONCURRENT_JOBS=1`, so the queued path is exercised for real.
 */

import { buildMockReconstruction, MOCK_FPS, MOCK_FRAME_COUNT, mockMetrics } from './mock-scene';
import type { Job, JobOptions, Reconstruction, Sample } from './types';

export interface SseFrame {
  type: string;
  data: unknown;
}

type Scenario = 'ok' | 'fail' | 'queue' | 'trunc';

const DECODE_MS = 900;
const TRACK_MS = 6100;
const OPTIMIZE_MS = 1420;
const TOTAL_MS = DECODE_MS + TRACK_MS + OPTIMIZE_MS;
const LOOP_AT = [DECODE_MS + 3400, DECODE_MS + 5200];
const FAIL_AT = DECODE_MS + 2600;
const QUEUE_STEP_MS = 2600;

interface MockJob {
  job: Job;
  scenario: Scenario;
  options: Required<JobOptions>;
  createdAt: number;
  startedAt: number | null;
  queueAhead: number;
  subs: Set<(f: SseFrame) => void>;
  lastProgressAt: number;
  loopsEmitted: number;
  lastStage: string | null;
  recon: Reconstruction | null;
  progress: {
    frames_done: number;
    frames_total: number;
    fps: number;
    keyframes: number;
    map_points: number;
    loop_closures: number;
    elapsed_ms: number;
  } | null;
}

const jobs = new Map<string, MockJob>();
let ticking: ReturnType<typeof setInterval> | null = null;
let seq = 0;

/** Mirrors the clips the real service ships, so a mock demo and a live demo read the same. */
export const SAMPLES: Sample[] = [
  {
    id: 'synthetic_loop',
    name: 'Synthetic room loop',
    description:
      'Virtual pinhole camera on a closed elliptical path inside a textured 7 m room. Returns to the start pose, so loop closure has a true match. Ships with ground-truth trajectory and cloud.',
    duration_s: 10.0,
    url: '/api/v1/samples/synthetic_loop/file',
  },
  {
    id: 'synthetic_corridor',
    name: 'Synthetic corridor (open-ended)',
    description:
      'Straight 9.6 m forward translation down a textured corridor. No revisited viewpoint, so loop closure cannot fire and drift accumulates monotonically. Ships with ground truth.',
    duration_s: 10.0,
    url: '/api/v1/samples/synthetic_corridor/file',
  },
  {
    id: 'desk_handheld',
    name: 'Handheld office desk (TUM fr1/desk)',
    description:
      'Real handheld 640×480 capture sweeping an office desk and returning to its starting viewpoint, with 100 Hz motion-capture ground truth. TUM RGB-D benchmark, CC BY 4.0.',
    duration_s: 10.0,
    url: '/api/v1/samples/desk_handheld/file',
  },
];

function scenarioFor(id: string): Scenario {
  if (id.startsWith('mk_fail')) return 'fail';
  if (id.startsWith('mk_queue')) return 'queue';
  if (id.startsWith('mk_trunc')) return 'trunc';
  return 'ok';
}

function newId(scenario: Scenario): string {
  seq += 1;
  return `mk_${scenario}_${Date.now().toString(36)}${seq}`;
}

function runningCount(): number {
  let n = 0;
  for (const m of jobs.values()) {
    if (m.startedAt !== null && m.job.status !== 'completed' && m.job.status !== 'failed') n += 1;
  }
  return n;
}

function makeJob(id: string, filename: string, options: JobOptions): MockJob {
  const scenario = scenarioFor(id);
  const truncated = scenario === 'trunc';
  const opts: Required<JobOptions> = {
    target_width: options.target_width ?? 640,
    max_frames: options.max_frames ?? (truncated ? MOCK_FRAME_COUNT : 1800),
    enable_loop_closure: options.enable_loop_closure ?? true,
  };
  const queueAhead = scenario === 'queue' ? 2 : runningCount() > 0 ? runningCount() : 0;
  const m: MockJob = {
    job: {
      job_id: id,
      status: queueAhead > 0 ? 'queued' : 'decoding',
      filename,
      created_at: new Date().toISOString(),
      video: truncated
        ? { width: 1920, height: 1080, fps: MOCK_FPS, frame_count: 720, duration_s: 24 }
        : { width: 1920, height: 1080, fps: MOCK_FPS, frame_count: MOCK_FRAME_COUNT, duration_s: MOCK_FRAME_COUNT / MOCK_FPS },
      metrics: null,
      error: null,
      queue_position: queueAhead,
      truncated,
    },
    scenario,
    options: opts,
    createdAt: Date.now(),
    startedAt: queueAhead > 0 ? null : Date.now(),
    queueAhead,
    subs: new Set(),
    lastProgressAt: 0,
    loopsEmitted: 0,
    lastStage: null,
    recon: null,
    progress: null,
  };
  jobs.set(id, m);
  ensureTicking();
  return m;
}

function emit(m: MockJob, type: string, data: unknown): void {
  for (const s of m.subs) s({ type, data });
}

function ensureTicking(): void {
  if (ticking !== null) return;
  ticking = setInterval(tickAll, 120);
}

function tickAll(): void {
  let active = false;
  for (const m of jobs.values()) {
    if (m.job.status === 'completed' || m.job.status === 'failed') continue;
    active = true;
    tick(m);
  }
  if (!active && ticking !== null) {
    clearInterval(ticking);
    ticking = null;
  }
}

function tick(m: MockJob): void {
  const now = Date.now();

  if (m.startedAt === null) {
    const waited = now - m.createdAt;
    const ahead = Math.max(0, m.queueAhead - Math.floor(waited / QUEUE_STEP_MS));
    if (ahead !== m.job.queue_position) {
      m.job.queue_position = ahead;
      emit(m, 'job.snapshot', m.job);
    }
    if (ahead === 0) {
      m.startedAt = now;
      m.job.status = 'decoding';
      emit(m, 'job.snapshot', m.job);
    }
    return;
  }

  const el = now - m.startedAt;
  const total = m.job.video?.frame_count ?? MOCK_FRAME_COUNT;
  const framesTotal = Math.min(total, m.options.max_frames);

  if (m.scenario === 'fail' && el >= FAIL_AT) {
    m.job.status = 'failed';
    m.job.error =
      'Tracking lost at frame 96 and relocalisation failed: the clip is close to pure rotation, so there is not enough parallax to triangulate new map points.';
    m.job.queue_position = null;
    emit(m, 'job.failed', { error: m.job.error });
    emit(m, 'job.snapshot', m.job);
    return;
  }

  const stage: Job['status'] = el < DECODE_MS ? 'decoding' : el < DECODE_MS + TRACK_MS ? 'tracking' : 'optimizing';
  if (el < TOTAL_MS && m.job.status !== stage) {
    m.job.status = stage;
    m.lastStage = stage;
    emit(m, 'job.stage', {
      stage,
      message:
        stage === 'decoding'
          ? `Decoding at ${m.options.target_width} px wide`
          : stage === 'tracking'
            ? 'ORB extraction · PnP tracking · local bundle adjustment'
            : m.options.enable_loop_closure
              ? 'Sim(3) pose-graph optimisation over the loop constraints'
              : 'Global bundle adjustment (loop closure disabled)',
    });
    emit(m, 'job.snapshot', m.job);
  }

  const trackT = Math.min(1, Math.max(0, (el - DECODE_MS) / TRACK_MS));
  const framesDone = Math.round(framesTotal * trackT);
  const keyframes = Math.max(1, Math.round(framesDone / 8));
  const mapPoints = Math.round(framesDone * 18.5 + Math.sin(el / 700) * 60);

  if (m.options.enable_loop_closure) {
    while (m.loopsEmitted < LOOP_AT.length && el >= LOOP_AT[m.loopsEmitted]!) {
      const idx = m.loopsEmitted;
      m.loopsEmitted += 1;
      emit(m, 'job.loop_closure', {
        from_kf: idx === 0 ? 28 : 36,
        to_kf: idx === 0 ? 3 : 1,
        inliers: idx === 0 ? 96 : 148,
      });
    }
  }

  if (now - m.lastProgressAt >= 150 && el < TOTAL_MS) {
    m.lastProgressAt = now;
    m.progress = {
      frames_done: framesDone,
      frames_total: framesTotal,
      fps: stage === 'tracking' ? Number((47 + Math.sin(el / 430) * 5.5 + Math.random() * 2.2).toFixed(1)) : 0,
      keyframes: stage === 'decoding' ? 0 : keyframes,
      map_points: stage === 'decoding' ? 0 : Math.max(0, mapPoints),
      loop_closures: m.loopsEmitted,
      elapsed_ms: el,
    };
    emit(m, 'job.progress', m.progress);
  }

  if (el >= TOTAL_MS) {
    const recon = reconFor(m);
    m.job.status = 'completed';
    m.job.metrics = recon.metrics;
    m.job.queue_position = null;
    emit(m, 'job.progress', {
      frames_done: framesTotal,
      frames_total: framesTotal,
      fps: 0,
      keyframes: recon.metrics.keyframes,
      map_points: recon.metrics.map_points,
      loop_closures: recon.metrics.loop_closures,
      elapsed_ms: TOTAL_MS,
    });
    emit(m, 'job.completed', { job_id: m.job.job_id, metrics: recon.metrics });
    emit(m, 'job.snapshot', m.job);
  }
}

/** Lets the FPS benchmark ask for a bigger cloud: `/j/<id>?points=50000`. */
function requestedPointCount(): number {
  const fallback = 38000;
  if (typeof window === 'undefined') return fallback;
  const q = new URLSearchParams(window.location.search).get('points');
  const n = q ? Number.parseInt(q, 10) : NaN;
  return Number.isFinite(n) ? Math.max(1000, Math.min(60000, n)) : fallback;
}

function reconFor(m: MockJob): Reconstruction {
  if (!m.recon) {
    m.recon = buildMockReconstruction(m.job.job_id, {
      points: requestedPointCount(),
      loopClosure: m.options.enable_loop_closure,
    });
    const keyframes = m.recon.poses.filter((p) => p.is_keyframe).length;
    m.recon.metrics = mockMetrics(m.recon.points.observations.length, keyframes, m.options.enable_loop_closure);
    m.recon.metrics.queue_wait_ms = m.startedAt ? Math.max(0, m.startedAt - m.createdAt) : 0;
    m.recon.metrics.frames_processed = m.recon.poses.length;
  }
  return m.recon;
}

/* ---------------- public surface used by lib/api.ts ---------------- */

export function mockSamples(): Sample[] {
  return SAMPLES;
}

export function mockCreateJob(filename: string, options: JobOptions, scenario: Scenario = 'ok'): Job {
  return makeJob(newId(scenario), filename, options).job;
}

export function mockGetJob(jobId: string): Job | null {
  const existing = jobs.get(jobId);
  if (existing) {
    // A completed job read back must carry its metrics.
    if (existing.job.status === 'completed' && !existing.job.metrics) existing.job.metrics = reconFor(existing).metrics;
    return existing.job;
  }
  // Deep links and hard reloads still work: re-create from the scenario encoded in the id.
  if (!jobId.startsWith('mk_')) return null;
  return makeJob(jobId, `${jobId.split('_')[1] ?? 'clip'}.mp4`, {}).job;
}

export function mockReconstruction(jobId: string): Reconstruction | null {
  const m = jobs.get(jobId);
  if (!m || m.job.status !== 'completed') return null;
  return reconFor(m);
}

/** Minimal EventSource-shaped object so `useEventSource` needs no mock-specific branch. */
export class MockEventSource {
  private listeners = new Map<string, Set<(e: MessageEvent) => void>>();
  private unsub: (() => void) | null = null;
  private ping: ReturnType<typeof setInterval>;
  onerror: ((e: Event) => void) | null = null;
  onopen: ((e: Event) => void) | null = null;

  constructor(jobId: string) {
    const m = jobs.get(jobId) ?? (mockGetJob(jobId) ? jobs.get(jobId)! : null);
    this.ping = setInterval(() => this.dispatch('ping', {}), 15000);
    // Deliver asynchronously so listeners registered right after construction still see the
    // opening snapshot — same ordering guarantee the browser gives for a real EventSource.
    setTimeout(() => {
      if (!m) {
        this.onerror?.(new Event('error'));
        return;
      }
      this.onopen?.(new Event('open'));
      this.dispatch('job.snapshot', m.job);
      if (m.progress) this.dispatch('job.progress', m.progress);
      if (m.job.status === 'completed' && m.job.metrics) {
        this.dispatch('job.completed', { job_id: m.job.job_id, metrics: m.job.metrics });
      }
      if (m.job.status === 'failed' && m.job.error) this.dispatch('job.failed', { error: m.job.error });
      const fn = (f: SseFrame) => this.dispatch(f.type, f.data);
      m.subs.add(fn);
      this.unsub = () => m.subs.delete(fn);
    }, 60);
  }

  private dispatch(type: string, data: unknown): void {
    const set = this.listeners.get(type);
    if (!set) return;
    const ev = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const l of set) l(ev);
  }

  addEventListener(type: string, listener: (e: MessageEvent) => void): void {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(listener);
  }

  close(): void {
    clearInterval(this.ping);
    this.unsub?.();
    this.unsub = null;
    this.listeners.clear();
  }
}

/* ---------------- exports built client-side so the mock demo is complete ---------------- */

export function mockPly(r: Reconstruction): Blob {
  const n = r.points.observations.length;
  const head = [
    'ply',
    'format ascii 1.0',
    'comment Driftless mock reconstruction — up to scale',
    `element vertex ${n}`,
    'property float x',
    'property float y',
    'property float z',
    'property uchar red',
    'property uchar green',
    'property uchar blue',
    'end_header',
    '',
  ].join('\n');
  const rows: string[] = new Array(n);
  for (let i = 0; i < n; i++) {
    rows[i] = `${r.points.xyz[i * 3]} ${r.points.xyz[i * 3 + 1]} ${r.points.xyz[i * 3 + 2]} ${r.points.rgb[i * 3]} ${r.points.rgb[i * 3 + 1]} ${r.points.rgb[i * 3 + 2]}`;
  }
  return new Blob([head + rows.join('\n') + '\n'], { type: 'application/octet-stream' });
}

export function mockTum(r: Reconstruction): Blob {
  const rows = r.poses.map((p) => {
    const [qw, qx, qy, qz] = p.quaternion;
    return `${p.t_s.toFixed(6)} ${p.position[0]} ${p.position[1]} ${p.position[2]} ${qx} ${qy} ${qz} ${qw}`;
  });
  return new Blob([`# timestamp tx ty tz qx qy qz qw\n${rows.join('\n')}\n`], { type: 'text/plain' });
}

export function mockReport(r: Reconstruction): Blob {
  const body = {
    job_id: r.job_id,
    generated_by: 'driftless-frontend mock',
    scale: 'up to scale — monocular reconstruction has no metric scale',
    metrics: r.metrics,
    loop_closures: r.loop_closures,
  };
  return new Blob([JSON.stringify(body, null, 2)], { type: 'application/json' });
}
