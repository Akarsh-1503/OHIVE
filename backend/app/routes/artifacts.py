"""Everything a completed job hands back: the web reconstruction, the exports, the previews."""

from __future__ import annotations

import asyncio
import gzip

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from app import exports, runner
from app.models import Reconstruction
from app.routes.deps import API_PREFIX, ApiError, _require_completed, _store

router = APIRouter()


@router.get(f"{API_PREFIX}/jobs/{{job_id}}/reconstruction", response_model=Reconstruction)
async def get_reconstruction(request: Request, job_id: str) -> Response:
    _require_completed(request, job_id)
    path = _store(request).artifact(job_id, runner.RECON_WEB)
    if path is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", "reconstruction payload is missing")
    body = await asyncio.to_thread(path.read_bytes)
    headers = {
        # The reconstruction is immutable once written, so it can be cached forever.
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": f'"{job_id}-recon"',
        "Vary": "Accept-Encoding",
    }
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        headers["Content-Encoding"] = "gzip"
        return Response(body, media_type="application/json", headers=headers)
    return Response(
        await asyncio.to_thread(gzip.decompress, body),
        media_type="application/json",
        headers=headers,
    )


@router.get(f"{API_PREFIX}/jobs/{{job_id}}/export/ply")
async def export_ply(request: Request, job_id: str) -> Response:
    return _artifact_response(
        request, job_id, runner.PLY_NAME, "application/octet-stream", f"{job_id}.ply"
    )


@router.get(f"{API_PREFIX}/jobs/{{job_id}}/export/tum")
async def export_tum(request: Request, job_id: str) -> Response:
    return _artifact_response(
        request, job_id, runner.TUM_NAME, "text/plain; charset=utf-8", f"{job_id}.tum"
    )


@router.get(f"{API_PREFIX}/jobs/{{job_id}}/export/report.json")
async def export_report(request: Request, job_id: str) -> Response:
    return _artifact_response(
        request, job_id, runner.REPORT_NAME, "application/json", f"{job_id}-report.json"
    )


@router.get(f"{API_PREFIX}/jobs/{{job_id}}/preview/{{frame_index}}")
async def preview(request: Request, job_id: str, frame_index: int) -> Response:
    record = _require_completed(request, job_id)
    if frame_index < 0:
        raise ApiError(400, "INVALID_PARAMETER", "frame_index must be >= 0")
    full = await asyncio.to_thread(_store(request).full_reconstruction, job_id)
    pose = None
    if full is not None:
        pose = next((p for p in full["poses"] if p["frame_index"] == frame_index), None)
    try:
        jpeg = await asyncio.to_thread(
            exports.render_preview,
            record.video_path,
            frame_index,
            pose,
            record.config.max_features,
        )
    except LookupError as exc:
        raise ApiError(404, "FRAME_NOT_FOUND", str(exc)) from exc
    return Response(
        jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


def _artifact_response(
    request: Request, job_id: str, name: str, media_type: str, download_name: str
) -> Response:
    """Exports stream off a worker thread; a full-resolution PLY can be tens of megabytes."""
    _require_completed(request, job_id)
    path = _store(request).artifact(job_id, name)
    if path is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", f"{name} has not been written for {job_id}")
    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
