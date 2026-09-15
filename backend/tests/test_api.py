"""Contract-shape, lifecycle, SSE, upload-rejection, queueing and sample tests."""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from httpx import AsyncClient
from pydantic import ValidationError

from app import main
from app.main import API_PREFIX
from app.models import Health, Job, Reconstruction
from tests.conftest import client_for, make_settings, read_sse, submit, wait_for_job


async def test_health_matches_contract(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/health")
    assert response.status_code == 200
    health = Health.model_validate(response.json())
    assert health.status == "ok"
    assert health.workers == 1
    assert health.cpu_count >= 1
    assert health.slam_backend == "stub"


async def test_job_lifecycle_and_contract_shapes(
    client: AsyncClient, make_video: Callable[..., Path]
) -> None:
    video = make_video()
    created = await submit(client, video)
    accepted = Job.model_validate(created)
    assert accepted.status in ("queued", "decoding")
    assert accepted.queue_position == 0  # nothing ahead of it
    assert accepted.filename == "clip.mp4"
    assert accepted.video is not None and accepted.video.frame_count > 0
    assert accepted.metrics is None
    assert accepted.truncated is False
    assert accepted.created_at.endswith("Z")

    finished = Job.model_validate(await wait_for_job(client, accepted.job_id))
    assert finished.status == "completed", finished.error
    assert finished.error is None
    assert finished.queue_position is None
    metrics = finished.metrics
    assert metrics is not None
    assert metrics.wall_ms > 0
    # Nothing else was running, so the only queue wait is task-scheduling latency.
    assert metrics.queue_wait_ms < 50
    assert metrics.frames_processed > 0
    assert metrics.map_points > 0
    assert metrics.keyframes > 0
    assert metrics.drift.reduction_pct > 0
    assert metrics.host.vcpu >= 1
    assert metrics.realtime_factor > 0

    recon_res = await client.get(f"{API_PREFIX}/jobs/{accepted.job_id}/reconstruction")
    assert recon_res.status_code == 200
    recon = Reconstruction.model_validate(recon_res.json())
    assert recon.job_id == accepted.job_id
    assert len(recon.poses) == metrics.frames_processed
    assert recon.poses[0].frame_index == 0
    assert [p.frame_index for p in recon.poses] == sorted(p.frame_index for p in recon.poses)
    assert len(recon.points.xyz) == 3 * len(recon.points.observations)
    assert len(recon.points.rgb) == 3 * len(recon.points.observations)
    assert all(0 <= v <= 255 for v in recon.points.rgb[:300])
    # World frame is the first keyframe camera frame, so pose 0 is the identity.
    assert recon.poses[0].position == [0.0, 0.0, 0.0]
    assert recon.poses[0].quaternion == [1.0, 0.0, 0.0, 0.0]
    assert recon.metrics.wall_ms == metrics.wall_ms

    report_res = await client.get(f"{API_PREFIX}/jobs/{accepted.job_id}/export/report.json")
    assert report_res.status_code == 200
    report = report_res.json()
    assert report["job_id"] == accepted.job_id
    assert report["config"]["backend"] == "stub"
    assert report["metrics"]["wall_ms"] == metrics.wall_ms
    assert "wall_ms" in report["timing_definition"]
    timing = report["timing"]
    assert timing["wall_ms"] == metrics.wall_ms
    assert timing["queue_wait_ms"] == metrics.queue_wait_ms
    assert timing["payload_emit_ms"] >= 0
    assert (
        timing["accept_to_bytes_on_disk_ms"] == timing["wall_ms"] + timing["payload_emit_ms"]
    )


async def test_wall_ms_excludes_queue_wait(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, max_concurrent_jobs=1, stub_duration_s=1.2)
    async with client_for(settings) as http:
        video = make_video()
        first = await submit(http, video)
        await asyncio.sleep(0.3)
        second = await submit(http, video)

        queued = Job.model_validate((await http.get(f"{API_PREFIX}/jobs/{second['job_id']}")).json())
        assert queued.status == "queued"
        assert queued.queue_position == 1

        done_first = Job.model_validate(await wait_for_job(http, first["job_id"]))
        done_second = Job.model_validate(await wait_for_job(http, second["job_id"]))
        assert done_first.metrics is not None and done_second.metrics is not None
        assert done_first.metrics.queue_wait_ms < 50
        assert done_second.metrics.queue_wait_ms > 500
        # The queued job's own processing time must not be inflated by the wait it endured.
        assert done_second.metrics.wall_ms < done_second.metrics.queue_wait_ms + 2000
        assert abs(done_second.metrics.wall_ms - done_first.metrics.wall_ms) < 2000


async def test_sse_ordering_with_late_subscriber(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, stub_duration_s=1.5)
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        job_id = job["job_id"]

        early = asyncio.create_task(read_sse(http, job_id))
        await asyncio.sleep(0.8)  # join mid-run
        late = asyncio.create_task(read_sse(http, job_id))
        early_events, late_events = await asyncio.gather(early, late)

    for events in (early_events, late_events):
        assert events[0][0] == "job.snapshot"
        assert events[0][1]["job_id"] == job_id
        assert events[-1][0] == "job.completed"
        assert events[-1][1]["metrics"]["wall_ms"] > 0
        # Nothing may follow the terminal event.
        assert [n for n, _ in events].count("job.completed") == 1

    names = [n for n, _ in early_events]
    assert "job.stage" in names
    assert "job.progress" in names
    assert "job.loop_closure" in names
    assert names.index("job.stage") < names.index("job.completed")

    progress = [d for n, d in early_events if n == "job.progress"]
    assert progress[0]["frames_done"] <= progress[-1]["frames_done"]
    assert progress[-1]["frames_total"] > 0
    assert set(progress[0]) == {
        "frames_done",
        "frames_total",
        "fps",
        "keyframes",
        "map_points",
        "loop_closures",
        "elapsed_ms",
    }
    # Coalescing: a 1.5 s run can emit at most ~10 progress frames at one per 150 ms.
    assert len(progress) <= 12, f"progress not coalesced: {len(progress)} events"

    closures = [d for n, d in early_events if n == "job.loop_closure"]
    assert set(closures[0]) == {"from_kf", "to_kf", "inliers"}

    # The late subscriber's snapshot reflects live state, not the initial queued state.
    assert late_events[0][1]["status"] in ("decoding", "tracking", "optimizing", "completed")
    assert late_events[1][0] == "job.progress"


async def test_sse_after_completion_replays_terminal_state(
    client: AsyncClient, make_video: Callable[..., Path]
) -> None:
    job = await submit(client, make_video())
    await wait_for_job(client, job["job_id"])
    events = await read_sse(client, job["job_id"])
    assert events[0][0] == "job.snapshot"
    assert events[0][1]["status"] == "completed"
    assert events[-1][0] == "job.completed"


@pytest.mark.parametrize(
    ("mime", "code"),
    [("text/plain", "UNSUPPORTED_MEDIA_TYPE"), ("application/pdf", "UNSUPPORTED_MEDIA_TYPE")],
)
async def test_upload_rejects_bad_mime(
    client: AsyncClient, make_video: Callable[..., Path], mime: str, code: str
) -> None:
    video = make_video()
    with video.open("rb") as fh:
        response = await client.post(
            f"{API_PREFIX}/jobs", files={"video": (video.name, fh, mime)}
        )
    assert response.status_code == 415
    body = response.json()
    assert set(body) == {"detail", "code"}
    assert body["code"] == code
    assert mime in body["detail"]


async def test_upload_rejects_oversized_file(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_upload_mb=1)
    async with client_for(settings) as http:
        blob = b"\x00" * (2 * 1024 * 1024)
        response = await http.post(
            f"{API_PREFIX}/jobs", files={"video": ("big.mp4", blob, "video/mp4")}
        )
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"


async def test_upload_rejects_undecodable_file(client: AsyncClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/jobs",
        files={"video": ("fake.mp4", b"this is definitely not an mp4", "video/mp4")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "UNDECODABLE_VIDEO"


async def test_upload_rejects_empty_file(client: AsyncClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/jobs", files={"video": ("empty.mp4", b"", "video/mp4")}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "EMPTY_UPLOAD"


async def test_upload_rejects_long_clip(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, max_video_duration_s=1.0)
    async with client_for(settings) as http:
        video = make_video(frames=120, fps=24.0)  # 5 s
        with video.open("rb") as fh:
            response = await http.post(
                f"{API_PREFIX}/jobs", files={"video": (video.name, fh, "video/mp4")}
            )
    assert response.status_code == 400
    assert response.json()["code"] == "VIDEO_TOO_LONG"


async def test_rejected_upload_leaves_no_job_dir(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    async with client_for(settings) as http:
        await http.post(
            f"{API_PREFIX}/jobs", files={"video": ("fake.mp4", b"nope", "video/mp4")}
        )
    assert list(settings.data_dir.iterdir()) == []


async def test_queueing_reports_position(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, max_concurrent_jobs=1, stub_duration_s=1.5)
    async with client_for(settings) as http:
        video = make_video()
        first = await submit(http, video)
        second = await submit(http, video)
        third = await submit(http, video)
        await asyncio.sleep(0.2)

        health = (await http.get(f"{API_PREFIX}/health")).json()
        assert health["active_jobs"] == 1
        assert health["queued_jobs"] == 2

        states = {
            j["job_id"]: j
            for j in [
                (await http.get(f"{API_PREFIX}/jobs/{x['job_id']}")).json()
                for x in (first, second, third)
            ]
        }
        assert states[first["job_id"]]["queue_position"] == 0
        assert states[second["job_id"]]["status"] == "queued"
        assert states[second["job_id"]]["queue_position"] == 1
        assert states[third["job_id"]]["queue_position"] == 2

        for job in (first, second, third):
            assert (await wait_for_job(http, job["job_id"], timeout=90))["status"] == "completed"


async def test_unknown_job_uses_contract_error_shape(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/jobs/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"detail", "code"}
    assert body["code"] == "JOB_NOT_FOUND"


async def test_reconstruction_blocked_until_completed(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, stub_duration_s=1.5)
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        response = await http.get(f"{API_PREFIX}/jobs/{job['job_id']}/reconstruction")
        assert response.status_code == 409
        assert response.json()["code"] == "JOB_NOT_COMPLETED"
        await wait_for_job(http, job["job_id"], timeout=90)


async def test_reconstruction_is_gzipped_and_point_capped(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    cap = 500
    settings = make_settings(tmp_path, max_points_web=cap)
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        await wait_for_job(http, job["job_id"])

        # httpx transparently decodes; ask for the raw bytes to prove the wire format.
        async with http.stream(
            "GET",
            f"{API_PREFIX}/jobs/{job['job_id']}/reconstruction",
            headers={"Accept-Encoding": "gzip"},
        ) as response:
            assert response.headers["content-encoding"] == "gzip"
            assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert "etag" in response.headers
            raw = b"".join([chunk async for chunk in response.aiter_raw()])
        payload = json.loads(gzip.decompress(raw))
        assert len(payload["points"]["observations"]) == cap
        assert len(payload["points"]["xyz"]) == 3 * cap
        assert len(raw) < len(json.dumps(payload).encode())

        # A client that cannot take gzip still gets valid JSON.
        plain = await http.get(
            f"{API_PREFIX}/jobs/{job['job_id']}/reconstruction",
            headers={"Accept-Encoding": "identity"},
        )
        assert "content-encoding" not in plain.headers
        assert plain.json()["job_id"] == job["job_id"]

        full = json.loads((settings.data_dir / job["job_id"] / "reconstruction.json").read_text())
        assert len(full["points"]["observations"]) > cap  # full cloud kept on disk


async def test_preview_returns_annotated_jpeg(
    client: AsyncClient, make_video: Callable[..., Path]
) -> None:
    job = await submit(client, make_video())
    await wait_for_job(client, job["job_id"])
    response = await client.get(f"{API_PREFIX}/jobs/{job['job_id']}/preview/3")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:3] == b"\xff\xd8\xff"  # JPEG SOI
    assert len(response.content) > 1000

    missing = await client.get(f"{API_PREFIX}/jobs/{job['job_id']}/preview/99999")
    assert missing.status_code == 404
    assert missing.json()["code"] == "FRAME_NOT_FOUND"


async def test_samples_absent_manifest_is_empty(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/samples")
    assert response.status_code == 200
    assert response.json() == []


async def test_samples_manifest_and_one_click_run(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path)
    video = make_video("desk_loop.mp4")
    (settings.samples_dir / "desk_loop.mp4").write_bytes(video.read_bytes())
    (settings.samples_dir / "manifest.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "id": "desk-loop",
                        "name": "Desk loop",
                        "description": "Handheld orbit of a desk.",
                        "duration_s": 2.0,
                        "file": "desk_loop.mp4",
                    }
                ]
            }
        )
    )
    # A read-only samples mount must still work: the service copies, never writes here.
    settings.samples_dir.chmod(0o555)
    try:
        async with client_for(settings) as http:
            listed = (await http.get(f"{API_PREFIX}/samples")).json()
            assert listed == [
                {
                    "id": "desk-loop",
                    "name": "Desk loop",
                    "description": "Handheld orbit of a desk.",
                    "duration_s": 2.0,
                    "url": f"{API_PREFIX}/samples/desk-loop/file",
                }
            ]
            media = await http.get(f"{API_PREFIX}/samples/desk-loop/file")
            assert media.status_code == 200 and len(media.content) > 0

            created = await http.post(f"{API_PREFIX}/jobs/from-sample/desk-loop")
            assert created.status_code == 201, created.text
            job = Job.model_validate(created.json())
            assert job.filename == "desk_loop.mp4"
            done = await wait_for_job(http, job.job_id)
            assert done["status"] == "completed"

            unknown = await http.post(f"{API_PREFIX}/jobs/from-sample/nope")
            assert unknown.status_code == 404
            assert unknown.json()["code"] == "SAMPLE_NOT_FOUND"
    finally:
        settings.samples_dir.chmod(0o755)


async def test_samples_tolerates_malformed_manifest(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    (settings.samples_dir / "manifest.json").write_text("{not json at all")
    async with client_for(settings) as http:
        assert (await http.get(f"{API_PREFIX}/samples")).json() == []


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["x-request-id"] == "abc123"
    generated = await client.get(f"{API_PREFIX}/health")
    assert len(generated.headers["x-request-id"]) == 16


async def test_max_frames_flags_truncation(
    client: AsyncClient, make_video: Callable[..., Path]
) -> None:
    job = await submit(client, make_video(frames=48), max_frames=10)
    assert job["truncated"] is True
    done = await wait_for_job(client, job["job_id"])
    assert done["metrics"]["frames_processed"] == 10


async def test_invalid_form_parameters_rejected(
    client: AsyncClient, make_video: Callable[..., Path]
) -> None:
    video = make_video()
    with video.open("rb") as fh:
        response = await client.post(
            f"{API_PREFIX}/jobs",
            files={"video": (video.name, fh, "video/mp4")},
            data={"target_width": "12"},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAMETER"


async def test_sse_for_unknown_job_is_404(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/jobs/nope/events")
    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"


async def test_multiple_subscribers_receive_identical_streams(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    settings = make_settings(tmp_path, stub_duration_s=0.8)
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        a, b, c = await asyncio.gather(
            read_sse(http, job["job_id"]),
            read_sse(http, job["job_id"]),
            read_sse(http, job["job_id"]),
        )
    assert [n for n, _ in a] == [n for n, _ in b] == [n for n, _ in c]
    assert a[-1][1]["metrics"] == b[-1][1]["metrics"] == c[-1][1]["metrics"]


async def test_sse_pings_and_tears_subscribers_down_on_disconnect(
    tmp_path: Path, make_video: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives `_event_stream` directly: httpx's ASGI transport buffers whole responses, so
    incremental delivery, keep-alive pings and disconnect teardown are not observable through
    the client and have to be exercised against the generator itself."""
    monkeypatch.setattr(main, "SSE_PING_INTERVAL_S", 0.05)
    settings = make_settings(tmp_path, max_concurrent_jobs=1, stub_duration_s=1.5)
    async with client_for(settings) as http:
        app = http.app  # type: ignore[attr-defined]
        busy = await submit(http, make_video())
        waiting = await submit(http, make_video())
        store = app.state.store
        request = cast(Request, SimpleNamespace(app=app))

        stream = main._event_stream(request, waiting["job_id"])
        first = await stream.__anext__()
        assert first.startswith(b"event: job.snapshot\ndata: ")
        assert json.loads(first.split(b"data: ")[1])["status"] == "queued"
        assert len(store._subscribers[waiting["job_id"]]) == 1

        # A queued job emits nothing of its own, so the next frame must be the keep-alive.
        assert await stream.__anext__() == b"event: ping\ndata: {}\n\n"

        await stream.aclose()  # client disconnect
        assert store._subscribers.get(waiting["job_id"], set()) == set()

        for job in (busy, waiting):
            assert (await wait_for_job(http, job["job_id"], timeout=90))["status"] == "completed"


async def test_real_backend_without_engine_fails_cleanly(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    """The `slam` package is imported lazily inside the worker, so a missing engine must
    surface as a failed job with an actionable message — never as a crashed service."""
    settings = make_settings(tmp_path, slam_backend="real")
    async with client_for(settings) as http:
        job = await submit(http, make_video())
        done = await wait_for_job(http, job["job_id"])
        assert done["status"] == "failed"
        assert done["metrics"] is None
        assert "SLAM engine unavailable" in done["error"]
        assert "SLAM_BACKEND=stub" in done["error"]

        health = (await http.get(f"{API_PREFIX}/health")).json()
        assert health["status"] == "ok" and health["active_jobs"] == 0

        events = await read_sse(http, job["job_id"])
        assert events[0][0] == "job.snapshot"
        assert events[-1][0] == "job.failed"
        assert set(events[-1][1]) == {"error"}


async def test_cors_preflight_allowed(client: AsyncClient) -> None:
    response = await client.options(
        f"{API_PREFIX}/jobs",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_openapi_exposes_every_contract_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])
    expected = {
        f"{API_PREFIX}/jobs",
        f"{API_PREFIX}/jobs/{{job_id}}",
        f"{API_PREFIX}/jobs/{{job_id}}/events",
        f"{API_PREFIX}/jobs/{{job_id}}/reconstruction",
        f"{API_PREFIX}/jobs/{{job_id}}/export/ply",
        f"{API_PREFIX}/jobs/{{job_id}}/export/tum",
        f"{API_PREFIX}/jobs/{{job_id}}/export/report.json",
        f"{API_PREFIX}/jobs/{{job_id}}/preview/{{frame_index}}",
        f"{API_PREFIX}/samples",
        f"{API_PREFIX}/jobs/from-sample/{{sample_id}}",
        f"{API_PREFIX}/health",
    }
    assert expected <= paths, expected - paths


def test_metrics_model_matches_contract_keys() -> None:
    from app.models import Drift, HostInfo, Metrics

    assert set(Metrics.model_fields) == {
        "wall_ms", "queue_wait_ms", "decode_ms", "tracking_ms", "optimize_ms",
        "frames_processed", "processing_fps", "realtime_factor", "keyframes", "map_points",
        "mean_reprojection_error_px", "median_track_length", "loop_closures",
        "loop_candidates_checked", "drift", "trajectory_length_m", "ba_runs", "host",
    }
    assert set(Drift.model_fields) == {
        "pre_optimization_loop_error_m", "post_optimization_loop_error_m",
        "reduction_pct", "scale_drift_ratio",
    }
    assert set(HostInfo.model_fields) == {"cpu", "vcpu", "ram_gb"}


def test_reconstruction_model_rejects_unknown_fields() -> None:
    payload: dict[str, Any] = {
        "job_id": "x", "poses": [], "loop_closures": [], "surprise": 1,
        "points": {"xyz": [], "rgb": [], "observations": []},
        "metrics": {},
    }
    with pytest.raises(ValidationError, match="surprise"):
        Reconstruction.model_validate(payload)
