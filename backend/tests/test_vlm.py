"""Response parsing, the stub provider, and retry / repair behaviour against a fake server."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any

import pytest
from openai import APIStatusError, APITimeoutError

from app.models import LEAD_FIELDS, Settings
from app.vlm import (
    CARD_SCHEMA,
    REPAIR_PROMPT,
    VlmClient,
    VlmError,
    normalise_payload,
    parse_card_json,
    stub_card,
)

VALID_PAYLOAD = {
    "raw_text": "Priya Raghavan\nNorthwind Robotics",
    "fields": {
        "first_name": {"value": "Priya", "confidence": 0.97},
        "last_name": {"value": "Raghavan", "confidence": 0.96},
        "job_title": {"value": "VP of Partnerships", "confidence": 0.91},
        "company": {"value": "Northwind Robotics", "confidence": 0.98},
        "location": {"value": "Bengaluru, KA, India", "confidence": 0.74},
        "phone": {"value": "+91 80 4718 2200", "confidence": 0.99},
        "email": {"value": "priya@northwind.io", "confidence": 0.99},
        "website": {"value": "northwind.io", "confidence": 0.88},
    },
}


# --------------------------------------------------------------------------- parsing


def test_schema_covers_every_contract_field() -> None:
    assert set(CARD_SCHEMA["properties"]["fields"]["properties"]) == set(LEAD_FIELDS)
    assert CARD_SCHEMA["properties"]["fields"]["required"] == list(LEAD_FIELDS)
    assert CARD_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(VALID_PAYLOAD),
        "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```",
        "```\n" + json.dumps(VALID_PAYLOAD) + "\n```",
        "Here you go:\n" + json.dumps(VALID_PAYLOAD) + "\nHope that helps!",
    ],
)
def test_tolerant_parser_recovers_the_object(text: str) -> None:
    assert parse_card_json(text) == VALID_PAYLOAD


def test_parser_handles_braces_inside_strings() -> None:
    payload = {"raw_text": "Suite {4}, 12 King St", "fields": {}}
    assert parse_card_json("noise " + json.dumps(payload) + " trailing") == payload


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", "{unbalanced"])
def test_parser_raises_a_retryable_error_on_garbage(text: str) -> None:
    with pytest.raises(VlmError) as caught:
        parse_card_json(text)
    assert caught.value.terminal is False


def test_normalise_payload_reads_the_nested_shape() -> None:
    values, confidences, raw_text = normalise_payload(VALID_PAYLOAD)
    assert values["first_name"] == "Priya"
    assert confidences["location"] == 0.74
    assert raw_text.startswith("Priya Raghavan")


def test_normalise_payload_accepts_a_flat_shape_with_a_parallel_confidence_map() -> None:
    flat: dict[str, Any] = {
        "raw_text": "x",
        "first_name": "Priya",
        "email": "priya@northwind.io",
        "confidence": {"first_name": 0.8, "email": 0.9},
    }
    values, confidences, _ = normalise_payload(flat)
    assert values["first_name"] == "Priya"
    assert confidences["first_name"] == 0.8
    assert confidences["email"] == 0.9
    assert values["company"] is None and confidences["company"] == 0.0


def test_normalise_payload_coerces_and_clamps() -> None:
    values, confidences, _ = normalise_payload(
        {
            "fields": {
                "phone": {"value": 918047182200, "confidence": 4.2},
                "company": {"value": "  ", "confidence": 0.9},
                "email": {"value": "a@b.co"},
            }
        }
    )
    assert values["phone"] == "918047182200"
    assert confidences["phone"] == 1.0  # clamped into 0..1
    assert values["company"] is None and confidences["company"] == 0.0
    assert confidences["email"] > 0.0  # a missing score falls back rather than zeroing out


# --------------------------------------------------------------------------- stub provider


def test_stub_is_deterministic_and_complete() -> None:
    first = stub_card("card_07.jpg")
    assert stub_card("card_07.jpg") == first
    values, confidences, raw_text = first
    assert set(values) == set(LEAD_FIELDS)
    assert all(values[name] for name in LEAD_FIELDS)
    assert all(0.0 < confidences[name] <= 1.0 for name in LEAD_FIELDS)
    assert values["first_name"] in raw_text


def test_stub_filename_keywords_drive_the_outcome() -> None:
    assert stub_card("scan_dup_a.jpg")[0] == stub_card("scan_dup_b.png")[0]
    assert stub_card("card_noemail.jpg")[0]["email"] is None
    assert stub_card("card_nophone.jpg")[0]["phone"] is None
    assert max(stub_card("card_blurry.jpg")[1].values()) < 0.5
    with pytest.raises(VlmError):
        stub_card("card_fail.jpg")


def test_different_filenames_produce_different_leads() -> None:
    emails = {stub_card(f"card_{index:02d}.jpg")[0]["email"] for index in range(12)}
    assert len(emails) > 6


# --------------------------------------------------------------------------- retries


def completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def status_error(code: int) -> APIStatusError:
    response = SimpleNamespace(
        status_code=code, headers={}, request=SimpleNamespace(method="POST", url="http://vlm/v1")
    )
    return APIStatusError(f"server said {code}", response=response, body=None)


class ScriptedCompletions:
    """Replays a script of exceptions and responses, recording every call."""

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fake_client(settings: Settings, script: list[object]) -> tuple[VlmClient, ScriptedCompletions]:
    client = VlmClient(
        dataclasses.replace(
            settings,
            vlm_provider="openai_compatible",
            vlm_base_url="http://vlm.invalid/v1",
            vlm_max_retries=2,
            vlm_retry_base_s=0.001,
        )
    )
    completions = ScriptedCompletions(script)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=_noop,
    )
    return client, completions


async def _noop() -> None:
    return None


async def test_retries_on_5xx_then_succeeds(settings: Settings) -> None:
    client, completions = fake_client(
        settings, [status_error(503), status_error(500), completion(json.dumps(VALID_PAYLOAD))]
    )
    result = await client.extract_card(b"jpeg", "card.jpg")
    assert result.attempts == 3
    assert result.repaired is False
    assert result.values["company"] == "Northwind Robotics"
    assert len(completions.calls) == 3
    assert client.health().endpoint_reachable is True
    assert client.health().warm is True


async def test_retries_on_timeout(settings: Settings) -> None:
    timeout = APITimeoutError(request=SimpleNamespace(method="POST", url="http://vlm/v1"))
    client, _ = fake_client(settings, [timeout, completion(json.dumps(VALID_PAYLOAD))])
    result = await client.extract_card(b"jpeg", "card.jpg")
    assert result.attempts == 2


async def test_gives_up_after_the_retry_budget(settings: Settings) -> None:
    client, completions = fake_client(settings, [status_error(502)] * 3)
    with pytest.raises(VlmError) as caught:
        await client.extract_card(b"jpeg", "card.jpg")
    assert caught.value.terminal is True
    assert "after 3 attempts" in str(caught.value)
    assert len(completions.calls) == 3


async def test_client_errors_are_terminal_and_not_retried(settings: Settings) -> None:
    client, completions = fake_client(settings, [status_error(400)])
    with pytest.raises(VlmError) as caught:
        await client.extract_card(b"jpeg", "card.jpg")
    assert caught.value.terminal is True
    assert len(completions.calls) == 1


async def test_repair_turn_recovers_unparseable_output(settings: Settings) -> None:
    client, completions = fake_client(
        settings,
        [
            completion("I'm sorry, I can't read that card."),
            completion(json.dumps(VALID_PAYLOAD)),
        ],
    )
    result = await client.extract_card(b"jpeg", "card.jpg")

    assert result.repaired is True
    assert result.attempts == 2
    assert result.values["email"] == "priya@northwind.io"

    repair_messages = completions.calls[1]["messages"]
    assert repair_messages[-1]["content"] == REPAIR_PROMPT
    assert repair_messages[-2]["role"] == "assistant"


async def test_repair_turn_failing_twice_raises(settings: Settings) -> None:
    client, _ = fake_client(settings, [completion("nope"), completion("still nope")])
    with pytest.raises(VlmError):
        await client.extract_card(b"jpeg", "card.jpg")


async def test_request_is_schema_constrained_and_sends_an_inline_data_url(
    settings: Settings,
) -> None:
    client, completions = fake_client(settings, [completion(json.dumps(VALID_PAYLOAD))])
    await client.extract_card(b"\xff\xd8jpegbytes", "card.jpg")

    call = completions.calls[0]
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["schema"] == CARD_SCHEMA
    assert call["response_format"]["json_schema"]["strict"] is True
    image_part = call["messages"][1]["content"][0]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_cold_endpoint_gets_the_long_timeout(settings: Settings) -> None:
    client, completions = fake_client(settings, [completion(json.dumps(VALID_PAYLOAD))] * 2)
    assert client.health().warm is False

    await client.extract_card(b"jpeg", "card.jpg")
    assert completions.calls[0]["timeout"] == client.settings.vlm_cold_timeout_s

    assert client.health().warm is True
    await client.extract_card(b"jpeg", "card.jpg")
    assert completions.calls[1]["timeout"] == client.settings.vlm_timeout_s


async def test_modal_proxy_headers_are_attached_when_configured(settings: Settings) -> None:
    client = VlmClient(
        dataclasses.replace(
            settings,
            vlm_provider="modal",
            vlm_base_url="http://vlm.invalid/v1",
            modal_proxy_key="key",
            modal_proxy_secret="secret",
        )
    )
    try:
        headers = client._client.default_headers
        assert headers["Modal-Key"] == "key"
        assert headers["Modal-Secret"] == "secret"
    finally:
        await client.aclose()


def test_non_stub_provider_requires_a_base_url(settings: Settings) -> None:
    with pytest.raises(ValueError, match="VLM_BASE_URL"):
        VlmClient(dataclasses.replace(settings, vlm_provider="modal", vlm_base_url=""))


async def test_stub_provider_is_always_warm_and_reachable(settings: Settings) -> None:
    client = VlmClient(settings)
    health = client.health()
    assert (health.provider, health.warm, health.endpoint_reachable) == ("stub", True, True)
    assert await client.warmup() is True
