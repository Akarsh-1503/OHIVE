'use client';

import { useId } from 'react';

/** Tiny fps history plot. Pure SVG, redrawn only when a progress event lands. */
export function Sparkline({
  values,
  width = 260,
  height = 40,
  label,
}: {
  values: number[];
  width?: number;
  height?: number;
  label: string;
}) {
  const id = useId();
  if (values.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed border-line/70 text-[0.62rem] text-ink-faint"
        style={{ height }}
        role="img"
        aria-label={`${label}: waiting for data`}
      >
        waiting for throughput…
      </div>
    );
  }

  // Windowed to the observed range, not to zero: tracking fps varies by a few percent and a
  // zero-based axis would draw that as a flat line.
  const hi = Math.max(...values);
  const lo = Math.min(...values);
  const pad = Math.max(0.5, (hi - lo) * 0.35);
  const top = hi + pad;
  const span = top - (lo - pad) || 1;
  const step = width / (values.length - 1);
  const y = (v: number) => height - ((v - (lo - pad)) / span) * height;
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`);
  const line = `M${pts.join(' L')}`;
  const area = `${line} L${width},${height} L0,${height} Z`;
  const last = values[values.length - 1] ?? 0;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full"
      style={{ height }}
      role="img"
      aria-label={`${label}: currently ${last.toFixed(1)}, peak ${hi.toFixed(1)}`}
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id}-fill)`} />
      <path d={line} fill="none" stroke="var(--color-accent)" strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx={width} cy={y(last)} r="2.2" fill="var(--color-accent)" />
    </svg>
  );
}
