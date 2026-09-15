"""Upload acceptance and rejection paths, including the formats the contract promises."""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

import pillow_heif
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Settings
from tests.conftest import card_bytes, card_image, upload_parts
from tests.test_contract import wait_for_batch

pillow_heif.register_heif_opener()


def client_with(settings: Settings, **overrides: object) -> TestClient:
    return TestClient(create_app(dataclasses.replace(settings, **overrides)))


def encoded(fmt: str, **save_kwargs: object) -> bytes:
    buffer = io.BytesIO()
    card_image().save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("filename", "content_type", "payload_factory"),
    [
        ("card.jpg", "image/jpeg", lambda: card_bytes("JPEG")),
        ("card.png", "image/png", lambda: encoded("PNG")),
        ("card.webp", "image/webp", lambda: encoded("WEBP")),
        ("card.heic", "image/heic", lambda: encoded("HEIF", quality=80)),
        ("card.pdf", "application/pdf", lambda: encoded("PDF", resolution=150)),
        # Browsers frequently mislabel HEIC; the extension is accepted as a second opinion.
        ("card.heic", "application/octet-stream", lambda: encoded("HEIF", quality=80)),
    ],
)
def test_accepted_formats_process_end_to_end(
    client: TestClient, filename: str, content_type: str, payload_factory
) -> None:
    response = client.post(
        "/api/v1/batches", files=[("files", (filename, payload_factory(), content_type))]
    )
    assert response.status_code == 201
    batch = wait_for_batch(client, response.json()["batch_id"])
    assert batch["status"] == "completed"
    assert batch["leads"][0]["first_name"]


def test_rejects_too_many_files(client: TestClient) -> None:
    response = client.post(
        "/api/v1/batches", files=upload_parts([f"card_{i:02d}.jpg" for i in range(26)])
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "TOO_MANY_FILES"
    assert "25" in body["detail"]


def test_rejects_a_file_over_the_size_limit(settings: Settings) -> None:
    with client_with(settings, max_file_mb=1) as client:
        oversized = b"\xff\xd8" + b"\x00" * (2 * 1024 * 1024)
        response = client.post(
            "/api/v1/batches", files=[("files", ("huge.jpg", oversized, "image/jpeg"))]
        )
    assert response.status_code == 422
    assert response.json()["code"] == "FILE_TOO_LARGE"


def test_rejects_an_unsupported_mime_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/batches", files=[("files", ("notes.txt", b"not a card", "text/plain"))]
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_rejects_an_empty_file(client: TestClient) -> None:
    response = client.post("/api/v1/batches", files=[("files", ("blank.jpg", b"", "image/jpeg"))])
    assert response.status_code == 422
    assert response.json()["code"] == "EMPTY_FILE"


def test_rejects_a_request_with_no_files(client: TestClient) -> None:
    response = client.post("/api/v1/batches", files=[])
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_rejects_an_oversized_body_with_413(settings: Settings) -> None:
    """Content-Length is checked before the body is read, so a huge post costs nothing."""
    with client_with(settings, max_files_per_batch=1, max_file_mb=1) as client:
        response = client.post(
            "/api/v1/batches",
            files=[("files", ("huge.jpg", b"\x00" * (4 * 1024 * 1024), "image/jpeg"))],
        )
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_a_rejected_batch_leaves_nothing_behind(client: TestClient, settings: Settings) -> None:
    before = client.get("/api/v1/health").status_code
    assert before == 200
    response = client.post(
        "/api/v1/batches",
        files=[
            *upload_parts(["good.jpg"]),
            ("files", ("notes.txt", b"not a card", "text/plain")),
        ],
    )
    assert response.status_code == 422
    images = Path(settings.data_dir) / "images"
    assert not any(images.iterdir()) if images.exists() else True


def test_an_undecodable_image_fails_the_card_not_the_batch(client: TestClient) -> None:
    """Content-type says JPEG but the bytes are junk: one card fails, the batch survives."""
    response = client.post(
        "/api/v1/batches",
        files=[
            *upload_parts(["good.jpg"]),
            ("files", ("broken.jpg", b"\xff\xd8notreallyajpeg", "image/jpeg")),
        ],
    )
    assert response.status_code == 201
    batch = wait_for_batch(client, response.json()["batch_id"])
    assert batch["status"] == "partial"
    broken = next(lead for lead in batch["leads"] if lead["filename"] == "broken.jpg")
    assert broken["status"] == "failed"
    assert "could not decode" in broken["error"]
