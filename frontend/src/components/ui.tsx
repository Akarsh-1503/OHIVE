'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Check } from 'lucide-react';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

type Variant = 'primary' | 'outline' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

const VARIANTS: Record<Variant, string> = {
  primary:
    'text-void font-semibold bg-[linear-gradient(100deg,var(--color-accent),var(--color-accent-2))] hover:brightness-110 shadow-[0_6px_28px_-10px_color-mix(in_oklch,var(--color-accent)_80%,transparent)]',
  outline: 'border border-line bg-surface/60 text-ink hover:bg-raised hover:border-ink-faint',
  ghost: 'text-ink-muted hover:text-ink hover:bg-raised/70',
  danger: 'border border-bad/50 text-bad hover:bg-bad/10',
};

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5 rounded-lg',
  md: 'h-10 px-4 text-sm gap-2 rounded-xl',
  lg: 'h-12 px-6 text-[15px] gap-2.5 rounded-xl',
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({
  variant = 'outline',
  size = 'md',
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      className={cx(
        'inline-flex select-none items-center justify-center whitespace-nowrap transition-[background,color,border-color,filter,opacity] duration-[120ms] disabled:pointer-events-none disabled:opacity-45',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'bad';
  children: ReactNode;
  className?: string;
}) {
  const tones: Record<string, string> = {
    neutral: 'border-line text-ink-muted bg-raised/50',
    accent: 'border-accent/40 text-accent bg-accent/10',
    ok: 'border-ok/40 text-ok bg-ok/10',
    warn: 'border-warn/45 text-warn bg-warn/10',
    bad: 'border-bad/45 text-bad bg-bad/10',
  };
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5 tracking-wide',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  indeterminate = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  indeterminate?: boolean;
}) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className="group flex cursor-pointer select-none items-center gap-2.5 text-sm text-ink-muted transition-colors hover:text-ink"
    >
      <span className="relative inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          className="peer absolute inset-0 cursor-pointer opacity-0"
        />
        <span
          aria-hidden
          className={cx(
            'pointer-events-none absolute inset-0 rounded-[6px] border transition-colors duration-[120ms]',
            checked || indeterminate
              ? 'border-transparent bg-[linear-gradient(120deg,var(--color-accent),var(--color-accent-2))]'
              : 'border-line bg-raised/60 group-hover:border-ink-faint',
            'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--color-accent)]',
          )}
        />
        {indeterminate && !checked ? (
          <span aria-hidden className="pointer-events-none relative h-0.5 w-2.5 rounded bg-void" />
        ) : null}
        {checked ? <Check aria-hidden className="relative h-3 w-3 text-void" strokeWidth={3.5} /> : null}
      </span>
      {label}
    </label>
  );
}

/** Minimal popover: click-outside + Escape + focus return. Anchored to the trigger. */
export function Popover({
  trigger,
  children,
  align = 'right',
  label,
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode;
  children: (props: { close: () => void }) => ReactNode;
  align?: 'left' | 'right';
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent): void => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative">
      {trigger({ open, toggle: () => setOpen((value) => !value) })}
      <AnimatePresence>
        {open ? (
          <motion.div
            role="dialog"
            aria-label={label}
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className={cx(
              'absolute top-[calc(100%+8px)] z-50 w-64 origin-top rounded-xl border border-line bg-surface p-3 shadow-[0_24px_60px_-20px_rgba(0,0,0,0.85)]',
              align === 'right' ? 'right-0' : 'left-0',
            )}
          >
            {children({ close: () => setOpen(false) })}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-ink-muted">
      {children}
    </div>
  );
}
