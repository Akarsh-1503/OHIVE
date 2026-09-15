'use client';

import { Line } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { Line2 } from 'three-stdlib';
import type { SceneData } from '@/lib/recon';
import type { ViewerRuntime } from './state';

export const LOOP_COLOR = '#ffb347';
const PULSE_MS = 1400;

function pulseAmount(runtime: ViewerRuntime, now: number): number {
  if (!runtime.pulseAt) return 0;
  const k = 1 - (now - runtime.pulseAt) / PULSE_MS;
  return k <= 0 ? 0 : k * k;
}

function toVectors(flat: Float32Array): THREE.Vector3[] {
  const out = new Array<THREE.Vector3>(flat.length / 3);
  for (let i = 0; i < out.length; i++) out[i] = new THREE.Vector3(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]);
  return out;
}

/**
 * The estimated path: a dim full-length line for context, plus a bright "travelled so far"
 * line whose instance count is trimmed each frame from the playhead. Trimming instances is
 * free; rebuilding geometry per frame would not be.
 */
export function TrajectoryPath({
  data,
  runtime,
  visible,
}: {
  data: SceneData;
  runtime: ViewerRuntime;
  visible: boolean;
}) {
  const points = useMemo(() => toVectors(data.trajectory), [data]);
  const glowRef = useRef<Line2>(null);
  const brightRef = useRef<Line2>(null);

  useFrame(() => {
    const segments = Math.max(1, Math.min(points.length - 1, Math.floor(runtime.frame)));
    const glow = glowRef.current;
    const bright = brightRef.current;
    if (bright) bright.geometry.instanceCount = segments;
    if (glow) {
      glow.geometry.instanceCount = segments;
      const p = pulseAmount(runtime, performance.now());
      const mat = glow.material;
      mat.opacity = 0.16 + p * 0.5;
      mat.linewidth = 7 + p * 9;
    }
  });

  if (points.length < 2) return null;
  return (
    <group visible={visible}>
      <Line points={points} color="#2a3a56" lineWidth={1.4} transparent opacity={0.75} depthWrite={false} />
      <Line ref={glowRef} points={points} color="#22d3ee" lineWidth={7} transparent opacity={0.16} depthWrite={false} />
      <Line ref={brightRef} points={points} color="#8ef2ff" lineWidth={1.9} transparent opacity={0.95} />
    </group>
  );
}

/** Every keyframe as a wireframe frustum, in one merged `LineSegments` draw call. */
export function KeyframeFrusta({
  data,
  runtime,
  visible,
}: {
  data: SceneData;
  runtime: ViewerRuntime;
  visible: boolean;
}) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.frustumSegments, 3));
    return g;
  }, [data]);
  const material = useMemo(
    () => new THREE.LineBasicMaterial({ color: new THREE.Color('#8b9bff'), transparent: true, opacity: 0.42 }),
    [],
  );

  useEffect(() => {
    return () => {
      geometry.dispose();
      material.dispose();
    };
  }, [geometry, material]);

  useFrame(() => {
    // Reveal frusta up to the playhead so scrubbing replays how the map was built.
    let shown = data.keyframeIndices.length;
    for (let i = 0; i < data.keyframeIndices.length; i++) {
      if (data.keyframeIndices[i]! > runtime.frame) {
        shown = i;
        break;
      }
    }
    geometry.setDrawRange(0, Math.max(0, shown) * data.frustumSegmentsPerKeyframe);
  });

  return <lineSegments geometry={geometry} material={material} visible={visible} frustumCulled={false} />;
}

/** Where the pose graph decided two moments in the clip are the same place. */
export function LoopArcs({ data, runtime, visible }: { data: SceneData; runtime: ViewerRuntime; visible: boolean }) {
  const refs = useRef<(Line2 | null)[]>([]);
  const markerRef = useRef<THREE.InstancedMesh>(null);
  const arcs = useMemo(() => data.loopArcs.map((a) => toVectors(a.curve)), [data]);

  const endpoints = useMemo(() => {
    const out: THREE.Vector3[] = [];
    for (const a of arcs) {
      if (a.length > 0) {
        out.push(a[0]!);
        out.push(a[a.length - 1]!);
      }
    }
    return out;
  }, [arcs]);

  useEffect(() => {
    const mesh = markerRef.current;
    if (!mesh) return;
    const m = new THREE.Matrix4();
    endpoints.forEach((p, i) => {
      m.makeTranslation(p.x, p.y, p.z);
      mesh.setMatrixAt(i, m);
    });
    mesh.instanceMatrix.needsUpdate = true;
  }, [endpoints]);

  useFrame(() => {
    const p = pulseAmount(runtime, performance.now());
    for (const line of refs.current) {
      if (!line) continue;
      line.material.opacity = 0.72 + p * 0.28;
      line.material.linewidth = 2.2 + p * 5;
    }
    const mesh = markerRef.current;
    if (mesh) {
      const s = 1 + p * 1.6;
      mesh.scale.setScalar(s);
    }
  });

  if (arcs.length === 0) return null;
  return (
    <group visible={visible}>
      {arcs.map((pts, i) => (
        <Line
          key={i}
          ref={(el: Line2 | null) => {
            refs.current[i] = el;
          }}
          points={pts}
          color={LOOP_COLOR}
          lineWidth={2.2}
          transparent
          opacity={0.72}
          depthWrite={false}
        />
      ))}
      <instancedMesh ref={markerRef} args={[undefined, undefined, Math.max(1, endpoints.length)]} frustumCulled={false}>
        <sphereGeometry args={[data.frustumScale * 0.28, 10, 8]} />
        <meshBasicMaterial color={LOOP_COLOR} transparent opacity={0.9} />
      </instancedMesh>
    </group>
  );
}

/** The camera as it was at the playhead: interpolated between the two nearest poses. */
export function PlayheadCamera({ data, runtime, visible }: { data: SceneData; runtime: ViewerRuntime; visible: boolean }) {
  const group = useRef<THREE.Group>(null);
  const qa = useMemo(() => new THREE.Quaternion(), []);
  const qb = useMemo(() => new THREE.Quaternion(), []);

  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const s = data.frustumScale * 1.5;
    const w = s * 0.72;
    const h = s * 0.52;
    const verts = new Float32Array([
      0, 0, 0, -w, -h, s, 0, 0, 0, w, -h, s, 0, 0, 0, w, h, s, 0, 0, 0, -w, h, s,
      -w, -h, s, w, -h, s, w, -h, s, w, h, s, w, h, s, -w, h, s, -w, h, s, -w, -h, s,
    ]);
    g.setAttribute('position', new THREE.BufferAttribute(verts, 3));
    return g;
  }, [data.frustumScale]);

  const material = useMemo(() => new THREE.LineBasicMaterial({ color: new THREE.Color('#ffffff') }), []);

  useEffect(() => {
    return () => {
      geometry.dispose();
      material.dispose();
    };
  }, [geometry, material]);

  useFrame(() => {
    const g = group.current;
    if (!g) return;
    // In follow-cam the scene camera sits inside this frustum, so it must be hidden per
    // frame rather than per render — `runtime.follow` never triggers a React update.
    g.visible = visible && !runtime.follow;
    if (!g.visible) return;
    const n = data.poseCount;
    const f = Math.max(0, Math.min(n - 1, runtime.frame));
    const i = Math.floor(f);
    const j = Math.min(n - 1, i + 1);
    const t = f - i;
    g.position.set(
      THREE.MathUtils.lerp(data.trajectory[i * 3]!, data.trajectory[j * 3]!, t),
      THREE.MathUtils.lerp(data.trajectory[i * 3 + 1]!, data.trajectory[j * 3 + 1]!, t),
      THREE.MathUtils.lerp(data.trajectory[i * 3 + 2]!, data.trajectory[j * 3 + 2]!, t),
    );
    qa.set(data.quaternions[i * 4]!, data.quaternions[i * 4 + 1]!, data.quaternions[i * 4 + 2]!, data.quaternions[i * 4 + 3]!);
    qb.set(data.quaternions[j * 4]!, data.quaternions[j * 4 + 1]!, data.quaternions[j * 4 + 2]!, data.quaternions[j * 4 + 3]!);
    g.quaternion.copy(qa).slerp(qb, t);
  });

  return (
    <group ref={group} visible={visible}>
      <lineSegments geometry={geometry} material={material} frustumCulled={false} />
      <mesh>
        <sphereGeometry args={[data.frustumScale * 0.16, 12, 10]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
    </group>
  );
}
