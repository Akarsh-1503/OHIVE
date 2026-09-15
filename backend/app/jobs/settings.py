"""Service configuration and the env parsing behind it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app import runner


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Every knob is an env var; see `.env.example`."""

    data_dir: Path
    slam_backend: str
    target_width: int
    max_features: int
    max_frames: int
    slam_workers: int
    max_concurrent_jobs: int
    max_points_web: int
    max_upload_mb: int
    max_video_duration_s: float
    job_ttl_hours: float
    cors_origins: list[str]
    samples_dir: Path
    stub_duration_s: float

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
        default_samples = Path(__file__).resolve().parents[3] / "samples"
        origins = os.environ.get("CORS_ORIGINS", "*")
        backend = os.environ.get("SLAM_BACKEND", "real").strip().lower()
        return cls(
            data_dir=data_dir,
            slam_backend="stub" if backend == "stub" else "real",
            target_width=_env_int("SLAM_TARGET_WIDTH", 640),
            max_features=_env_int("SLAM_MAX_FEATURES", 1200),
            max_frames=_env_int("SLAM_MAX_FRAMES", 1800),
            slam_workers=_env_int("SLAM_WORKERS", 4),
            max_concurrent_jobs=max(1, _env_int("SLAM_MAX_CONCURRENT_JOBS", 1)),
            max_points_web=_env_int("SLAM_MAX_POINTS_WEB", 60_000),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 200),
            max_video_duration_s=_env_float("MAX_VIDEO_DURATION_S", 60.0),
            job_ttl_hours=_env_float("JOB_TTL_HOURS", 24.0),
            cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
            samples_dir=Path(os.environ.get("SAMPLES_DIR", default_samples)).resolve(),
            stub_duration_s=_env_float("SLAM_STUB_DURATION_S", 1.2),
        )

    def runner_config(
        self, target_width: int, max_frames: int, enable_loop_closure: bool
    ) -> runner.RunnerConfig:
        return runner.RunnerConfig(
            target_width=target_width,
            max_features=self.max_features,
            enable_loop_closure=enable_loop_closure,
            max_frames=max_frames,
            workers=self.slam_workers,
            backend=self.slam_backend,
            max_points_web=self.max_points_web,
            stub_duration_s=self.stub_duration_s,
        )
