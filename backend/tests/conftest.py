"""Shared fixtures. Everything runs against the `stub` VLM provider: no GPU, no network."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

from app.main import create_app
from app.models import Batch, Lead, Settings, utc_now_iso
from app.pipeline import Event, Pipeline
from app.store import Store
from app.vlm import VlmClient

CARD_LINES = (
    "PRIYA RAGHAVAN",
    "VP of Partnerships",
    "Northwind Robotics",
    "Bengaluru, KA, India",
    "+91 80 4718 2200",
    "priya@northwind.io",
    "www.northwind.io",
)


def card_image(
    width: int = 1000,
    height: int = 600,
    blur: float = 0.0,
    darken: float = 0.0,
    lines: tuple[str, ...] = CARD_LINES,
    border: bool = True,
) -> Image.Image:
    """A synthetic business card that the quality heuristics rate as clean."""
    image = Image.new("RGB", (width, height), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    y = 60
    for line in lines:
        # Stamp the bitmap font a few times to fake a heavier weight.
        for dx in range(3):
            for dy in range(3):
                draw.text((60 + dx, y + dy), line, fill=(20, 20, 28))
        y += 60
    if border:
        draw.rectangle([20, 20, width - 20, height - 20], outline=(20, 20, 28), width=3)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    if darken:
        image = Image.blend(image, Image.new("RGB", image.size, (0, 0, 0)), darken)
    return image


def card_bytes(fmt: str = "JPEG", **kwargs: float) -> bytes:
    buffer = io.BytesIO()
    card_image(**kwargs).save(buffer, format=fmt, quality=92)
    return buffer.getvalue()


def upload_parts(names: list[str], payload: bytes | None = None) -> list[tuple[str, tuple]]:
    blob = payload if payload is not None else card_bytes()
    return [("files", (name, blob, "image/jpeg")) for name in names]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        vlm_provider="stub",
        data_dir=str(tmp_path / "data"),
        vlm_concurrency=4,
        vlm_retry_base_s=0.001,
        cors_origins=["*"],
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@dataclass
class Rig:
    settings: Settings
    store: Store
    vlm: VlmClient
    pipeline: Pipeline

    def seed_batch(self, filenames: list[str]) -> str:
        batch_id = "batch" + str(abs(hash(tuple(filenames))))[:10]
        self.store.create_batch(
            Batch(batch_id=batch_id, status="queued", total=len(filenames), pending=len(filenames))
        )
        payload = card_bytes()
        for position, name in enumerate(filenames):
            card_id = f"{batch_id}-card{position}"
            path = self.store.save_image(batch_id, card_id, ".jpg", payload)
            lead = Lead(
                card_id=card_id,
                batch_id=batch_id,
                filename=name,
                status="queued",
                created_at=utc_now_iso(),
            )
            self.store.insert_card(lead, position, path, "image/jpeg")
        return batch_id

    def card_ids(self, batch_id: str) -> list[str]:
        return [lead.card_id for lead in self.store.list_leads(batch_id)]


@pytest.fixture
async def rig(settings: Settings) -> Rig:
    store = Store(settings.data_dir)
    store.connect()
    vlm = VlmClient(settings)
    pipeline = Pipeline(store, vlm, settings)
    try:
        yield Rig(settings, store, vlm, pipeline)
    finally:
        await pipeline.shutdown()
        await vlm.aclose()
        store.close()


async def collect(pipeline: Pipeline, batch_id: str, limit: int = 500) -> list[Event]:
    """Read a batch's SSE frames until `batch.completed` and parse them back into events."""
    events: list[Event] = []
    async for frame in pipeline.stream(batch_id):
        head, _, body = frame.partition("\n")
        events.append(Event(head.removeprefix("event: "), json.loads(body.removeprefix("data: "))))
        if events[-1].name == "batch.completed" or len(events) >= limit:
            break
    return events
