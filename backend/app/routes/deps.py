"""Shared route constants, the contract's error type, and the request-scoped accessors.

Everything a router needs from the running application is reached through `request.app.state`,
which the lifespan populates. Keeping the accessors here rather than in `main` is what lets the
dependency arrow point one way: `main` -> `routes` -> `deps`.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.jobs import JobStore

VERSION = "1.0.0"
API_PREFIX = "/api/v1"


class ApiError(StarletteHTTPException):
    """HTTPException that also carries the contract's SNAKE_CASE `code`."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def _store(request: Request) -> JobStore:
    store: JobStore = request.app.state.store
    return store


def _require_job(request: Request, job_id: str) -> Any:
    record = _store(request).get(job_id)
    if record is None:
        raise ApiError(404, "JOB_NOT_FOUND", f"no job with id {job_id}")
    return record


def _require_completed(request: Request, job_id: str) -> Any:
    record = _require_job(request, job_id)
    if record.status != "completed":
        raise ApiError(
            409, "JOB_NOT_COMPLETED", f"job {job_id} is {record.status}, not completed"
        )
    return record
