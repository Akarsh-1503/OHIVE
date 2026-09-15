"""Batch creation, retrieval, retry and the two export formats."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

from app.export import build_csv, build_workbook, export_filename
from app.models import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_EXTENSIONS,
    Batch,
    Lead,
    RetryRequest,
    Settings,
    utc_now_iso,
)
from app.pipeline import Pipeline
from app.routes.deps import (
    API_PREFIX,
    ApiError,
    _require_batch,
    get_pipeline,
    get_settings,
    get_store,
)
from app.store import Store

CHUNK = 1024 * 1024

router = APIRouter()


# --------------------------------------------------------------------------- uploads


def _validate_upload(file: UploadFile) -> None:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    suffix = Path(file.filename or "").suffix.lower()
    # Browsers mislabel HEIC as application/octet-stream, so the extension is a valid
    # second opinion — but one of the two must match.
    if content_type not in ALLOWED_CONTENT_TYPES and suffix not in ALLOWED_EXTENSIONS:
        raise ApiError(
            HTTP_422_UNPROCESSABLE_CONTENT,
            "UNSUPPORTED_MEDIA_TYPE",
            f"{file.filename or 'file'}: {content_type or 'unknown type'} is not accepted. "
            "Upload JPEG, PNG, WebP, HEIC/HEIF or PDF.",
        )


async def _persist_upload(
    store: Store, batch_id: str, card_id: str, file: UploadFile, config: Settings
) -> tuple[Path, str]:
    suffix = Path(file.filename or "").suffix.lower() or ".bin"
    directory = store.images_dir / batch_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{card_id}{suffix}"
    written = 0
    with path.open("wb") as handle:
        while chunk := await file.read(CHUNK):
            written += len(chunk)
            if written > config.max_file_bytes:
                handle.close()
                path.unlink(missing_ok=True)
                raise ApiError(
                    HTTP_422_UNPROCESSABLE_CONTENT,
                    "FILE_TOO_LARGE",
                    f"{file.filename or 'file'} exceeds the {config.max_file_mb} MB limit.",
                )
            handle.write(chunk)
    if written == 0:
        path.unlink(missing_ok=True)
        raise ApiError(
            HTTP_422_UNPROCESSABLE_CONTENT, "EMPTY_FILE", f"{file.filename or 'file'} is empty."
        )
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        content_type = {
            ".pdf": "application/pdf", ".png": "image/png", ".webp": "image/webp",
            ".heic": "image/heic", ".heif": "image/heif",
        }.get(suffix, "image/jpeg")
    return path, content_type


# --------------------------------------------------------------------------- routes


@router.post(f"{API_PREFIX}/batches", response_model=Batch, status_code=201, tags=["batches"])
async def create_batch(
    files: list[UploadFile] = File(..., description="Business card images or PDFs"),
    store: Store = Depends(get_store),
    pipeline: Pipeline = Depends(get_pipeline),
    config: Settings = Depends(get_settings),
) -> Batch:
    if not files:
        raise ApiError(HTTP_422_UNPROCESSABLE_CONTENT, "NO_FILES", "Upload at least one file.")
    if len(files) > config.max_files_per_batch:
        raise ApiError(
            HTTP_422_UNPROCESSABLE_CONTENT,
            "TOO_MANY_FILES",
            f"{len(files)} files submitted; the limit is {config.max_files_per_batch} "
            "per batch.",
        )
    for file in files:
        _validate_upload(file)

    batch_id = uuid.uuid4().hex
    batch = Batch(batch_id=batch_id, status="queued", total=len(files), pending=len(files))
    store.create_batch(batch)

    card_ids: list[str] = []
    try:
        for position, file in enumerate(files):
            card_id = uuid.uuid4().hex
            path, content_type = await _persist_upload(store, batch_id, card_id, file, config)
            lead = Lead(
                card_id=card_id,
                batch_id=batch_id,
                filename=file.filename or f"card_{position + 1}",
                status="queued",
                created_at=utc_now_iso(),
            )
            store.insert_card(lead, position, path, content_type)
            card_ids.append(card_id)
    except ApiError:
        store.delete_batch(batch_id)
        raise

    pipeline.start_batch(batch_id, card_ids)
    return _require_batch(store, batch_id)


@router.get(f"{API_PREFIX}/batches/{{batch_id}}", response_model=Batch, tags=["batches"])
async def get_batch(batch_id: str, store: Store = Depends(get_store)) -> Batch:
    return _require_batch(store, batch_id)


@router.post(f"{API_PREFIX}/batches/{{batch_id}}/retry", response_model=Batch, tags=["batches"])
async def retry_batch(
    batch_id: str,
    body: RetryRequest | None = None,
    store: Store = Depends(get_store),
    pipeline: Pipeline = Depends(get_pipeline),
) -> Batch:
    batch = _require_batch(store, batch_id)
    known = {lead.card_id for lead in batch.leads}
    requested = (body.card_ids if body else None) or store.failed_card_ids(batch_id)
    unknown = [card_id for card_id in requested if card_id not in known]
    if unknown:
        raise ApiError(
            HTTP_422_UNPROCESSABLE_CONTENT,
            "CARD_NOT_IN_BATCH",
            f"Cards not in this batch: {', '.join(unknown)}.",
        )
    if not requested:
        return batch

    for card_id in requested:
        lead = store.get_lead(card_id)
        if lead is not None:
            store.update_lead(
                lead.model_copy(update={"status": "queued", "error": None, "duplicate_of": None})
            )
    store.reopen_batch(batch_id)
    pipeline.start_batch(batch_id, list(requested))
    return _require_batch(store, batch_id)


@router.get(f"{API_PREFIX}/batches/{{batch_id}}/export.xlsx", tags=["export"])
async def export_xlsx(
    batch_id: str,
    include_low_confidence: bool = Query(True),
    include_duplicates: bool = Query(True),
    store: Store = Depends(get_store),
    config: Settings = Depends(get_settings),
) -> Response:
    batch = _require_batch(store, batch_id)
    payload = await asyncio.to_thread(
        build_workbook,
        batch,
        store.list_raw(batch_id),
        config.vlm_provider,
        config.vlm_model,
        include_low_confidence,
        include_duplicates,
    )
    return _download(
        payload,
        export_filename(batch_id, "xlsx"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get(f"{API_PREFIX}/batches/{{batch_id}}/export.csv", tags=["export"])
async def export_csv(
    batch_id: str,
    include_low_confidence: bool = Query(True),
    include_duplicates: bool = Query(True),
    store: Store = Depends(get_store),
) -> Response:
    batch = _require_batch(store, batch_id)
    payload = build_csv(batch, include_low_confidence, include_duplicates)
    return _download(payload, export_filename(batch_id, "csv"), "text/csv; charset=utf-8")


def _download(payload: bytes, filename: str, media_type: str) -> Response:
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
