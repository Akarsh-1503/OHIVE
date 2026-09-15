'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, FileVideo, Loader2, Rocket, Upload, X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { createJobFromUpload, explain } from '@/lib/api';
import { megabytes, secs } from '@/lib/format';
import { recordRun } from '@/lib/runs';
import type { JobOptions } from '@/lib/types';
import { SampleRow } from './sample-row';
import { Badge, Button, Collapsible, Panel, Slider, Toggle, cx } from './ui';

const MAX_BYTES = 200 * 1024 * 1024;
const MAX_DURATION_S = 60;
const MIME_ALLOW = ['video/mp4', 'video/quicktime', 'video/x-matroska', 'video/webm', 'video/avi', 'video/x-msvideo'];
const EXT_ALLOW = ['.mp4', '.mov', '.mkv', '.webm', '.avi'];

interface Probe {
  width: number;
  height: number;
  duration_s: number;
  fps: number | null;
}

interface FrameMeta {
  mediaTime: number;
}
type VideoWithRvfc = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: FrameMeta) => void) => number;
};

/** Reads duration/resolution from the file itself, and estimates fps from frame callbacks. */
function probeVideo(url: string): Promise<Probe> {
  return new Promise((resolve, reject) => {
    const v = document.createElement('video') as VideoWithRvfc;
    v.preload = 'metadata';
    v.muted = true;
    v.src = url;
    v.onerror = () => reject(new Error('This file could not be decoded in the browser.'));
    v.onloadedmetadata = () => {
      const base: Probe = {
        width: v.videoWidth,
        height: v.videoHeight,
        duration_s: Number.isFinite(v.duration) ? v.duration : 0,
        fps: null,
      };
      if (typeof v.requestVideoFrameCallback !== 'function') {
        resolve(base);
        return;
      }
      const times: number[] = [];
      const tick = (_now: number, meta: FrameMeta) => {
        times.push(meta.mediaTime);
        if (times.length < 10) {
          v.requestVideoFrameCallback?.(tick);
          return;
        }
        const deltas = times
          .slice(1)
          .map((t, i) => t - times[i]!)
          .filter((d) => d > 0.0005)
          .sort((a, b) => a - b);
        const median = deltas[Math.floor(deltas.length / 2)];
        v.pause();
        resolve({ ...base, fps: median ? Math.round(1 / median) : null });
      };
      v.requestVideoFrameCallback?.(tick);
      void v.play().catch(() => resolve(base));
      // Never block the UI on a codec that refuses to decode frames off-screen.
      setTimeout(() => resolve(base), 1600);
    };
  });
}

export function Ingest() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [options, setOptions] = useState<Required<JobOptions>>({
    target_width: 640,
    max_frames: 1800,
    enable_loop_closure: true,
  });
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  const clear = useCallback(() => {
    setFile(null);
    setProbe(null);
    setObjectUrl((u) => {
      if (u) URL.revokeObjectURL(u);
      return null;
    });
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  const accept = useCallback(
    async (next: File) => {
      const name = next.name.toLowerCase();
      const extOk = EXT_ALLOW.some((e) => name.endsWith(e));
      if (!MIME_ALLOW.includes(next.type) && !extOk) {
        toast.error('That file type is not supported.', { description: 'Use MP4, MOV, MKV, WebM or AVI.' });
        return;
      }
      if (next.size > MAX_BYTES) {
        toast.error('That file is over the 200 MB limit.', {
          description: `${megabytes(next.size)} — trim the clip or re-encode it smaller.`,
        });
        return;
      }
      const url = URL.createObjectURL(next);
      setFile(next);
      setObjectUrl(url);
      setProbe(null);
      try {
        const p = await probeVideo(url);
        setProbe(p);
        if (p.duration_s > MAX_DURATION_S) {
          toast.warning(`That clip is ${secs(p.duration_s)} long.`, {
            description: `Runs are capped at ${optionsRef.current.max_frames} frames, so the tail will be truncated.`,
          });
        }
      } catch (e: unknown) {
        toast.error('Could not read that video.', { description: e instanceof Error ? e.message : undefined });
        clear();
      }
    },
    [clear],
  );

  const submit = async () => {
    if (!file) return;
    setUploadPct(0);
    try {
      const job = await createJobFromUpload(file, options, setUploadPct);
      recordRun({
        job_id: job.job_id,
        label: file.name,
        source: 'upload',
        loop_closure: options.enable_loop_closure,
        started_at: Date.now(),
      });
      router.push(`/j/${job.job_id}`);
    } catch (e: unknown) {
      const { title, detail } = explain(e);
      toast.error(title, { description: detail ?? undefined });
      setUploadPct(null);
    }
  };

  const tooLong = probe !== null && probe.duration_s > MAX_DURATION_S;

  return (
    <div className="space-y-8">
      <Panel className="p-4 sm:p-5">
        <input
          ref={inputRef}
          type="file"
          accept={[...MIME_ALLOW, ...EXT_ALLOW].join(',')}
          className="sr-only"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void accept(f);
          }}
        />

        <AnimatePresence mode="wait" initial={false}>
          {!file ? (
            <motion.div key="drop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  const f = e.dataTransfer.files?.[0];
                  if (f) void accept(f);
                }}
                className={cx(
                  'flex w-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-10 transition-all duration-200 sm:py-14',
                  dragging
                    ? 'border-accent bg-accent/10 scale-[1.01]'
                    : 'border-line bg-raised/30 hover:border-accent/50 hover:bg-raised/50',
                )}
              >
                <span
                  className={cx(
                    'flex h-12 w-12 items-center justify-center rounded-full border transition-colors',
                    dragging ? 'border-accent bg-accent/20 text-accent' : 'border-line bg-surface text-ink-muted',
                  )}
                >
                  <Upload size={20} aria-hidden />
                </span>
                <span className="text-base font-medium text-ink">Drop a video, or click to choose</span>
                <span className="num text-xs text-ink-faint">MP4 · MOV · MKV · WebM · AVI — up to 200 MB, 60 s</span>
              </button>
            </motion.div>
          ) : (
            <motion.div
              key="preview"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
              className="grid gap-4 sm:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]"
            >
              <video
                src={objectUrl ?? undefined}
                muted
                loop
                autoPlay
                playsInline
                className="aspect-video w-full rounded-lg border border-line bg-void object-cover"
              />
              <div className="min-w-0">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 truncate text-sm font-medium text-ink">
                      <FileVideo size={15} className="shrink-0 text-accent" aria-hidden />
                      <span className="truncate">{file.name}</span>
                    </p>
                    <p className="num mt-1 text-xs text-ink-faint">{megabytes(file.size)}</p>
                  </div>
                  <Button variant="ghost" size="sm" icon={X} onClick={clear} aria-label="Remove selected video">
                    Clear
                  </Button>
                </div>

                <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
                  {[
                    ['Duration', probe ? secs(probe.duration_s) : '…'],
                    ['Resolution', probe ? `${probe.width}×${probe.height}` : '…'],
                    ['Frame rate', probe ? (probe.fps ? `${probe.fps} fps` : 'n/a') : '…'],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">{k}</dt>
                      <dd className="num mt-0.5 text-sm text-ink">{v}</dd>
                    </div>
                  ))}
                </dl>

                {tooLong ? (
                  <p className="mt-3 flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
                    <span>
                      Over the 60 s guideline. The run will be capped at <span className="num">{options.max_frames}</span>{' '}
                      frames and flagged as truncated.
                    </span>
                  </p>
                ) : null}

                <div className="mt-4">
                  <Button
                    variant="primary"
                    size="lg"
                    icon={uploadPct === null ? Rocket : Loader2}
                    onClick={() => void submit()}
                    disabled={uploadPct !== null}
                    className="w-full sm:w-auto"
                  >
                    {uploadPct === null ? 'Reconstruct' : `Uploading ${Math.round(uploadPct * 100)}%`}
                  </Button>
                  {uploadPct !== null ? (
                    <div className="mt-2 h-[3px] w-full overflow-hidden rounded-full bg-line">
                      <div
                        className="h-full rounded-full bg-linear-to-r from-accent to-accent-2 transition-[width] duration-150 ease-linear"
                        style={{ width: `${uploadPct * 100}%` }}
                      />
                    </div>
                  ) : null}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="mt-4">
          <Collapsible
            title="Advanced options"
            right={
              !options.enable_loop_closure ? (
                <Badge tone="warn">loop closure off</Badge>
              ) : (
                <span className="num text-[0.68rem] text-ink-faint">{options.target_width} px</span>
              )
            }
          >
            <Slider
              label="Target width"
              value={options.target_width}
              min={320}
              max={1280}
              step={32}
              display={`${options.target_width} px`}
              onChange={(v) => setOptions((o) => ({ ...o, target_width: v }))}
            />
            <p className="-mt-1 mb-3 text-[0.68rem] leading-relaxed text-ink-faint">
              Frames are downscaled to this width before feature extraction. Smaller is faster; below ~480 px the tracker
              starts losing corners.
            </p>
            <Slider
              label="Max frames"
              value={options.max_frames}
              min={120}
              max={3000}
              step={60}
              display={String(options.max_frames)}
              onChange={(v) => setOptions((o) => ({ ...o, max_frames: v }))}
            />
            <p className="-mt-1 mb-3 text-[0.68rem] leading-relaxed text-ink-faint">
              Hard cap on processed frames. Anything past it is dropped and the job comes back flagged truncated.
            </p>
            <Toggle
              checked={options.enable_loop_closure}
              onChange={(v) => setOptions((o) => ({ ...o, enable_loop_closure: v }))}
              label="Loop closure + pose-graph optimisation"
              hint="Off = raw visual odometry, drift left uncorrected. Run a clip both ways to see the difference."
            />
          </Collapsible>
        </div>
      </Panel>

      <section aria-labelledby="samples-heading">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 id="samples-heading" className="text-sm font-semibold tracking-tight text-ink">
            Try a sample
          </h2>
          <p className="text-xs text-ink-faint">Built-in clips — no upload, one click, real reconstruction.</p>
        </div>
        <SampleRow options={options} />
      </section>
    </div>
  );
}
