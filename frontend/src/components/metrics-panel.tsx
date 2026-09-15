'use client';

import { Check, Cpu, TriangleAlert } from 'lucide-react';
import { fixed, int, ms, pct } from '@/lib/format';
import type { Metrics } from '@/lib/types';
import { Badge, cx } from './ui';

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px]">
      <dt className="text-xs text-ink-muted">
        {label}
        {hint ? <span className="ml-1.5 text-[0.65rem] text-ink-faint">{hint}</span> : null}
      </dt>
      <dd className="num shrink-0 text-xs text-ink">{value}</dd>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line pt-3">
      <h3 className="mb-1 text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">{title}</h3>
      <dl>{children}</dl>
    </section>
  );
}

/** Wall-clock split as one stacked bar — the shape of the cost is the point, not the digits. */
function TimingBar({ m }: { m: Metrics }) {
  const known = m.decode_ms + m.tracking_ms + m.optimize_ms;
  const other = Math.max(0, m.wall_ms - known);
  const parts = [
    { label: 'decode', value: m.decode_ms, color: 'var(--color-accent)' },
    { label: 'track', value: m.tracking_ms, color: 'var(--color-accent-2)' },
    { label: 'optimise', value: m.optimize_ms, color: 'oklch(0.66 0.15 330)' },
    { label: 'other', value: other, color: 'var(--color-line)' },
  ].filter((p) => p.value > 0);
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return (
    <div className="mt-2">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-raised">
        {parts.map((p) => (
          <div key={p.label} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} title={`${p.label} ${ms(p.value)}`} />
        ))}
      </div>
      <ul className="num mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[0.65rem] text-ink-faint">
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: p.color }} aria-hidden />
            {p.label} {ms(p.value)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DriftBar({ m }: { m: Metrics }) {
  const pre = m.drift.pre_optimization_loop_error_m;
  const post = m.drift.post_optimization_loop_error_m;
  const max = Math.max(pre, post, 1e-6);
  const rows = [
    { label: 'before PGO', value: pre, color: 'var(--color-bad)' },
    { label: 'after PGO', value: post, color: 'var(--color-ok)' },
  ];
  return (
    <div className="mt-2 space-y-1.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2">
          <span className="w-20 shrink-0 text-[0.65rem] text-ink-faint">{r.label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised">
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{ width: `${Math.max(2, (r.value / max) * 100)}%`, background: r.color }}
            />
          </div>
          <span className="num w-16 shrink-0 text-right text-[0.68rem] text-ink">{r.value.toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

export function MetricsPanel({ metrics, videoDurationS }: { metrics: Metrics; videoDurationS: number | null }) {
  const wallS = metrics.wall_ms / 1000;
  // Requirement #6: a ~10 s clip must reconstruct in about 10 s of wall clock. Faster than
  // realtime (factor >= 1) is the pass condition; the raw seconds are shown next to it.
  const pass = metrics.realtime_factor >= 1;
  const noClosure = metrics.loop_closures === 0;

  return (
    <div className="space-y-3">
      <div
        className={cx(
          'rounded-[var(--radius-card)] border p-3',
          pass ? 'border-ok/40 bg-ok/8' : 'border-warn/40 bg-warn/8',
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Realtime factor</p>
            <p className={cx('num mt-1 text-3xl leading-none font-semibold', pass ? 'text-ok' : 'text-warn')}>
              {fixed(metrics.realtime_factor, 2)}×
            </p>
          </div>
          <Badge tone={pass ? 'ok' : 'warn'}>
            {pass ? <Check size={11} aria-hidden /> : <TriangleAlert size={11} aria-hidden />}
            {pass ? 'pass' : 'below target'}
          </Badge>
        </div>
        <p className="num mt-2 text-[0.7rem] leading-relaxed text-ink-muted">
          {videoDurationS !== null ? `${fixed(videoDurationS, 2)} s of video` : `${int(metrics.frames_processed)} frames`}{' '}
          reconstructed in {fixed(wallS, 2)} s wall clock.
        </p>
        <p className="mt-1 text-[0.65rem] leading-relaxed text-ink-faint">
          Target: a 10 s clip inside ~10 s, i.e. a factor of 1.00× or better. Queue wait is excluded from{' '}
          <span className="num">wall_ms</span> and reported separately below.
        </p>
      </div>

      <Group title="Performance">
        <Row label="wall_ms" value={ms(metrics.wall_ms)} hint="upload accepted → map ready" />
        <Row label="queue_wait_ms" value={ms(metrics.queue_wait_ms)} hint="excluded from wall_ms" />
        <Row label="processing_fps" value={`${fixed(metrics.processing_fps, 1)} fps`} hint="end to end" />
        <Row label="frames_processed" value={int(metrics.frames_processed)} />
        <TimingBar m={metrics} />
      </Group>

      <Group title="Map">
        <Row label="keyframes" value={int(metrics.keyframes)} />
        <Row label="map_points" value={int(metrics.map_points)} />
        <Row label="mean_reprojection_error_px" value={`${fixed(metrics.mean_reprojection_error_px, 2)} px`} />
        <Row label="median_track_length" value={`${int(metrics.median_track_length)} frames`} />
        <Row label="trajectory_length_m" value={`${fixed(metrics.trajectory_length_m, 2)}`} hint="up-to-scale units" />
        <Row label="ba_runs" value={int(metrics.ba_runs)} />
      </Group>

      <Group title="Drift">
        <Row label="loop_closures" value={int(metrics.loop_closures)} />
        <Row label="loop_candidates_checked" value={int(metrics.loop_candidates_checked)} />
        <Row label="scale_drift_ratio" value={`${fixed(metrics.drift.scale_drift_ratio, 3)}×`} hint="Sim(3) correction" />
        <Row label="reduction_pct" value={pct(metrics.drift.reduction_pct)} />
        <DriftBar m={metrics} />
        <p className="mt-2 text-[0.65rem] leading-relaxed text-ink-faint">
          {noClosure
            ? 'Loop closure was disabled for this run, so nothing corrected the accumulated error — the two bars are the same by definition.'
            : 'Residual distance between keyframes the pose graph decided are the same place, before and after Sim(3) optimisation. Up-to-scale units.'}
        </p>
      </Group>

      <Group title="Host">
        <div className="num flex items-start gap-2 pt-1 text-[0.7rem] text-ink-muted">
          <Cpu size={13} className="mt-0.5 shrink-0 text-ink-faint" aria-hidden />
          <span>
            {metrics.host.cpu} · {metrics.host.vcpu} vCPU · {metrics.host.ram_gb} GB RAM
          </span>
        </div>
      </Group>
    </div>
  );
}
