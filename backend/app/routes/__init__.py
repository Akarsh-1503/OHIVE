"""Router assembly. The app factory mounts `api_router` and nothing else.

Paths are spelled out in full inside each module rather than assembled from an
`APIRouter(prefix=...)`, because two of them — `/` and the `/health` alias the container
probe uses — deliberately sit outside `/api/v1`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes.batches import router as batches_router
from app.routes.events import router as events_router
from app.routes.health import router as health_router
from app.routes.leads import router as leads_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(batches_router)
api_router.include_router(events_router)
api_router.include_router(leads_router)

__all__ = ["api_router"]
