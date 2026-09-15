"""Human review: editing a lead and serving the original upload beside it."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

from app.extract import (
    make_thumbnail,
    make_web_jpeg,
    normalise_phone,
    normalise_website,
    recompute_after_edit,
    region_from_location,
)
from app.models import LEAD_FIELDS, Lead, LeadPatch, Settings
from app.routes.deps import (
    API_PREFIX,
    ApiError,
    _require_lead,
    get_settings,
    get_store,
)
from app.store import Store

BROWSER_RENDERABLE = frozenset(
    {"image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp"}
)

router = APIRouter()


@router.patch(f"{API_PREFIX}/leads/{{card_id}}", response_model=Lead, tags=["leads"])
async def patch_lead(
    card_id: str,
    patch: LeadPatch,
    store: Store = Depends(get_store),
    config: Settings = Depends(get_settings),
) -> Lead:
    lead = _require_lead(store, card_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(
            HTTP_422_UNPROCESSABLE_CONTENT,
            "EMPTY_PATCH",
            "Provide at least one field to update.",
        )

    update: dict[str, Any] = {}
    for key, value in changes.items():
        update[key] = value.strip() or None if isinstance(value, str) else value
    if update.get("website"):
        update["website"] = normalise_website(update["website"])
    if update.get("email"):
        update["email"] = update["email"].lower()
    if "phone" in update:
        location = update.get("location", lead.location)
        region = region_from_location(location) or config.default_phone_region
        printed, e164 = normalise_phone(update["phone"], region)
        update["phone"], update["phone_e164"] = printed, e164

    explicit_status = update.pop("status", None)
    updated = recompute_after_edit(
        lead.model_copy(update=update),
        {key for key in changes if key in LEAD_FIELDS},
        config.confidence_threshold,
    )
    if explicit_status is not None:
        updated = updated.model_copy(update={"status": explicit_status})
    store.update_lead(updated)
    return updated


@router.get(f"{API_PREFIX}/cards/{{card_id}}/image", tags=["cards"])
async def card_image(
    card_id: str,
    thumb: int = Query(0, ge=0, le=1),
    store: Store = Depends(get_store),
) -> Response:
    _require_lead(store, card_id)
    source = store.get_card_source(card_id)
    if source is None or not source[0].exists():
        raise ApiError(404, "IMAGE_NOT_FOUND", f"No stored image for card {card_id}.")
    path, content_type, batch_id = source

    if thumb:
        cached = store.thumb_path(batch_id, card_id)
        if not cached.exists():
            raw = await asyncio.to_thread(path.read_bytes)
            payload = await asyncio.to_thread(make_thumbnail, raw, content_type, path.name)
            await asyncio.to_thread(cached.write_bytes, payload)
        return _inline(await asyncio.to_thread(cached.read_bytes), "image/webp")

    if content_type in BROWSER_RENDERABLE:
        return _inline(await asyncio.to_thread(path.read_bytes), content_type)

    # HEIC and PDF cannot go straight into an <img>; hand back a rendered JPEG instead.
    cached = path.with_suffix(".render.jpg")
    if not cached.exists():
        raw = await asyncio.to_thread(path.read_bytes)
        payload = await asyncio.to_thread(make_web_jpeg, raw, content_type, path.name)
        await asyncio.to_thread(cached.write_bytes, payload)
    return _inline(await asyncio.to_thread(cached.read_bytes), "image/jpeg")


def _inline(payload: bytes, media_type: str) -> Response:
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
