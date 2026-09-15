"""Pydantic models mirroring `_contracts/assignment2-api.md` exactly.

Field names and nesting here are the contract. If a name changes, the contract file changes
first. `created_at` is a plain string rather than a `datetime` because the contract pins the
`...Z` spelling and pydantic v2 would emit `+00:00`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "decoding", "tracking", "optimizing", "completed", "failed"]
Stage = Literal["decoding", "tracking", "optimizing"]


def utc_now_iso() -> str:
    """Contract timestamp spelling: RFC3339 UTC, second precision, trailing `Z`."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoInfo(Strict):
    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float


class HostInfo(Strict):
    cpu: str
    vcpu: int
    ram_gb: int


class Drift(Strict):
    pre_optimization_loop_error_m: float
    post_optimization_loop_error_m: float
    reduction_pct: float
    scale_drift_ratio: float


class Metrics(Strict):
    wall_ms: int
    queue_wait_ms: int
    decode_ms: int
    tracking_ms: int
    optimize_ms: int
    frames_processed: int
    processing_fps: float
    realtime_factor: float
    keyframes: int
    map_points: int
    mean_reprojection_error_px: float
    median_track_length: float
    loop_closures: int
    loop_candidates_checked: int
    drift: Drift
    trajectory_length_m: float
    ba_runs: int
    host: HostInfo


class Job(Strict):
    job_id: str
    status: JobStatus
    filename: str
    created_at: str
    video: VideoInfo | None = None
    metrics: Metrics | None = None
    error: str | None = None
    queue_position: int | None = None
    truncated: bool = False


class Pose(Strict):
    frame_index: int
    t_s: float
    is_keyframe: bool
    position: list[float] = Field(min_length=3, max_length=3)
    quaternion: list[float] = Field(min_length=4, max_length=4)
    tracked_points: int
    reprojection_error_px: float


class PointCloud(Strict):
    xyz: list[float]
    rgb: list[int]
    observations: list[int]


class LoopClosure(Strict):
    from_kf: int
    to_kf: int
    inliers: int
    scale: float


class Reconstruction(Strict):
    job_id: str
    poses: list[Pose]
    points: PointCloud
    loop_closures: list[LoopClosure]
    metrics: Metrics


class Sample(Strict):
    id: str
    name: str
    description: str
    duration_s: float
    url: str


class Health(Strict):
    status: Literal["ok"]
    version: str
    workers: int
    cpu_count: int
    active_jobs: int
    queued_jobs: int
    slam_backend: Literal["real", "stub"]


class ErrorResponse(Strict):
    detail: str
    code: str


# --- SSE payloads -----------------------------------------------------------------------


class StageEvent(Strict):
    stage: Stage
    message: str


class ProgressEvent(Strict):
    frames_done: int
    frames_total: int
    fps: float
    keyframes: int
    map_points: int
    loop_closures: int
    elapsed_ms: int


class LoopClosureEvent(Strict):
    from_kf: int
    to_kf: int
    inliers: int


class CompletedEvent(Strict):
    job_id: str
    metrics: Metrics


class FailedEvent(Strict):
    error: str
