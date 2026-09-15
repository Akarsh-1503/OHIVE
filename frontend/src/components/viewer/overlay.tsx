'use client';

import { AnimatePresence, motion } from 'framer-motion';
import {
  Box,
  Camera,
  Crosshair,
  Eye,
  Grid3x3,
  Link2,
  Orbit,
  Pause,
  Play,
  Route,
  Settings2,
  SkipBack,
  SkipForward,
  Video,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { SceneData } from '@/lib/recon';
import { Button, Segmented, Slider, cx } from '../ui';
import { LOOP_COLOR } from './trajectory';
import type { ColorMode, ViewerRuntime, ViewerSettings, ViewPreset } from './state';

const COLOR_MODES: { value: ColorMode; label: string; title: string }[] = [
  { value: 'rgb', label: 'RGB', title: 'Colour sampled from the source frame that triangulated each point' },
  { value: 'height', label: 'Height', title: 'Colour by height along the up axis' },
  { value: 'observations', label: 'Obs', title: 'Colour by how many keyframes observed each point' },
];

const LAYER_ITEMS = [
  { key: 'cloud', label: 'Point cloud', icon: Box },
  { key: 'trajectory', label: 'Trajectory', icon: Route },
  { key: 'keyframes', label: 'Keyframes', icon: Camera },
  { key: 'loops', label: 'Loop edges', icon: Link2 },
  { key: 'grid', label: 'Grid', icon: Grid3x3 },
] as const;

const HEIGHT_GRADIENT = 'linear-gradient(90deg,#0c4a6e,#22d3ee,#818cf8,#a78bfa,#fcd34d)';
const OBS_GRADIENT = 'linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#bbdf27,#fde725)';

export function ViewerOverlay({
  data,
  runtime,
  settings,
  setSettings,
  containerRef,
}: {
  data: SceneData;
  runtime: ViewerRuntime;
  settings: ViewerSettings;
  setSettings: (next: ViewerSettings) => void;
  containerRef: React.RefObject<HTMLDivElement | null>;
}) {
  const [playing, setPlaying] = useState(false);
  const [follow, setFollow] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [sheetOpen, setSheetOpen] = useState(false);
  const presetCount = useRef(0);
  const scrubRef = useRef<HTMLInputElement>(null);
  const timeRef = useRef<HTMLSpanElement>(null);
  const frameRef = useRef<HTMLSpanElement>(null);
  const fpsRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    runtime.scrubberEl = scrubRef.current;
    runtime.timeEl = timeRef.current;
    runtime.frameEl = frameRef.current;
    runtime.fpsEl = fpsRef.current;
    runtime.onPlayStateChange = setPlaying;
    return () => {
      runtime.scrubberEl = null;
      runtime.timeEl = null;
      runtime.frameEl = null;
      runtime.fpsEl = null;
      runtime.onPlayStateChange = null;
    };
  }, [runtime]);

  const togglePlay = useCallback(() => {
    if (!runtime.playing && runtime.frame >= data.poseCount - 1) runtime.frame = 0;
    runtime.playing = !runtime.playing;
    setPlaying(runtime.playing);
    runtime.invalidate?.();
  }, [runtime, data.poseCount]);

  const seek = useCallback(
    (frame: number) => {
      runtime.frame = Math.max(0, Math.min(data.poseCount - 1, frame));
      if (scrubRef.current) scrubRef.current.value = String(Math.floor(runtime.frame));
      runtime.invalidate?.();
    },
    [runtime, data.poseCount],
  );

  const setFollowMode = useCallback(
    (next: boolean) => {
      runtime.follow = next;
      setFollow(next);
      runtime.invalidate?.();
    },
    [runtime],
  );

  const fit = useCallback(() => {
    runtime.fitRequest += 1;
    setFollow(false);
    runtime.invalidate?.();
  }, [runtime]);

  const preset = useCallback(
    (p: ViewPreset) => {
      presetCount.current += 1;
      runtime.presetRequest = { preset: p, n: presetCount.current };
      setFollow(false);
      runtime.invalidate?.();
    },
    [runtime],
  );

  // Keyboard control for playback and view, scoped to the viewer so it never hijacks the page.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') && e.key !== ' ') return;
      switch (e.key) {
        case ' ':
          e.preventDefault();
          togglePlay();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          seek(runtime.frame - (e.shiftKey ? 10 : 1));
          break;
        case 'ArrowRight':
          e.preventDefault();
          seek(runtime.frame + (e.shiftKey ? 10 : 1));
          break;
        case 'Home':
          e.preventDefault();
          seek(0);
          break;
        case 'End':
          e.preventDefault();
          seek(data.poseCount - 1);
          break;
        case 'f':
          fit();
          break;
        case 'v':
          setFollowMode(!runtime.follow);
          break;
        case '1':
          preset('top');
          break;
        case '2':
          preset('front');
          break;
        case '3':
          preset('side');
          break;
        case 'c': {
          const order: ColorMode[] = ['rgb', 'height', 'observations'];
          const idx = order.indexOf(settings.colorMode);
          setSettings({ ...settings, colorMode: order[(idx + 1) % order.length]! });
          break;
        }
        default:
          break;
      }
    };
    el.addEventListener('keydown', onKey);
    return () => el.removeEventListener('keydown', onKey);
  }, [containerRef, data.poseCount, fit, preset, runtime, seek, setFollowMode, setSettings, settings, togglePlay]);

  const layerPanel = (
    <>
      <div className="space-y-1.5">
        <p className="text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Layers</p>
        {LAYER_ITEMS.map(({ key, label, icon: Icon }) => {
          const on = settings.layers[key];
          return (
            <button
              key={key}
              type="button"
              role="switch"
              aria-checked={on}
              onClick={() => setSettings({ ...settings, layers: { ...settings.layers, [key]: !on } })}
              className={cx(
                'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors',
                on ? 'bg-accent/12 text-ink' : 'text-ink-faint hover:bg-raised/60',
              )}
            >
              <Icon size={13} className={on ? 'text-accent' : ''} aria-hidden />
              <span className="flex-1 text-left">{label}</span>
              <span
                className={cx('h-1.5 w-1.5 rounded-full', on ? 'bg-accent' : 'bg-line')}
                style={key === 'loops' && on ? { background: LOOP_COLOR } : undefined}
              />
            </button>
          );
        })}
      </div>

      <div className="mt-3 border-t border-line pt-3">
        <p className="mb-1.5 text-[0.62rem] tracking-[0.09em] text-ink-faint uppercase">Colour by</p>
        <Segmented
          label="Point colour mode"
          options={COLOR_MODES}
          value={settings.colorMode}
          onChange={(v) => setSettings({ ...settings, colorMode: v })}
        />
        {settings.colorMode !== 'rgb' ? (
          <div className="mt-2">
            <div
              className="h-1.5 w-full rounded-full"
              style={{ background: settings.colorMode === 'height' ? HEIGHT_GRADIENT : OBS_GRADIENT }}
              aria-hidden
            />
            <div className="num mt-1 flex justify-between text-[0.6rem] text-ink-faint">
              {settings.colorMode === 'height' ? (
                <>
                  <span>low</span>
                  <span>high</span>
                </>
              ) : (
                <>
                  <span>1 kf</span>
                  <span>{data.maxObservations} kf</span>
                </>
              )}
            </div>
            <p className="mt-1 text-[0.65rem] leading-snug text-ink-faint">
              {settings.colorMode === 'height'
                ? 'Height along the up axis. Monocular output is not gravity-aligned, so "up" is the first keyframe’s up.'
                : 'Keyframes that observed each point. Dark points are weakly constrained and the first to be wrong.'}
            </p>
          </div>
        ) : null}
      </div>

      <div className="mt-3 border-t border-line pt-2">
        <Slider
          label="Point size"
          value={settings.pointSize}
          min={0.4}
          max={3}
          step={0.05}
          display={`${settings.pointSize.toFixed(2)}×`}
          onChange={(v) => setSettings({ ...settings, pointSize: v })}
        />
        <Slider
          label="Density"
          value={settings.density}
          min={0.05}
          max={1}
          step={0.05}
          display={`${Math.round(data.pointCount * settings.density).toLocaleString('en-US')} pts`}
          onChange={(v) => setSettings({ ...settings, density: v })}
        />
      </div>
    </>
  );

  return (
    <>
      {/* Desktop: docked control column */}
      <div className="pointer-events-none absolute inset-0 hidden lg:block">
        <div className="pointer-events-auto absolute top-3 left-3 w-56 rounded-[var(--radius-card)] border border-line bg-surface/85 p-3 backdrop-blur-md">
          {layerPanel}
        </div>

        {/* Beside the layer column rather than in the top-right corner: the job page docks
            its metrics panel there, and it would swallow every click on these buttons. */}
        <div className="pointer-events-auto absolute top-3 left-[15.5rem] flex items-center gap-2">
          <div className="flex gap-1 rounded-lg border border-line bg-surface/85 p-1 backdrop-blur-md">
            {(['top', 'front', 'side'] as ViewPreset[]).map((p, i) => (
              <button
                key={p}
                type="button"
                onClick={() => preset(p)}
                title={`${p} view (${i + 1})`}
                className="rounded-md px-2.5 py-1.5 text-xs text-ink-muted capitalize transition-colors hover:bg-raised hover:text-ink"
              >
                {p}
              </button>
            ))}
            <button
              type="button"
              onClick={fit}
              title="Fit to scene (f)"
              className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-accent transition-colors hover:bg-accent/15"
            >
              <Crosshair size={13} aria-hidden />
              Fit
            </button>
          </div>
          <div className="num flex items-center gap-1.5 rounded-lg border border-line bg-surface/85 px-2.5 py-1.5 text-[0.68rem] text-ink-faint backdrop-blur-md">
            <Eye size={12} aria-hidden />
            <span ref={fpsRef}>—</span>
            <span>fps</span>
            <span className="mx-1 h-3 w-px bg-line" />
            <span>{data.pointCount.toLocaleString('en-US')} pts</span>
          </div>
        </div>
      </div>

      {/* Mobile: one button, panels become a bottom sheet */}
      <div className="pointer-events-auto absolute top-3 right-3 lg:hidden">
        <Button size="sm" variant="subtle" icon={Settings2} onClick={() => setSheetOpen(true)}>
          View
        </Button>
      </div>
      <AnimatePresence>
        {sheetOpen ? (
          <>
            {/* Fixed rather than absolute, so the sheet is measured against the phone screen
                instead of the viewer box and its last control never lands under the fold. */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setSheetOpen(false)}
              className="fixed inset-0 z-40 bg-void/70 lg:hidden"
              aria-hidden
            />
            <motion.div
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              exit={{ y: '100%' }}
              transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
              className="thin-scroll fixed inset-x-0 bottom-0 z-50 max-h-[82dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-surface px-4 pt-3 pb-8 lg:hidden"
              role="dialog"
              aria-label="Viewer controls"
            >
              <div className="mb-3 flex items-center justify-between">
                <p className="text-sm font-semibold">Viewer controls</p>
                <Button size="sm" variant="ghost" icon={X} onClick={() => setSheetOpen(false)} aria-label="Close controls">
                  Close
                </Button>
              </div>
              {layerPanel}
              <div className="mt-3 border-t border-line pt-3">
                <div className="flex gap-1">
                  {(['top', 'front', 'side'] as ViewPreset[]).map((p) => (
                    <Button key={p} size="sm" variant="subtle" onClick={() => preset(p)} className="flex-1 capitalize">
                      {p}
                    </Button>
                  ))}
                  <Button size="sm" variant="subtle" icon={Crosshair} onClick={fit}>
                    Fit
                  </Button>
                </div>
              </div>
            </motion.div>
          </>
        ) : null}
      </AnimatePresence>

      {/* Playback bar */}
      <div className="pointer-events-auto absolute inset-x-0 bottom-0 border-t border-line bg-surface/85 px-3 py-2.5 backdrop-blur-md sm:px-4">
        <div className="flex items-center gap-2 sm:gap-3">
          <Button
            size="sm"
            variant="primary"
            icon={playing ? Pause : Play}
            onClick={togglePlay}
            aria-label={playing ? 'Pause replay' : 'Play replay along the trajectory'}
          >
            <span className="hidden sm:inline">{playing ? 'Pause' : 'Replay'}</span>
          </Button>
          <button
            type="button"
            onClick={() => seek(0)}
            aria-label="Jump to start"
            title="Jump to start (Home)"
            className="hidden rounded-md p-1.5 text-ink-muted hover:bg-raised hover:text-ink sm:block"
          >
            <SkipBack size={15} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => seek(data.poseCount - 1)}
            aria-label="Jump to end"
            title="Jump to end (End)"
            className="hidden rounded-md p-1.5 text-ink-muted hover:bg-raised hover:text-ink sm:block"
          >
            <SkipForward size={15} aria-hidden />
          </button>

          <input
            ref={scrubRef}
            type="range"
            min={0}
            max={Math.max(1, data.poseCount - 1)}
            step={1}
            defaultValue={0}
            aria-label="Scrub through the captured trajectory"
            className="min-w-0 flex-1"
            onChange={(e) => seek(Number(e.target.value))}
          />

          <div className="num shrink-0 text-[0.7rem] text-ink-muted">
            <span ref={timeRef}>0.00s</span>
            <span className="mx-1.5 text-ink-faint">·</span>
            <span ref={frameRef} className="hidden sm:inline">
              1/{data.poseCount}
            </span>
          </div>

          <div className="hidden items-center gap-1 sm:flex">
            {[0.5, 1, 2].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  runtime.speed = s;
                  setSpeed(s);
                }}
                aria-pressed={speed === s}
                className={cx(
                  'num rounded-md px-1.5 py-1 text-[0.68rem] transition-colors',
                  speed === s ? 'bg-accent/15 text-accent' : 'text-ink-faint hover:text-ink',
                )}
              >
                {s}×
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setFollowMode(!follow)}
            aria-pressed={follow}
            aria-label="Toggle follow camera"
            title="Toggle free orbit / follow camera (v)"
            className={cx(
              'flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors',
              follow ? 'border-accent/60 bg-accent/15 text-accent' : 'border-line text-ink-muted hover:text-ink',
            )}
          >
            {follow ? <Video size={13} aria-hidden /> : <Orbit size={13} aria-hidden />}
            <span className="hidden sm:inline">{follow ? 'Follow cam' : 'Free orbit'}</span>
          </button>
        </div>
        <p className="mt-1.5 hidden text-[0.62rem] text-ink-faint lg:block">
          Space play/pause · ←/→ step · Home/End · f fit · 1/2/3 views · c colour mode · v follow cam
        </p>
      </div>
    </>
  );
}
