"""The SSE progress stream for a batch."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.pipeline import Pipeline
from app.routes.deps import API_PREFIX, _require_batch, get_pipeline, get_store
from app.store import Store

router = APIRouter()


@router.get(f"{API_PREFIX}/batches/{{batch_id}}/events", tags=["batches"])
async def batch_events(
    batch_id: str,
    store: Store = Depends(get_store),
    pipeline: Pipeline = Depends(get_pipeline),
) -> StreamingResponse:
    _require_batch(store, batch_id)
    return StreamingResponse(
        pipeline.stream(batch_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
