"""The system/user prompts and the JSON schema that constrains the model's output."""

from __future__ import annotations

from typing import Any

from app.models import LEAD_FIELDS

SYSTEM_PROMPT = (
    "You are a meticulous data-entry specialist digitising business cards. "
    "You read the card exactly as printed and never invent information. "
    "If a field is not visible on the card, its value is null. "
    "You always answer with a single JSON object and nothing else."
)

USER_PROMPT = """Read this business card and extract the lead's details.

Rules:
1. `raw_text` must be a faithful line-by-line transcription of every piece of text on the
   card, top to bottom, preserving the original spelling. Do not summarise it.
2. Extract these fields into `fields`, each as {"value": <string or null>, "confidence": <0..1>}:
   - first_name  : given name only, without titles such as Dr. or Prof.
   - last_name   : family name including particles (van, de, bin) and generational suffixes
                   (Jr., III), but not qualifications such as PhD or MBA
   - job_title   : the role exactly as printed, e.g. "VP of Partnerships"
   - company     : the organisation name, without the legal suffix if it is a logo flourish
   - location    : the city, region and country as printed, e.g. "Bengaluru, KA, India".
                   Omit the street address, building name, suite and postcode.
   - phone       : the primary phone number exactly as printed, including any country code.
                   Prefer a mobile or direct line over a switchboard or fax number.
   - email       : the email address exactly as printed
   - website     : the web address exactly as printed
3. `confidence` is your own honest reading certainty for that field: 0.95+ when the text is
   crisp and unambiguous, 0.5-0.8 when you had to guess a character, below 0.4 when the text
   is barely legible. A null value must have confidence 0.
4. Never copy a value from another field. Never translate or reformat what is printed.

Return only the JSON object."""

REPAIR_PROMPT = (
    "Your previous output was not valid JSON matching the required schema. "
    "Return only the JSON object — no prose, no markdown code fences, no trailing commas. "
    "Every field must be present with both `value` and `confidence`."
)


def _build_schema() -> dict[str, Any]:
    """Inline the per-field object eight times rather than using `$ref`.

    Guided-decoding backends (xgrammar, outlines) have uneven `$ref` support; an inlined
    schema is a few more bytes on the wire and works everywhere.
    """
    field_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["value", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "raw_text": {"type": "string"},
            "fields": {
                "type": "object",
                "properties": {name: dict(field_schema) for name in LEAD_FIELDS},
                "required": list(LEAD_FIELDS),
                "additionalProperties": False,
            },
        },
        "required": ["raw_text", "fields"],
        "additionalProperties": False,
    }


CARD_SCHEMA = _build_schema()
