"""Pydantic models mirroring `_contracts/assignment1-api.md` exactly, plus runtime settings.

Timestamps are plain strings rather than `datetime` so the wire format is unambiguously the
contract's `2026-09-14T09:12:03Z` and never pydantic's `+00:00` offset form.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LeadStatus = Literal["queued", "processing", "completed", "needs_review", "failed"]
BatchStatus = Literal["queued", "processing", "completed", "partial", "failed"]
VlmProvider = Literal["modal", "openai_compatible", "stub"]

# The eight extracted fields, in contract order. Used by the export, the confidence model and
# the PATCH handler, so it lives in one place.
LEAD_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "job_title",
    "company",
    "location",
    "phone",
    "email",
    "website",
)

# Weighted mean for `overall_confidence`: what a salesperson needs to act on a lead.
# Name / company / email / phone carry the batch; title, location and website are garnish.
FIELD_WEIGHTS: dict[str, float] = {
    "first_name": 0.15,
    "last_name": 0.15,
    "job_title": 0.05,
    "company": 0.20,
    "location": 0.025,
    "phone": 0.15,
    "email": 0.25,
    "website": 0.025,
}

APP_VERSION = "1.0.0"

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "image/heic-sequence",
        "image/heif-sequence",
        "application/pdf",
    }
)

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"}
)


def utc_now_iso() -> str:
    """ISO-8601 UTC with a `Z` suffix and second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_iso_ms() -> str:
    """ISO-8601 UTC with millisecond precision, for `card.started`.

    A client that connects mid-batch cannot derive when a card began from the arrival time
    of the event, so the server states it. Seconds are too coarse for per-card timing.
    """
    stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")


class Confidence(BaseModel):
    """Per-field confidence, 0..1. Always present; a null field scores 0.0."""

    model_config = ConfigDict(extra="forbid")

    first_name: float = 0.0
    last_name: float = 0.0
    job_title: float = 0.0
    company: float = 0.0
    location: float = 0.0
    phone: float = 0.0
    email: float = 0.0
    website: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {f: float(getattr(self, f)) for f in LEAD_FIELDS}


class Lead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    batch_id: str
    filename: str
    status: LeadStatus = "queued"

    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    company: str | None = None
    location: str | None = None
    phone: str | None = None
    phone_e164: str | None = None
    email: str | None = None
    website: str | None = None

    confidence: Confidence = Field(default_factory=Confidence)
    overall_confidence: float = 0.0
    quality_flags: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    # Null until the model has actually read the card: a queued or failed lead has no
    # transcription and no measured work, and reporting 0 there would read as "instant".
    raw_text: str | None = None
    edited: bool = False
    processing_ms: int | None = None
    error: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: BatchStatus = "queued"
    total: int = 0
    #: Cards the model finished reading — `completed` *and* `needs_review`, i.e. "extracted".
    #: The per-status split comes from `leads[]`; see the contract.
    completed: int = 0
    failed: int = 0
    pending: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    finished_at: str | None = None
    elapsed_ms: int = 0
    leads: list[Lead] = Field(default_factory=list)


class LeadPatch(BaseModel):
    """Partial Lead field map accepted by `PATCH /leads/{card_id}`."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    company: str | None = None
    location: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    status: LeadStatus | None = None
    duplicate_of: str | None = None


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_ids: list[str] | None = None


class VlmHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: VlmProvider
    model: str
    endpoint_reachable: bool
    warm: bool
    last_latency_ms: int | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"] = "ok"
    version: str = APP_VERSION
    vlm: VlmHealth
    uptime_s: int


class WarmupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warming: bool = True


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
    code: str


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env_str(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env_str(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Every field maps to an env var in `.env.example`."""

    vlm_provider: VlmProvider = "stub"
    vlm_base_url: str = ""
    vlm_model: str = "Qwen/Qwen2.5-VL-3B-Instruct"
    vlm_api_key: str = ""
    modal_proxy_key: str = ""
    modal_proxy_secret: str = ""
    vlm_concurrency: int = 6
    # The GPU scales to zero and a cold boot has been measured at 205-301 s, so anything
    # under 300 s fails the first request after an idle period. 420 leaves real margin, and
    # it is the same number in the compose files — a compose default silently overrides
    # this one, so they must not disagree.
    vlm_timeout_s: float = 420.0
    vlm_cold_timeout_s: float = 420.0
    vlm_max_retries: int = 3
    vlm_retry_base_s: float = 1.5
    # Explicit 0.0: the model's own generation_config.json otherwise applies
    # repetition_penalty=1.05, which vLLM honours and which corrupts transcription.
    vlm_temperature: float = 0.0
    # max_model_len is 4096 and a 1280 px image is ~1280 prompt tokens.
    vlm_max_tokens: int = 1024

    data_dir: str = "/data"
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    log_level: str = "INFO"

    confidence_threshold: float = 0.65
    duplicate_fuzzy_threshold: float = 90.0
    max_files_per_batch: int = 25
    max_file_mb: int = 12
    default_phone_region: str = "US"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        provider = _env_str("VLM_PROVIDER", "stub").lower()
        if provider not in ("modal", "openai_compatible", "stub"):
            provider = "stub"
        origins = [o.strip() for o in _env_str("CORS_ORIGINS", "*").split(",") if o.strip()]
        return cls(
            vlm_provider=provider,  # type: ignore[arg-type]
            vlm_base_url=_env_str("VLM_BASE_URL", "").rstrip("/"),
            vlm_model=_env_str("VLM_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct"),
            vlm_api_key=_env_str("VLM_API_KEY", ""),
            modal_proxy_key=_env_str("MODAL_PROXY_KEY", ""),
            modal_proxy_secret=_env_str("MODAL_PROXY_SECRET", ""),
            vlm_concurrency=max(1, _env_int("VLM_CONCURRENCY", 6)),
            vlm_timeout_s=_env_float("VLM_TIMEOUT_S", 420.0),
            vlm_cold_timeout_s=_env_float("VLM_COLD_TIMEOUT_S", 420.0),
            vlm_max_retries=max(0, _env_int("VLM_MAX_RETRIES", 3)),
            vlm_retry_base_s=_env_float("VLM_RETRY_BASE_S", 1.5),
            vlm_temperature=_env_float("VLM_TEMPERATURE", 0.0),
            vlm_max_tokens=_env_int("VLM_MAX_TOKENS", 1024),
            data_dir=_env_str("DATA_DIR", "/data"),
            cors_origins=origins or ["*"],
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", 0.65),
            duplicate_fuzzy_threshold=_env_float("DUPLICATE_FUZZY_THRESHOLD", 90.0),
            max_files_per_batch=_env_int("MAX_FILES_PER_BATCH", 25),
            max_file_mb=_env_int("MAX_FILE_MB", 12),
            default_phone_region=_env_str("DEFAULT_PHONE_REGION", "US").upper(),
        )
