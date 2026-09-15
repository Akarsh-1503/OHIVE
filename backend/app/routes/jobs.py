"""Job admission — upload, one-click sample run, and the job snapshot."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request, UploadFile

from app import exports, runner
from app.jobs import Settings
from app.models import Job
from app.routes.deps import API_PREFIX, ApiError, _require_job, _store
from app.routes.samples import _find_sample

UPLOAD_CHUNK = 1 << 20

# The contract's allowlist. `video/x-msvideo` is the spelling browsers actually send for AVI.
ALLOWED_MIME = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/x-matroska",
        "video/webm",
        "video/avi",
        "video/x-msvideo",
    }
)

log = logging.getLogger("driftless.api")

router = APIRouter()


@router.post(f"{API_PREFIX}/jobs", response_model=Job, status_code=201)
async def create_job(
    request: Request,
    video: UploadFile,
    max_frames: int | None = Form(default=None),
    target_width: int = Form(default=0),
    enable_loop_closure: bool = Form(default=True),
) -> Job:
    store = _store(request)
    settings = store.settings
    width = target_width or settings.target_width
    frames_cap = max_frames if max_frames is not None else settings.max_frames
    if not 64 <= width <= 4096:
        raise ApiError(400, "INVALID_PARAMETER", "target_width must be in [64, 4096]")
    if frames_cap < 1:
        raise ApiError(400, "INVALID_PARAMETER", "max_frames must be >= 1")
    if (video.content_type or "").split(";")[0].strip().lower() not in ALLOWED_MIME:
        raise ApiError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            f"content type {video.content_type!r} is not an accepted video type "
            f"({', '.join(sorted(ALLOWED_MIME))})",
        )

    job_id, job_dir = store.new_job_dir()
    filename = Path(video.filename or "upload.mp4").name
    suffix = Path(filename).suffix.lower() or ".mp4"
    dest = job_dir / f"{runner.SOURCE_NAME}{suffix}"
    try:
        size = await _stream_to_disk(video, dest, settings.max_upload_mb * 1024 * 1024)
        meta = _validate_video(dest, settings)
    except ApiError:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    # The clock starts here, not at the top of the handler: everything above is receiving
    # bytes off the wire and proving they decode, which is the client's bandwidth, not our
    # processing. "Upload-accepted" is the instant the service commits to the job.
    accepted_monotonic = time.monotonic()

    config = settings.runner_config(width, frames_cap, enable_loop_closure)
    record = store.register(
        job_id, job_dir, dest, filename, meta, config, accepted_monotonic
    )
    log.info(
        "job accepted",
        extra={"job_id": job_id, "bytes": size, "duration_s": meta["duration_s"]},
    )
    return Job.model_validate(record.to_public(store.queue_position(job_id)))


@router.post(f"{API_PREFIX}/jobs/from-sample/{{sample_id}}", response_model=Job, status_code=201)
async def create_job_from_sample(
    request: Request,
    sample_id: str,
    target_width: int | None = Form(default=None),
    max_frames: int | None = Form(default=None),
    enable_loop_closure: bool = Form(default=True),
) -> Job:
    store = _store(request)
    settings = store.settings
    # Same knobs as /jobs, validated before anything is created on disk. Without them the
    # UI's "run it both ways to see the drift difference" comparison silently runs the
    # same configuration twice.
    width = target_width or settings.target_width
    frames_cap = max_frames if max_frames is not None else settings.max_frames
    if not 64 <= width <= 4096:
        raise ApiError(400, "INVALID_PARAMETER", "target_width must be in [64, 4096]")
    if frames_cap < 1:
        raise ApiError(400, "INVALID_PARAMETER", "max_frames must be >= 1")

    entry = await asyncio.to_thread(_find_sample, settings.samples_dir, sample_id)
    if entry is None:
        raise ApiError(404, "SAMPLE_NOT_FOUND", f"no sample with id {sample_id}")
    source = entry["path"]
    if source is None or not source.exists():
        raise ApiError(404, "SAMPLE_NOT_FOUND", f"sample {sample_id} has no media file")

    job_id, job_dir = store.new_job_dir()
    dest = job_dir / f"{runner.SOURCE_NAME}{source.suffix.lower() or '.mp4'}"
    # Copy rather than symlink: the samples directory is mounted read-only in the
    # container and the job directory must own its own media for the TTL janitor.
    await asyncio.to_thread(shutil.copyfile, source, dest)
    try:
        meta = _validate_video(dest, settings)
    except ApiError:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    # Same clock rule as /jobs: the copy and the decode probe are admission, not work.
    accepted_monotonic = time.monotonic()

    config = settings.runner_config(width, frames_cap, enable_loop_closure)
    record = store.register(
        job_id, job_dir, dest, source.name, meta, config, accepted_monotonic
    )
    log.info("sample job accepted", extra={"job_id": job_id, "sample_id": sample_id})
    return Job.model_validate(record.to_public(store.queue_position(job_id)))


@router.get(f"{API_PREFIX}/jobs/{{job_id}}", response_model=Job)
async def get_job(request: Request, job_id: str) -> Job:
    _require_job(request, job_id)
    snapshot = _store(request).snapshot(job_id)
    return Job.model_validate(snapshot)


async def _stream_to_disk(upload: UploadFile, dest: Path, max_bytes: int) -> int:
    """Spool the upload to disk in chunks — a 200 MB clip must never sit in the heap."""
    size = 0
    with dest.open("wb") as fh:
        while chunk := await upload.read(UPLOAD_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                raise ApiError(
                    413,
                    "FILE_TOO_LARGE",
                    f"upload exceeds the {max_bytes // (1024 * 1024)} MB limit",
                )
            fh.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise ApiError(400, "EMPTY_UPLOAD", "uploaded file is empty")
    return size


def _validate_video(path: Path, settings: Settings) -> dict[str, Any]:
    """Prove the clip decodes before a job id is issued, then enforce the duration limit."""
    try:
        meta = exports.probe_video(path)
    except ValueError as exc:
        raise ApiError(400, "UNDECODABLE_VIDEO", f"video could not be decoded: {exc}") from exc
    if meta["frame_count"] <= 0:
        raise ApiError(400, "UNDECODABLE_VIDEO", "video contains no decodable frames")
    if meta["duration_s"] > settings.max_video_duration_s:
        raise ApiError(
            400,
            "VIDEO_TOO_LONG",
            f"clip is {meta['duration_s']:.1f}s, limit is "
            f"{settings.max_video_duration_s:.0f}s",
        )
    return meta
