"""Shared fixtures. The whole suite runs offline against SLAM_BACKEND=stub."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from app.jobs import Settings
from app.main import API_PREFIX, create_app


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    data_dir = tmp_path / "data"
    samples_dir = tmp_path / "samples"
    data_dir.mkdir(exist_ok=True)
    samples_dir.mkdir(exist_ok=True)
    base: dict[str, Any] = {
        "data_dir": data_dir,
        "slam_backend": "stub",
        "target_width": 640,
        "max_features": 400,
        "max_frames": 1800,
        "slam_workers": 2,
        "max_concurrent_jobs": 1,
        "max_points_web": 60_000,
        "max_upload_mb": 200,
        "max_video_duration_s": 60.0,
        "job_ttl_hours": 24.0,
        "cors_origins": ["*"],
        "samples_dir": samples_dir,
        "stub_duration_s": 0.35,
    }
    base.update(overrides)
    return Settings(**base)


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://driftless") as http:
            http.app = app  # type: ignore[attr-defined]
            yield http


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with client_for(make_settings(tmp_path)) as http:
        yield http


@pytest.fixture
def make_video(tmp_path: Path) -> Callable[..., Path]:
    def _make(name: str = "clip.mp4", frames: int = 48, fps: float = 24.0, size: int = 160) -> Path:
        path = tmp_path / name
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (size, size)
        )
        assert writer.isOpened(), "OpenCV could not open an mp4 writer"
        rng = np.random.default_rng(7)
        texture = rng.integers(0, 255, (size, size, 3), dtype=np.uint8)
        for i in range(frames):
            frame = np.roll(texture, i * 2, axis=1).copy()
            cv2.rectangle(frame, (20 + i, 30), (60 + i, 90), (255, 255, 255), -1)
            writer.write(frame)
        writer.release()
        assert path.exists() and path.stat().st_size > 0
        return path

    return _make


async def submit(http: AsyncClient, video: Path, **form: Any) -> dict[str, Any]:
    with video.open("rb") as fh:
        response = await http.post(
            f"{API_PREFIX}/jobs",
            files={"video": (video.name, fh, "video/mp4")},
            data={k: str(v) for k, v in form.items()},
        )
    assert response.status_code == 201, response.text
    job: dict[str, Any] = response.json()
    return job


async def wait_for_job(http: AsyncClient, job_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        response = await http.get(f"{API_PREFIX}/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job: dict[str, Any] = response.json()
        if job["status"] in ("completed", "failed"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


async def read_sse(http: AsyncClient, job_id: str, limit: float = 30.0) -> list[tuple[str, Any]]:
    """Drain an SSE stream until it closes, returning (event, data) pairs."""
    events: list[tuple[str, Any]] = []
    async with http.stream(
        "GET", f"{API_PREFIX}/jobs/{job_id}/events", timeout=limit
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name: str | None = None
        async for line in response.aiter_lines():
            line = line.rstrip("\r")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name is not None:
                events.append((name, json.loads(line[6:])))
                if name in ("job.completed", "job.failed"):
                    break
                name = None
    return events
