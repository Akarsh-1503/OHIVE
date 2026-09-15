/**
 * Synthetic reconstruction for `NEXT_PUBLIC_MOCK=1`.
 *
 * Produces exactly the shapes in `_contracts/assignment2-api.md`: a closed-loop handheld
 * camera path around a room, a few thousand coloured map points, and loop closures. It is
 * deterministic (seeded) so screenshots and e2e runs are reproducible.
 *
 * Everything is authored in the contract's convention — camera looks down +Z, +X right,
 * +Y down — and is finally re-expressed in the first keyframe's camera frame, exactly as a
 * real monocular pipeline reports it.
 */

import type { Metrics, Pose, Reconstruction } from './types';

const FRAME_COUNT = 300;
const FPS = 30;
const KEYFRAME_EVERY = 8;

/** mulberry32 — small, fast, good enough for scene noise. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Vec3 = [number, number, number];
type Quat = [number, number, number, number]; // w, x, y, z

function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function norm(a: Vec3): Vec3 {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/** Rotation matrix (column-major columns r, d, f) -> quaternion [w,x,y,z]. */
function matToQuat(r: Vec3, d: Vec3, f: Vec3): Quat {
  const m00 = r[0],
    m10 = r[1],
    m20 = r[2];
  const m01 = d[0],
    m11 = d[1],
    m21 = d[2];
  const m02 = f[0],
    m12 = f[1],
    m22 = f[2];
  const tr = m00 + m11 + m22;
  if (tr > 0) {
    const s = Math.sqrt(tr + 1) * 2;
    return [0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s];
  }
  if (m00 > m11 && m00 > m22) {
    const s = Math.sqrt(1 + m00 - m11 - m22) * 2;
    return [(m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s];
  }
  if (m11 > m22) {
    const s = Math.sqrt(1 + m11 - m00 - m22) * 2;
    return [(m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s];
  }
  const s = Math.sqrt(1 + m22 - m00 - m11) * 2;
  return [(m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s];
}

function quatMul(a: Quat, b: Quat): Quat {
  const [aw, ax, ay, az] = a;
  const [bw, bx, by, bz] = b;
  return [
    aw * bw - ax * bx - ay * by - az * bz,
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
  ];
}

function quatConj(q: Quat): Quat {
  return [q[0], -q[1], -q[2], -q[3]];
}

function quatRotate(q: Quat, v: Vec3): Vec3 {
  const [w, x, y, z] = q;
  const tx = 2 * (y * v[2] - z * v[1]);
  const ty = 2 * (z * v[0] - x * v[2]);
  const tz = 2 * (x * v[1] - y * v[0]);
  return [
    v[0] + w * tx + (y * tz - z * ty),
    v[1] + w * ty + (z * tx - x * tz),
    v[2] + w * tz + (x * ty - y * tx),
  ];
}

/** Yaw about the world "down" axis (+Y in this convention). */
function yawQuat(theta: number): Quat {
  return [Math.cos(theta / 2), 0, Math.sin(theta / 2), 0];
}

interface RawPose {
  p: Vec3;
  q: Quat;
}

/** Handheld walk around a closed elliptical loop, looking outward at the walls. */
function cameraPath(rand: () => number): RawPose[] {
  const centre: Vec3 = [0, 0, 1];
  const ex = 2.75;
  const ez = 1.95;
  const out: RawPose[] = [];
  for (let i = 0; i < FRAME_COUNT; i++) {
    const u = i / FRAME_COUNT;
    const a = u * Math.PI * 2;
    const wobble = (rand() - 0.5) * 0.012;
    const p: Vec3 = [
      centre[0] + ex * Math.sin(a) + wobble,
      centre[1] + 0.06 * Math.sin(a * 3.1) + (rand() - 0.5) * 0.01,
      centre[2] + ez * Math.cos(a) + wobble,
    ];
    const tangent = norm([ex * Math.cos(a), 0, -ez * Math.sin(a)]);
    const outward = norm([p[0] - centre[0], 0, p[2] - centre[2]]);
    // Look mostly outward with a bit of lead along the direction of travel, plus a slow
    // pitch bob — this is what a person filming a room actually does.
    const f = norm([
      tangent[0] * 0.5 + outward[0] * 0.87,
      0.09 * Math.sin(a * 2.3) + (rand() - 0.5) * 0.004,
      tangent[2] * 0.5 + outward[2] * 0.87,
    ]);
    const r = norm(cross([0, 1, 0], f));
    const d = norm(cross(f, r));
    out.push({ p, q: matToQuat(r, d, f) });
  }
  return out;
}

interface RawPoint {
  p: Vec3;
  c: [number, number, number];
  kf: number;
}

function pushBox(
  acc: RawPoint[],
  rand: () => number,
  n: number,
  min: Vec3,
  max: Vec3,
  base: [number, number, number],
  spread: number,
): void {
  for (let i = 0; i < n; i++) {
    // Bias to the shell of the box: SLAM sees surfaces, not volumes.
    const p: Vec3 = [
      min[0] + rand() * (max[0] - min[0]),
      min[1] + rand() * (max[1] - min[1]),
      min[2] + rand() * (max[2] - min[2]),
    ];
    const axis = Math.min(2, Math.floor(rand() * 3)) as 0 | 1 | 2;
    p[axis] = (rand() < 0.5 ? min[axis] : max[axis]) + (rand() - 0.5) * 0.015;
    const l = (rand() - 0.5) * spread;
    acc.push({
      p,
      c: [
        Math.max(0, Math.min(255, base[0] + l)),
        Math.max(0, Math.min(255, base[1] + l)),
        Math.max(0, Math.min(255, base[2] + l)),
      ],
      kf: 0,
    });
  }
}

function pushPlane(
  acc: RawPoint[],
  rand: () => number,
  n: number,
  origin: Vec3,
  u: Vec3,
  v: Vec3,
  base: [number, number, number],
  spread: number,
  thickness: number,
): void {
  for (let i = 0; i < n; i++) {
    const a = rand();
    const b = rand();
    // Clustered texture: real ORB features bunch on corners and texture, they are not uniform.
    const clump = rand() < 0.45 ? 0.5 + (rand() - 0.5) * 0.22 : a;
    const jitter = (rand() - 0.5) * thickness;
    const nrm = norm(cross(u, v));
    const p: Vec3 = [
      origin[0] + u[0] * clump + v[0] * b + nrm[0] * jitter,
      origin[1] + u[1] * clump + v[1] * b + nrm[1] * jitter,
      origin[2] + u[2] * clump + v[2] * b + nrm[2] * jitter,
    ];
    const l = (rand() - 0.5) * spread;
    acc.push({
      p,
      c: [
        Math.max(0, Math.min(255, base[0] + l)),
        Math.max(0, Math.min(255, base[1] + l)),
        Math.max(0, Math.min(255, base[2] + l)),
      ],
      kf: 0,
    });
  }
}

function buildRoom(rand: () => number, target: number): RawPoint[] {
  const acc: RawPoint[] = [];
  const share = (f: number) => Math.round(target * f);
  const X = 3.7;
  const Zn = -2.7;
  const Zf = 4.7;
  const FLOOR = 1.5;
  const CEIL = -1.35;

  // Walls
  pushPlane(acc, rand, share(0.12), [-X, CEIL, Zn], [0, FLOOR - CEIL, 0], [0, 0, Zf - Zn], [146, 158, 176], 40, 0.03);
  pushPlane(acc, rand, share(0.12), [X, CEIL, Zn], [0, FLOOR - CEIL, 0], [0, 0, Zf - Zn], [152, 148, 162], 40, 0.03);
  pushPlane(acc, rand, share(0.09), [-X, CEIL, Zf], [2 * X, 0, 0], [0, FLOOR - CEIL, 0], [186, 170, 146], 36, 0.03);
  pushPlane(acc, rand, share(0.08), [-X, CEIL, Zn], [2 * X, 0, 0], [0, FLOOR - CEIL, 0], [136, 148, 168], 36, 0.03);
  // Floor and ceiling
  pushPlane(acc, rand, share(0.15), [-X, FLOOR, Zn], [2 * X, 0, 0], [0, 0, Zf - Zn], [118, 92, 70], 32, 0.02);
  pushPlane(acc, rand, share(0.03), [-X, CEIL, Zn], [2 * X, 0, 0], [0, 0, Zf - Zn], [168, 172, 180], 18, 0.02);

  // Furniture — distinct colours so the RGB view reads as a real scan
  pushBox(acc, rand, share(0.07), [-3.2, 0.72, 1.9], [-1.4, 1.5, 2.8], [126, 82, 52], 30); // desk
  pushBox(acc, rand, share(0.05), [-3.1, -0.6, 2.1], [-2.3, 0.7, 2.7], [70, 78, 96], 24); // monitor
  pushBox(acc, rand, share(0.06), [2.0, -0.5, -1.1], [3.4, 1.5, 0.5], [206, 170, 108], 34); // shelf
  pushBox(acc, rand, share(0.05), [0.4, 0.55, 3.5], [2.0, 1.5, 4.4], [140, 144, 152], 28); // cabinet
  pushBox(acc, rand, share(0.05), [-0.9, 0.1, -2.2], [0.4, 1.5, -1.3], [72, 142, 84], 40); // plant
  pushBox(acc, rand, share(0.04), [1.1, -1.35, 1.3], [1.5, 1.5, 1.7], [172, 168, 160], 20); // pillar
  pushBox(acc, rand, share(0.04), [-1.9, 0.9, -0.5], [-0.9, 1.5, 0.4], [88, 92, 112], 26); // chair

  // Clutter: small high-texture blobs scattered on surfaces
  const clutter = target - acc.length;
  for (let i = 0; i < clutter; i++) {
    const cx = -X + rand() * 2 * X;
    const cz = Zn + rand() * (Zf - Zn);
    const onFloor = rand() < 0.6;
    const cy = onFloor ? FLOOR - rand() * 0.22 : CEIL + rand() * (FLOOR - CEIL);
    const hue = rand();
    acc.push({
      p: [cx + (rand() - 0.5) * 0.12, cy + (rand() - 0.5) * 0.12, cz + (rand() - 0.5) * 0.12],
      c: [124 + hue * 100, 118 + rand() * 90, 112 + (1 - hue) * 100],
      kf: 0,
    });
  }
  return acc;
}

export interface ScenePresets {
  points: number;
  loopClosure: boolean;
  seed?: number;
}

/**
 * Without loop closure, pose k picks up a cumulative yaw + scale error. Points inherit the
 * drift of the keyframe that first triangulated them, which is what smears a real map.
 */
function driftFor(kf: number, enabled: boolean): { q: Quat; s: number } {
  if (enabled) return { q: [1, 0, 0, 0], s: 1 };
  return { q: yawQuat(kf * 0.0061), s: 1 + kf * 0.0049 };
}

export function buildMockReconstruction(jobId: string, presets: ScenePresets): Reconstruction {
  const rand = rng(presets.seed ?? 0x5eed_1234);
  const path = cameraPath(rand);
  const raw = buildRoom(rand, presets.points);

  const kfIndices: number[] = [];
  for (let i = 0; i < FRAME_COUNT; i++) if (i % KEYFRAME_EVERY === 0) kfIndices.push(i);
  const kfPositions = kfIndices.map((i) => path[i]!.p);

  // Nearest keyframe = the one that most plausibly first saw the point.
  for (const pt of raw) {
    let best = 0;
    let bestD = Infinity;
    for (let k = 0; k < kfPositions.length; k++) {
      const kp = kfPositions[k]!;
      const d = (pt.p[0] - kp[0]) ** 2 + (pt.p[1] - kp[1]) ** 2 + (pt.p[2] - kp[2]) ** 2;
      if (d < bestD) {
        bestD = d;
        best = k;
      }
    }
    pt.kf = best;
  }

  // Apply drift in the room frame, then re-express everything in keyframe 0's camera frame.
  const posed: RawPose[] = path.map((pose, i) => {
    const kf = Math.min(kfPositions.length - 1, Math.floor(i / KEYFRAME_EVERY));
    const { q: dq, s } = driftFor(kf, presets.loopClosure);
    const p = quatRotate(dq, pose.p);
    return { p: [p[0] * s, p[1] * s, p[2] * s], q: quatMul(dq, pose.q) };
  });

  const p0 = posed[0]!;
  const q0inv = quatConj(p0.q);
  const toWorld = (p: Vec3): Vec3 => quatRotate(q0inv, sub(p, p0.p));

  const poses: Pose[] = posed.map((pose, i) => {
    const pos = toWorld(pose.p);
    const q = quatMul(q0inv, pose.q);
    const isKf = i % KEYFRAME_EVERY === 0;
    const drop = Math.max(0, Math.sin(i / 37) * 90);
    return {
      frame_index: i,
      t_s: Number((i / FPS).toFixed(4)),
      is_keyframe: isKf,
      position: [round4(pos[0]), round4(pos[1]), round4(pos[2])],
      quaternion: [round4(q[0]), round4(q[1]), round4(q[2]), round4(q[3])],
      tracked_points: Math.round(420 + drop + rand() * 90),
      reprojection_error_px: Number((0.52 + rand() * 0.55).toFixed(3)),
    };
  });

  const xyz: number[] = [];
  const rgb: number[] = [];
  const observations: number[] = [];
  for (const pt of raw) {
    const { q: dq, s } = driftFor(pt.kf, presets.loopClosure);
    const dp = quatRotate(dq, pt.p);
    const w = toWorld([dp[0] * s, dp[1] * s, dp[2] * s]);
    xyz.push(round4(w[0]), round4(w[1]), round4(w[2]));
    rgb.push(Math.round(pt.c[0]), Math.round(pt.c[1]), Math.round(pt.c[2]));
    const kp = kfPositions[pt.kf]!;
    const d = Math.hypot(pt.p[0] - kp[0], pt.p[1] - kp[1], pt.p[2] - kp[2]);
    // Track lengths in a real map are exponentially distributed: most points are seen by a
    // handful of keyframes, a few survive dozens. Proximity to the path scales the mean.
    const draw = -2.4 * Math.log(1 - rand() * 0.997);
    observations.push(Math.max(1, Math.min(24, Math.round(1 + draw * (0.45 + 1.7 * Math.exp(-d / 2.8))))));
  }

  const nKf = kfIndices.length;
  const loop_closures = presets.loopClosure
    ? [
        { from_kf: nKf - 2, to_kf: 1, inliers: 148, scale: 1.031 },
        { from_kf: nKf - 6, to_kf: 34 % nKf, inliers: 96, scale: 1.012 },
      ]
    : [];

  const metrics = mockMetrics(raw.length, nKf, presets.loopClosure);
  return { job_id: jobId, poses, points: { xyz, rgb, observations }, loop_closures, metrics };
}

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

export function mockMetrics(mapPoints: number, keyframes: number, loopClosure: boolean): Metrics {
  const wall = loopClosure ? 8420 : 6890;
  const videoDuration = FRAME_COUNT / FPS;
  return {
    wall_ms: wall,
    queue_wait_ms: 0,
    decode_ms: 640,
    tracking_ms: loopClosure ? 5310 : 5240,
    optimize_ms: loopClosure ? 2470 : 1010,
    frames_processed: FRAME_COUNT,
    processing_fps: Number((FRAME_COUNT / (wall / 1000)).toFixed(1)),
    realtime_factor: Number((videoDuration / (wall / 1000)).toFixed(3)),
    keyframes,
    map_points: mapPoints,
    mean_reprojection_error_px: loopClosure ? 0.81 : 1.34,
    median_track_length: loopClosure ? 7 : 6,
    loop_closures: loopClosure ? 2 : 0,
    loop_candidates_checked: loopClosure ? 41 : 0,
    drift: loopClosure
      ? {
          pre_optimization_loop_error_m: 0.412,
          post_optimization_loop_error_m: 0.031,
          reduction_pct: 92.5,
          scale_drift_ratio: 1.07,
        }
      : {
          pre_optimization_loop_error_m: 0.487,
          post_optimization_loop_error_m: 0.487,
          reduction_pct: 0,
          scale_drift_ratio: 1.184,
        },
    trajectory_length_m: loopClosure ? 6.94 : 7.62,
    ba_runs: keyframes,
    host: { cpu: 'Intel Cascade Lake @3.1GHz', vcpu: 8, ram_gb: 32 },
  };
}

export const MOCK_FRAME_COUNT = FRAME_COUNT;
export const MOCK_FPS = FPS;
