"""Router assembly. The app factory mounts `api_router` and nothing else.

Paths are spelled out in full inside each module rather than assembled from an
`APIRouter(prefix=...)`, and the include order below is the match order FastAPI will use.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes.artifacts import router as artifacts_router
from app.routes.events import router as events_router
from app.routes.health import router as health_router
from app.routes.jobs import router as jobs_router
from app.routes.samples import router as samples_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(jobs_router)
api_router.include_router(events_router)
api_router.include_router(artifacts_router)
api_router.include_router(samples_router)

__all__ = ["api_router"]
