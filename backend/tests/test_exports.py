"""Byte-level export sanity, the quality-aware point cap, and the TTL janitor."""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from httpx import AsyncClient

from app import exports
from app.jobs import JobStore
from app.main import API_PREFIX
from tests.conftest import client_for, make_settings, submit, wait_for_job


async def test_ply_export_parses_back_with_matching_vertex_count(
    client: AsyncClient, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    job = await submit(client, make_video())
    done = await wait_for_job(client, job["job_id"])
    response = await client.get(f"{API_PREFIX}/jobs/{job['job_id']}/export/ply")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].endswith('.ply"')

    body = response.content
    assert body.startswith(b"ply\nformat binary_little_endian 1.0\n")
    path = tmp_path / "out.ply"
    path.write_bytes(body)
    count, rows = exports.read_ply(path)

    assert count == done["metrics"]["map_points"]
    header_len = body.index(b"end_header\n") + len(b"end_header\n")
    assert len(body) == header_len + count * 15  # 3 float32 + 3 uint8 per vertex
    assert np.isfinite(rows["x"]).all()
    assert rows["red"].max() <= 255

    # First vertex must byte-match a manual little-endian unpack of the payload.
    x, y, z, r, g, b = struct.unpack_from("<fffBBB", body, header_len)
    assert (x, y, z) == pytest.approx((rows["x"][0], rows["y"][0], rows["z"][0]))
    assert (r, g, b) == (rows["red"][0], rows["green"][0], rows["blue"][0])


async def test_tum_export_parses_back_with_matching_pose_count(
    client: AsyncClient, make_video: Callable[..., Path], tmp_path: Path
) -> None:
    job = await submit(client, make_video())
    done = await wait_for_job(client, job["job_id"])
    response = await client.get(f"{API_PREFIX}/jobs/{job['job_id']}/export/tum")
    assert response.status_code == 200

    path = tmp_path / "out.tum"
    path.write_text(response.text)
    rows = exports.read_tum(path)
    assert len(rows) == done["metrics"]["frames_processed"]

    recon = (await client.get(f"{API_PREFIX}/jobs/{job['job_id']}/reconstruction")).json()
    first_pose = recon["poses"][0]
    ts, tx, ty, tz, qx, qy, qz, qw = rows[0]
    assert ts == pytest.approx(first_pose["t_s"], abs=1e-6)
    assert [tx, ty, tz] == pytest.approx(first_pose["position"], abs=1e-6)
    # Contract carries [qw,qx,qy,qz]; TUM is scalar-last.
    assert [qw, qx, qy, qz] == pytest.approx(first_pose["quaternion"], abs=1e-6)
    assert np.linalg.norm([qx, qy, qz, qw]) == pytest.approx(1.0, abs=1e-5)

    timestamps = [r[0] for r in rows]
    assert timestamps == sorted(timestamps)


def test_write_ply_rejects_mismatched_colours(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="rgb has"):
        exports.write_ply(tmp_path / "bad.ply", [0.0, 0.0, 0.0, 1.0, 1.0, 1.0], [1, 2, 3])


def test_write_tum_handles_empty_trajectory(tmp_path: Path) -> None:
    path = tmp_path / "empty.tum"
    assert exports.write_tum(path, []) == 0
    assert exports.read_tum(path) == []


def test_subsample_keeps_the_best_observed_points() -> None:
    n = 5000
    rng = np.random.default_rng(1)
    obs = rng.integers(1, 40, n)
    points = {
        "xyz": rng.normal(0, 1, n * 3).tolist(),
        "rgb": rng.integers(0, 255, n * 3).tolist(),
        "observations": obs.tolist(),
        "reprojection_error": rng.uniform(0.1, 3.0, n).tolist(),
    }
    capped = exports.subsample_for_web(points, 1000)

    assert len(capped["observations"]) == 1000
    assert len(capped["xyz"]) == 3000
    assert len(capped["rgb"]) == 3000
    # Quality-aware, not random and not a head slice: the survivors are far better observed
    # than the population average, and the single best point is always kept.
    assert np.mean(capped["observations"]) > np.mean(obs) * 1.4
    assert max(capped["observations"]) == obs.max()
    assert capped != exports.subsample_for_web({**points, "observations": obs[::-1].tolist()}, 1000)


def test_subsample_is_a_noop_below_the_cap() -> None:
    points = {"xyz": [1.0, 2.0, 3.0], "rgb": [10, 20, 30], "observations": [4]}
    assert exports.subsample_for_web(points, 60_000) == {
        "xyz": [1.0, 2.0, 3.0],
        "rgb": [10, 20, 30],
        "observations": [4],
    }


def test_subsample_is_deterministic() -> None:
    rng = np.random.default_rng(3)
    points = {
        "xyz": rng.normal(0, 1, 9000).tolist(),
        "rgb": rng.integers(0, 255, 9000).tolist(),
        "observations": rng.integers(1, 30, 3000).tolist(),
    }
    assert exports.subsample_for_web(points, 700) == exports.subsample_for_web(points, 700)


def test_probe_video_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "garbage.mp4"
    path.write_bytes(b"\x00\x01\x02not a video")
    with pytest.raises(ValueError):
        exports.probe_video(path)


def test_probe_video_reads_real_metadata(make_video: Callable[..., Path]) -> None:
    meta = exports.probe_video(make_video(frames=36, fps=24.0, size=160))
    assert meta["width"] == 160
    assert meta["height"] == 160
    assert meta["fps"] == pytest.approx(24.0, abs=0.5)
    assert meta["frame_count"] == 36
    assert meta["duration_s"] == pytest.approx(1.5, abs=0.1)


async def test_janitor_reaps_expired_jobs(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, job_ttl_hours=1.0)
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        await wait_for_job(http, job["job_id"])
        store: JobStore = http.app.state.store  # type: ignore[attr-defined]
        job_dir = settings.data_dir / job["job_id"]
        assert job_dir.exists()

        assert store.reap() == 0  # fresh job survives
        assert job_dir.exists()

        # Backdate the directory past the TTL.
        old = time.time() - 2 * 3600
        import os

        os.utime(job_dir, (old, old))
        assert store.reap() == 1
        assert not job_dir.exists()
        assert store.get(job["job_id"]) is None
        assert (await http.get(f"{API_PREFIX}/jobs/{job['job_id']}")).status_code == 404


async def test_janitor_skips_running_jobs(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, job_ttl_hours=0.0, stub_duration_s=1.5)
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        store: JobStore = http.app.state.store  # type: ignore[attr-defined]
        # TTL of zero makes everything expired; the running job must still be protected.
        assert store.reap() == 0
        assert (settings.data_dir / job["job_id"]).exists()
        await wait_for_job(http, job["job_id"], timeout=90)
        assert store.reap() == 1
