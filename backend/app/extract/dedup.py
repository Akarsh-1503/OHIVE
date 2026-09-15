"""Duplicate detection across the cards already seen in a batch."""

from __future__ import annotations

from rapidfuzz import fuzz

from app.extract.normalise import _ascii_fold
from app.models import Lead


def _identity_key(lead: Lead) -> str:
    return " ".join(
        part for part in (lead.first_name, lead.last_name, lead.company) if part
    ).strip()


def find_duplicate(lead: Lead, earlier: list[Lead], fuzzy_threshold: float) -> str | None:
    """Match against cards already seen in this batch. Returns the *first-seen* card_id.

    Duplicates are flagged, never dropped — two photos of the same card are a scanning
    artefact, but two cards for the same person are a real signal the reviewer wants.
    """
    email = (lead.email or "").strip().lower()
    for candidate in earlier:
        if email and (candidate.email or "").strip().lower() == email:
            return _root_of(candidate, earlier)
    if lead.phone_e164:
        for candidate in earlier:
            if candidate.phone_e164 == lead.phone_e164:
                return _root_of(candidate, earlier)

    key = _identity_key(lead)
    if len(key) < 6 or not lead.company:
        return None
    best_id: str | None = None
    best_score = fuzzy_threshold
    for candidate in earlier:
        candidate_key = _identity_key(candidate)
        if len(candidate_key) < 6 or not candidate.company:
            continue
        score = fuzz.token_set_ratio(_ascii_fold(key).lower(), _ascii_fold(candidate_key).lower())
        if score >= best_score:
            best_score = score
            best_id = _root_of(candidate, earlier)
    return best_id


def _root_of(candidate: Lead, earlier: list[Lead]) -> str:
    """Follow a duplicate chain back to the original card so groups share one anchor."""
    seen: set[str] = set()
    current = candidate
    by_id = {lead.card_id: lead for lead in earlier}
    while current.duplicate_of and current.duplicate_of not in seen:
        seen.add(current.duplicate_of)
        parent = by_id.get(current.duplicate_of)
        if parent is None:
            return current.duplicate_of
        current = parent
    return current.card_id
