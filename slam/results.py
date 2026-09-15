"""Output structures: exactly the `Metrics` and `Reconstruction` shapes in
`_contracts/assignment2-api.md`, plus the extra fields the service asked for.

Kept separate from the pipeline because they are the contract with the rest of
the system: the backend, the exports and the benchmark all read these and none
of them care how tracking works.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .config import SlamConfig
from .geometry import quat_from_R, se3_inv
from .io import write_ply, write_tum

Array = np.ndarray
ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class DriftMetrics:
    pre_optimization_loop_error_m: float = 0.0
    post_optimization_loop_error_m: float = 0.0
    reduction_pct: float = 0.0
    scale_drift_ratio: float = 1.0

    def to_dict(self) -> dict[str, float]:
        return {
            "pre_optimization_loop_error_m": round(self.pre_optimization_loop_error_m, 6),
            "post_optimization_loop_error_m": round(self.post_optimization_loop_error_m, 6),
            "reduction_pct": round(self.reduction_pct, 3),
            "scale_drift_ratio": round(self.scale_drift_ratio, 6),
        }


@dataclass
class Metrics:
    # wall_ms, queue_wait_ms, processing_fps and realtime_factor are owned by the
    # service: it is the only thing that knows when the upload was accepted and
    # how long the job queued. The library leaves them at zero and reports its own
    # measured split on `Reconstruction.timings` instead.
    wall_ms: int = 0
    queue_wait_ms: int = 0
    decode_ms: int = 0
    tracking_ms: int = 0
    optimize_ms: int = 0
    frames_processed: int = 0
    processing_fps: float = 0.0
    realtime_factor: float = 0.0
    keyframes: int = 0
    map_points: int = 0
    mean_reprojection_error_px: float = 0.0
    median_track_length: int = 0
    loop_closures: int = 0
    loop_candidates_checked: int = 0
    drift: DriftMetrics = field(default_factory=DriftMetrics)
    trajectory_length_m: float = 0.0
    ba_runs: int = 0
    host: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wall_ms": int(self.wall_ms),
            "queue_wait_ms": int(self.queue_wait_ms),
            "decode_ms": int(self.decode_ms),
            "tracking_ms": int(self.tracking_ms),
            "optimize_ms": int(self.optimize_ms),
            "frames_processed": int(self.frames_processed),
            "processing_fps": round(self.processing_fps, 2),
            "realtime_factor": round(self.realtime_factor, 3),
            "keyframes": int(self.keyframes),
            "map_points": int(self.map_points),
            "mean_reprojection_error_px": round(self.mean_reprojection_error_px, 4),
            "median_track_length": int(self.median_track_length),
            "loop_closures": int(self.loop_closures),
            "loop_candidates_checked": int(self.loop_candidates_checked),
            "drift": self.drift.to_dict(),
            "trajectory_length_m": round(self.trajectory_length_m, 4),
            "ba_runs": int(self.ba_runs),
            "host": self.host,
        }


@dataclass
class PoseRecord:
    frame_index: int
    t_s: float
    is_keyframe: bool
    ref_kf_id: int
    T_cr: Array               # camera-from-reference-keyframe-camera, scale free
    tracked_points: int
    reprojection_error_px: float
    T_cw: Array = field(default_factory=lambda: np.eye(4))


@dataclass
class Reconstruction:
    job_id: str
    poses: list[PoseRecord]
    xyz: Array
    rgb: Array
    observations: Array
    loop_closures: list[dict[str, Any]]
    metrics: Metrics
    config: SlamConfig
    video: dict[str, Any]
    intrinsics: dict[str, float]      # what tracking actually used, after refinement
    timings: dict[str, float]         # measured stage split, for the benchmark
    point_errors: Array = field(default_factory=lambda: np.zeros(0, np.float32))

    def positions(self) -> Array:
        return np.array([se3_inv(p.T_cw)[:3, 3] for p in self.poses])

    def quaternions(self) -> Array:
        return np.array([quat_from_R(se3_inv(p.T_cw)[:3, :3]) for p in self.poses])

    def to_dict(self) -> dict[str, Any]:
        pos = self.positions()
        quat = self.quaternions()
        poses = [
            {
                "frame_index": int(p.frame_index),
                "t_s": round(float(p.t_s), 6),
                "is_keyframe": bool(p.is_keyframe),
                "position": [float(x) for x in pos[i]],
                "quaternion": [float(x) for x in quat[i]],
                "tracked_points": int(p.tracked_points),
                "reprojection_error_px": round(float(p.reprojection_error_px), 4),
            }
            for i, p in enumerate(self.poses)
        ]
        keep = self._web_subset()
        return {
            "job_id": self.job_id,
            "poses": poses,
            "points": {
                "xyz": self.xyz[keep].astype(np.float32).ravel().tolist(),
                "rgb": self.rgb[keep].astype(np.uint8).ravel().tolist(),
                "observations": self.observations[keep].astype(np.int32).tolist(),
                "reprojection_error": [
                    round(float(e), 3) for e in self.point_errors[keep]
                ],
            },
            "loop_closures": self.loop_closures,
            "metrics": self.metrics.to_dict(),
        }

    def _web_subset(self) -> Array:
        n = self.xyz.shape[0]
        cap = self.config.max_points_web
        if n <= cap:
            return np.arange(n)
        # Keep the best-supported points: most observations first, lowest
        # reprojection error breaking ties. They are both the most trustworthy
        # and the least noisy-looking in the viewer.
        err = self.point_errors if self.point_errors.size == n else np.zeros(n)
        return np.lexsort((err, -self.observations))[:cap]

    def to_ply(self, path: str | Path) -> Path:
        return write_ply(path, self.xyz, self.rgb)

    def to_tum(self, path: str | Path) -> Path:
        times = np.array([p.t_s for p in self.poses])
        return write_tum(path, times, self.positions(), self.quaternions())


