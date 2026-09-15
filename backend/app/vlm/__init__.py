"""Vision-language model access: prompt, JSON-schema-constrained call, retries, providers."""

from __future__ import annotations

from app.vlm.client import WARM_TTL_S, VlmClient, VlmResult, stub_card
from app.vlm.parse import VlmError, normalise_payload, parse_card_json
from app.vlm.prompt import CARD_SCHEMA, REPAIR_PROMPT, SYSTEM_PROMPT, USER_PROMPT

__all__ = [
    "CARD_SCHEMA",
    "REPAIR_PROMPT",
    "SYSTEM_PROMPT",
    "USER_PROMPT",
    "WARM_TTL_S",
    "VlmClient",
    "VlmError",
    "VlmResult",
    "normalise_payload",
    "parse_card_json",
    "stub_card",
]
