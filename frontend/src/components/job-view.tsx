'use client';

import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowLeft, ChevronRight, Gauge, Link2, RadioTower, TriangleAlert, WifiOff, X } from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, eventsUrl, explain, getJob, getReconstruction } from '@/lib/api';
import { fixed, int } from '@/lib/format';
import { buildSceneData, type SceneData } from '@/lib/recon';
import { findRun, listRuns } from '@/lib/runs';
import type {
  CompletedEvent,
  FailedEvent,
  Job,
  LoopClosureEvent,
  ProgressEvent,
  StageEvent,
} from '@/lib/types';
import { useEventSource, type SseHandlers } from '@/lib/use-event-source';
import { AccessibleTable } from './accessible-table';
import { Aurora } from './aurora';
import { Exports } from './exports';
import { FailedState, QueuedState, UnknownJobState } from './job-states';
import { MetricsPanel } from './metrics-panel';
import { PipelineRail } from './pipeline-rail';
import { Telemetry } from './telemetry';
import { Badge, Button, Panel, cx } from './ui';
import { createRuntime } from './viewer/state';
import { ViewerSkeleton } from './viewer-skeleton';

// three.js is ~250 kB of the bundle and is useless until a reconstruction exists, so it is
// fetched while the job is still running rather than blocking first paint.
const MemoViewer = memo(dynamic(() => import('./viewer/viewer').then((m) => m.Viewer), { ssr: false }));

export function JobView({ jobId }: { jobId: string }) {
  const reduced = useReducedMotion() ?? false;
  const runtime = useMemo(() => createRuntime(), []);
  const [job, setJob] = useState<Job | null>(null);
  const [unknown, setUnknown] = useState(false);
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [loops, setLoops] = useState<LoopClosureEvent[]>([]);
  const [fpsHistory, setFpsHistory] = useState<number[]>([]);
  const [scene, setScene] = useState<SceneData | null>(null);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const [pulseKey, setPulseKey] = useState(0);
  const [panelOpen, setPanelOpen] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const loadingScene = useRef(false);
  const announcedAt = useRef(-1);

  // Rendered on the client only: formatting an ISO instant in the viewer's own timezone is
  // exactly the kind of thing that makes a server-rendered string disagree with the browser.
  const startedAt = useMemo(() => {
    if (!job?.created_at) return null;
    const d = new Date(job.created_at);
    return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }, [job?.created_at]);

  const run = useMemo(() => findRun(jobId), [jobId]);
  const otherRuns = useMemo(() => listRuns().filter((r) => r.job_id !== jobId), [jobId]);

  const refresh = useCallback(async () => {
    try {
      const j = await getJob(jobId);
      setJob(j);
    } catch (e: unknown) {
      // Only a definitive 404 means "no such job" — a flaky network must not wipe the screen.
      if (e instanceof ApiError && (e.code === 'JOB_NOT_FOUND' || e.status === 404)) {
        setUnknown(true);
        setJob(null);
      }
    }
  }, [jobId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handlers = useMemo<SseHandlers>(
    () => ({
      'job.snapshot': (d) => setJob(d as Job),
      'job.stage': (d) => {
        const s = d as StageEvent;
        setStageMessage(s.message);
        setJob((j) => (j ? { ...j, status: s.stage } : j));
      },
      'job.progress': (d) => {
        const p = d as ProgressEvent;
        setProgress(p);
        if (p.fps > 0) setFpsHistory((h) => (h.length > 59 ? [...h.slice(-59), p.fps] : [...h, p.fps]));
      },
      'job.loop_closure': (d) => {
        const l = d as LoopClosureEvent;
        setLoops((ls) => [...ls, l]);
        setPulseKey((k) => k + 1);
        runtime.pulseAt = performance.now();
        runtime.invalidate?.();
        toast.success('Loop closed', {
          description: `Keyframe ${l.from_kf} recognised keyframe ${l.to_kf} — ${l.inliers} inliers. Drift folds back through the pose graph.`,
          icon: <Link2 size={16} />,
        });
      },
      'job.completed': (d) => {
        const c = d as CompletedEvent;
        setJob((j) => (j ? { ...j, status: 'completed', metrics: c.metrics, queue_position: null } : j));
      },
      'job.failed': (d) => {
        const f = d as FailedEvent;
        setJob((j) => (j ? { ...j, status: 'failed', error: f.error, queue_position: null } : j));
      },
      ping: () => undefined,
    }),
    [runtime],
  );

  const terminal = job?.status === 'completed' || job?.status === 'failed';
  const connection = useEventSource(job && !terminal ? eventsUrl(jobId) : null, handlers, {
    enabled: Boolean(job) && !terminal,
    poll: refresh,
  });

  // Completion can arrive over SSE or via the polling fallback; both land here.
  useEffect(() => {
    if (job?.status !== 'completed' || scene || loadingScene.current) return;
    loadingScene.current = true;
    getReconstruction(jobId)
      .then((r) => {
        setScene(buildSceneData(r));
        setJob((j) => (j && !j.metrics ? { ...j, metrics: r.metrics } : j));
        // Replay the closures once the geometry exists, so the arcs announce themselves.
        if (r.loop_closures.length > 0) runtime.pulseAt = performance.now();
      })
      .catch((e: unknown) => setSceneError(explain(e).title))
      .finally(() => {
        loadingScene.current = false;
      });
  }, [job?.status, jobId, scene, runtime]);

  useEffect(() => {
    if (!job) return;
    const total = progress?.frames_total ?? job.video?.frame_count ?? 0;
    const done = progress?.frames_done ?? 0;
    const decile = total > 0 ? Math.floor((done / total) * 10) : -1;
    if (job.status === 'completed') {
      if (announcedAt.current !== 100) {
        announcedAt.current = 100;
        setAnnouncement('Reconstruction complete. The 3D map is ready to explore.');
      }
      return;
    }
    if (decile !== announcedAt.current) {
      announcedAt.current = decile;
      setAnnouncement(`${job.status}: ${int(done)} of ${int(total)} frames reconstructed.`);
    }
  }, [job, progress]);

  if (unknown) return <UnknownJobState jobId={jobId} />;

  if (job?.status === 'queued') {
    return (
      <>
        <Aurora state="processing" />
        <QueuedState position={job.queue_position ?? 0} />
      </>
    );
  }

  if (job?.status === 'failed') {
    return (
      <>
        <Aurora state="failed" />
        <FailedState error={job.error} onRetry={null} />
      </>
    );
  }

  const processing = Boolean(job) && !terminal;
  const metrics = job?.metrics ?? null;

  const panelBody = (
    <div className="space-y-4">
      {processing && job ? (
        <Telemetry job={job} progress={progress} stageMessage={stageMessage} fpsHistory={fpsHistory} loops={loops} />
      ) : null}
      {metrics ? <MetricsPanel metrics={metrics} videoDurationS={job?.video?.duration_s ?? null} /> : null}
      {job?.status === 'completed' ? (
        <div className="border-t border-line pt-3">
          <h3 className="mb-2 text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Exports</h3>
          <Exports jobId={jobId} />
        </div>
      ) : null}
      {otherRuns.length > 0 ? (
        <div className="border-t border-line pt-3">
          <h3 className="mb-2 text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Other runs this session</h3>
          <ul className="space-y-1">
            {otherRuns.map((r) => (
              <li key={r.job_id}>
                <Link
                  href={`/j/${r.job_id}`}
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-ink-muted transition-colors hover:bg-raised hover:text-ink"
                >
                  <span className="min-w-0 flex-1 truncate">{r.label}</span>
                  <Badge tone={r.loop_closure ? 'accent' : 'warn'}>{r.loop_closure ? 'closed' : 'no closure'}</Badge>
                  <ChevronRight size={13} aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[0.65rem] leading-snug text-ink-faint">
            Same clip with and without loop closure? Compare the drift block and the shape of the trajectory.
          </p>
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="relative min-h-dvh">
      <Aurora state={job?.status === 'completed' ? 'done' : 'processing'} />

      <div className="sr-only" aria-live="polite" role="status">
        {announcement}
      </div>

      <div className="relative z-10 mx-auto w-full max-w-[110rem] px-3 pb-12 sm:px-5">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-2 py-4">
          <Link
            href="/"
            className="flex items-center gap-1.5 text-xs text-ink-faint transition-colors hover:text-accent"
            aria-label="Back to upload"
          >
            <ArrowLeft size={14} aria-hidden />
            <span className="hidden sm:inline">Driftless</span>
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold text-ink">{job?.filename ?? 'Loading…'}</h1>
            <p className="num truncate text-[0.68rem] text-ink-faint">
              {jobId}
              {/* Source geometry only. Frame count and duration are already carried by the
                  playback counter and the realtime-factor sentence. */}
              {job?.video ? (
                <>
                  <span className="mx-1.5">·</span>
                  {job.video.width}×{job.video.height}
                  <span className="mx-1.5">·</span>
                  {fixed(job.video.fps, 0)} fps
                </>
              ) : null}
              {startedAt ? (
                <>
                  <span className="mx-1.5">·</span>
                  {startedAt}
                </>
              ) : null}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {run && !run.loop_closure ? <Badge tone="warn">loop closure off</Badge> : null}
            {job?.truncated ? <Badge tone="warn">truncated</Badge> : null}
            <ConnectionChip status={connection} processing={processing} />
            <Badge tone={job?.status === 'completed' ? 'ok' : 'accent'}>{job?.status ?? 'loading'}</Badge>
          </div>
          <div className="w-full lg:max-w-2xl">
            <PipelineRail status={job?.status ?? 'queued'} />
          </div>
        </header>

        {job?.truncated ? (
          <p className="mb-3 flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              This clip was longer than the frame cap. Only the first{' '}
              <span className="num">{int(job.metrics?.frames_processed ?? progress?.frames_total ?? 0)}</span> frames were
              reconstructed — the map covers the start of the clip, not all of it.
            </span>
          </p>
        ) : null}

        <div className="relative h-[calc(100dvh-13rem)] min-h-[26rem] overflow-hidden rounded-[var(--radius-card)] border border-line">
          {scene ? (
            <MemoViewer data={scene} runtime={runtime} flyIn={!reduced} className="h-full w-full" />
          ) : sceneError ? (
            <div className="flex h-full items-center justify-center px-6 text-center">
              <div>
                <p className="text-sm text-ink">The reconstruction could not be loaded.</p>
                <p className="mt-1 text-xs text-ink-faint">{sceneError}</p>
                <Button variant="subtle" size="sm" className="mt-3" onClick={() => void refresh()}>
                  Retry
                </Button>
              </div>
            </div>
          ) : (
            <ViewerSkeleton
              mapPoints={progress?.map_points ?? 0}
              keyframes={progress?.keyframes ?? 0}
              pulseKey={pulseKey}
              stage={job?.status ?? 'starting'}
            />
          )}

          {/* Docked panel, large screens */}
          <div className="pointer-events-none absolute top-3 right-3 bottom-20 hidden w-[21rem] lg:block">
            <AnimatePresence initial={false}>
              {panelOpen ? (
                <motion.div
                  key="panel"
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
                  className="pointer-events-auto flex h-full flex-col overflow-hidden rounded-[var(--radius-card)] border border-line bg-surface/90 backdrop-blur-md"
                >
                  <div className="flex items-center gap-2 border-b border-line px-3 py-2">
                    <Gauge size={14} className="text-accent" aria-hidden />
                    <h2 className="flex-1 text-xs font-semibold tracking-wide text-ink">
                      {processing ? 'Live telemetry' : 'Metrics'}
                    </h2>
                    <button
                      type="button"
                      onClick={() => setPanelOpen(false)}
                      aria-label="Collapse metrics panel"
                      className="rounded-md p-1 text-ink-faint hover:bg-raised hover:text-ink"
                    >
                      <X size={14} aria-hidden />
                    </button>
                  </div>
                  <div className="thin-scroll flex-1 overflow-y-auto p-3">{panelBody}</div>
                </motion.div>
              ) : (
                <motion.button
                  key="collapsed"
                  type="button"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  onClick={() => setPanelOpen(true)}
                  className="pointer-events-auto ml-auto flex items-center gap-2 rounded-lg border border-line bg-surface/90 px-3 py-2 text-xs text-ink-muted backdrop-blur-md hover:text-ink"
                >
                  <Gauge size={14} className="text-accent" aria-hidden />
                  {processing ? 'Telemetry' : 'Metrics'}
                </motion.button>
              )}
            </AnimatePresence>
          </div>

          {/* Small screens: the same panel as a bottom sheet */}
          <div className="absolute top-3 left-3 lg:hidden">
            <Button size="sm" variant="subtle" icon={Gauge} onClick={() => setSheetOpen(true)}>
              {processing ? 'Telemetry' : 'Metrics'}
            </Button>
          </div>
        </div>

        <AnimatePresence>
          {sheetOpen ? (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setSheetOpen(false)}
                className="fixed inset-0 z-40 bg-void/70 lg:hidden"
                aria-hidden
              />
              <motion.div
                role="dialog"
                aria-label={processing ? 'Live telemetry' : 'Metrics'}
                initial={{ y: '100%' }}
                animate={{ y: 0 }}
                exit={{ y: '100%' }}
                transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
                className="thin-scroll fixed inset-x-0 bottom-0 z-50 max-h-[82dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-surface px-4 pt-3 pb-8 lg:hidden"
              >
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold">{processing ? 'Live telemetry' : 'Metrics'}</h2>
                  <Button size="sm" variant="ghost" icon={X} onClick={() => setSheetOpen(false)} aria-label="Close panel">
                    Close
                  </Button>
                </div>
                {panelBody}
              </motion.div>
            </>
          ) : null}
        </AnimatePresence>

        <section className="mt-4 space-y-3">
          {scene ? <AccessibleTable data={scene} metrics={metrics} /> : null}
          <Panel className="p-4">
            <h2 className="text-xs font-semibold tracking-wide text-ink">Reading this map honestly</h2>
            <ul className="mt-2 space-y-1.5 text-[0.72rem] leading-relaxed text-ink-faint">
              <li>
                Distances are <span className="text-ink-muted">up to scale</span>. A single camera cannot recover metric
                scale, so every length is in arbitrary units that are consistent with each other and nothing else.
              </li>
              <li>
                The world frame is the first keyframe&apos;s camera. Nothing is gravity-aligned, so the reference grid is
                a display aid placed at the 3rd percentile of the cloud height, not a measured floor.
              </li>
              <li>
                The cloud is capped at 60 000 points for the web payload. The PLY export carries the full map.
              </li>
            </ul>
          </Panel>
        </section>
      </div>
    </div>
  );
}

function ConnectionChip({ status, processing }: { status: string; processing: boolean }) {
  if (!processing) return null;
  if (status === 'open' || status === 'connecting') {
    return (
      <span className="flex items-center gap-1.5 text-[0.68rem] text-ink-faint">
        <RadioTower size={12} className="text-ok" aria-hidden />
        live
      </span>
    );
  }
  const polling = status === 'polling';
  return (
    <span
      className={cx(
        'flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.68rem]',
        polling ? 'border-warn/40 bg-warn/10 text-warn' : 'border-line bg-raised text-ink-muted',
      )}
      role="status"
    >
      <WifiOff size={11} aria-hidden />
      {polling ? 'stream lost — polling' : 'reconnecting…'}
    </span>
  );
}
