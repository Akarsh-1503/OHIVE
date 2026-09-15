"""The SSE progress stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.routes.deps import API_PREFIX, _require_job, _store

router = APIRouter()


@router.get(f"{API_PREFIX}/jobs/{{job_id}}/events")
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    _require_job(request, job_id)
    return StreamingResponse(
        _event_stream(request, job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def _event_stream(request: Request, job_id: str) -> AsyncIterator[bytes]:
    # The keep-alive interval is an app-level knob owned by `app.main`, which imports this
    # module to assemble its router; reading it through the module at call time keeps that
    # dependency from becoming an import cycle.
    from app import main

    store = _store(request)
    # subscribe() and the terminal check must happen with no await between them: the loop is
    # single-threaded, so this pair is atomic and a job cannot complete in the gap and have
    # its terminal event both replayed here and delivered through the queue.
    q = store.subscribe(job_id)
    snapshot = store.snapshot(job_id)
    terminal = store.terminal_event(job_id)
    last_progress = store.last_progress(job_id)

    if snapshot is None:
        store.unsubscribe(job_id, q)
        return

    try:
        yield _sse("job.snapshot", snapshot)
        if last_progress is not None:
            # Late subscriber: replay the current progress so its bar isn't stuck at zero.
            yield _sse("job.progress", last_progress)
        if terminal is not None:
            yield _sse(terminal[0], terminal[1])
            return

        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=main.SSE_PING_INTERVAL_S)
            except TimeoutError:
                yield _sse("ping", {})
                continue
            if item is None:
                return
            yield _sse(item[0], item[1])
    finally:
        store.unsubscribe(job_id, q)
