"""The synthetic `stub` backend: a plausible reconstruction with no engine behind it.

Like the real adapter, everything here runs in a *child process*.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.runner import adapter


def _stub_reconstruction(
    task: adapter.WorkerTask, on_progress: Callable[[dict[str, Any]], None]
) -> dict[str, Any]:
    """A plausible synthetic reconstruction: camera on a helix, points on the surrounding
    shell, contract-shaped metrics, realistic progress pacing. Deterministic per job id.
    """
    cfg = task.config
    rng = np.random.default_rng(abs(hash(task.job_id)) % (2**32))

    video = task.job_header.get("video") or {}
    fps = float(video.get("fps") or 30.0)
    frame_count = int(video.get("frame_count") or 300)
    # Floor the synthetic clip at 12 frames so a trivially short probe still yields a map,
    # but never above the caller's max_frames — truncation has to be observable.
    n_frames = max(2, min(max(frame_count, 12), cfg.max_frames))

    poses, kf_indices = _helix_poses(n_frames, fps, rng)
    colors = _sample_colors(Path(task.video_path), n_frames)
    xyz, rgb, obs, perr = _shell_points(rng, colors, len(kf_indices))

    closures: list[dict[str, Any]] = []
    if cfg.enable_loop_closure and len(kf_indices) >= 8:
        closures = [
            {"from_kf": len(kf_indices) - 2, "to_kf": 2, "inliers": int(rng.integers(90, 190)),
             "scale": round(float(rng.uniform(0.97, 1.06)), 4)},
            {"from_kf": len(kf_indices) - 1, "to_kf": 4, "inliers": int(rng.integers(70, 150)),
             "scale": round(float(rng.uniform(0.98, 1.04)), 4)},
        ]

    decode_ms, tracking_ms, optimize_ms = _pace_stub(
        task, poses, kf_indices, len(xyz) // 3, closures, on_progress
    )

    traj = np.asarray([p["position"] for p in poses], dtype=np.float64)
    traj_len = float(np.linalg.norm(np.diff(traj, axis=0), axis=1).sum())
    pre_err = float(rng.uniform(0.28, 0.62))
    post_err = pre_err * float(rng.uniform(0.05, 0.12))

    metrics = {
        "wall_ms": 0,
        "queue_wait_ms": 0,
        "decode_ms": decode_ms,
        "tracking_ms": tracking_ms,
        "optimize_ms": optimize_ms,
        "frames_processed": n_frames,
        "processing_fps": 0.0,
        "realtime_factor": 0.0,
        "keyframes": len(kf_indices),
        "map_points": len(xyz) // 3,
        "mean_reprojection_error_px": round(float(np.mean(perr)), 3),
        "median_track_length": float(np.median(obs)),
        "loop_closures": len(closures),
        "loop_candidates_checked": int(len(kf_indices) * 1.4) + len(closures),
        "drift": {
            "pre_optimization_loop_error_m": round(pre_err, 4),
            "post_optimization_loop_error_m": round(post_err, 4),
            "reduction_pct": round(100.0 * (1.0 - post_err / pre_err), 2),
            "scale_drift_ratio": closures[-1]["scale"] if closures else 1.0,
        },
        "trajectory_length_m": round(traj_len, 3),
        "ba_runs": len(kf_indices),
        "host": adapter.host_info(),
    }
    return {
        "job_id": task.job_id,
        "poses": poses,
        "points": {
            "xyz": [round(float(v), 5) for v in xyz],
            "rgb": [int(v) for v in rgb],
            "observations": [int(v) for v in obs],
            "reprojection_error": [round(float(v), 4) for v in perr],
        },
        "loop_closures": closures,
        "metrics": metrics,
    }


def _helix_poses(
    n_frames: int, fps: float, rng: np.random.Generator
) -> tuple[list[dict[str, Any]], list[int]]:
    """Camera orbiting an object on a rising helix, re-based so pose 0 is the world origin
    (the contract's world frame is the first keyframe camera frame)."""
    radius, turns, rise = 1.5, 1.6, 0.45
    kf_stride = max(4, n_frames // 36)

    mats: list[np.ndarray] = []
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        angle = 2.0 * math.pi * turns * t
        pos = np.array([radius * math.sin(angle), -rise * t, radius * math.cos(angle)])
        forward = -pos / np.linalg.norm(pos)  # look back at the orbit centre
        down = np.array([0.0, 1.0, 0.0])
        down = down - np.dot(down, forward) * forward
        down /= np.linalg.norm(down)
        right = np.cross(down, forward)
        rot = np.column_stack([right, down, forward])  # world-from-camera
        mat = np.eye(4)
        mat[:3, :3], mat[:3, 3] = rot, pos
        mats.append(mat)

    base_inv = np.linalg.inv(mats[0])
    poses: list[dict[str, Any]] = []
    kf_indices: list[int] = []
    for i, mat in enumerate(mats):
        rebased = base_inv @ mat
        is_kf = i % kf_stride == 0
        if is_kf:
            kf_indices.append(i)
        poses.append(
            {
                "frame_index": i,
                "t_s": round(i / fps, 6),
                "is_keyframe": is_kf,
                "position": [round(float(v), 6) for v in rebased[:3, 3]],
                "quaternion": _quat_from_matrix(rebased[:3, :3]),
                "tracked_points": int(rng.integers(320, 940)),
                "reprojection_error_px": round(float(rng.uniform(0.45, 1.15)), 3),
            }
        )
    return poses, kf_indices


def _quat_from_matrix(rot: np.ndarray) -> list[float]:
    """Shepperd's method — branch on the largest diagonal term for numerical stability."""
    trace = float(np.trace(rot))
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw, qx = 0.25 * s, (rot[2, 1] - rot[1, 2]) / s
        qy, qz = (rot[0, 2] - rot[2, 0]) / s, (rot[1, 0] - rot[0, 1]) / s
    elif rot[0, 0] > rot[1, 1] and rot[0, 0] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[0, 0] - rot[1, 1] - rot[2, 2]) * 2.0
        qw, qx = (rot[2, 1] - rot[1, 2]) / s, 0.25 * s
        qy, qz = (rot[0, 1] + rot[1, 0]) / s, (rot[0, 2] + rot[2, 0]) / s
    elif rot[1, 1] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[1, 1] - rot[0, 0] - rot[2, 2]) * 2.0
        qw, qx = (rot[0, 2] - rot[2, 0]) / s, (rot[0, 1] + rot[1, 0]) / s
        qy, qz = 0.25 * s, (rot[1, 2] + rot[2, 1]) / s
    else:
        s = math.sqrt(1.0 + rot[2, 2] - rot[0, 0] - rot[1, 1]) * 2.0
        qw, qx = (rot[1, 0] - rot[0, 1]) / s, (rot[0, 2] + rot[2, 0]) / s
        qy, qz = (rot[1, 2] + rot[2, 1]) / s, 0.25 * s
    quat = np.array([qw, qx, qy, qz])
    quat /= np.linalg.norm(quat)
    return [round(float(v), 6) for v in quat]


def _sample_colors(video_path: Path, n_frames: int) -> np.ndarray:
    """Pull a colour palette out of the real clip so the stub cloud looks like the scene."""
    fallback = np.array([[168, 154, 132], [96, 110, 124], [204, 198, 186]], dtype=np.uint8)
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        try:
            if not cap.isOpened():
                return fallback
            swatches: list[np.ndarray] = []
            for k in range(8):
                cap.set(cv2.CAP_PROP_POS_FRAMES, float(k * max(1, n_frames // 8)))
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                small = cv2.resize(frame, (16, 16), interpolation=cv2.INTER_AREA)
                swatches.append(small.reshape(-1, 3)[:, ::-1])  # BGR -> RGB
        finally:
            cap.release()
        if swatches:
            return np.concatenate(swatches).astype(np.uint8)
    except Exception:  # noqa: BLE001 - colour sampling is cosmetic
        pass
    return fallback


def _shell_points(
    rng: np.random.Generator, palette: np.ndarray, n_keyframes: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Points on a cylindrical shell plus a floor plane — the structure a desk-orbit clip
    actually produces. Returns flattened xyz/rgb plus per-point observations and error."""
    n_wall, n_floor = 4200, 1800
    theta = rng.uniform(0.0, 2.0 * math.pi, n_wall)
    r = rng.normal(2.6, 0.22, n_wall)
    wall = np.column_stack([r * np.sin(theta), rng.uniform(-1.4, 0.9, n_wall), r * np.cos(theta)])
    fr = np.sqrt(rng.uniform(0.05, 1.0, n_floor)) * 2.8
    fth = rng.uniform(0.0, 2.0 * math.pi, n_floor)
    floor = np.column_stack(
        [fr * np.sin(fth), rng.normal(0.95, 0.05, n_floor), fr * np.cos(fth)]
    )
    pts = np.vstack([wall, floor]).astype(np.float32)

    n = len(pts)
    colors = palette[rng.integers(0, len(palette), n)]
    colors = np.clip(colors.astype(np.int16) + rng.integers(-14, 15, (n, 3)), 0, 255)

    # Observation counts are heavy-tailed in a real map: most points seen 2-4 times, a few
    # structural points tracked across most keyframes.
    obs = np.clip(rng.pareto(1.6, n) * 3.0 + 2.0, 2, max(3, n_keyframes)).astype(np.int32)
    perr = np.clip(1.8 / np.sqrt(obs) + rng.normal(0.0, 0.12, n), 0.08, 3.0).astype(np.float32)
    return pts.ravel(), colors.astype(np.uint8).ravel(), obs, perr


def _pace_stub(
    task: adapter.WorkerTask,
    poses: list[dict[str, Any]],
    kf_indices: list[int],
    n_points: int,
    closures: list[dict[str, Any]],
    on_progress: Callable[[dict[str, Any]], None],
) -> tuple[int, int, int]:
    """Replay the reconstruction at a believable cadence so SSE consumers exercise the real
    coalescing path. Returns (decode_ms, tracking_ms, optimize_ms)."""
    total_s = max(0.05, task.config.stub_duration_s)
    decode_s, optimize_s = total_s * 0.08, total_s * 0.22
    tracking_s = total_s - decode_s - optimize_s
    n_frames = len(poses)
    tick = max(1, n_frames // 24)  # ~24 progress emissions, coalescer handles the rest

    t_start = time.monotonic()
    time.sleep(decode_s)
    on_progress({"event": "stage", "stage": "tracking", "message": "Tracking features"})
    closure_at = {len(kf_indices) - 2: 0, len(kf_indices) - 1: 1}
    fired: set[int] = set()

    for i in range(n_frames):
        frac = (i + 1) / n_frames
        target = t_start + decode_s + tracking_s * frac
        now = time.monotonic()
        if target > now:
            time.sleep(target - now)
        kf_done = sum(1 for k in kf_indices if k <= i)
        if kf_done in closure_at and closure_at[kf_done] not in fired and closures:
            idx = closure_at[kf_done]
            fired.add(idx)
            on_progress({"event": "loop_closure", **closures[idx]})
        if i % tick == 0 or i == n_frames - 1:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            on_progress(
                {
                    "frames_done": i + 1,
                    "frames_total": n_frames,
                    "fps": round((i + 1) / max(elapsed_ms / 1000.0, 1e-3), 1),
                    "keyframes": kf_done,
                    "map_points": int(n_points * frac),
                    "loop_closures": len(fired),
                    "elapsed_ms": elapsed_ms,
                }
            )

    on_progress({"event": "stage", "stage": "optimizing", "message": "Global bundle adjustment"})
    time.sleep(optimize_s)
    return int(decode_s * 1000), int(tracking_s * 1000), int(optimize_s * 1000)
