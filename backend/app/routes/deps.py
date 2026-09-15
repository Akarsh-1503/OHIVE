"""Request-scoped dependencies shared by the routers, and the contract's error type.

Everything the routers need from the running application is reached through
`request.app.state`, which the lifespan populates. Keeping the accessors here rather than in
`main` is what lets the dependency arrow point one way: `main` -> `routes` -> `deps`.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.models import Batch, Lead, Settings
from app.pipeline import Pipeline
from app.store import Store
from app.vlm import VlmClient

API_PREFIX = "/api/v1"


class ApiError(HTTPException):
    """HTTPException that carries the contract's `code` alongside `detail`."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


def get_vlm(request: Request) -> VlmClient:
    return request.app.state.vlm


def _require_batch(store: Store, batch_id: str) -> Batch:
    batch = store.get_batch(batch_id)
    if batch is None:
        raise ApiError(404, "BATCH_NOT_FOUND", f"No batch with id {batch_id}.")
    return batch


def _require_lead(store: Store, card_id: str) -> Lead:
    lead = store.get_lead(card_id)
    if lead is None:
        raise ApiError(404, "CARD_NOT_FOUND", f"No card with id {card_id}.")
    return lead
