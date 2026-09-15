'use client';

/**
 * Everything the render loop mutates lives here, not in React state.
 *
 * Playback runs at 60 fps; routing it through `useState` would re-render the whole panel
 * tree on every frame. The UI writes into this object, `useFrame` reads it, and the few
 * numbers a human needs to see are written straight into DOM nodes.
 */

export type ColorMode = 'rgb' | 'height' | 'observations';
export type ViewPreset = 'top' | 'front' | 'side';

export interface Layers {
  cloud: boolean;
  trajectory: boolean;
  keyframes: boolean;
  loops: boolean;
  grid: boolean;
}

export interface ViewerSettings {
  colorMode: ColorMode;
  pointSize: number;
  density: number;
  layers: Layers;
}

export const DEFAULT_SETTINGS: ViewerSettings = {
  colorMode: 'rgb',
  pointSize: 1.5,
  density: 1,
  layers: { cloud: true, trajectory: true, keyframes: true, loops: true, grid: true },
};

export interface ViewerRuntime {
  /** Playhead in fractional frame index. */
  frame: number;
  playing: boolean;
  follow: boolean;
  speed: number;
  /** Bumped to request a camera move; consumed by the rig. */
  fitRequest: number;
  presetRequest: { preset: ViewPreset; n: number } | null;
  /** performance.now() of the last loop-closure flash. */
  pulseAt: number;
  /** DOM nodes written directly from the render loop. */
  scrubberEl: HTMLInputElement | null;
  timeEl: HTMLElement | null;
  frameEl: HTMLElement | null;
  fpsEl: HTMLElement | null;
  /** Set by the rig so the overlay can disable orbit hints while following. */
  onPlayStateChange: ((playing: boolean) => void) | null;
  /** r3f's `invalidate`, published by the driver so overlay controls can request a frame. */
  invalidate: (() => void) | null;
}

export function createRuntime(): ViewerRuntime {
  return {
    frame: 0,
    playing: false,
    follow: false,
    speed: 1,
    fitRequest: 0,
    presetRequest: null,
    pulseAt: 0,
    scrubberEl: null,
    timeEl: null,
    frameEl: null,
    fpsEl: null,
    onPlayStateChange: null,
    invalidate: null,
  };
}
