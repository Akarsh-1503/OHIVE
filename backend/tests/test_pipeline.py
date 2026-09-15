"""Batch lifecycle and SSE semantics, including late and concurrent subscribers."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import pytest

from app import pipeline as pipeline_module
from tests.conftest import Rig, collect


async def run_batch(rig: Rig, filenames: list[str]) -> tuple[str, list]:
    """Subscribe first, then start the batch, so the live event order is observable."""
    batch_id = rig.seed_batch(filenames)
    collector = asyncio.create_task(collect(rig.pipeline, batch_id))
    await asyncio.sleep(0.05)  # let the subscriber register before work starts
    rig.pipeline.start_batch(batch_id, rig.card_ids(batch_id))
    return batch_id, await asyncio.wait_for(collector, timeout=20)


async def test_full_batch_lifecycle_over_sse(rig: Rig) -> None:
    names = [f"card_{index:02d}.jpg" for index in range(6)]
    batch_id, events = await run_batch(rig, names)

    assert events[0].name == "batch.snapshot"
    assert events[0].data["batch_id"] == batch_id
    assert events[0].data["total"] == 6
    assert events[-1].name == "batch.completed"
    assert events[-1].data["status"] == "completed"
    assert events[-1].data["completed"] == 6
    assert events[-1].data["pending"] == 0
    assert events[-1].data["finished_at"] is not None

    started = [event for event in events if event.name == "card.started"]
    completed = [event for event in events if event.name == "card.completed"]
    assert len(started) == 6
    assert len(completed) == 6
    assert {event.data["card_id"] for event in started} == set(rig.card_ids(batch_id))
    assert {event.data["filename"] for event in started} == set(names)

    # Every card is announced before it is delivered.
    order = [(event.name, event.data["card_id"]) for event in events if "card_id" in event.data]
    for card_id in rig.card_ids(batch_id):
        assert order.index(("card.started", card_id)) < order.index(("card.completed", card_id))

    progress = [event for event in events if event.name == "batch.progress"]
    assert progress, "expected at least one coalesced progress event"
    assert progress[-1].data == {
        "completed": 6,
        "failed": 0,
        "total": 6,
        "elapsed_ms": progress[-1].data["elapsed_ms"],
    }
    assert [event.data["completed"] for event in progress] == sorted(
        event.data["completed"] for event in progress
    )


async def test_card_started_carries_a_millisecond_server_timestamp(rig: Rig) -> None:
    """The client cannot infer this from arrival time if it connected mid-batch."""
    batch_id, events = await run_batch(rig, ["card_01.jpg", "card_02.jpg"])
    started = [event for event in events if event.name == "card.started"]
    assert len(started) == 2

    for event in started:
        assert set(event.data) == {"card_id", "filename", "started_at"}
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.data["started_at"])
        # Parseable as a real instant, and inside the batch's own lifetime.
        stamp = datetime.fromisoformat(event.data["started_at"].replace("Z", "+00:00"))
        assert stamp.tzinfo is not None
        batch = rig.store.get_batch(batch_id)
        assert batch is not None
        assert stamp >= datetime.fromisoformat(batch.created_at.replace("Z", "+00:00"))


async def test_queued_cards_have_no_transcription_or_timing(rig: Rig) -> None:
    """`raw_text` and `processing_ms` cannot exist before the model has read the card."""
    batch_id = rig.seed_batch(["card_01.jpg"])
    lead = rig.store.list_leads(batch_id)[0]
    assert lead.status == "queued"
    assert lead.raw_text is None
    assert lead.processing_ms is None

    _, events = await run_batch(rig, ["ok_01.jpg", "card_fail_02.jpg"])
    completed = next(e for e in events if e.name == "card.completed").data
    assert completed["raw_text"] and completed["processing_ms"] > 0

    failed_id = next(e for e in events if e.name == "card.failed").data["card_id"]
    failed = rig.store.get_lead(failed_id)
    assert failed is not None and failed.raw_text is None


async def test_completed_lead_events_carry_the_full_lead(rig: Rig) -> None:
    _, events = await run_batch(rig, ["card_01.jpg"])
    lead = next(event for event in events if event.name == "card.completed").data
    assert lead["status"] in ("completed", "needs_review")
    assert lead["first_name"] and lead["company"]
    assert set(lead["confidence"]) == {
        "first_name", "last_name", "job_title", "company",
        "location", "phone", "email", "website",
    }
    assert lead["processing_ms"] > 0


async def test_failed_card_reports_card_failed_and_partial_batch(rig: Rig) -> None:
    _, events = await run_batch(rig, ["ok_01.jpg", "card_fail_02.jpg"])
    failures = [event for event in events if event.name == "card.failed"]
    assert len(failures) == 1
    assert "unreadable" in failures[0].data["error"]
    assert events[-1].data["status"] == "partial"
    assert events[-1].data["failed"] == 1
    assert events[-1].data["completed"] == 1


async def test_late_subscriber_never_misses_the_end_state(rig: Rig) -> None:
    batch_id = rig.seed_batch([f"card_{index:02d}.jpg" for index in range(8)])
    rig.pipeline.start_batch(batch_id, rig.card_ids(batch_id))
    await asyncio.sleep(0.06)  # join mid-flight, after some cards have already finished

    events = await asyncio.wait_for(collect(rig.pipeline, batch_id), timeout=20)
    assert events[0].name == "batch.snapshot"
    assert events[-1].name == "batch.completed"

    # Snapshot plus live deltas must together account for every card in a terminal state.
    seen = {
        lead["card_id"]
        for lead in events[0].data["leads"]
        if lead["status"] in ("completed", "needs_review", "failed")
    }
    seen |= {event.data["card_id"] for event in events if event.name == "card.completed"}
    assert seen == set(rig.card_ids(batch_id))


async def test_subscriber_after_completion_gets_snapshot_then_completed(rig: Rig) -> None:
    batch_id, _ = await run_batch(rig, ["card_01.jpg"])
    events = await asyncio.wait_for(collect(rig.pipeline, batch_id), timeout=5)
    assert [event.name for event in events] == ["batch.snapshot", "batch.completed"]
    assert events[0].data["status"] == "completed"


async def test_two_concurrent_subscribers_both_see_the_whole_batch(rig: Rig) -> None:
    batch_id = rig.seed_batch([f"card_{index:02d}.jpg" for index in range(4)])
    first = asyncio.create_task(collect(rig.pipeline, batch_id))
    second = asyncio.create_task(collect(rig.pipeline, batch_id))
    await asyncio.sleep(0.05)
    rig.pipeline.start_batch(batch_id, rig.card_ids(batch_id))

    left, right = await asyncio.wait_for(asyncio.gather(first, second), timeout=20)
    for events in (left, right):
        assert events[0].name == "batch.snapshot"
        assert events[-1].name == "batch.completed"
        assert len([e for e in events if e.name == "card.completed"]) == 4
    assert rig.pipeline.bus.subscriber_count(batch_id) == 0  # both cleaned up on exit


async def test_subscriber_is_unregistered_when_the_client_disconnects(rig: Rig) -> None:
    batch_id = rig.seed_batch(["card_01.jpg"])
    stream = rig.pipeline.stream(batch_id)
    await anext(stream)
    assert rig.pipeline.bus.subscriber_count(batch_id) == 1
    await stream.aclose()
    assert rig.pipeline.bus.subscriber_count(batch_id) == 0


async def test_keepalive_ping_on_an_idle_stream(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_module, "KEEPALIVE_S", 0.02)
    batch_id = rig.seed_batch(["card_01.jpg"])  # seeded but never started: nothing to emit
    stream = rig.pipeline.stream(batch_id)
    try:
        frames = [await asyncio.wait_for(anext(stream), timeout=2) for _ in range(3)]
    finally:
        await stream.aclose()
    assert frames[0].startswith("event: batch.snapshot")
    assert frames[1] == "event: ping\ndata: {}\n\n"
    assert frames[2] == "event: ping\ndata: {}\n\n"


async def test_stream_for_an_unknown_batch_yields_nothing(rig: Rig) -> None:
    assert [frame async for frame in rig.pipeline.stream("does-not-exist")] == []


async def test_duplicates_are_flagged_not_dropped(rig: Rig) -> None:
    batch_id, events = await run_batch(rig, ["scan_dup_a.jpg", "scan_dup_b.jpg", "other_01.jpg"])
    leads = rig.store.list_leads(batch_id)
    assert len(leads) == 3

    duplicates = [lead for lead in leads if lead.duplicate_of]
    assert len(duplicates) == 1
    anchor = next(lead for lead in leads if lead.card_id == duplicates[0].duplicate_of)
    assert anchor.duplicate_of is None
    assert anchor.email == duplicates[0].email
    assert events[-1].data["completed"] == 3  # nothing was dropped


async def test_retry_requeues_and_reprocesses_a_card(rig: Rig) -> None:
    batch_id, _ = await run_batch(rig, ["ok_01.jpg", "card_fail_02.jpg"])
    failed_ids = rig.store.failed_card_ids(batch_id)
    assert len(failed_ids) == 1

    for card_id in failed_ids:
        lead = rig.store.get_lead(card_id)
        assert lead is not None
        rig.store.update_lead(lead.model_copy(update={"status": "queued", "error": None}))
    rig.store.reopen_batch(batch_id)

    collector = asyncio.create_task(collect(rig.pipeline, batch_id))
    await asyncio.sleep(0.05)
    rig.pipeline.start_batch(batch_id, failed_ids)
    events = await asyncio.wait_for(collector, timeout=20)

    assert [event.data["card_id"] for event in events if event.name == "card.started"] == failed_ids
    assert events[-1].data["status"] == "partial"
    # The card that already succeeded is untouched by the retry.
    assert events[-1].data["completed"] == 1


async def test_processing_is_bounded_by_the_vlm_semaphore(rig: Rig) -> None:
    assert rig.vlm.semaphore._value == rig.settings.vlm_concurrency
    batch_id, _ = await run_batch(rig, [f"card_{index:02d}.jpg" for index in range(10)])
    assert rig.vlm.semaphore._value == rig.settings.vlm_concurrency  # every permit returned
    batch = rig.store.get_batch(batch_id)
    assert batch is not None and batch.elapsed_ms > 0
    assert all(lead.processing_ms > 0 for lead in batch.leads)


async def test_card_started_fires_when_a_slot_is_won_not_when_queued(rig: Rig) -> None:
    """`started_at` must mean "work began", or per-card timing is meaningless.

    With more cards than concurrency slots, the number of cards that have started but not
    yet finished can never exceed the semaphore bound.
    """
    names = [f"card_{index:02d}.jpg" for index in range(10)]
    _, events = await run_batch(rig, names)

    in_flight = 0
    peak = 0
    for event in events:
        if event.name == "card.started":
            in_flight += 1
            peak = max(peak, in_flight)
        elif event.name in ("card.completed", "card.failed"):
            in_flight -= 1

    assert peak <= rig.settings.vlm_concurrency, (
        f"{peak} cards reported as started at once with only "
        f"{rig.settings.vlm_concurrency} slots"
    )
    assert peak > 1, "expected genuine concurrency in this fixture"
    assert len([e for e in events if e.name == "card.started"]) == 10
    assert in_flight == 0


async def test_started_at_timestamps_are_spread_across_the_batch(rig: Rig) -> None:
    """All ten cards cannot legitimately share one start time at 4-way concurrency."""
    _, events = await run_batch(rig, [f"card_{index:02d}.jpg" for index in range(10)])
    stamps = {e.data["started_at"] for e in events if e.name == "card.started"}
    assert len(stamps) > 1
