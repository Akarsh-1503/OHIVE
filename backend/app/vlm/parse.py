"""Tolerant parsing of a model response into the card payload shape.

Output is constrained with ``response_format={"type": "json_schema", ...}`` so vLLM's guided
decoding guarantees a parseable object. The tolerant parser here is belt-and-braces for
servers that ignore the parameter and wrap the JSON in prose or code fences.
"""

from __future__ import annotations

import json
from typing import Any

from app.models import LEAD_FIELDS


class VlmError(RuntimeError):
    """A card could not be extracted. `terminal` means retrying will not help."""

    def __init__(self, message: str, *, terminal: bool = True) -> None:
        super().__init__(message)
        self.terminal = terminal


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    newline = body.find("\n")
    if newline != -1 and body[:newline].strip().lower() in ("json", "json5", ""):
        body = body[newline + 1 :]
    end = body.rfind("```")
    return (body[:end] if end != -1 else body).strip()


def _outermost_object(text: str) -> str | None:
    """Find the outermost balanced `{...}`, ignoring braces inside JSON strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_card_json(text: str) -> dict[str, Any]:
    """Tolerant parse of a model response into the card payload shape."""
    candidate = _strip_fences(text)
    for attempt in (candidate, _outermost_object(candidate) or ""):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise VlmError("model did not return a JSON object", terminal=False)


def normalise_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, str | None], dict[str, float], str]:
    """Accept either the nested `{value, confidence}` shape or a flat + `confidence` map."""
    raw_text = payload.get("raw_text") or payload.get("rawText") or ""
    if not isinstance(raw_text, str):
        raw_text = str(raw_text)

    container = payload.get("fields")
    flat_confidence = payload.get("confidence")
    if not isinstance(container, dict):
        container = payload
    if not isinstance(flat_confidence, dict):
        flat_confidence = {}

    values: dict[str, str | None] = {}
    confidences: dict[str, float] = {}
    for name in LEAD_FIELDS:
        entry = container.get(name)
        value: Any = None
        score: Any = flat_confidence.get(name)
        if isinstance(entry, dict):
            value = entry.get("value")
            if entry.get("confidence") is not None:
                score = entry.get("confidence")
        else:
            value = entry
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            value = None
        text = value.strip() if value else ""
        values[name] = text or None
        try:
            numeric = float(score)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            numeric = 0.75 if text else 0.0
        confidences[name] = min(1.0, max(0.0, numeric)) if text else 0.0
    return values, confidences, raw_text.strip()
