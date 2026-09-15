'use client';

import { AnimatePresence, motion, useMotionValue } from 'framer-motion';
import {
  ChevronLeft,
  ChevronRight,
  Copy,
  Maximize2,
  RotateCcw,
  Save,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { cardImageUrl, patchLead } from '@/lib/api';
import { useLead, useStore } from '@/lib/store';
import { FIELD_LABELS, LEAD_FIELDS, QUALITY_FLAG_LABELS, type LeadField } from '@/lib/types';
import { ConfidenceBar, ConfidenceRing, confidenceTone } from '../metrics';
import { Badge, Button, cx } from '../ui';

const LOW_CONFIDENCE = 0.72;

export function ReviewDrawer({
  cardId,
  onClose,
  onStep,
  position,
}: {
  cardId: string;
  onClose: () => void;
  onStep: (delta: 1 | -1) => void;
  position: { index: number; total: number };
}) {
  const lead = useLead(cardId);
  const store = useStore();
  const panelRef = useRef<HTMLElement>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  // Reset the edit buffer whenever the drawer moves to another card.
  useEffect(() => {
    setDrafts({});
  }, [cardId]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // preventScroll matters on mobile: focusing the panel otherwise nudges the visual
    // viewport and clips the drawer header.
    panelRef.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  const dirtyFields = Object.keys(drafts).filter(
    (field) => drafts[field] !== (lead?.[field as LeadField] ?? ''),
  ) as LeadField[];

  const save = useCallback(
    async (fields: LeadField[]) => {
      if (!lead || fields.length === 0) return;
      const patch: Partial<Record<LeadField, string | null>> = {};
      const rollback: Partial<Record<LeadField, string | null>> = {};
      for (const field of fields) {
        const next = drafts[field]?.trim() ?? '';
        patch[field] = next === '' ? null : next;
        rollback[field] = lead[field];
      }

      setSaving(true);
      store.patchLead(cardId, { ...patch, edited: true });
      try {
        const updated = await patchLead(cardId, patch);
        store.cardCompleted(updated);
        setDrafts((current) => {
          const next = { ...current };
          for (const field of fields) delete next[field];
          return next;
        });
        toast.success('Lead updated', {
          description: `${fields.map((f) => FIELD_LABELS[f]).join(', ')} saved.`,
        });
      } catch {
        store.patchLead(cardId, rollback);
        toast.error('Could not save', { description: 'The change was rolled back.' });
      } finally {
        setSaving(false);
      }
    },
    [cardId, drafts, lead, store],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      const typing = target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA';

      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        void save(dirtyFields);
        return;
      }
      if (typing) return;
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        onStep(1);
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        onStep(-1);
      }
      if (event.key === 'Tab') trapFocus(event, panelRef.current);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dirtyFields, onClose, onStep, save]);

  if (!lead) return null;

  const name = [lead.first_name, lead.last_name].filter(Boolean).join(' ') || lead.filename;

  return (
    <>
      <motion.div
        key="backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.22 }}
        onClick={onClose}
        className="fixed inset-0 z-40 bg-void/75 backdrop-blur-[3px]"
        aria-hidden
      />

      <motion.aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`Review ${name}`}
        initial={{ opacity: 0, y: 28 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 20, transition: { duration: 0.18 } }}
        transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1] }}
        className="fixed inset-0 z-50 flex flex-col border-line bg-surface outline-none sm:inset-y-0 sm:left-auto sm:right-0 sm:w-[min(1180px,94vw)] sm:border-l sm:shadow-[0_0_120px_-20px_rgba(0,0,0,0.9)]"
      >
        <header className="flex items-center gap-3 border-b border-line px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-[15px] font-medium tracking-tight text-ink">{name}</h2>
              {lead.status === 'needs_review' ? <Badge tone="warn">Needs review</Badge> : null}
              {lead.status === 'failed' ? <Badge tone="bad">Failed</Badge> : null}
              {lead.edited ? <Badge tone="accent">Edited</Badge> : null}
              {lead.duplicate_of ? (
                <Badge tone="neutral">
                  <Copy className="h-3 w-3" aria-hidden />
                  Duplicate
                </Badge>
              ) : null}
            </div>
            <p className="tnum truncate text-[11.5px] text-ink-muted">{lead.filename}</p>
          </div>

          <div className="flex items-center gap-1">
            <span className="tnum mr-1 hidden text-[12px] text-ink-muted sm:inline">
              {position.index + 1}/{position.total}
            </span>
            <IconButton label="Previous card (←)" onClick={() => onStep(-1)}>
              <ChevronLeft className="h-4 w-4" />
            </IconButton>
            <IconButton label="Next card (→)" onClick={() => onStep(1)}>
              <ChevronRight className="h-4 w-4" />
            </IconButton>
            <IconButton label="Close (Esc)" onClick={onClose}>
              <X className="h-4 w-4" />
            </IconButton>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-rows-[minmax(200px,38vh)_1fr] overflow-hidden sm:grid-cols-[1.15fr_minmax(380px,0.85fr)] sm:grid-rows-1">
          <ImageViewer cardId={cardId} alt={`Business card for ${name}`} />

          <div className="min-h-0 overflow-y-auto border-t border-line px-4 py-5 sm:border-l sm:border-t-0 sm:px-6">
            <div className="mb-5 flex items-center gap-4">
              <ConfidenceRing value={lead.overall_confidence} size={54} stroke={3.5} />
              <div className="min-w-0">
                <div className="text-[13px] text-ink">
                  Overall confidence{' '}
                  <span className="tnum font-medium">
                    {Math.round(lead.overall_confidence * 100)}%
                  </span>
                </div>
                <div className="tnum mt-0.5 text-[11.5px] text-ink-muted">
                  {lead.processing_ms ? `${(lead.processing_ms / 1000).toFixed(2)}s` : '—'} ·{' '}
                  {new Date(lead.created_at).toLocaleTimeString()}
                </div>
              </div>
            </div>

            {lead.quality_flags.length > 0 ? (
              <div className="mb-5 flex flex-wrap gap-1.5">
                {lead.quality_flags.map((flag) => (
                  <Badge key={flag} tone="warn">
                    {QUALITY_FLAG_LABELS[flag] ?? flag.replace(/_/g, ' ')}
                  </Badge>
                ))}
              </div>
            ) : null}

            {lead.status === 'failed' ? (
              <div className="mb-5 rounded-xl border border-bad/40 bg-bad/[0.06] p-3 text-[13px] leading-6 text-bad">
                {lead.error}
              </div>
            ) : null}

            <div className="space-y-2.5">
              {LEAD_FIELDS.map((field) => (
                <FieldRow
                  key={field}
                  field={field}
                  value={drafts[field] ?? lead[field] ?? ''}
                  confidence={lead.confidence[field] ?? 0}
                  dirty={dirtyFields.includes(field)}
                  onChange={(next) => setDrafts((current) => ({ ...current, [field]: next }))}
                  onCommit={() => void save(dirtyFields.includes(field) ? [field] : [])}
                />
              ))}
            </div>

            {lead.raw_text ? (
              <details className="group mt-5 rounded-xl border border-line bg-raised/30 p-3">
                <summary className="cursor-pointer list-none text-[11px] uppercase tracking-[0.16em] text-ink-muted transition-colors hover:text-ink">
                  Raw transcription
                </summary>
                <pre className="mt-3 whitespace-pre-wrap font-mono text-[11.5px] leading-5 text-ink-muted">
                  {lead.raw_text}
                </pre>
              </details>
            ) : null}
          </div>
        </div>

        <footer className="flex items-center gap-3 border-t border-line px-4 py-3 sm:px-6">
          <p className="hidden flex-1 text-[11.5px] text-ink-muted sm:block">
            <kbd className="tnum rounded border border-line px-1">←</kbd>{' '}
            <kbd className="tnum rounded border border-line px-1">→</kbd> move ·{' '}
            <kbd className="rounded border border-line px-1">Esc</kbd> close ·{' '}
            <kbd className="rounded border border-line px-1">⌘↵</kbd> save
          </p>
          <span className="flex-1 sm:hidden" />
          <AnimatePresence>
            {dirtyFields.length > 0 ? (
              <motion.span
                initial={{ opacity: 0, x: 6 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                className="tnum text-[12px] text-accent"
              >
                {dirtyFields.length} unsaved
              </motion.span>
            ) : null}
          </AnimatePresence>
          <Button
            variant="primary"
            size="sm"
            disabled={dirtyFields.length === 0 || saving}
            onClick={() => void save(dirtyFields)}
          >
            <Save className="h-3.5 w-3.5" aria-hidden />
            Save changes
          </Button>
        </footer>
      </motion.aside>
    </>
  );
}

function FieldRow({
  field,
  value,
  confidence,
  dirty,
  onChange,
  onCommit,
}: {
  field: LeadField;
  value: string;
  confidence: number;
  dirty: boolean;
  onChange: (next: string) => void;
  onCommit: () => void;
}) {
  const flagged = confidence > 0 && confidence < LOW_CONFIDENCE;
  const missing = confidence === 0 && value === '';
  const tone = confidenceTone(confidence);

  return (
    <div
      className={cx(
        'rounded-xl border px-3 py-2.5 transition-colors duration-200',
        flagged || missing ? 'border-warn/45 bg-warn/[0.05]' : 'border-line bg-raised/30',
        dirty && 'border-accent/60 bg-accent/[0.06]',
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <label
          htmlFor={`field-${field}`}
          className={cx(
            'text-[10.5px] font-medium uppercase tracking-[0.16em]',
            flagged || missing ? 'text-warn' : 'text-ink-muted',
          )}
        >
          {FIELD_LABELS[field]}
        </label>
        <span
          className={cx(
            'tnum text-[11px]',
            tone === 'ok' && 'text-ok',
            tone === 'accent' && 'text-accent',
            tone === 'warn' && 'text-warn',
            tone === 'bad' && 'text-ink-faint',
          )}
        >
          {confidence > 0 ? `${Math.round(confidence * 100)}%` : 'no read'}
        </span>
      </div>
      <input
        id={`field-${field}`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onCommit}
        placeholder="—"
        spellCheck={false}
        className={cx(
          'mt-1 w-full bg-transparent text-[14px] text-ink outline-none placeholder:text-ink-faint',
          field === 'phone' || field === 'email' || field === 'website' ? 'tnum' : '',
        )}
      />
      <ConfidenceBar value={confidence} className="mt-2" />
    </div>
  );
}

function ImageViewer({ cardId, alt }: { cardId: string; alt: string }) {
  const frame = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const x = useMotionValue(0);
  const y = useMotionValue(0);

  const reset = useCallback(() => {
    setZoom(1);
    x.set(0);
    y.set(0);
  }, [x, y]);

  useEffect(() => {
    reset();
  }, [cardId, reset]);

  return (
    <div
      ref={frame}
      className="relative min-h-0 overflow-hidden bg-[radial-gradient(120%_120%_at_50%_0%,oklch(0.2_0.02_265),var(--color-void))]"
      onWheel={(event) => {
        const next = Math.min(5, Math.max(1, zoom * (1 - event.deltaY * 0.0015)));
        setZoom(next);
        if (next === 1) {
          x.set(0);
          y.set(0);
        }
      }}
    >
      <motion.div layoutId={`card-image-${cardId}`} className="absolute inset-0 p-4 sm:p-8">
        <motion.div
          drag={zoom > 1}
          dragConstraints={frame}
          dragElastic={0.06}
          style={{ x, y, scale: zoom }}
          onDoubleClick={() => (zoom > 1 ? reset() : setZoom(2.4))}
          className={cx('h-full w-full', zoom > 1 ? 'cursor-grab active:cursor-grabbing' : '')}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={cardImageUrl(cardId)}
            alt={alt}
            draggable={false}
            className="h-full w-full rounded-lg object-contain shadow-[0_30px_80px_-30px_rgba(0,0,0,0.9)]"
          />
        </motion.div>
      </motion.div>

      <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-surface/85 px-1.5 py-1 backdrop-blur-sm">
        <IconButton label="Zoom out" onClick={() => setZoom((z) => Math.max(1, z - 0.5))}>
          <ZoomOut className="h-3.5 w-3.5" />
        </IconButton>
        <span className="tnum w-12 text-center text-[11.5px] text-ink-muted">
          {Math.round(zoom * 100)}%
        </span>
        <IconButton label="Zoom in" onClick={() => setZoom((z) => Math.min(5, z + 0.5))}>
          <ZoomIn className="h-3.5 w-3.5" />
        </IconButton>
        <IconButton label="Reset view" onClick={reset}>
          <RotateCcw className="h-3.5 w-3.5" />
        </IconButton>
        <IconButton label="Open original in a new tab" href={cardImageUrl(cardId)}>
          <Maximize2 className="h-3.5 w-3.5" />
        </IconButton>
      </div>
    </div>
  );
}

function IconButton({
  label,
  onClick,
  href,
  children,
}: {
  label: string;
  onClick?: () => void;
  href?: string;
  children: React.ReactNode;
}) {
  const className =
    'flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-raised hover:text-ink';
  if (href) {
    return (
      <a href={href} target="_blank" rel="noreferrer" aria-label={label} title={label} className={className}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} className={className}>
      {children}
    </button>
  );
}

/** Keeps Tab inside the dialog without pulling in a focus-trap dependency. */
function trapFocus(event: KeyboardEvent, panel: HTMLElement | null): void {
  if (!panel) return;
  const focusable = panel.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
  );
  if (focusable.length === 0) return;
  const first = focusable[0] as HTMLElement;
  const last = focusable[focusable.length - 1] as HTMLElement;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
