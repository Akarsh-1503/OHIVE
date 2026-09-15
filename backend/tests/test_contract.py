"""Every response shape is asserted against the literal field names in the contract file.

The frontend is written against `_contracts/assignment1-api.md`; if a name drifts here, this
file fails rather than the browser.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.conftest import upload_parts

LEAD_KEYS = {
    "card_id", "batch_id", "filename", "status",
    "first_name", "last_name", "job_title", "company", "location",
    "phone", "phone_e164", "email", "website",
    "confidence", "overall_confidence", "quality_flags", "duplicate_of",
    "raw_text", "edited", "processing_ms", "error", "created_at",
}
CONFIDENCE_KEYS = {
    "first_name", "last_name", "job_title", "company",
    "location", "phone", "email", "website",
}
BATCH_KEYS = {
    "batch_id", "status", "total", "completed", "failed", "pending",
    "created_at", "finished_at", "elapsed_ms", "leads",
}
LEAD_STATUSES = {"queued", "processing", "completed", "needs_review", "failed"}
BATCH_STATUSES = {"queued", "processing", "completed", "partial", "failed"}


def wait_for_batch(client: TestClient, batch_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/batches/{batch_id}").json()
        if body["status"] in ("completed", "partial", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"batch {batch_id} did not finish within {timeout}s")


def test_health_matches_contract(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "vlm", "uptime_s"}
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert set(body["vlm"]) == {
        "provider", "model", "endpoint_reachable", "warm", "last_latency_ms"
    }
    assert body["vlm"]["provider"] in ("modal", "openai_compatible", "stub")
    assert isinstance(body["vlm"]["endpoint_reachable"], bool)
    assert isinstance(body["vlm"]["warm"], bool)
    assert isinstance(body["uptime_s"], int)


def test_health_alias_for_container_probe(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_warmup_returns_warming_flag(client: TestClient) -> None:
    response = client.post("/api/v1/vlm/warmup")
    assert response.status_code == 200
    assert response.json() == {"warming": True}


def test_post_batches_returns_queued_batch(client: TestClient) -> None:
    response = client.post("/api/v1/batches", files=upload_parts(["card_01.jpg", "card_02.jpg"]))
    assert response.status_code == 201
    batch = response.json()
    assert set(batch) == BATCH_KEYS
    assert batch["status"] in BATCH_STATUSES
    assert batch["total"] == 2
    assert batch["finished_at"] is None
    assert len(batch["leads"]) == 2
    for lead in batch["leads"]:
        assert set(lead) == LEAD_KEYS
        assert lead["status"] in LEAD_STATUSES
        assert set(lead["confidence"]) == CONFIDENCE_KEYS
        assert lead["batch_id"] == batch["batch_id"]
        assert lead["edited"] is False
        if lead["status"] == "queued":
            # Nothing has read the card yet, so these are null rather than 0 / "".
            assert lead["raw_text"] is None
            assert lead["processing_ms"] is None


def test_failed_lead_has_no_transcription_or_timing(client: TestClient) -> None:
    batch_id = client.post(
        "/api/v1/batches", files=upload_parts(["card_fail_01.jpg"])
    ).json()["batch_id"]
    lead = wait_for_batch(client, batch_id)["leads"][0]
    assert lead["status"] == "failed"
    assert lead["raw_text"] is None
    assert lead["error"]


def test_completed_lead_shape(client: TestClient) -> None:
    batch_id = client.post("/api/v1/batches", files=upload_parts(["card_01.jpg"])).json()["batch_id"]
    batch = wait_for_batch(client, batch_id)
    assert set(batch) == BATCH_KEYS
    assert batch["status"] == "completed"
    assert batch["finished_at"] is not None and batch["finished_at"].endswith("Z")
    assert batch["created_at"].endswith("Z")
    assert batch["elapsed_ms"] > 0
    assert batch["completed"] == 1 and batch["failed"] == 0 and batch["pending"] == 0

    lead = batch["leads"][0]
    assert set(lead) == LEAD_KEYS
    assert lead["status"] in ("completed", "needs_review")
    assert 0.0 <= lead["overall_confidence"] <= 1.0
    assert all(0.0 <= value <= 1.0 for value in lead["confidence"].values())
    assert isinstance(lead["quality_flags"], list)
    assert lead["raw_text"]
    assert lead["processing_ms"] > 0
    assert lead["error"] is None
    assert lead["phone_e164"] is None or lead["phone_e164"].startswith("+")


def test_batch_counters_match_the_documented_semantics(client: TestClient) -> None:
    """`completed` means "extracted" — status completed *and* needs_review."""
    batch_id = client.post(
        "/api/v1/batches",
        files=upload_parts(["card_01.jpg", "card_02.jpg", "card_fail_03.jpg"]),
    ).json()["batch_id"]
    batch = wait_for_batch(client, batch_id)

    extracted = [lead for lead in batch["leads"] if lead["status"] in ("completed", "needs_review")]
    assert batch["completed"] == len(extracted)
    assert batch["failed"] == sum(1 for lead in batch["leads"] if lead["status"] == "failed") == 1
    assert batch["total"] == batch["completed"] + batch["failed"] + batch["pending"]
    assert batch["total"] == len(batch["leads"]) == 3


def test_patch_lead_shape_and_semantics(client: TestClient) -> None:
    batch_id = client.post("/api/v1/batches", files=upload_parts(["card_01.jpg"])).json()["batch_id"]
    batch = wait_for_batch(client, batch_id)
    card_id = batch["leads"][0]["card_id"]

    response = client.patch(
        f"/api/v1/leads/{card_id}",
        json={"first_name": "Priyanka", "company": "Northwind Robotics Pvt Ltd"},
    )
    assert response.status_code == 200
    lead = response.json()
    assert set(lead) == LEAD_KEYS
    assert lead["first_name"] == "Priyanka"
    assert lead["company"] == "Northwind Robotics Pvt Ltd"
    assert lead["edited"] is True
    assert lead["confidence"]["first_name"] == 1.0
    assert lead["confidence"]["company"] == 1.0
    assert client.get(f"/api/v1/batches/{batch_id}").json()["leads"][0]["edited"] is True


def test_retry_returns_batch(client: TestClient) -> None:
    batch_id = client.post(
        "/api/v1/batches", files=upload_parts(["ok_01.jpg", "card_fail_02.jpg"])
    ).json()["batch_id"]
    batch = wait_for_batch(client, batch_id)
    assert batch["status"] == "partial"
    failed = [lead for lead in batch["leads"] if lead["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"]

    response = client.post(f"/api/v1/batches/{batch_id}/retry", json={})
    assert response.status_code == 200
    assert set(response.json()) == BATCH_KEYS
    assert wait_for_batch(client, batch_id)["status"] == "partial"


def test_retry_rejects_foreign_card(client: TestClient) -> None:
    batch_id = client.post("/api/v1/batches", files=upload_parts(["card_01.jpg"])).json()["batch_id"]
    wait_for_batch(client, batch_id)
    response = client.post(f"/api/v1/batches/{batch_id}/retry", json={"card_ids": ["nope"]})
    assert response.status_code == 422
    assert response.json()["code"] == "CARD_NOT_IN_BATCH"


def test_card_image_and_thumbnail(client: TestClient) -> None:
    batch = client.post("/api/v1/batches", files=upload_parts(["card_01.jpg"])).json()
    card_id = batch["leads"][0]["card_id"]

    full = client.get(f"/api/v1/cards/{card_id}/image")
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("image/jpeg")

    thumb = client.get(f"/api/v1/cards/{card_id}/image", params={"thumb": 1})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    assert 0 < len(thumb.content) < len(full.content)
    # Second read comes from the on-disk cache and must be byte-identical.
    assert client.get(f"/api/v1/cards/{card_id}/image", params={"thumb": 1}).content == thumb.content


def test_error_shape_is_detail_plus_code(client: TestClient) -> None:
    for path in ("/api/v1/batches/missing", "/api/v1/cards/missing/image"):
        response = client.get(path)
        assert response.status_code == 404
        body = response.json()
        assert set(body) == {"detail", "code"}
        assert body["code"] in ("BATCH_NOT_FOUND", "CARD_NOT_FOUND")
        assert isinstance(body["detail"], str) and body["detail"]


def test_request_id_header_round_trips(client: TestClient) -> None:
    generated = client.get("/api/v1/health")
    assert generated.headers["X-Request-ID"]
    echoed = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["X-Request-ID"] == "abc-123"


def test_sse_late_subscriber_gets_snapshot_then_completed(client: TestClient) -> None:
    batch_id = client.post("/api/v1/batches", files=upload_parts(["card_01.jpg"])).json()["batch_id"]
    wait_for_batch(client, batch_id)

    with client.stream("GET", f"/api/v1/batches/{batch_id}/events") as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        names = [
            line.removeprefix("event: ")
            for line in stream.iter_lines()
            if line.startswith("event: ")
        ]
    assert names == ["batch.snapshot", "batch.completed"]
