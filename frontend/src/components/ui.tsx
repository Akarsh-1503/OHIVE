'use client';

import { motion, useMotionValue, useReducedMotion, useSpring } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import { ChevronDown } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

/* ------------------------------- Button ------------------------------- */

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'subtle' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  icon?: LucideIcon;
  disabled?: boolean;
  type?: 'button' | 'submit';
  title?: string;
  className?: string;
  'aria-pressed'?: boolean;
  'aria-label'?: string;
};

export function Button({
  children,
  onClick,
  variant = 'subtle',
  size = 'md',
  icon: Icon,
  disabled,
  type = 'button',
  title,
  className,
  ...aria
}: ButtonProps) {
  const base =
    'relative inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors duration-150 disabled:opacity-40 disabled:pointer-events-none select-none';
  const sizes = { sm: 'h-8 px-3 text-xs', md: 'h-10 px-4 text-sm', lg: 'h-12 px-6 text-[0.95rem]' };
  const variants = {
    primary:
      'text-void bg-linear-to-r from-accent to-accent-2 hover:brightness-110 shadow-[0_6px_24px_-8px_var(--color-accent)]',
    subtle: 'bg-raised text-ink border border-line hover:border-accent/60 hover:bg-raised/80',
    ghost: 'text-ink-muted hover:text-ink hover:bg-raised/70',
    danger: 'bg-bad/15 text-bad border border-bad/40 hover:bg-bad/25',
  };
  return (
    <motion.button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      whileTap={{ scale: 0.97 }}
      transition={{ duration: 0.12 }}
      className={cx(base, sizes[size], variants[variant], className)}
      {...aria}
    >
      {Icon ? <Icon size={size === 'sm' ? 14 : 16} strokeWidth={2} aria-hidden /> : null}
      {children}
    </motion.button>
  );
}

/* -------------------------------- Panel ------------------------------- */

export function Panel({
  children,
  className,
  as = 'section',
}: {
  children: ReactNode;
  className?: string;
  as?: 'section' | 'div' | 'aside';
}) {
  const Tag = as;
  return (
    <Tag
      className={cx(
        'rounded-[var(--radius-card)] border border-line bg-surface/80 backdrop-blur-[2px] shadow-[0_18px_50px_-30px_rgba(0,0,0,0.9)]',
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function PanelHeader({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
      <div>
        <h2 className="text-sm font-semibold tracking-tight text-ink">{title}</h2>
        {sub ? <p className="mt-0.5 text-xs text-ink-faint">{sub}</p> : null}
      </div>
      {right}
    </header>
  );
}

/* -------------------------------- Badge ------------------------------- */

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'bad';
  className?: string;
}) {
  const tones = {
    neutral: 'border-line bg-raised text-ink-muted',
    accent: 'border-accent/40 bg-accent/10 text-accent',
    ok: 'border-ok/40 bg-ok/10 text-ok',
    warn: 'border-warn/40 bg-warn/10 text-warn',
    bad: 'border-bad/40 bg-bad/10 text-bad',
  };
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.68rem] font-medium tracking-wide uppercase',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ---------------------------- Rolling number --------------------------- */

/**
 * Numbers settle with a spring instead of flashing. The spring writes straight into the DOM
 * node — React never re-renders on stream ticks.
 */
export function RollingNumber({
  value,
  format,
  className,
}: {
  value: number;
  format: (v: number) => string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const formatRef = useRef(format);
  formatRef.current = format;
  const reduced = useReducedMotion();
  const raw = useMotionValue(value);
  const spring = useSpring(raw, { stiffness: 170, damping: 26, mass: 0.7 });

  useEffect(() => {
    if (reduced) {
      if (ref.current) ref.current.textContent = formatRef.current(value);
      return;
    }
    raw.set(value);
  }, [value, raw, reduced]);

  useEffect(() => {
    if (reduced) return;
    return spring.on('change', (v) => {
      if (ref.current) ref.current.textContent = formatRef.current(v);
    });
  }, [spring, reduced]);

  return (
    <span ref={ref} className={cx('num', className)}>
      {format(value)}
    </span>
  );
}

/* -------------------------------- Stat -------------------------------- */

export function Stat({
  label,
  value,
  unit,
  tone,
  hint,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  tone?: 'accent' | 'ok' | 'warn' | 'bad';
  hint?: string;
}) {
  const toneClass =
    tone === 'ok' ? 'text-ok' : tone === 'warn' ? 'text-warn' : tone === 'bad' ? 'text-bad' : tone === 'accent' ? 'text-accent' : 'text-ink';
  return (
    <div className="min-w-0">
      <div className="text-[0.66rem] font-medium tracking-[0.09em] text-ink-faint uppercase">{label}</div>
      <div className={cx('mt-1 flex items-baseline gap-1', toneClass)}>
        <span className="num text-[1.35rem] leading-none font-semibold">{value}</span>
        {unit ? <span className="num text-xs text-ink-faint">{unit}</span> : null}
      </div>
      {hint ? <div className="mt-1 text-[0.68rem] text-ink-faint">{hint}</div> : null}
    </div>
  );
}

/* ------------------------------- Toggle ------------------------------- */

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <label htmlFor={id} className="min-w-0 cursor-pointer select-none">
        <span className="block text-sm text-ink">{label}</span>
        {hint ? <span className="mt-0.5 block text-xs text-ink-faint">{hint}</span> : null}
      </label>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          'relative mt-0.5 h-5 w-9 shrink-0 rounded-full border transition-colors duration-200',
          checked ? 'border-accent/70 bg-accent/30' : 'border-line bg-raised',
          disabled && 'opacity-40',
        )}
      >
        <motion.span
          layout
          transition={{ type: 'spring', stiffness: 500, damping: 34 }}
          className={cx(
            'absolute top-[2px] h-3.5 w-3.5 rounded-full',
            checked ? 'right-[2px] bg-accent' : 'left-[2px] bg-ink-faint',
          )}
        />
      </button>
    </div>
  );
}

/* ------------------------------- Slider ------------------------------- */

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  display,
  'aria-label': ariaLabel,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  display?: string;
  'aria-label'?: string;
}) {
  const id = useId();
  return (
    <div className="py-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-xs text-ink-muted">
          {label}
        </label>
        <span className="num text-xs text-ink-faint">{display ?? value}</span>
      </div>
      <input
        id={id}
        type="range"
        className="mt-1 w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={ariaLabel ?? label}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

/* ----------------------------- Segmented ------------------------------ */

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string; title?: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-lg border border-line bg-raised/60 p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            onClick={() => onChange(o.value)}
            className="relative flex-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors duration-150"
          >
            {active ? (
              <motion.span
                layoutId={`seg-${label}`}
                transition={{ type: 'spring', stiffness: 420, damping: 36 }}
                className="absolute inset-0 rounded-md border border-accent/40 bg-accent/15"
              />
            ) : null}
            <span className={cx('relative z-10', active ? 'text-accent' : 'text-ink-muted hover:text-ink')}>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ---------------------------- Collapsible ----------------------------- */

export function Collapsible({
  title,
  children,
  defaultOpen = false,
  right,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  right?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className="rounded-[var(--radius-card)] border border-line bg-surface/60">
      <div className="flex items-center">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((o) => !o)}
          className="flex flex-1 items-center gap-2 px-4 py-3 text-left text-sm text-ink-muted transition-colors hover:text-ink"
        >
          <ChevronDown
            size={15}
            className="transition-transform duration-200"
            style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }}
            aria-hidden
          />
          {title}
        </button>
        {right ? <div className="pr-3">{right}</div> : null}
      </div>
      <motion.div
        id={id}
        initial={false}
        animate={{ height: open ? 'auto' : 0, opacity: open ? 1 : 0 }}
        transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
        className="overflow-hidden"
      >
        <div className="border-t border-line px-4 py-3">{children}</div>
      </motion.div>
    </div>
  );
}
