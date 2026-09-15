'use client';

import { Grid } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { useReducedMotion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import type { SceneData } from '@/lib/recon';
import { CameraRig, PlaybackDriver } from './camera-rig';
import { ViewerOverlay } from './overlay';
import { PointCloud } from './point-cloud';
import { DEFAULT_SETTINGS, type ViewerRuntime, type ViewerSettings } from './state';
import { KeyframeFrusta, LoopArcs, PlayheadCamera, TrajectoryPath } from './trajectory';

/**
 * Probe for a usable WebGL context before mounting a Canvas.
 *
 * Without this, a browser with hardware acceleration switched off (or a GPU blocklist, or a
 * hardened/enterprise profile) gets "Error creating WebGL context" thrown as an unhandled
 * rejection and a black box where the map should be, with the full control surface still
 * drawn around it. That reads as a broken application rather than an unsupported browser.
 */
function detectWebGL(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    const canvas = document.createElement('canvas');
    const gl =
      canvas.getContext('webgl2') ??
      canvas.getContext('webgl') ??
      canvas.getContext('experimental-webgl');
    if (!gl) return false;
    // Release it immediately; holding a second context costs a GPU process slot.
    (gl as WebGLRenderingContext).getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch {
    return false;
  }
}

export function Viewer({
  data,
  runtime,
  flyIn = true,
  className,
}: {
  data: SceneData;
  runtime: ViewerRuntime;
  flyIn?: boolean;
  className?: string;
}) {
  const reduced = useReducedMotion() ?? false;
  const containerRef = useRef<HTMLDivElement>(null);
  const [settings, setSettings] = useState<ViewerSettings>(DEFAULT_SETTINGS);
  // Probed on the client only: the server has no canvas, and rendering the fallback during
  // SSR would flash "unsupported" on every load before hydration corrects it.
  const [webgl, setWebgl] = useState<boolean | null>(null);
  useEffect(() => setWebgl(detectWebGL()), []);

  // Replay the reconstruction once when a finished map first appears: rewind to the start
  // and play. Watching the trajectory and cloud build themselves is what makes the result
  // legible -- landing on a static finished scene reads as a still image, and the Replay
  // control is easy to miss. `autoplay` is one-shot per job; the driver clears `playing`
  // when it reaches the end, leaving the completed map on screen.
  //
  // With reduced motion, skip straight to the finished state rather than animating.
  useEffect(() => {
    if (reduced) {
      runtime.frame = data.poseCount - 1;
      runtime.playing = false;
    } else {
      runtime.frame = 0;
      runtime.playing = true;
      runtime.onPlayStateChange?.(true);
    }
    runtime.invalidate?.();
  }, [data, runtime, reduced]);

  const r = data.bounds.radius;

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      role="region"
      aria-label="3D reconstruction viewer. Use the accessible trajectory table below for a text alternative."
      className={`relative isolate overflow-hidden bg-void outline-none ${className ?? ''}`}
    >
      <div
        aria-hidden
        className="absolute inset-0 -z-10"
        style={{
          background:
            'radial-gradient(120% 90% at 50% 0%, oklch(0.24 0.05 250 / 0.85), transparent 60%), radial-gradient(90% 70% at 20% 100%, oklch(0.22 0.07 285 / 0.6), transparent 65%), var(--color-void)',
        }}
      />
      {webgl === false ? <NoWebGL data={data} /> : null}
      {webgl !== false ? (
      <Canvas
        frameloop="demand"
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
        camera={{ fov: 55, near: Math.max(0.001, r * 0.004), far: r * 80, position: [r, r * 0.6, r] }}
        onCreated={({ gl }) => gl.setClearAlpha(0)}
      >
        <PlaybackDriver data={data} runtime={runtime} />
        <CameraRig data={data} runtime={runtime} reducedMotion={reduced} flyIn={flyIn} />

        <PointCloud
          data={data}
          colorMode={settings.colorMode}
          pointSize={settings.pointSize}
          density={settings.density}
          visible={settings.layers.cloud}
        />
        <TrajectoryPath data={data} runtime={runtime} visible={settings.layers.trajectory} />
        <KeyframeFrusta data={data} runtime={runtime} visible={settings.layers.keyframes} />
        <LoopArcs data={data} runtime={runtime} visible={settings.layers.loops} />
        <PlayheadCamera data={data} runtime={runtime} visible={settings.layers.keyframes} />

        <Grid
          visible={settings.layers.grid}
          position={[data.bounds.centre[0], data.groundY - r * 0.02, data.bounds.centre[2]]}
          args={[r * 4, r * 4]}
          cellSize={r * 0.12}
          cellThickness={0.5}
          cellColor="#16203a"
          sectionSize={r * 0.6}
          sectionThickness={0.9}
          sectionColor="#23375e"
          fadeDistance={r * 3.4}
          fadeStrength={1.6}
          infiniteGrid
        />
      </Canvas>
      ) : null}

      {webgl !== false ? (
        <ViewerOverlay
          data={data}
          runtime={runtime}
          settings={settings}
          setSettings={setSettings}
          containerRef={containerRef}
        />
      ) : null}
    </div>
  );
}

/**
 * Shown instead of the canvas when the browser cannot give us WebGL.
 *
 * The reconstruction is not lost in this case -- the poses, the metrics and the exports are
 * all still there -- so this says what happened, how to fix it, and where the same data can
 * be read without a GPU.
 */
function NoWebGL({ data }: { data: SceneData }) {
  return (
    <div className="absolute inset-0 grid place-items-center p-6">
      <div className="max-w-lg rounded-2xl border border-line bg-surface/70 p-6 backdrop-blur-sm">
        <h3 className="text-[15px] font-medium text-ink">This browser cannot open a 3D view</h3>
        <p className="mt-2 text-[13px] leading-6 text-ink-muted">
          The reconstruction finished successfully &mdash; {data.poseCount.toLocaleString()} camera
          poses and {data.pointCount.toLocaleString()} map points are ready. Only the WebGL
          canvas failed, because hardware acceleration is switched off or blocked in this
          browser.
        </p>
        <p className="mt-3 text-[13px] leading-6 text-ink-muted">
          In Chrome, enable <span className="text-ink">Settings &rarr; System &rarr; Use graphics
          acceleration when available</span> and restart, or check{' '}
          <span className="tnum text-ink">chrome://gpu</span>. Safari and Firefox generally work
          without any change.
        </p>
        <p className="mt-3 text-[13px] leading-6 text-ink-muted">
          Nothing is blocked in the meantime: the full trajectory is in the{' '}
          <span className="text-ink">trajectory table below</span>, and the PLY, TUM and
          report.json exports all carry the complete result.
        </p>
      </div>
    </div>
  );
}
