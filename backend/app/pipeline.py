"""Batch orchestration and the in-process pub/sub that backs the SSE stream.

`POST /batches` must return immediately, so the work is fanned out onto a background task
whose concurrency is bounded by the VLM semaphore. Progress is broadcast to any number of
SSE subscribers, including one that connects halfway through — a late subscriber gets a
`batch.snapshot` built from the database first, so it can never miss the end state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.extract import build_lead, find_duplicate, prepare_image
from app.models import BatchStatus, Lead, Settings, utc_now_iso, utc_now_iso_ms
from app.store import Store
from app.vlm import VlmClient, VlmError

log = logging.getLogger("leadforge.pipeline")

# `batch.progress` fires once per card; at 6-way concurrency that is a burst of writes the UI
# cannot use. Coalesce to at most one every 200 ms, with the final one always sent.
PROGRESS_COALESCE_MS = 200
KEEPALIVE_S = 15.0
TERMINAL_BATCH_STATUS: frozenset[str] = frozenset({"completed", "partial", "failed"})


def _image_workers() -> int:
    """Cores actually available to this process, capped so the event loop keeps a slice.

    `sched_getaffinity` respects CPU pinning inside a container; `cpu_count` is the macOS
    and Windows fallback.
    """
    available = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    )
    return max(2, min(8, available or 4))


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any]


def sse_frame(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


class EventBus:
    """Fan-out of batch events to every connected SSE client."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}

    def register(self, batch_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.setdefault(batch_id, set()).add(queue)
        return queue

    def unregister(self, batch_id: str, queue: asyncio.Queue[Event]) -> None:
        subscribers = self._subscribers.get(batch_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(batch_id, None)

    def subscriber_count(self, batch_id: str) -> int:
        return len(self._subscribers.get(batch_id, ()))

    def publish(self, batch_id: str, name: str, data: dict[str, Any]) -> None:
        event = Event(name, data)
        for queue in tuple(self._subscribers.get(batch_id, ())):
            queue.put_nowait(event)


class Pipeline:
    def __init__(self, store: Store, vlm: VlmClient, settings: Settings) -> None:
        self.store = store
        self.vlm = vlm
        self.settings = settings
        self.bus = EventBus()
        self._tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen: dict[str, list[Lead]] = {}
        self._last_progress_ms: dict[str, float] = {}
        # Decoding and downscaling is CPU-bound, so it wants a pool sized to the cores, not
        # to the network concurrency. Handing 25 photos to the default executor at once just
        # makes 25 threads fight over the GIL and every card look slow.
        self._image_pool = ThreadPoolExecutor(
            max_workers=_image_workers(), thread_name_prefix="leadforge-img"
        )

    # ---- lifecycle ---------------------------------------------------------

    def start_batch(self, batch_id: str, card_ids: list[str]) -> None:
        """Kick off background processing. Returns as soon as the task is scheduled."""
        if not card_ids:
            return
        task = asyncio.create_task(self._run_batch(batch_id, card_ids), name=f"batch:{batch_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._tasks.clear()
        self._image_pool.shutdown(wait=False, cancel_futures=True)

    # ---- orchestration -----------------------------------------------------

    async def _run_batch(self, batch_id: str, card_ids: list[str]) -> None:
        self.store.set_batch_status(batch_id, "processing")
        self._locks.setdefault(batch_id, asyncio.Lock())
        # Seed the dedup window with whatever already succeeded, so a retry does not lose
        # the duplicate relationships established on the first pass.
        self._seen[batch_id] = [
            lead
            for lead in self.store.list_leads(batch_id)
            if lead.status in ("completed", "needs_review")
        ]
        try:
            async with asyncio.TaskGroup() as group:
                for card_id in card_ids:
                    group.create_task(self._process_card(batch_id, card_id))
        except* Exception as group_error:  # pragma: no cover - _process_card swallows its own
            log.exception("batch %s fan-out failed: %s", batch_id, group_error.exceptions)
        finally:
            await self._finalise(batch_id)

    async def _process_card(self, batch_id: str, card_id: str) -> None:
        lead = self.store.get_lead(card_id)
        if lead is None:
            return
        # Hold a slot across preprocessing *and* inference. Everything below is real work
        # on this card, which is what lets `started_at` and `processing_ms` agree: a card
        # waiting its turn is still `queued`, and is not reported as a slow card.
        async with self.vlm.semaphore:
            await self._extract_one(batch_id, card_id, lead)
        self._publish_progress(batch_id)

    async def _extract_one(self, batch_id: str, card_id: str, lead: Lead) -> None:
        started = time.monotonic()
        lead = lead.model_copy(update={"status": "processing", "error": None})
        self.store.update_lead(lead)
        self.bus.publish(
            batch_id,
            "card.started",
            {
                "card_id": card_id,
                "filename": lead.filename,
                # Server-stated, so a client that connects mid-batch can still time the card.
                "started_at": utc_now_iso_ms(),
            },
        )

        try:
            source = self.store.get_card_source(card_id)
            if source is None:
                raise VlmError("stored upload is missing", terminal=True)
            path, content_type, _ = source
            raw = await asyncio.to_thread(path.read_bytes)
            prepared = await asyncio.get_running_loop().run_in_executor(
                self._image_pool, prepare_image, raw, lead.filename, content_type
            )
            result = await self.vlm.extract_card(prepared.jpeg_bytes, lead.filename)
            lead = build_lead(
                lead,
                result,
                prepared.quality_flags,
                self.settings.confidence_threshold,
                self.settings.default_phone_region,
            )
            # Work done on this card, not wall clock. Both terms are measured inside their
            # own worker, so a card that merely queued behind five others is not reported as
            # a slow card. Batch wall clock lives in `Batch.elapsed_ms`.
            lead = lead.model_copy(
                update={"processing_ms": prepared.duration_ms + result.latency_ms}
            )

            async with self._locks[batch_id]:
                earlier = self._seen.setdefault(batch_id, [])
                duplicate_of = find_duplicate(
                    lead, earlier, self.settings.duplicate_fuzzy_threshold
                )
                lead = lead.model_copy(update={"duplicate_of": duplicate_of})
                earlier.append(lead)

            self.store.update_lead(lead, raw_json=result.payload)
            self.bus.publish(batch_id, "card.completed", lead.model_dump(mode="json"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            log.warning("card %s failed: %s", card_id, message)
            lead = lead.model_copy(
                update={
                    "status": "failed",
                    "error": message,
                    "processing_ms": int((time.monotonic() - started) * 1000),
                }
            )
            self.store.update_lead(lead)
            self.bus.publish(batch_id, "card.failed", {"card_id": card_id, "error": message})

    async def _finalise(self, batch_id: str) -> None:
        leads = self.store.list_leads(batch_id)
        failed = sum(1 for lead in leads if lead.status == "failed")
        succeeded = sum(1 for lead in leads if lead.status in ("completed", "needs_review"))
        status: BatchStatus = (
            "failed" if failed and not succeeded else "partial" if failed else "completed"
        )
        self.store.set_batch_status(batch_id, status, finished_at=utc_now_iso())
        self._publish_progress(batch_id, force=True)

        batch = self.store.get_batch(batch_id)
        if batch is not None:
            self.bus.publish(batch_id, "batch.completed", batch.model_dump(mode="json"))
        self._seen.pop(batch_id, None)
        self._locks.pop(batch_id, None)
        self._last_progress_ms.pop(batch_id, None)

    def _publish_progress(self, batch_id: str, *, force: bool = False) -> None:
        now = time.monotonic() * 1000
        last = self._last_progress_ms.get(batch_id, 0.0)
        if not force and (now - last) < PROGRESS_COALESCE_MS:
            return
        self._last_progress_ms[batch_id] = now
        batch = self.store.get_batch(batch_id)
        if batch is None:
            return
        self.bus.publish(
            batch_id,
            "batch.progress",
            {
                "completed": batch.completed,
                "failed": batch.failed,
                "total": batch.total,
                "elapsed_ms": batch.elapsed_ms,
            },
        )

    # ---- SSE ---------------------------------------------------------------

    async def stream(self, batch_id: str) -> AsyncIterator[str]:
        """Yield SSE frames for `batch_id` until the batch completes or the client leaves."""
        queue = self.bus.register(batch_id)
        try:
            snapshot = self.store.get_batch(batch_id)
            if snapshot is None:
                return
            yield sse_frame("batch.snapshot", snapshot.model_dump(mode="json"))

            # A subscriber that arrives after the last card finished still needs the closing
            # event, so synthesise it from the persisted terminal state.
            if snapshot.status in TERMINAL_BATCH_STATUS:
                yield sse_frame("batch.completed", snapshot.model_dump(mode="json"))
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    yield sse_frame("ping", {})
                    continue
                yield sse_frame(event.name, event.data)
                if event.name == "batch.completed":
                    return
        finally:
            self.bus.unregister(batch_id, queue)
