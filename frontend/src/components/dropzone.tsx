'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { ArrowRight, ClipboardPaste, FileWarning, Loader2, Sparkles, Upload, X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, createBatch, getHealth } from '@/lib/api';
import { Button, cx } from './ui';

const MAX_FILES = 25;
const MAX_BYTES = 12 * 1024 * 1024;
const ACCEPTED = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/heic',
  'image/heif',
  'application/pdf',
]);
const ACCEPT_ATTR = '.jpg,.jpeg,.png,.webp,.heic,.heif,.pdf';

interface Queued {
  id: string;
  file: File;
  url: string;
}

let counter = 0;

export function Dropzone() {
  const router = useRouter();
  const [queue, setQueue] = useState<Queued[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  // Only used to warn before committing, so a failed probe simply means no warning.
  const [modelCold, setModelCold] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const queueRef = useRef<Queued[]>([]);
  queueRef.current = queue;

  useEffect(
    () => () => {
      for (const item of queueRef.current) URL.revokeObjectURL(item.url);
    },
    [],
  );

  const commit = useCallback((next: Queued[]) => {
    queueRef.current = next;
    setQueue(next);
  }, []);

  const add = useCallback(
    (incoming: File[]) => {
      if (incoming.length === 0) return;
      const current = queueRef.current;
      const problems: string[] = [];
      const accepted: Queued[] = [];
      const seen = new Set(current.map((item) => `${item.file.name}:${item.file.size}`));

      for (const file of incoming) {
        const key = `${file.name}:${file.size}`;
        const type = file.type || guessType(file.name);
        if (seen.has(key)) {
          problems.push(`${file.name} is already in the batch.`);
          continue;
        }
        if (!ACCEPTED.has(type)) {
          problems.push(`${file.name} is not a supported card format.`);
          continue;
        }
        if (file.size > MAX_BYTES) {
          problems.push(
            `${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is 12 MB.`,
          );
          continue;
        }
        if (current.length + accepted.length >= MAX_FILES) {
          problems.push(`A batch holds ${MAX_FILES} cards — ${file.name} was not added.`);
          continue;
        }
        seen.add(key);
        counter += 1;
        accepted.push({ id: `f${counter}`, file, url: URL.createObjectURL(file) });
      }

      setErrors(problems);
      if (accepted.length > 0) commit([...current, ...accepted]);
    },
    [commit],
  );

  const remove = useCallback(
    (id: string) => {
      const target = queueRef.current.find((item) => item.id === id);
      if (target) URL.revokeObjectURL(target.url);
      commit(queueRef.current.filter((item) => item.id !== id));
    },
    [commit],
  );

  const clear = useCallback(() => {
    for (const item of queueRef.current) URL.revokeObjectURL(item.url);
    commit([]);
    setErrors([]);
  }, [commit]);

  // Paste-from-clipboard: screenshotting a card and hitting Cmd+V is the fastest path in.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent): void => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      add(files);
      toast.success(`${files.length} card${files.length === 1 ? '' : 's'} pasted`);
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [add]);

  const loadSamples = async (): Promise<void> => {
    setBusy(true);
    try {
      const manifest = (await (await fetch('/samples/manifest.json')).json()) as { files: string[] };
      const files = await Promise.all(
        manifest.files.map(async (name) => {
          const blob = await (await fetch(`/samples/${name}`)).blob();
          return new File([blob], name, { type: 'image/jpeg' });
        }),
      );
      clear();
      add(files);
      toast.success(`Loaded ${files.length} sample cards`);
    } catch {
      toast.error('Could not load the sample corpus');
    } finally {
      setBusy(false);
    }
  };

  // Polled only while files are staged: no point probing an empty dropzone, and the
  // endpoint is cached server-side so this never touches the GPU.
  useEffect(() => {
    if (queue.length === 0) return;
    const controller = new AbortController();
    const check = async () => {
      try {
        const health = await getHealth(controller.signal);
        setModelCold(!health.vlm.warm);
      } catch {
        /* a failed probe just means no warning */
      }
    };
    void check();
    const id = setInterval(() => void check(), 10_000);
    return () => {
      controller.abort();
      clearInterval(id);
    };
  }, [queue.length]);

  const submit = async (): Promise<void> => {
    if (queue.length === 0 || busy) return;
    setBusy(true);
    setProgress(0);
    try {
      const batch = await createBatch(
        queue.map((item) => item.file),
        setProgress,
      );
      router.push(`/b/${batch.batch_id}`);
    } catch (error) {
      setBusy(false);
      const message = error instanceof ApiError ? error.message : 'Upload failed';
      setErrors([message]);
      toast.error('Upload failed', { description: message });
    }
  };

  const onDrop = (event: React.DragEvent): void => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    add(Array.from(event.dataTransfer.files));
  };

  return (
    <div className="w-full">
      <div
        onDragEnter={(event) => {
          event.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => {
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) setDragging(false);
        }}
        onDrop={onDrop}
        className="relative"
      >
        <motion.button
          type="button"
          onClick={() => inputRef.current?.click()}
          animate={{ scale: dragging ? 1.012 : 1 }}
          transition={{ type: 'spring', stiffness: 340, damping: 26 }}
          className={cx(
            'group relative flex w-full flex-col items-center justify-center overflow-hidden rounded-[20px] border-2 border-dashed px-6 py-12 text-center transition-colors duration-200 sm:py-16',
            dragging
              ? 'border-accent bg-accent/[0.07]'
              : 'border-line bg-surface/40 hover:border-ink-faint hover:bg-surface/60',
          )}
          aria-label="Add business card images — drag and drop, or activate to browse"
        >
          <span className="dropzone-grid pointer-events-none absolute inset-0 opacity-60" aria-hidden />
          <AnimatePresence>
            {dragging ? (
              <motion.span
                key="glow"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                aria-hidden
                className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_60%_at_50%_50%,color-mix(in_oklch,var(--color-accent)_22%,transparent),transparent_70%)]"
              />
            ) : null}
          </AnimatePresence>

          <motion.span
            animate={{ y: dragging ? -6 : 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 20 }}
            className={cx(
              'relative mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border transition-colors duration-200',
              dragging
                ? 'border-accent/50 bg-accent/15 text-accent'
                : 'border-line bg-raised/70 text-ink-muted group-hover:text-ink',
            )}
          >
            <Upload className="h-6 w-6" aria-hidden />
          </motion.span>

          <span className="relative text-[17px] font-medium text-ink sm:text-lg">
            {dragging ? 'Release to add these cards' : 'Drop business cards here'}
          </span>
          <span className="relative mt-2 max-w-md text-[13.5px] leading-6 text-ink-muted">
            JPEG, PNG, WebP, HEIC or PDF — up to {MAX_FILES} cards, 12 MB each. You can also paste
            from the clipboard.
          </span>
        </motion.button>

        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT_ATTR}
          className="sr-only"
          onChange={(event) => {
            add(Array.from(event.target.files ?? []));
            event.target.value = '';
          }}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => void loadSamples()} disabled={busy}>
          <Sparkles className="h-3.5 w-3.5" aria-hidden />
          Load sample cards
        </Button>
        <span className="hidden items-center gap-1.5 text-[12px] text-ink-muted sm:flex">
          <ClipboardPaste className="h-3.5 w-3.5" aria-hidden />
          or paste with ⌘V
        </span>
        <span className="ml-auto tnum text-[12px] text-ink-muted">
          {queue.length}/{MAX_FILES} cards
        </span>
      </div>

      <AnimatePresence initial={false}>
        {errors.length > 0 ? (
          <motion.ul
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-3 space-y-1.5 overflow-hidden"
            role="alert"
          >
            {errors.map((message) => (
              <li
                key={message}
                className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad/[0.07] px-3 py-2 text-[13px] text-bad"
              >
                <FileWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                {message}
              </li>
            ))}
          </motion.ul>
        ) : null}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {queue.length > 0 ? (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <ul className="mt-5 grid grid-cols-3 gap-2.5 sm:grid-cols-5 lg:grid-cols-7">
              <AnimatePresence initial={false}>
                {queue.map((item, index) => (
                  <motion.li
                    key={item.id}
                    layout
                    initial={{ opacity: 0, scale: 0.7, y: 14 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.8, transition: { duration: 0.14 } }}
                    transition={{
                      type: 'spring',
                      stiffness: 420,
                      damping: 28,
                      delay: Math.min(index * 0.035, 0.4),
                    }}
                    className="group relative aspect-[1.75/1] overflow-hidden rounded-lg border border-line bg-raised"
                  >
                    {/* Local object URLs, so next/image would only add indirection here. */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={item.url}
                      alt={item.file.name}
                      className="h-full w-full object-cover opacity-85 transition-opacity group-hover:opacity-100"
                    />
                    <button
                      type="button"
                      onClick={() => remove(item.id)}
                      aria-label={`Remove ${item.file.name}`}
                      className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full border border-line bg-void/80 text-ink-muted opacity-0 transition-opacity hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <X className="h-3 w-3" aria-hidden />
                    </button>
                    <span className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-void/90 to-transparent px-1.5 pb-1 pt-4 text-[10px] text-ink-muted">
                      {item.file.name}
                    </span>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>

            <div className="mt-6 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
              <Button
                variant="primary"
                size="lg"
                onClick={() => void submit()}
                disabled={busy}
                className="relative overflow-hidden sm:min-w-56"
              >
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    Uploading {Math.round(progress * 100)}%
                  </>
                ) : (
                  <>
                    Extract {queue.length} card{queue.length === 1 ? '' : 's'}
                    <ArrowRight className="h-4 w-4" aria-hidden />
                  </>
                )}
              </Button>
              <Button variant="ghost" size="lg" disabled={busy} onClick={clear}>
                Clear
              </Button>
            </div>

            {/* Stated at the point of commitment, not only in the status pill. A reviewer
                who sees "Extract" stall for three minutes with no explanation concludes the
                app is broken; one who was told expects it. */}
            <AnimatePresence initial={false}>
              {modelCold && !busy ? (
                <motion.p
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  className="mt-3 text-[12.5px] leading-5 text-ink-muted"
                >
                  <span className="font-medium text-warn">Heads up:</span> the GPU is scaled to
                  zero right now, so the first card will take about 3½ minutes while the model loads — the rest follow at 6–11 s each. Nothing is stuck. Use{' '}
                  <span className="font-medium">Warm up the GPU</span> above to start it now.
                </motion.p>
              ) : null}
            </AnimatePresence>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function guessType(name: string): string {
  const extension = name.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    png: 'image/png',
    webp: 'image/webp',
    heic: 'image/heic',
    heif: 'image/heif',
    pdf: 'application/pdf',
  };
  return map[extension] ?? '';
}
