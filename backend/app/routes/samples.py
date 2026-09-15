"""The built-in demo clips: the manifest listing and the media itself."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from app.models import Sample
from app.routes.deps import API_PREFIX, ApiError, _store

log = logging.getLogger("driftless.api")

router = APIRouter()


@router.get(f"{API_PREFIX}/samples", response_model=list[Sample])
async def list_samples(request: Request) -> list[Sample]:
    entries = await asyncio.to_thread(_read_manifest, _store(request).settings.samples_dir)
    return [Sample.model_validate({k: v for k, v in e.items() if k != "path"})
            for e in entries]


@router.get(f"{API_PREFIX}/samples/{{sample_id}}/file")
async def sample_file(request: Request, sample_id: str) -> Response:
    settings = _store(request).settings
    entry = await asyncio.to_thread(_find_sample, settings.samples_dir, sample_id)
    if entry is None or entry["path"] is None or not entry["path"].exists():
        raise ApiError(404, "SAMPLE_NOT_FOUND", f"no media for sample {sample_id}")
    # FileResponse honours Range, which is what a <video> element issues while scrubbing.
    return FileResponse(
        entry["path"],
        media_type="video/mp4",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _read_manifest(samples_dir: Path) -> list[dict[str, Any]]:
    """Read the samples manifest that another agent generates.

    Tolerant by design: a missing directory, a missing manifest or a malformed one all yield
    an empty list rather than a 500, because `/samples` must never break the landing page.
    """
    manifest = samples_dir / "manifest.json"
    if not manifest.exists():
        return []
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("samples manifest unreadable", extra={"path": str(manifest)})
        return []
    items = raw.get("samples", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []

    entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sample_id = str(item.get("id") or item.get("sample_id") or "").strip()
        if not sample_id:
            continue
        rel = item.get("file") or item.get("path") or item.get("filename")
        path = None
        if isinstance(rel, str) and rel:
            candidate = (samples_dir / rel).resolve()
            if candidate.is_relative_to(samples_dir) and candidate.exists():
                path = candidate
        entries.append(
            {
                "id": sample_id,
                "name": str(item.get("name") or sample_id),
                "description": str(item.get("description") or ""),
                "duration_s": float(item.get("duration_s") or 0.0),
                "url": str(item.get("url") or f"{API_PREFIX}/samples/{sample_id}/file"),
                "path": path,
            }
        )
    return entries


def _find_sample(samples_dir: Path, sample_id: str) -> dict[str, Any] | None:
    return next((e for e in _read_manifest(samples_dir) if e["id"] == sample_id), None)
