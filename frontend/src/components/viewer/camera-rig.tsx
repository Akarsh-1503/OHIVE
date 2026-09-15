'use client';

import { OrbitControls } from '@react-three/drei';
import { useFrame, useThree } from '@react-three/fiber';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import type { SceneData } from '@/lib/recon';
import type { ViewerRuntime, ViewPreset } from './state';

const PRESET_DIR: Record<ViewPreset, [number, number, number]> = {
  top: [0.001, 1, 0.02],
  front: [0, 0.16, 1],
  side: [1, 0.16, 0],
};
const HOME_DIR: [number, number, number] = [0.85, 0.52, 1];

interface Move {
  fromPos: THREE.Vector3;
  toPos: THREE.Vector3;
  fromTarget: THREE.Vector3;
  toTarget: THREE.Vector3;
  start: number;
  duration: number;
}

function easeOutExpo(t: number): number {
  return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
}

/**
 * Orbit / fit / preset transitions and the first-person follow camera.
 *
 * All camera work happens inside `useFrame` against mutable refs — the React tree never
 * re-renders while the camera is moving, which is what keeps this at 60 fps.
 */
export function CameraRig({
  data,
  runtime,
  reducedMotion,
  flyIn,
}: {
  data: SceneData;
  runtime: ViewerRuntime;
  reducedMotion: boolean;
  flyIn: boolean;
}) {
  const { camera, invalidate } = useThree();
  const controls = useRef<OrbitControlsImpl>(null);
  const move = useRef<Move | null>(null);
  const lastFit = useRef(0);
  const lastPreset = useRef(0);
  const wasFollowing = useRef(false);
  const pendingIntro = useRef(true);

  const centre = useMemo(
    () => new THREE.Vector3(data.focus.centre[0], data.focus.centre[1], data.focus.centre[2]),
    [data],
  );

  const corners = useMemo(() => {
    const { min, max } = data.focus;
    const out: THREE.Vector3[] = [];
    for (const x of [min[0], max[0]]) for (const y of [min[1], max[1]]) for (const z of [min[2], max[2]]) out.push(new THREE.Vector3(x, y, z));
    return out;
  }, [data.focus]);

  /**
   * Exact fit: the smallest distance along `dir` that keeps all eight bounding-box corners
   * inside the frustum. Fitting a bounding *sphere* instead would push the camera far too far
   * back for a scene that is much wider than it is tall, which every room scan is.
   */
  const positionFor = useCallback(
    (dir: [number, number, number], scale = 1): THREE.Vector3 => {
      const cam = camera as THREE.PerspectiveCamera;
      const tanY = Math.tan(((cam.fov ?? 55) * Math.PI) / 360);
      const tanX = tanY * (cam.aspect || 1.6);
      const d = new THREE.Vector3(dir[0], dir[1], dir[2]).normalize();
      const ref = Math.abs(d.y) > 0.95 ? UP_ALT : UP;
      const right = new THREE.Vector3().crossVectors(ref, d).normalize();
      const up = new THREE.Vector3().crossVectors(d, right).normalize();
      let lateral = data.focus.radius * 0.25;
      let depth = 0;
      for (const c of corners) {
        const v = TMP_C.subVectors(c, centre);
        lateral = Math.max(lateral, Math.abs(v.dot(up)) / tanY, Math.abs(v.dot(right)) / tanX);
        depth = Math.max(depth, Math.abs(v.dot(d)));
      }
      // Lateral extent sets the framing; only a fraction of the depth extent is added back.
      // Paying for the full near corner would leave the map tiny in the middle of the frame.
      return d.multiplyScalar((lateral + depth * 0.5) * 1.12 * scale).add(centre);
    },
    [camera, centre, corners, data.focus.radius],
  );

  const startMove = useCallback(
    (toPos: THREE.Vector3, toTarget: THREE.Vector3, duration: number) => {
      if (reducedMotion || duration <= 0) {
        camera.position.copy(toPos);
        controls.current?.target.copy(toTarget);
        controls.current?.update();
        invalidate();
        return;
      }
      move.current = {
        fromPos: camera.position.clone(),
        toPos,
        fromTarget: controls.current?.target.clone() ?? centre.clone(),
        toTarget,
        start: performance.now(),
        duration,
      };
      invalidate();
    },
    [camera, centre, invalidate, reducedMotion],
  );

  useFrame((state, delta) => {
    const c = controls.current;

    // Opening shot, deferred to the first rendered frame: the exact fit depends on the
    // camera's aspect ratio, which is not known until the canvas has been measured.
    if (pendingIntro.current && state.size.width > 0) {
      pendingIntro.current = false;
      const home = positionFor(HOME_DIR);
      c?.target.copy(centre);
      if (!flyIn || reducedMotion) {
        camera.position.copy(home);
        c?.update();
      } else {
        camera.position.copy(positionFor([0.25, 1.3, 1.5], 1.9));
        startMove(home, centre.clone(), 2100);
      }
      invalidate();
    }

    if (runtime.fitRequest !== lastFit.current) {
      lastFit.current = runtime.fitRequest;
      runtime.follow = false;
      startMove(positionFor(HOME_DIR), centre.clone(), 620);
    }
    if (runtime.presetRequest && runtime.presetRequest.n !== lastPreset.current) {
      lastPreset.current = runtime.presetRequest.n;
      runtime.follow = false;
      startMove(positionFor(PRESET_DIR[runtime.presetRequest.preset]), centre.clone(), 620);
    }

    if (runtime.follow) {
      if (!wasFollowing.current) {
        wasFollowing.current = true;
        move.current = null;
        if (c) c.enabled = false;
      }
      const n = data.poseCount;
      const f = Math.max(0, Math.min(n - 1, runtime.frame));
      const i = Math.floor(f);
      const j = Math.min(n - 1, i + 1);
      const t = f - i;
      TMP_A.set(data.trajectory[i * 3]!, data.trajectory[i * 3 + 1]!, data.trajectory[i * 3 + 2]!);
      TMP_B.set(data.trajectory[j * 3]!, data.trajectory[j * 3 + 1]!, data.trajectory[j * 3 + 2]!);
      TMP_A.lerp(TMP_B, t);
      QA.set(data.quaternions[i * 4]!, data.quaternions[i * 4 + 1]!, data.quaternions[i * 4 + 2]!, data.quaternions[i * 4 + 3]!);
      QB.set(data.quaternions[j * 4]!, data.quaternions[j * 4 + 1]!, data.quaternions[j * 4 + 2]!, data.quaternions[j * 4 + 3]!);
      QA.slerp(QB, t).multiply(CV_TO_GL);
      if (reducedMotion) {
        camera.position.copy(TMP_A);
        camera.quaternion.copy(QA);
      } else {
        // Exponential smoothing: follows the pose without inheriting handheld jitter.
        const k = 1 - Math.exp(-delta * 9);
        camera.position.lerp(TMP_A, k);
        camera.quaternion.slerp(QA, 1 - Math.exp(-delta * 7));
      }
      invalidate();
      return;
    }

    if (wasFollowing.current) {
      wasFollowing.current = false;
      if (c) {
        c.enabled = true;
        c.target.copy(centre);
        c.update();
      }
      startMove(positionFor(HOME_DIR), centre.clone(), 700);
    }

    const m = move.current;
    if (m) {
      const k = easeOutExpo(Math.min(1, (performance.now() - m.start) / m.duration));
      camera.position.lerpVectors(m.fromPos, m.toPos, k);
      if (c) {
        c.target.lerpVectors(m.fromTarget, m.toTarget, k);
        c.update();
      }
      if (k >= 1) move.current = null;
      invalidate();
    }
  }, -2);

  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping
      dampingFactor={0.08}
      rotateSpeed={0.8}
      panSpeed={0.8}
      zoomSpeed={0.9}
      minDistance={data.bounds.radius * 0.05}
      maxDistance={data.bounds.radius * 12}
    />
  );
}

const TMP_A = new THREE.Vector3();
const TMP_B = new THREE.Vector3();
const TMP_C = new THREE.Vector3();
const UP = new THREE.Vector3(0, 1, 0);
const UP_ALT = new THREE.Vector3(0, 0, 1);
const QA = new THREE.Quaternion();
const QB = new THREE.Quaternion();
/** OpenCV camera axes (+Z forward, +Y down) -> OpenGL camera axes (-Z forward, +Y up). */
const CV_TO_GL = new THREE.Quaternion(1, 0, 0, 0);

/**
 * Advances the playhead, keeps the demand render loop alive while something is moving, and
 * writes the readouts straight into their DOM nodes.
 */
export function PlaybackDriver({ data, runtime }: { data: SceneData; runtime: ViewerRuntime }) {
  const { invalidate } = useThree();
  const lastWritten = useRef(-1);
  const fpsAcc = useRef({ frames: 0, since: 0, lastFrameAt: 0 });

  useEffect(() => {
    runtime.invalidate = invalidate;
    return () => {
      runtime.invalidate = null;
    };
  }, [invalidate, runtime]);

  // `frameloop="demand"` renders nothing while the scene is still, so the counter below
  // stops being updated and would otherwise sit on a stale number for as long as the user
  // looks at it. A dash is the honest reading for "not rendering right now".
  useEffect(() => {
    const timer = setInterval(() => {
      if (!runtime.fpsEl || performance.now() - fpsAcc.current.lastFrameAt < 400) return;
      fpsAcc.current.frames = 0;
      fpsAcc.current.since = 0;
      runtime.fpsEl.textContent = '—';
    }, 400);
    return () => clearInterval(timer);
  }, [runtime]);

  const clipFps = useMemo(() => {
    const span = data.times[data.poseCount - 1]! - data.times[0]!;
    return span > 0 ? (data.poseCount - 1) / span : 30;
  }, [data]);

  useFrame((_state, delta) => {
    const now = performance.now();
    const n = data.poseCount;

    if (runtime.playing) {
      runtime.frame += delta * clipFps * runtime.speed;
      if (runtime.frame >= n - 1) {
        runtime.frame = n - 1;
        runtime.playing = false;
        runtime.onPlayStateChange?.(false);
      }
      invalidate();
    }
    if (now - runtime.pulseAt < 1400) invalidate();

    const f = Math.floor(runtime.frame);
    if (f !== lastWritten.current) {
      lastWritten.current = f;
      if (runtime.scrubberEl && document.activeElement !== runtime.scrubberEl) {
        runtime.scrubberEl.value = String(f);
      }
      if (runtime.timeEl) runtime.timeEl.textContent = `${(data.times[f] ?? 0).toFixed(2)}s`;
      if (runtime.frameEl) runtime.frameEl.textContent = `${f + 1}/${n}`;
    }

    const acc = fpsAcc.current;
    acc.lastFrameAt = now;
    acc.frames += 1;
    if (acc.since === 0) acc.since = now;
    if (now - acc.since >= 500) {
      if (runtime.fpsEl) runtime.fpsEl.textContent = `${Math.round((acc.frames * 1000) / (now - acc.since))}`;
      acc.frames = 0;
      acc.since = now;
    }
  }, -1);

  return null;
}
