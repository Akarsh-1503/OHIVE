'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Link2, Loader2 } from 'lucide-react';
import { int } from '@/lib/format';

/**
 * Shown while the job is still running. The SSE stream carries counters, not geometry, so
 * this deliberately does not pretend to draw a partial map — it says what it is waiting for.
 */
export function ViewerSkeleton({
  mapPoints,
  keyframes,
  pulseKey,
  stage,
}: {
  mapPoints: number;
  keyframes: number;
  pulseKey: number;
  stage: string;
}) {
  return (
    <div className="relative h-full w-full overflow-hidden bg-void">
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(120% 90% at 50% 10%, oklch(0.24 0.05 250 / 0.9), transparent 60%), radial-gradient(90% 70% at 20% 100%, oklch(0.22 0.07 285 / 0.6), transparent 65%)',
        }}
      />

      {/* Receding wireframe floor: cheap, and it reads as "3D space, nothing in it yet". */}
      <div aria-hidden className="absolute inset-x-0 bottom-0 h-[60%] [perspective:420px]">
        <motion.div
          className="absolute inset-0 origin-bottom"
          style={{
            transform: 'rotateX(72deg)',
            backgroundImage:
              'linear-gradient(to right, oklch(0.42 0.08 235 / 0.35) 1px, transparent 1px), linear-gradient(to bottom, oklch(0.42 0.08 235 / 0.35) 1px, transparent 1px)',
            backgroundSize: '56px 56px',
            maskImage: 'linear-gradient(to top, black, transparent 78%)',
          }}
          animate={{ backgroundPositionY: ['0px', '56px'] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: 'linear' }}
        />
      </div>

      <motion.div
        aria-hidden
        className="absolute inset-x-0 h-px bg-linear-to-r from-transparent via-accent to-transparent"
        animate={{ top: ['12%', '78%', '12%'], opacity: [0, 0.8, 0] }}
        transition={{ duration: 4.2, repeat: Infinity, ease: 'easeInOut' }}
      />

      <div className="absolute inset-0 flex flex-col items-center justify-center px-6 text-center">
        <Loader2 size={22} className="animate-spin text-accent" aria-hidden />
        <p className="mt-3 text-sm font-medium text-ink">Reconstructing — {stage}</p>
        <p className="num mt-1.5 text-xs text-ink-muted">
          {int(keyframes)} keyframes · {int(mapPoints)} map points so far
        </p>
        <p className="mt-3 max-w-sm text-[0.7rem] leading-relaxed text-ink-faint">
          The progress stream carries counters, not geometry. The map arrives as one payload when optimisation
          finishes, and the camera flies in on it.
        </p>
      </div>

      <AnimatePresence>
        {pulseKey > 0 ? (
          <motion.div
            key={pulseKey}
            initial={{ opacity: 0.85, scale: 0.35 }}
            animate={{ opacity: 0, scale: 1.6 }}
            transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }}
            className="pointer-events-none absolute inset-0 m-auto flex h-64 w-64 items-center justify-center rounded-full border-2 border-ok"
          >
            <span className="flex items-center gap-1.5 rounded-full border border-ok/50 bg-void/80 px-3 py-1 text-xs text-ok">
              <Link2 size={12} aria-hidden />
              loop closed
            </span>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
