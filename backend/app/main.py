"""FastAPI application: the factory, CORS, middleware, lifespan and the error handlers.

Implements `_contracts/assignment1-api.md` v1. Everything lives under `/api/v1`; a bare
`/health` alias exists purely so the container healthcheck does not need to know the prefix.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.status import (
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.models import APP_VERSION, ErrorResponse, Settings
from app.pipeline import Pipeline
from app.routes import api_router
from app.store import Store
from app.vlm import VlmClient

log = logging.getLogger("leadforge")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store = Store(config.data_dir)
        store.connect()
        vlm = VlmClient(config)
        pipeline = Pipeline(store, vlm, config)
        application.state.settings = config
        application.state.store = store
        application.state.vlm = vlm
        application.state.pipeline = pipeline
        application.state.started_at = time.monotonic()
        application.state.background = set()
        log.info(
            "leadforge started",
            extra={"extra_fields": {"provider": config.vlm_provider, "model": config.vlm_model}},
        )
        try:
            yield
        finally:
            await pipeline.shutdown()
            await vlm.aclose()
            store.close()

    application = FastAPI(
        title="LeadForge API",
        version=APP_VERSION,
        description="Business cards in. Pipeline-ready leads out.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
        # Every non-2xx answers with the contract's {detail, code}; say so in the schema
        # so the generated client the frontend uses knows the shape.
        responses={
            "4XX": {"model": ErrorResponse, "description": "Client error"},
            "5XX": {"model": ErrorResponse, "description": "Server error"},
        },
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials="*" not in config.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )
    _register_middleware(application, config)
    _register_error_handlers(application)
    application.include_router(api_router)
    return application


# --------------------------------------------------------------------------- middleware


def _register_middleware(application: FastAPI, config: Settings) -> None:
    # A whole batch at the documented limits; anything larger is rejected before it is read.
    max_body = config.max_files_per_batch * config.max_file_bytes + 2 * 1024 * 1024

    @application.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.monotonic()

        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_body:
            response: Response = JSONResponse(
                status_code=HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "detail": f"Request body exceeds {max_body // (1024 * 1024)} MB.",
                    "code": "PAYLOAD_TOO_LARGE",
                },
            )
        else:
            response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                }
            },
        )
        return response


def _register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or _default_code(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc.detail), "code": code},
            headers=getattr(exc, "headers", None),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = first.get("msg", "Invalid request.")
        detail = f"{location}: {message}" if location else message
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": detail, "code": "VALIDATION_ERROR"},
        )

    @application.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled error",
            extra={"extra_fields": {"request_id": getattr(request.state, "request_id", None)}},
        )
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": f"Internal error: {exc.__class__.__name__}",
                "code": "INTERNAL_ERROR",
            },
        )


def _default_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        503: "SERVICE_UNAVAILABLE",
    }.get(status_code, "ERROR")


app = create_app()
