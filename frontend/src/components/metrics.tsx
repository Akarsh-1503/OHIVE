'use client';

import {
  motion,
  useAnimationFrame,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
  type MotionValue,
} from 'framer-motion';
import { useEffect, useId, useRef, useState } from 'react';
import { cx } from './ui';

const SPRING = { stiffness: 120, damping: 24, mass: 0.7 } as const;

/** Metrics settle with a spring instead of snapping between renders. */
export function NumberRoll({
  value,
  format = (v) => Math.round(v).toString(),
  className,
}: {
  value: number;
  format?: (value: number) => string;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const spring = useSpring(value, SPRING);
  const text = useTransform(spring, format);

  useEffect(() => {
    spring.set(value);
  }, [spring, value]);

  if (reduce) return <span className={cx('tnum', className)}>{format(value)}</span>;
  return <motion.span className={cx('tnum', className)}>{text}</motion.span>;
}

/** Wall-clock elapsed, driven by rAF into a motion value — no re-render per frame. */
export function LiveElapsed({
  startedAt,
  frozenMs,
  running,
  className,
}: {
  startedAt: number;
  frozenMs: number;
  running: boolean;
  className?: string;
}) {
  const ms = useMotionValue(frozenMs);
  const text = useTransform(ms, formatDuration);

  useAnimationFrame(() => {
    if (!running || !startedAt) return;
    ms.set(Date.now() - startedAt);
  });

  useEffect(() => {
    if (!running) ms.set(frozenMs);
  }, [running, frozenMs, ms]);

  return <motion.span className={cx('tnum', className)}>{text}</motion.span>;
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}:${String(seconds).padStart(2, '0')}` : `${seconds}s`;
}

export function confidenceTone(value: number): 'ok' | 'accent' | 'warn' | 'bad' {
  if (value >= 0.85) return 'ok';
  // 0.72 is the review threshold the backend uses; below it a human should look.
  if (value >= 0.72) return 'accent';
  if (value > 0) return 'warn';
  return 'bad';
}

// High-confidence bars are deliberately quieter than low-confidence ones: the eye should
// land on the fields that need a human, not on the eight that are fine.
const TONE_VAR: Record<string, string> = {
  ok: 'color-mix(in oklch, var(--color-ok) 62%, transparent)',
  accent: 'color-mix(in oklch, var(--color-accent) 78%, transparent)',
  warn: 'var(--color-warn)',
  bad: 'var(--color-bad)',
};

export function ConfidenceRing({
  value,
  size = 42,
  stroke = 3,
  label,
}: {
  value: number;
  size?: number;
  stroke?: number;
  label?: string;
}) {
  const gradientId = useId();
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const spring = useSpring(0, { stiffness: 90, damping: 20 });
  const offset = useTransform(spring, (v) => circumference * (1 - v));

  useEffect(() => {
    spring.set(value);
  }, [spring, value]);

  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={label ?? `Confidence ${Math.round(value * 100)} percent`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" />
            <stop offset="100%" stopColor="var(--color-accent-2)" />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-line)"
          strokeWidth={stroke}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={value < 0.72 ? TONE_VAR.warn : `url(#${gradientId})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          style={{ strokeDashoffset: offset }}
        />
      </svg>
      <span className="tnum absolute inset-0 flex items-center justify-center text-[11px] font-medium text-ink">
        {Math.round(value * 100)}
      </span>
    </div>
  );
}

export function ConfidenceBar({ value, className }: { value: number; className?: string }) {
  const tone = confidenceTone(value);
  return (
    <div className={cx('h-[3px] w-full overflow-hidden rounded-full bg-line/70', className)}>
      <motion.div
        className="h-full rounded-full"
        // Scale from the left edge so the bar grows rather than centre-expanding.
        style={{ background: TONE_VAR[tone], transformOrigin: 'left' }}
        initial={{ scaleX: 0 }}
        animate={{ scaleX: Math.max(0.02, value) }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      />
    </div>
  );
}

/** Progress driven by a motion value the batch view writes into — never by re-render. */
export function ProgressBar({
  progress,
  className,
}: {
  progress: MotionValue<number>;
  className?: string;
}) {
  const width = useTransform(progress, (v) => `${Math.max(0, Math.min(1, v)) * 100}%`);
  return (
    <div className={cx('h-[3px] w-full overflow-hidden rounded-full bg-line/60', className)}>
      <motion.div
        style={{ width }}
        className="h-full rounded-full bg-[linear-gradient(90deg,var(--color-accent),var(--color-accent-2))]"
      />
    </div>
  );
}

/** Reads a motion value into text without re-rendering the parent. */
export function MotionNumber({
  value,
  format,
  className,
}: {
  value: MotionValue<number>;
  format: (value: number) => string;
  className?: string;
}) {
  const text = useTransform(value, format);
  return <motion.span className={cx('tnum', className)}>{text}</motion.span>;
}

/** Counts up once on mount — used for the hero stat strip. */
export function useMountedValue(target: number, delayMs = 0): number {
  const [value, setValue] = useState(0);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const timer = setTimeout(() => setValue(target), delayMs);
    return () => clearTimeout(timer);
  }, [target, delayMs]);
  return value;
}
