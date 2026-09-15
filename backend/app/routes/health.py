"""Liveness plus the queue depth a reviewer needs to interpret a slow response."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

from app.models import Health
from app.routes.deps import API_PREFIX, VERSION, _store

router = APIRouter()


@router.get(f"{API_PREFIX}/health", response_model=Health)
async def health(request: Request) -> Health:
    store = _store(request)
    active, queued = store.counts()
    return Health(
        status="ok",
        version=VERSION,
        workers=store.settings.max_concurrent_jobs,
        cpu_count=os.cpu_count() or 1,
        active_jobs=active,
        queued_jobs=queued,
        slam_backend=store.settings.slam_backend,  # type: ignore[arg-type]
    )
