"""Driftless API — the app factory, middleware, lifespan, CORS and the error handlers.

Implements `_contracts/assignment2-api.md` v1 under the `/api/v1` prefix; the routes
themselves live in `app.routes`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.jobs import JobStore, Settings
from app.routes import api_router
from app.routes.deps import API_PREFIX, VERSION
from app.routes.events import _event_stream

SSE_PING_INTERVAL_S = 15.0

__all__ = [
    "API_PREFIX",
    "SSE_PING_INTERVAL_S",
    "VERSION",
    "_event_stream",
    "app",
    "create_app",
    "main",
]

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


# --- logging ----------------------------------------------------------------------------

_LOG_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _LOG_RESERVED}
        )
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    for noisy in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy).handlers[:] = [handler]
        logging.getLogger(noisy).propagate = False


log = logging.getLogger("driftless.api")


# --- errors -----------------------------------------------------------------------------


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


# --- app --------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings: Settings = app.state.settings
    store = JobStore(settings)
    app.state.store = store
    await store.start()
    try:
        yield
    finally:
        await store.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Driftless",
        version=VERSION,
        description="Monocular video in. Metric-consistent sparse map out.",
        lifespan=lifespan,
    )
    app.state.settings = settings or Settings.from_env()
    origins = app.state.settings.cors_origins

    # No GZipMiddleware. The only response big enough to matter is /reconstruction, which is
    # gzipped once at write time and served pre-compressed; blanket middleware would instead
    # buffer whole sample videos into memory and burn CPU re-compressing JPEG previews.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Encoding"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        # SSE streams are long-lived; logging their duration on completion is noise.
        if request.url.path.endswith("/events"):
            return response
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "request_id": rid,
            },
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
        code = getattr(exc, "code", None) or _default_code(exc.status_code)
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> Response:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        loc = ".".join(str(p) for p in first.get("loc", ())[1:])
        return _error(422, "INVALID_PARAMETER", f"{loc or 'request'}: {first.get('msg', '')}")

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
        log.exception("unhandled error", extra={"path": request.url.path})
        return _error(500, "INTERNAL_ERROR", "internal server error")

    app.include_router(api_router)
    return app


def _default_code(status_code: int) -> str:
    return {
        400: "INVALID_PARAMETER",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "JOB_NOT_COMPLETED",
        413: "FILE_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        422: "INVALID_PARAMETER",
    }.get(status_code, "INTERNAL_ERROR")


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_config=None,
    )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
