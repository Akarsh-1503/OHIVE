'use client';

import { Table } from 'lucide-react';
import { fixed, int } from '@/lib/format';
import type { SceneData } from '@/lib/recon';
import type { Metrics } from '@/lib/types';

/**
 * Text alternative to the canvas. A WebGL scene cannot be made accessible, so the same
 * numbers it draws — every keyframe pose, and the metric block — are published as a real
 * table that a screen reader can walk.
 */
export function AccessibleTable({ data, metrics }: { data: SceneData; metrics: Metrics | null }) {
  const rows = Array.from(data.keyframeIndices).map((frame) => {
    const p = data.poses[frame]!;
    return {
      frame,
      t: p.t_s,
      x: p.position[0],
      y: p.position[1],
      z: p.position[2],
      tracked: p.tracked_points,
      err: p.reprojection_error_px,
    };
  });

  return (
    <details className="group rounded-[var(--radius-card)] border border-line bg-surface/60">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm text-ink-muted transition-colors hover:text-ink">
        <Table size={14} aria-hidden />
        Trajectory and metrics as a table
        <span className="num ml-auto text-xs text-ink-faint">{rows.length} keyframes</span>
      </summary>
      <div className="border-t border-line p-4">
        <p className="mb-3 text-xs leading-relaxed text-ink-faint">
          Keyframe poses in the reconstruction&apos;s own frame (world = first keyframe camera), positions in
          up-to-scale units, quaternions omitted for readability — the TUM export carries them.
        </p>
        <div className="thin-scroll max-h-80 overflow-auto rounded-lg border border-line">
          <table className="w-full min-w-[34rem] border-collapse text-left">
            <caption className="sr-only">Keyframe poses: frame index, timestamp, position, tracked points and reprojection error</caption>
            <thead className="sticky top-0 bg-raised">
              <tr className="text-[0.62rem] tracking-[0.08em] text-ink-faint uppercase">
                <th scope="col" className="px-3 py-2 font-medium">frame</th>
                <th scope="col" className="px-3 py-2 font-medium">t (s)</th>
                <th scope="col" className="px-3 py-2 font-medium">x</th>
                <th scope="col" className="px-3 py-2 font-medium">y</th>
                <th scope="col" className="px-3 py-2 font-medium">z</th>
                <th scope="col" className="px-3 py-2 font-medium">tracked</th>
                <th scope="col" className="px-3 py-2 font-medium">reproj px</th>
              </tr>
            </thead>
            <tbody className="num text-xs">
              {rows.map((r) => (
                <tr key={r.frame} className="border-t border-line/60 text-ink-muted odd:bg-void/30">
                  <td className="px-3 py-1.5">{r.frame}</td>
                  <td className="px-3 py-1.5">{fixed(r.t, 2)}</td>
                  <td className="px-3 py-1.5">{fixed(r.x, 3)}</td>
                  <td className="px-3 py-1.5">{fixed(r.y, 3)}</td>
                  <td className="px-3 py-1.5">{fixed(r.z, 3)}</td>
                  <td className="px-3 py-1.5">{int(r.tracked)}</td>
                  <td className="px-3 py-1.5">{fixed(r.err, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {metrics ? (
          <table className="mt-4 w-full text-left">
            <caption className="sr-only">Reconstruction metrics</caption>
            <tbody className="num text-xs">
              {(
                [
                  ['wall_ms', int(metrics.wall_ms)],
                  ['queue_wait_ms', int(metrics.queue_wait_ms)],
                  ['processing_fps', fixed(metrics.processing_fps, 1)],
                  ['realtime_factor', fixed(metrics.realtime_factor, 2)],
                  ['keyframes', int(metrics.keyframes)],
                  ['map_points', int(metrics.map_points)],
                  ['mean_reprojection_error_px', fixed(metrics.mean_reprojection_error_px, 2)],
                  ['median_track_length', int(metrics.median_track_length)],
                  ['loop_closures', int(metrics.loop_closures)],
                  ['pre_optimization_loop_error_m', fixed(metrics.drift.pre_optimization_loop_error_m, 3)],
                  ['post_optimization_loop_error_m', fixed(metrics.drift.post_optimization_loop_error_m, 3)],
                  ['reduction_pct', fixed(metrics.drift.reduction_pct, 1)],
                  ['scale_drift_ratio', fixed(metrics.drift.scale_drift_ratio, 3)],
                  ['trajectory_length_m', fixed(metrics.trajectory_length_m, 2)],
                ] as const
              ).map(([k, v]) => (
                <tr key={k} className="border-t border-line/60">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink-faint">
                    {k}
                  </th>
                  <td className="py-1.5 text-ink">{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </details>
  );
}
