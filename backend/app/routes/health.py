"""Liveness and the fire-and-forget VLM warm-up."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.models import APP_VERSION, HealthResponse, WarmupResponse
from app.routes.deps import API_PREFIX, get_vlm
from app.vlm import VlmClient

router = APIRouter()


@router.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "leadforge", "version": APP_VERSION, "api": API_PREFIX}


@router.get("/health", include_in_schema=False, response_model=HealthResponse)
async def health_alias(request: Request) -> HealthResponse:
    return _health(request)


@router.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["ops"])
async def health(request: Request) -> HealthResponse:
    return _health(request)


@router.post(f"{API_PREFIX}/vlm/warmup", response_model=WarmupResponse, tags=["ops"])
async def warmup(request: Request, vlm: VlmClient = Depends(get_vlm)) -> WarmupResponse:
    # Fire-and-forget by contract: the caller must not wait out a 4 minute cold start.
    background: set[asyncio.Task[Any]] = request.app.state.background
    task = asyncio.create_task(vlm.warmup())
    background.add(task)
    task.add_done_callback(background.discard)
    return WarmupResponse(warming=True)


def _health(request: Request) -> HealthResponse:
    vlm: VlmClient = request.app.state.vlm
    health = vlm.health()
    return HealthResponse(
        status="ok" if health.endpoint_reachable or health.provider == "stub" else "degraded",
        version=APP_VERSION,
        vlm=health,
        uptime_s=int(time.monotonic() - request.app.state.started_at),
    )
