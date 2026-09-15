"""Adapter over the `slam` package, plus the worker entrypoint that the
ProcessPoolExecutor calls.

Everything below `run_job` executes in a *child process*. It may not touch FastAPI state; it
talks to the API process only through the `progress` queue proxy and its return value.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any

from app import exports
from app.runner import stub

# Artifact filenames inside DATA_DIR/{job_id}/
SOURCE_NAME = "source"
RECON_FULL = "reconstruction.json"
RECON_WEB = "web.json.gz"
PLY_NAME = "cloud.ply"
TUM_NAME = "trajectory.tum"
REPORT_NAME = "report.json"


@dataclass(frozen=True)
class RunnerConfig:
    """Per-job SLAM configuration. Must be picklable — it crosses a process boundary."""

    target_width: int = 640
    max_features: int = 1200
    enable_loop_closure: bool = True
    max_frames: int = 1800
    workers: int = 4
    backend: str = "real"
    max_points_web: int = 60_000
    stub_duration_s: float = 1.2

    def to_public_dict(self) -> dict[str, Any]:
        """The subset echoed back in report.json."""
        return {
            "target_width": self.target_width,
            "max_features": self.max_features,
            "enable_loop_closure": self.enable_loop_closure,
            "max_frames": self.max_frames,
            "workers": self.workers,
            "backend": self.backend,
            "max_points_web": self.max_points_web,
        }


@dataclass(frozen=True)
class WorkerTask:
    job_id: str
    video_path: str
    job_dir: str
    config: RunnerConfig
    accepted_monotonic: float
    queue_wait_ms: int
    job_header: dict[str, Any] = field(default_factory=dict)


def host_info() -> dict[str, Any]:
    """Best-effort host description for the metrics block; never raises."""
    cpu = platform.processor() or platform.machine() or "unknown"
    try:
        import subprocess

        if platform.system() == "Darwin":
            cpu = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip() or cpu
        elif platform.system() == "Linux":
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except Exception:  # noqa: BLE001 - host probing is decorative, never fatal
        pass

    vcpu = os.cpu_count() or 1
    ram_gb = 0
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            ram_gb = round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30)
    except (ValueError, OSError):
        ram_gb = 0
    return {"cpu": cpu, "vcpu": vcpu, "ram_gb": ram_gb}


# --- worker entrypoint ------------------------------------------------------------------


def run_job(task: WorkerTask, progress: Queue) -> dict[str, Any]:
    """Run one reconstruction end to end in a child process.

    Returns a small summary; the bulky reconstruction is left on disk so nothing large has to
    be pickled back across the process boundary.
    """
    job_dir = Path(task.job_dir)
    emitted_closures: list[dict[str, int]] = []

    def emit(kind: str, data: dict[str, Any]) -> None:
        # A dead or full parent queue must never take down a running reconstruction.
        with contextlib.suppress(Exception):
            progress.put({"job_id": task.job_id, "kind": kind, "data": data})

    def on_progress(ev: dict[str, Any]) -> None:
        """Demux the engine callback. See `docs` note in the report: an event without an
        explicit `event` discriminator is treated as a `job.progress` payload."""
        kind = ev.get("event", "progress")
        if kind == "stage":
            emit("stage", {"stage": ev["stage"], "message": ev.get("message", "")})
        elif kind == "loop_closure":
            closure = {
                "from_kf": int(ev["from_kf"]),
                "to_kf": int(ev["to_kf"]),
                "inliers": int(ev["inliers"]),
            }
            emitted_closures.append(closure)
            emit("loop_closure", closure)
        else:
            emit("progress", {k: v for k, v in ev.items() if k != "event"})

    emit("stage", {"stage": "decoding", "message": "Decoding video and detecting features"})

    if task.config.backend == "stub":
        full = stub._stub_reconstruction(task, on_progress)
    else:
        full = _real_reconstruction(task, on_progress)

    emit("stage", {"stage": "optimizing", "message": "Bundle adjustment and pose graph"})

    # Guarantee the frontend sees every closure even if the engine only reported them in the
    # final reconstruction rather than through the callback.
    seen = {(c["from_kf"], c["to_kf"]) for c in emitted_closures}
    for closure in full.get("loop_closures", []):
        key = (closure["from_kf"], closure["to_kf"])
        if key not in seen:
            emit(
                "loop_closure",
                {
                    "from_kf": closure["from_kf"],
                    "to_kf": closure["to_kf"],
                    "inliers": closure["inliers"],
                },
            )

    points = full["points"]
    ply_path, tum_path = job_dir / PLY_NAME, job_dir / TUM_NAME
    n_vertices = _write_ply(full, ply_path, points)
    n_poses = _write_tum(full, tum_path)
    web_points = exports.subsample_for_web(points, task.config.max_points_web)

    # Measurement point for wall_ms: the reconstruction is complete and every heavy artifact
    # (full PLY, TUM, subsampled web cloud) is materialised. Only the terminal JSON emit of
    # the already-computed payload falls outside — see report.json:timing_definition.
    wall_ms = int((time.monotonic() - task.accepted_monotonic) * 1000) - task.queue_wait_ms
    metrics = _finalise_metrics(full["metrics"], wall_ms, task)
    full["metrics"] = metrics
    full["job_id"] = task.job_id

    # The payload embeds its own metrics, so serialising it cannot be inside wall_ms without
    # circularity. Measure it instead and publish it in report.json so the exclusion is
    # auditable rather than hand-waved.
    emit_started = time.monotonic()
    exports.dump_json(job_dir / RECON_FULL, full)
    web = {
        "job_id": task.job_id,
        "poses": full["poses"],
        "points": web_points,
        "loop_closures": full["loop_closures"],
        "metrics": metrics,
    }
    (job_dir / RECON_WEB).write_bytes(
        gzip.compress(json.dumps(web, separators=(",", ":")).encode("utf-8"), 6)
    )
    emit_ms = int((time.monotonic() - emit_started) * 1000)

    summary = {
        "metrics": metrics,
        "points_total": n_vertices,
        "points_web": len(web_points["observations"]),
        "poses": n_poses,
        "loop_closures": full["loop_closures"],
    }
    report = exports.build_report(
        {**task.job_header, "metrics": metrics, "status": "completed"},
        task.config.to_public_dict(),
        {
            "points_total": n_vertices,
            "points_web": summary["points_web"],
            "poses": n_poses,
            "loop_closures": len(full["loop_closures"]),
        },
        {
            "wall_ms": metrics["wall_ms"],
            "queue_wait_ms": metrics["queue_wait_ms"],
            "payload_emit_ms": emit_ms,
            "accept_to_bytes_on_disk_ms": metrics["wall_ms"] + emit_ms,
        },
    )
    exports.dump_json(job_dir / REPORT_NAME, report)
    return summary


def _write_ply(full: dict[str, Any], path: Path, points: dict[str, Any]) -> int:
    """Prefer the engine's own writer so the exported bytes are the engine's ground truth."""
    writer = full.pop("_to_ply", None)
    if writer is not None:
        writer(str(path))
        count, _ = exports.read_ply(path)
        return count
    return exports.write_ply(path, points["xyz"], points["rgb"])


def _write_tum(full: dict[str, Any], path: Path) -> int:
    writer = full.pop("_to_tum", None)
    if writer is not None:
        writer(str(path))
        return len(exports.read_tum(path))
    return exports.write_tum(path, full["poses"])


_DRIFT_KEYS = (
    "pre_optimization_loop_error_m",
    "post_optimization_loop_error_m",
    "reduction_pct",
    "scale_drift_ratio",
)
_METRIC_INTS = (
    "decode_ms",
    "tracking_ms",
    "optimize_ms",
    "frames_processed",
    "keyframes",
    "map_points",
    "loop_closures",
    "loop_candidates_checked",
    "ba_runs",
)
_METRIC_FLOATS = ("mean_reprojection_error_px", "median_track_length", "trajectory_length_m")


def _finalise_metrics(raw: dict[str, Any], wall_ms: int, task: WorkerTask) -> dict[str, Any]:
    """Project the engine's metrics onto the contract and stamp the authoritative timings.

    The engine's own `wall_ms` only covers its `run()` call; the contract's `wall_ms` is an
    end-to-end service number, so the service owns it. Projecting (rather than passing through)
    means an engine that adds a field cannot break the response schema.
    """
    metrics: dict[str, Any] = {
        "wall_ms": max(wall_ms, 0),
        "queue_wait_ms": max(task.queue_wait_ms, 0),
    }
    for key in _METRIC_INTS:
        metrics[key] = int(raw.get(key, 0))
    for key in _METRIC_FLOATS:
        metrics[key] = float(raw.get(key, 0.0))

    drift = raw.get("drift") or {}
    metrics["drift"] = {k: float(drift.get(k, 0.0)) for k in _DRIFT_KEYS}
    host = raw.get("host") or host_info()
    metrics["host"] = {
        "cpu": str(host.get("cpu", "unknown")),
        "vcpu": int(host.get("vcpu", os.cpu_count() or 1)),
        "ram_gb": int(host.get("ram_gb", 0)),
    }

    seconds = metrics["wall_ms"] / 1000.0
    duration_s = float((task.job_header.get("video") or {}).get("duration_s") or 0.0)
    metrics["processing_fps"] = (
        round(metrics["frames_processed"] / seconds, 2) if seconds > 0 else 0.0
    )
    metrics["realtime_factor"] = round(duration_s / seconds, 3) if seconds > 0 else 0.0
    return metrics


# --- real engine ------------------------------------------------------------------------


def _real_reconstruction(
    task: WorkerTask, on_progress: Callable[[dict[str, Any]], None]
) -> dict[str, Any]:
    """Import and drive the `slam` package. Imported lazily so the service boots without it."""
    try:
        from slam import SlamConfig, SlamPipeline
    except ImportError as exc:
        raise RuntimeError(
            "SLAM engine unavailable: the `slam` package could not be imported "
            f"({exc}). Set SLAM_BACKEND=stub to run the service without it."
        ) from exc

    cfg = SlamConfig(
        target_width=task.config.target_width,
        max_features=task.config.max_features,
        enable_loop_closure=task.config.enable_loop_closure,
        max_frames=task.config.max_frames,
    )
    pipeline = SlamPipeline(cfg)
    recon = pipeline.run(task.video_path, on_progress=on_progress)
    full: dict[str, Any] = recon.to_dict()

    # Bind the engine's own exporters if it has them; `_write_ply`/`_write_tum` pop these.
    if hasattr(recon, "to_ply"):
        full["_to_ply"] = recon.to_ply
    if hasattr(recon, "to_tum"):
        full["_to_tum"] = recon.to_tum
    return full
