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

  // Start the playhead at the end of the clip so the completed map is shown in full.
  useEffect(() => {
    runtime.frame = data.poseCount - 1;
    runtime.invalidate?.();
  }, [data, runtime]);

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

      <ViewerOverlay
        data={data}
        runtime={runtime}
        settings={settings}
        setSettings={setSettings}
        containerRef={containerRef}
      />
    </div>
  );
}
