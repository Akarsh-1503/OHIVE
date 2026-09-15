'use client';

import { motion } from 'framer-motion';
import { Link2, Zap } from 'lucide-react';
import { int, ms } from '@/lib/format';
import type { Job, LoopClosureEvent, ProgressEvent } from '@/lib/types';
import { Sparkline } from './sparkline';
import { Badge, RollingNumber, Stat } from './ui';

export function Telemetry({
  job,
  progress,
  stageMessage,
  fpsHistory,
  loops,
}: {
  job: Job;
  progress: ProgressEvent | null;
  stageMessage: string | null;
  fpsHistory: number[];
  loops: LoopClosureEvent[];
}) {
  const done = progress?.frames_done ?? 0;
  const total = progress?.frames_total ?? job.video?.frame_count ?? 0;
  const fraction = total > 0 ? Math.min(1, done / total) : 0;
  const elapsed = progress?.elapsed_ms ?? 0;
  const videoFps = job.video?.fps ?? 30;
  // Projected, not final: seconds of footage reconstructed per second of wall clock so far.
  const liveRealtime = elapsed > 0 ? done / videoFps / (elapsed / 1000) : 0;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Frames reconstructed</p>
          <p className="num text-xs text-ink-muted">
            <RollingNumber value={done} format={(v) => int(v)} />
            <span className="text-ink-faint"> / {int(total)}</span>
          </p>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-raised">
          <motion.div
            className="h-full rounded-full bg-linear-to-r from-accent to-accent-2"
            initial={false}
            animate={{ width: `${fraction * 100}%` }}
            transition={{ duration: 0.15, ease: 'linear' }}
          />
        </div>
        {stageMessage ? <p className="mt-2 text-xs leading-snug text-ink-faint">{stageMessage}</p> : null}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-4">
        <Stat
          label="Processing"
          value={<RollingNumber value={progress?.fps ?? 0} format={(v) => v.toFixed(1)} />}
          unit="fps"
          tone="accent"
        />
        <Stat
          label="Realtime (live)"
          value={<RollingNumber value={liveRealtime} format={(v) => `${v.toFixed(2)}×`} />}
          tone={liveRealtime >= 1 ? 'ok' : 'warn'}
        />
        <Stat label="Keyframes" value={<RollingNumber value={progress?.keyframes ?? 0} format={(v) => int(v)} />} />
        <Stat label="Map points" value={<RollingNumber value={progress?.map_points ?? 0} format={(v) => int(v)} />} />
        <Stat label="Elapsed" value={<RollingNumber value={elapsed} format={(v) => ms(v)} />} />
        <Stat
          label="Closures"
          value={<RollingNumber value={progress?.loop_closures ?? loops.length} format={(v) => int(v)} />}
          tone={loops.length > 0 ? 'ok' : undefined}
        />
      </div>

      <div>
        <p className="mb-1.5 flex items-center gap-1.5 text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">
          <Zap size={11} aria-hidden /> Throughput
        </p>
        <Sparkline values={fpsHistory} label="Tracking throughput in frames per second" />
      </div>

      {loops.length > 0 ? (
        <div>
          <p className="mb-1.5 text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Loop closures</p>
          <ul className="space-y-1">
            {loops.map((l, i) => (
              <motion.li
                key={`${l.from_kf}-${l.to_kf}-${i}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ type: 'spring', stiffness: 380, damping: 26 }}
                className="num flex items-center gap-2 rounded-md border border-ok/30 bg-ok/8 px-2 py-1.5 text-[0.7rem] text-ok"
              >
                <Link2 size={12} aria-hidden />
                kf {l.from_kf} → kf {l.to_kf}
                <span className="ml-auto text-ink-faint">{l.inliers} inliers</span>
              </motion.li>
            ))}
          </ul>
        </div>
      ) : null}

      {job.truncated ? (
        <Badge tone="warn">
          truncated — first {int(total)} frames only
        </Badge>
      ) : null}
    </div>
  );
}
