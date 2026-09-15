"""The provider abstraction: transport, retries, cold-start handling and health.

Three providers share one code path:

* ``modal``             — an OpenAI-compatible vLLM server on Modal, optionally behind proxy
                          auth tokens sent as ``Modal-Key`` / ``Modal-Secret`` headers.
* ``openai_compatible`` — any other ``/v1`` base URL (local vLLM, llama.cpp, OpenAI itself).
* ``stub``              — a deterministic fake driven by the filename. No GPU, no network,
                          which is what makes the whole test suite runnable offline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import random
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from app.models import LEAD_FIELDS, Settings, VlmHealth
from app.vlm.parse import VlmError, normalise_payload, parse_card_json
from app.vlm.prompt import CARD_SCHEMA, REPAIR_PROMPT, SYSTEM_PROMPT, USER_PROMPT

log = logging.getLogger("leadforge.vlm")

# Modal scales the GPU container to zero after a fixed idle window. Treat the endpoint as cold
# again once this much time has passed without a successful call, so /health stays honest and
# the next request gets the long cold-start timeout rather than a spurious read timeout.
# This MUST track `scaledown_window` in infra/modal/qwen_vlm.py: if it is shorter, /health
# reports "cold" while the GPU is actually still up; if longer, a reviewer is told the next
# call is warm when it will in fact pay a ~210 s cold start.
WARM_TTL_S = 900.0


@dataclass
class VlmResult:
    values: dict[str, str | None]
    confidences: dict[str, float]
    raw_text: str
    latency_ms: int
    attempts: int
    repaired: bool
    model: str
    payload: dict[str, Any] = field(default_factory=dict)


class VlmClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = settings.vlm_provider
        self.model = settings.vlm_model
        self.semaphore = asyncio.Semaphore(settings.vlm_concurrency)
        self._client: AsyncOpenAI | None = None
        self._last_ok_ts: float = 0.0
        self._last_latency_ms: int | None = None
        # Optimistic until a call actually fails. /health must never probe the endpoint:
        # any request wakes the scaled-to-zero GPU, so a polling frontend would pin a
        # container warm and burn credit. Reachability is therefore observed, not tested.
        self._reachable: bool = True
        self._warming = False
        self._warm_lock = asyncio.Lock()
        self._stub_latency_s = max(0.0, float(os.environ.get("STUB_LATENCY_MS", "20"))) / 1000.0

        if self.provider != "stub":
            if not settings.vlm_base_url:
                raise ValueError(f"VLM_BASE_URL is required for VLM_PROVIDER={self.provider}")
            headers: dict[str, str] = {}
            if self.provider == "modal" and settings.modal_proxy_key:
                headers["Modal-Key"] = settings.modal_proxy_key
                headers["Modal-Secret"] = settings.modal_proxy_secret
            self._client = AsyncOpenAI(
                base_url=settings.vlm_base_url,
                api_key=settings.vlm_api_key or "not-needed",
                max_retries=0,  # retries and backoff are owned by _complete below
                default_headers=headers or None,
                timeout=settings.vlm_cold_timeout_s,
            )

    # ---- health ------------------------------------------------------------

    @property
    def warm(self) -> bool:
        if self.provider == "stub":
            return True
        return (time.monotonic() - self._last_ok_ts) < WARM_TTL_S

    def health(self) -> VlmHealth:
        return VlmHealth(
            provider=self.provider,
            model=self.model,
            endpoint_reachable=self._reachable,
            warm=self.warm,
            last_latency_ms=self._last_latency_ms,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def warmup(self) -> bool:
        """Wake a scaled-to-zero endpoint. Safe to call concurrently; only one call gets through."""
        if self.provider == "stub":
            self._reachable = True
            return True
        if self._warm_lock.locked():
            return self.warm
        async with self._warm_lock:
            self._warming = True
            started = time.monotonic()
            try:
                assert self._client is not None
                await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "ok"}],
                    max_tokens=1,
                    temperature=0.0,
                    timeout=self.settings.vlm_cold_timeout_s,
                )
            except Exception as exc:
                self._reachable = False
                log.warning("vlm warmup failed: %s", exc)
                return False
            finally:
                self._warming = False
            self._mark_ok(int((time.monotonic() - started) * 1000))
            return True

    def _mark_ok(self, latency_ms: int) -> None:
        self._last_ok_ts = time.monotonic()
        self._last_latency_ms = latency_ms
        self._reachable = True

    # ---- extraction --------------------------------------------------------

    async def extract_card(self, jpeg_bytes: bytes, filename: str) -> VlmResult:
        """Extract one card.

        This does *not* take `self.semaphore`. The pipeline holds it across preprocessing
        and inference together, so that `card.started` can be emitted at the moment a slot
        is actually won rather than when the task was created — otherwise every card in a
        25-card batch reports the same `started_at` and per-card timing is meaningless.
        """
        if self.provider == "stub":
            return await self._stub_extract(filename)
        return await self._remote_extract(jpeg_bytes)

    async def _remote_extract(self, jpeg_bytes: bytes) -> VlmResult:
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": USER_PROMPT},
                ],
            },
        ]

        started = time.monotonic()
        text, attempts = await self._complete(messages)
        repaired = False
        try:
            payload = parse_card_json(text)
        except VlmError:
            # One repair turn: hand the model its own bad output back. Cheaper and far more
            # reliable than a blind resample when the server has no guided decoding.
            repaired = True
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": REPAIR_PROMPT})
            text, extra = await self._complete(messages)
            attempts += extra
            payload = parse_card_json(text)

        latency_ms = int((time.monotonic() - started) * 1000)
        self._mark_ok(latency_ms)
        values, confidences, raw_text = normalise_payload(payload)
        return VlmResult(
            values=values,
            confidences=confidences,
            raw_text=raw_text,
            latency_ms=latency_ms,
            attempts=attempts,
            repaired=repaired,
            model=self.model,
            payload=payload,
        )

    async def _complete(self, messages: list[dict[str, Any]]) -> tuple[str, int]:
        """One chat completion with exponential backoff on 5xx / 429 / timeouts."""
        assert self._client is not None
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(self.settings.vlm_max_retries + 1):
            attempts += 1
            # A cold container can take 2-4 minutes to answer the first request.
            timeout = (
                self.settings.vlm_timeout_s if self.warm else self.settings.vlm_cold_timeout_s
            )
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self.settings.vlm_temperature,
                    max_tokens=self.settings.vlm_max_tokens,
                    timeout=timeout,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "business_card",
                            "schema": CARD_SCHEMA,
                            "strict": True,
                        },
                    },
                )
            except (APITimeoutError, APIConnectionError) as exc:
                self._reachable = False
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise VlmError(
                        f"vlm rejected the request ({exc.status_code}): {exc.message}",
                        terminal=True,
                    ) from exc
                last_error = exc
            else:
                choice = response.choices[0] if response.choices else None
                content = (choice.message.content if choice and choice.message else None) or ""
                if not content.strip():
                    last_error = VlmError("vlm returned an empty completion", terminal=False)
                else:
                    return content, attempts

            if attempt < self.settings.vlm_max_retries:
                # Full jitter: spreads a batch's worth of retries instead of resonating.
                base = self.settings.vlm_retry_base_s * (2**attempt)
                delay = min(20.0, base) * (0.5 + random.random() / 2)
                log.warning(
                    "vlm attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt + 1,
                    self.settings.vlm_max_retries + 1,
                    type(last_error).__name__,
                    delay,
                )
                await asyncio.sleep(delay)

        raise VlmError(
            f"vlm unavailable after {attempts} attempts: {last_error}", terminal=True
        ) from last_error

    # ---- stub --------------------------------------------------------------

    async def _stub_extract(self, filename: str) -> VlmResult:
        started = time.monotonic()
        if self._stub_latency_s:
            await asyncio.sleep(self._stub_latency_s)
        values, confidences, raw_text = stub_card(filename)
        latency_ms = int((time.monotonic() - started) * 1000)
        self._mark_ok(latency_ms)
        return VlmResult(
            values=values,
            confidences=confidences,
            raw_text=raw_text,
            latency_ms=latency_ms,
            attempts=1,
            repaired=False,
            model=self.model,
            payload={
                "raw_text": raw_text,
                "fields": {
                    name: {"value": values[name], "confidence": confidences[name]}
                    for name in LEAD_FIELDS
                },
            },
        )


# --------------------------------------------------------------------------- stub data

_FIRSTS = (
    "Priya", "Marcus", "Yuki", "Elena", "Tobias", "Aisha", "Daniel", "Sofia",
    "Rahul", "Nora", "Mateo", "Ingrid",
)
_LASTS = (
    "Raghavan", "Okonkwo", "Tanaka", "Vasquez", "van Dijk", "Haddad", "O'Neill", "Lindqvist",
    "Mehta", "Bergström", "de Souza", "Kowalski",
)
_COMPANIES = (
    ("Northwind Robotics", "northwind.io"),
    ("Cedar Grid Energy", "cedargrid.com"),
    ("Halcyon Analytics", "halcyon-analytics.com"),
    ("Beacon Freight Systems", "beaconfreight.co"),
    ("Lumen Health Labs", "lumenhealth.ai"),
    ("Ironvale Manufacturing", "ironvale.co.uk"),
    ("Tidewater Capital", "tidewatercap.com"),
    ("Sable & Finch Design", "sablefinch.studio"),
    ("Quantum Fields Research", "quantumfields.org"),
    ("Meridian Logistics", "meridianlogistics.sg"),
)
_TITLES = (
    "VP of Partnerships", "Head of Engineering", "Regional Sales Director",
    "Chief Operating Officer", "Senior Account Executive", "Product Manager",
    "Director of Procurement", "Founder & CEO",
)
# (city line, phone country code, national number, phone region)
_LOCATIONS = (
    ("Bengaluru, KA, India", "+91", "80 4718 2200"),
    ("Manchester, England, United Kingdom", "+44", "161 496 0118"),
    ("Austin, TX, United States", "+1", "512 555 0147"),
    ("Rotterdam, Zuid-Holland, Netherlands", "+31", "10 205 4488"),
    ("Yokohama, Kanagawa, Japan", "+81", "45 227 9310"),
    ("São Paulo, SP, Brazil", "+55", "11 3045 7720"),
    ("Stockholm, Stockholms län, Sweden", "+46", "8 559 21 400"),
    ("Singapore, Singapore", "+65", "6812 4470"),
)


def _slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in folded if ch.isascii() and ch.isalnum())


def _vary_number(national: str, digest: bytes) -> str:
    """Give each persona its own subscriber number while keeping the area code intact.

    Without this every card sharing a city would also share a phone number, and the
    duplicate detector would correctly — but uselessly — pair up unrelated leads.
    """
    replacement = f"{int.from_bytes(digest[:2], 'big') % 10000:04d}"
    characters = list(national)
    taken = 0
    for index in range(len(characters) - 1, -1, -1):
        if characters[index].isdigit():
            characters[index] = replacement[3 - taken]
            taken += 1
            if taken == 4:
                break
    return "".join(characters)


def stub_card(filename: str) -> tuple[dict[str, str | None], dict[str, float], str]:
    """Deterministic fake extraction driven by the filename.

    Filename keywords steer the outcome so the offline demo and the tests can exercise every
    branch: `fail`, `dup`, `blur`/`lowconf`, `noemail`, `nophone`.
    """
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    if "fail" in stem:
        raise VlmError("stub provider: simulated unreadable card", terminal=True)

    seed = "duplicate-persona" if "dup" in stem else stem
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    first = _FIRSTS[digest[0] % len(_FIRSTS)]
    last = _LASTS[digest[1] % len(_LASTS)]
    company, domain = _COMPANIES[digest[2] % len(_COMPANIES)]
    title = _TITLES[digest[3] % len(_TITLES)]
    city, dial, national = _LOCATIONS[digest[4] % len(_LOCATIONS)]

    email: str | None = f"{_slug(first)}.{_slug(last)}@{domain}"
    phone: str | None = f"{dial} {_vary_number(national, digest)}"
    if "noemail" in stem:
        email = None
    if "nophone" in stem:
        phone = None

    values: dict[str, str | None] = {
        "first_name": first,
        "last_name": last,
        "job_title": title,
        "company": company,
        "location": city,
        "phone": phone,
        "email": email,
        "website": f"www.{domain}",
    }

    low = "blur" in stem or "lowconf" in stem
    confidences: dict[str, float] = {}
    for index, name in enumerate(LEAD_FIELDS):
        if values[name] is None:
            confidences[name] = 0.0
            continue
        jitter = (digest[5 + index] % 9) / 100.0
        confidences[name] = round((0.34 if low else 0.90) + jitter, 3)

    lines = [
        f"{first} {last}",
        title,
        company,
        city,
        f"T {phone}" if phone else "",
        email or "",
        f"www.{domain}",
    ]
    raw_text = "\n".join(line for line in lines if line)
    return values, confidences, raw_text
