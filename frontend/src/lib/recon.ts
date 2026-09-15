/**
 * Turns the contract's flat JSON arrays into GPU-ready typed arrays exactly once.
 *
 * Nothing here ever allocates a per-point JavaScript object: a 60 000-point payload becomes
 * four typed arrays and is handed straight to a single `THREE.Points`.
 *
 * Convention: the API reports an OpenCV world (X right, Y **down**, Z forward). three.js wants
 * Y up, so every position is mapped (x, y, z) -> (x, -y, -z) and every quaternion is
 * pre-multiplied by a 180° rotation about X. That is a pure basis change — no data is altered.
 */

import type { LoopClosure, Pose, Reconstruction } from './types';

export interface Bounds {
  min: [number, number, number];
  max: [number, number, number];
  centre: [number, number, number];
  radius: number;
}

export interface LoopArc {
  from_kf: number;
  to_kf: number;
  inliers: number;
  scale: number;
  /** Sampled arc polyline in three-space. */
  curve: Float32Array;
}

export interface SceneData {
  pointCount: number;
  /** Point order is shuffled once so the density slider can subsample with `setDrawRange`. */
  positions: Float32Array;
  colorSource: Uint8Array;
  colorHeight: Uint8Array;
  colorObs: Uint8Array;
  observations: Uint8Array;
  maxObservations: number;

  poseCount: number;
  /** Per-frame camera centres, three-space, flat xyz. */
  trajectory: Float32Array;
  /** Per-frame world-from-camera rotation, three-space, flat xyzw. */
  quaternions: Float32Array;
  times: Float32Array;
  trackedPoints: Uint16Array;
  reprojErrors: Float32Array;

  keyframeIndices: Uint16Array;
  /** Merged wireframe frusta for every keyframe: one LineSegments draw call. */
  frustumSegments: Float32Array;
  frustumSegmentsPerKeyframe: number;
  frustumScale: number;

  loopArcs: LoopArc[];
  bounds: Bounds;
  /** What the camera frames: the whole trajectory plus the core of the cloud, outliers excluded. */
  focus: Bounds;
  /** 3rd-percentile height of the cloud — where the reference grid is drawn. */
  groundY: number;
  poses: Pose[];
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Stop = readonly [number, number, number];

const HEIGHT_RAMP: Stop[] = [
  [12, 74, 110],
  [34, 211, 238],
  [129, 140, 248],
  [167, 139, 250],
  [252, 211, 77],
];

/** Viridis, 6-stop. Sequential and colourblind-safe, the right choice for a quality view. */
const OBS_RAMP: Stop[] = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [187, 223, 39],
  [253, 231, 37],
];

function sampleRamp(ramp: Stop[], t: number, out: Uint8Array, at: number): void {
  const c = Math.max(0, Math.min(1, t)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(c));
  const f = c - i;
  const a = ramp[i]!;
  const b = ramp[i + 1]!;
  out[at] = a[0] + (b[0] - a[0]) * f;
  out[at + 1] = a[1] + (b[1] - a[1]) * f;
  out[at + 2] = a[2] + (b[2] - a[2]) * f;
}

function buildFrustum(scale: number): Float32Array {
  // Local axes are the contract's camera axes: +Z forward, +Y down, +X right.
  const d = scale;
  const w = scale * 0.72;
  const h = scale * 0.52;
  const apex: [number, number, number] = [0, 0, 0];
  const c: [number, number, number][] = [
    [-w, -h, d],
    [w, -h, d],
    [w, h, d],
    [-w, h, d],
  ];
  const segs: [number, number, number][][] = [
    [apex, c[0]!],
    [apex, c[1]!],
    [apex, c[2]!],
    [apex, c[3]!],
    [c[0]!, c[1]!],
    [c[1]!, c[2]!],
    [c[2]!, c[3]!],
    [c[3]!, c[0]!],
    // little "up" nub so the frustum's roll is readable
    [c[3]!, [0, -h * 1.9, d]],
    [c[2]!, [0, -h * 1.9, d]],
  ];
  const out = new Float32Array(segs.length * 6);
  segs.forEach((s, i) => {
    out.set(s[0]!, i * 6);
    out.set(s[1]!, i * 6 + 3);
  });
  return out;
}

/** Rotate a vector by a three-space quaternion given as (x, y, z, w). */
function rotate(q: Float32Array, qi: number, vx: number, vy: number, vz: number): [number, number, number] {
  const x = q[qi]!;
  const y = q[qi + 1]!;
  const z = q[qi + 2]!;
  const w = q[qi + 3]!;
  const tx = 2 * (y * vz - z * vy);
  const ty = 2 * (z * vx - x * vz);
  const tz = 2 * (x * vy - y * vx);
  return [vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx)];
}

export function buildSceneData(recon: Reconstruction): SceneData {
  const n = recon.points.observations.length;
  const srcXyz = recon.points.xyz;
  const srcRgb = recon.points.rgb;
  const srcObs = recon.points.observations;

  const positions = new Float32Array(n * 3);
  const colorSource = new Uint8Array(n * 3);
  const colorHeight = new Uint8Array(n * 3);
  const colorObs = new Uint8Array(n * 3);
  const observations = new Uint8Array(n);

  // Fisher–Yates over indices, seeded: subsampling by draw range then stays spatially uniform.
  const order = new Uint32Array(n);
  for (let i = 0; i < n; i++) order[i] = i;
  const rand = mulberry32(0x9e3779b9);
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const t = order[i]!;
    order[i] = order[j]!;
    order[j] = t;
  }

  let minX = Infinity,
    minY = Infinity,
    minZ = Infinity,
    maxX = -Infinity,
    maxY = -Infinity,
    maxZ = -Infinity,
    maxObs = 1;

  for (let k = 0; k < n; k++) {
    const s = order[k]! * 3;
    const x = srcXyz[s] ?? 0;
    const y = -(srcXyz[s + 1] ?? 0);
    const z = -(srcXyz[s + 2] ?? 0);
    const d = k * 3;
    positions[d] = x;
    positions[d + 1] = y;
    positions[d + 2] = z;
    colorSource[d] = srcRgb[s] ?? 0;
    colorSource[d + 1] = srcRgb[s + 1] ?? 0;
    colorSource[d + 2] = srcRgb[s + 2] ?? 0;
    const o = Math.min(255, srcObs[order[k]!] ?? 1);
    observations[k] = o;
    if (o > maxObs) maxObs = o;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    if (z > maxZ) maxZ = z;
  }

  const spanY = maxY - minY || 1;
  const obsSpan = Math.max(1, maxObs - 1);
  for (let k = 0; k < n; k++) {
    sampleRamp(HEIGHT_RAMP, (positions[k * 3 + 1]! - minY) / spanY, colorHeight, k * 3);
    // Observation counts are heavily skewed towards short tracks; a square-root ramp spends
    // most of the colour range where most of the points actually are.
    sampleRamp(OBS_RAMP, Math.sqrt((observations[k]! - 1) / obsSpan), colorObs, k * 3);
  }

  const centre: [number, number, number] = [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2];
  const radius = Math.max(0.001, Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) / 2);

  // Percentiles over a strided sample: used for the ground plane and for camera framing.
  // Fitting the camera to the true extents puts the nearest corner on the frustum edge and
  // leaves the actual map small in frame, so the camera frames the core instead.
  const sampleStride = Math.max(1, Math.floor(n / 4000));
  const sx: number[] = [];
  const sy: number[] = [];
  const sz: number[] = [];
  for (let k = 0; k < n; k += sampleStride) {
    sx.push(positions[k * 3]!);
    sy.push(positions[k * 3 + 1]!);
    sz.push(positions[k * 3 + 2]!);
  }
  const asc = (a: number, b: number) => a - b;
  sx.sort(asc);
  sy.sort(asc);
  sz.sort(asc);
  const q = (arr: number[], f: number): number => arr[Math.min(arr.length - 1, Math.max(0, Math.floor(arr.length * f)))] ?? 0;
  const groundY = q(sy, 0.03);

  /* ---- poses ---- */
  const poses = recon.poses;
  const pn = poses.length;
  const trajectory = new Float32Array(pn * 3);
  const quaternions = new Float32Array(pn * 4);
  const times = new Float32Array(pn);
  const trackedPoints = new Uint16Array(pn);
  const reprojErrors = new Float32Array(pn);
  const kfList: number[] = [];

  for (let i = 0; i < pn; i++) {
    const p = poses[i]!;
    trajectory[i * 3] = p.position[0];
    trajectory[i * 3 + 1] = -p.position[1];
    trajectory[i * 3 + 2] = -p.position[2];
    const [qw, qx, qy, qz] = p.quaternion;
    // q_three = Rx(pi) * q_api
    quaternions[i * 4] = qw;
    quaternions[i * 4 + 1] = -qz;
    quaternions[i * 4 + 2] = qy;
    quaternions[i * 4 + 3] = -qx;
    times[i] = p.t_s;
    trackedPoints[i] = Math.min(65535, p.tracked_points);
    reprojErrors[i] = p.reprojection_error_px;
    if (p.is_keyframe) kfList.push(i);
  }

  const keyframeIndices = new Uint16Array(kfList);
  const frustumScale = radius * 0.032;
  const proto = buildFrustum(frustumScale);
  const perKf = proto.length / 3;
  const frustumSegments = new Float32Array(kfList.length * proto.length);
  for (let k = 0; k < kfList.length; k++) {
    const i = kfList[k]!;
    const px = trajectory[i * 3]!;
    const py = trajectory[i * 3 + 1]!;
    const pz = trajectory[i * 3 + 2]!;
    for (let v = 0; v < perKf; v++) {
      const r = rotate(quaternions, i * 4, proto[v * 3]!, proto[v * 3 + 1]!, proto[v * 3 + 2]!);
      const o = k * proto.length + v * 3;
      frustumSegments[o] = px + r[0];
      frustumSegments[o + 1] = py + r[1];
      frustumSegments[o + 2] = pz + r[2];
    }
  }

  const loopArcs = recon.loop_closures.map((lc) => buildLoopArc(lc, trajectory, keyframeIndices, centre, radius));

  // Framing volume: the 9th–91st percentile of the cloud, always extended to contain the
  // whole camera path — the trajectory is the one thing that must never be cropped.
  const fMin: [number, number, number] = [q(sx, 0.09), q(sy, 0.09), q(sz, 0.09)];
  const fMax: [number, number, number] = [q(sx, 0.91), q(sy, 0.91), q(sz, 0.91)];
  for (let i = 0; i < pn; i++) {
    for (let a = 0; a < 3; a++) {
      const v = trajectory[i * 3 + a]!;
      if (v < fMin[a]!) fMin[a] = v;
      if (v > fMax[a]!) fMax[a] = v;
    }
  }
  const focus: Bounds = {
    min: fMin,
    max: fMax,
    centre: [(fMin[0] + fMax[0]) / 2, (fMin[1] + fMax[1]) / 2, (fMin[2] + fMax[2]) / 2],
    radius: Math.max(0.001, Math.hypot(fMax[0] - fMin[0], fMax[1] - fMin[1], fMax[2] - fMin[2]) / 2),
  };

  return {
    pointCount: n,
    positions,
    colorSource,
    colorHeight,
    colorObs,
    observations,
    maxObservations: maxObs,
    poseCount: pn,
    trajectory,
    quaternions,
    times,
    trackedPoints,
    reprojErrors,
    keyframeIndices,
    frustumSegments,
    frustumSegmentsPerKeyframe: perKf,
    frustumScale,
    loopArcs,
    bounds: { min: [minX, minY, minZ], max: [maxX, maxY, maxZ], centre, radius },
    focus,
    groundY,
    poses,
  };
}

const ARC_SAMPLES = 40;

function buildLoopArc(
  lc: LoopClosure,
  trajectory: Float32Array,
  keyframeIndices: Uint16Array,
  centre: [number, number, number],
  radius: number,
): LoopArc {
  const kfCount = keyframeIndices.length;
  const a = keyframeIndices[Math.max(0, Math.min(kfCount - 1, lc.from_kf))] ?? 0;
  const b = keyframeIndices[Math.max(0, Math.min(kfCount - 1, lc.to_kf))] ?? 0;
  const ax = trajectory[a * 3]!;
  const ay = trajectory[a * 3 + 1]!;
  const az = trajectory[a * 3 + 2]!;
  const bx = trajectory[b * 3]!;
  const by = trajectory[b * 3 + 1]!;
  const bz = trajectory[b * 3 + 2]!;
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const mz = (az + bz) / 2;
  // Bulge away from the scene centre so the arc never hides inside the cloud.
  let ox = mx - centre[0];
  let oz = mz - centre[2];
  const ol = Math.hypot(ox, oz) || 1;
  ox /= ol;
  oz /= ol;
  const lift = radius * 0.18 + Math.hypot(ax - bx, ay - by, az - bz) * 0.22;
  const cx = mx + ox * lift * 0.5;
  const cy = my + lift;
  const cz = mz + oz * lift * 0.5;

  const curve = new Float32Array((ARC_SAMPLES + 1) * 3);
  for (let i = 0; i <= ARC_SAMPLES; i++) {
    const t = i / ARC_SAMPLES;
    const u = 1 - t;
    curve[i * 3] = u * u * ax + 2 * u * t * cx + t * t * bx;
    curve[i * 3 + 1] = u * u * ay + 2 * u * t * cy + t * t * by;
    curve[i * 3 + 2] = u * u * az + 2 * u * t * cz + t * t * bz;
  }
  return { from_kf: lc.from_kf, to_kf: lc.to_kf, inliers: lc.inliers, scale: lc.scale, curve };
}
