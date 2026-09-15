"""Per-field scoring, cross-field validation and the overall confidence a reviewer sees."""

from __future__ import annotations

from app.extract.normalise import (
    _alnum,
    _collapse,
    normalise_email,
    normalise_location,
    normalise_phone,
    normalise_website,
    phone_region,
    region_from_location,
    reorder_name_from_email,
    repair_website_from_email,
    split_name,
    website_is_valid,
)
from app.models import FIELD_WEIGHTS, LEAD_FIELDS, Confidence, Lead
from app.vlm import VlmResult

# Multiplicative penalties applied to the weighted mean. Floored in aggregate at 0.55 so a
# pile of soft flags can never bury an otherwise crisp read.
QUALITY_PENALTIES: dict[str, float] = {
    "blurry": 0.90,
    "low_resolution": 0.94,
    "dark": 0.96,
    "low_contrast": 0.96,
    "no_name": 0.85,
    "no_company": 0.93,
    "no_email": 0.93,
    "no_phone": 0.95,
    "invalid_email": 0.92,
    "unparseable_phone": 0.97,
    "phone_location_mismatch": 0.97,
}
QUALITY_MULTIPLIER_FLOOR = 0.55


def _bump(scores: dict[str, float], name: str, delta: float) -> None:
    if scores.get(name, 0.0) > 0.0:
        scores[name] = round(min(0.99, scores[name] + delta), 4)


def _floor(scores: dict[str, float], name: str, ceiling: float) -> None:
    scores[name] = round(min(scores.get(name, 0.0), ceiling), 4)


def cross_validate(
    values: dict[str, str | None], scores: dict[str, float], flags: list[str]
) -> None:
    """Adjust per-field confidence using agreement *between* fields. Mutates in place."""
    email = values.get("email")
    website = values.get("website")
    company = values.get("company")
    first = values.get("first_name")
    last = values.get("last_name")

    if email and "@" in email:
        local, _, domain = email.rpartition("@")
        local_key = _alnum(local)
        first_key, last_key = _alnum(first or ""), _alnum(last or "")

        # The local part corroborating the name is the single strongest signal on a card:
        # two independent readings of the same string agreeing.
        name_match = False
        if len(first_key) >= 3 and first_key in local_key:
            name_match = True
        if len(last_key) >= 3 and last_key in local_key:
            name_match = True
        if first_key and len(last_key) >= 3 and (first_key[0] + last_key) == local_key:
            name_match = True
        if name_match:
            _bump(scores, "first_name", 0.05)
            _bump(scores, "last_name", 0.05)
            _bump(scores, "email", 0.05)
            flags.append("name_email_match")

        registrable = domain.split(".")[0]
        if website and domain == website:
            _bump(scores, "company", 0.06)
            _bump(scores, "website", 0.06)
            _bump(scores, "email", 0.04)
            flags.append("domain_website_match")
        elif company and len(registrable) >= 4 and registrable in _alnum(company):
            _bump(scores, "company", 0.05)
            _bump(scores, "email", 0.03)
            flags.append("domain_company_match")

    location_region = region_from_location(values.get("location"))
    number_region = phone_region(values.get("phone_e164"))
    if location_region and number_region and location_region != number_region:
        scores["phone"] = round(scores.get("phone", 0.0) * 0.8, 4)
        scores["location"] = round(scores.get("location", 0.0) * 0.8, 4)
        flags.append("phone_location_mismatch")

    # A model that copies one field into another is guessing, not reading.
    if company and values.get("job_title") and _alnum(company) == _alnum(values["job_title"]):
        _floor(scores, "job_title", 0.4)
        _floor(scores, "company", 0.6)
    if first and last and _alnum(first) == _alnum(last):
        _floor(scores, "first_name", 0.5)
        _floor(scores, "last_name", 0.5)


def quality_multiplier(flags: list[str]) -> float:
    multiplier = 1.0
    for flag in flags:
        multiplier *= QUALITY_PENALTIES.get(flag, 1.0)
    return max(QUALITY_MULTIPLIER_FLOOR, round(multiplier, 4))


def overall_confidence(scores: dict[str, float], flags: list[str]) -> float:
    """Weighted mean over the fields that were actually extracted, discounted by quality."""
    weighted = sum(FIELD_WEIGHTS[name] * scores.get(name, 0.0) for name in LEAD_FIELDS)
    total_weight = sum(FIELD_WEIGHTS[name] for name in LEAD_FIELDS if scores.get(name, 0.0) > 0.0)
    if total_weight == 0.0:
        return 0.0
    return round(min(1.0, (weighted / total_weight) * quality_multiplier(flags)), 4)


# --------------------------------------------------------------------------- assembly

# Flags that explain a confidence adjustment rather than describe a problem with the card.
# They are kept out of the reviewer-facing list: a corrected name order is a thing we got
# right, not a defect the reviewer needs to act on.
_INFORMATIONAL_FLAGS = frozenset(
    {
        "name_email_match",
        "domain_website_match",
        "domain_company_match",
        "name_order_corrected",
    }
)


def build_lead(
    lead: Lead,
    result: VlmResult,
    image_flags: list[str],
    confidence_threshold: float,
    default_region: str = "US",
) -> Lead:
    """Normalise a raw VLM reading into the contract's `Lead`, with a scored confidence block."""
    flags = list(image_flags)
    values: dict[str, str | None] = dict(result.values)
    scores: dict[str, float] = {name: result.confidences.get(name, 0.0) for name in LEAD_FIELDS}

    # The model sometimes puts the whole printed name in `first_name` and repeats the
    # surname in `last_name` ("Tan Wei Ming" / "Ming"). Joining blindly duplicates it.
    raw_first, raw_last = values.get("first_name") or "", values.get("last_name") or ""
    if raw_last and _alnum(raw_last) and _alnum(raw_first).endswith(_alnum(raw_last)):
        printed_name = raw_first
    else:
        printed_name = f"{raw_first} {raw_last}".strip()
    first, last = split_name(printed_name or None)
    values["first_name"], values["last_name"] = first, last

    values["job_title"] = _collapse(values.get("job_title"))
    values["company"] = _collapse(values.get("company"))
    values["location"] = normalise_location(values.get("location"))
    values["website"] = normalise_website(values.get("website"))

    # The card's own address is the best region hint; DEFAULT_PHONE_REGION only covers
    # numbers printed with no country code on a card with no recognisable address.
    region = region_from_location(values["location"]) or default_region
    printed_phone, e164 = normalise_phone(values.get("phone"), region)
    values["phone"] = printed_phone
    values["phone_e164"] = e164

    email_result = normalise_email(values.get("email"), values["website"], values["company"])
    values["email"] = email_result.value

    if email_result.valid:
        values["first_name"], values["last_name"], name_flipped = reorder_name_from_email(
            values["first_name"], values["last_name"], values["email"]
        )
        if name_flipped:
            flags.append("name_order_corrected")

        values["website"], website_repaired = repair_website_from_email(
            values["website"], values["email"]
        )
        if website_repaired:
            _floor(scores, "website", 0.80)

    # Validation failures floor the score rather than deleting the value.
    if values["email"] and not email_result.valid:
        flags.append("invalid_email")
        _floor(scores, "email", 0.30)
    elif email_result.repaired:
        _floor(scores, "email", 0.80)
    if printed_phone and not e164:
        flags.append("unparseable_phone")
        _floor(scores, "phone", 0.35)
    if values["website"] and not website_is_valid(values["website"]):
        _floor(scores, "website", 0.35)

    for name in LEAD_FIELDS:
        if not values.get(name):
            scores[name] = 0.0

    cross_validate(values, scores, flags)

    if not (values["first_name"] or values["last_name"]):
        flags.append("no_name")
    if not values["company"]:
        flags.append("no_company")
    if not values["email"]:
        flags.append("no_email")
    if not values["phone"]:
        flags.append("no_phone")

    overall = overall_confidence(scores, flags)
    status = "completed" if overall >= confidence_threshold else "needs_review"

    # Informational agreement flags are kept out of the reviewer-facing list: they explain a
    # confidence bump, they are not a problem with the card.
    visible_flags = [f for f in dict.fromkeys(flags) if f not in _INFORMATIONAL_FLAGS]

    return lead.model_copy(
        update={
            "status": status,
            "first_name": values["first_name"],
            "last_name": values["last_name"],
            "job_title": values["job_title"],
            "company": values["company"],
            "location": values["location"],
            "phone": values["phone"],
            "phone_e164": values["phone_e164"],
            "email": values["email"],
            "website": values["website"],
            "confidence": Confidence(**{k: round(v, 4) for k, v in scores.items()}),
            "overall_confidence": overall,
            "quality_flags": visible_flags,
            "raw_text": result.raw_text or None,
            "processing_ms": result.latency_ms,
            "error": None,
        }
    )


def recompute_after_edit(lead: Lead, edited_fields: set[str], threshold: float) -> Lead:
    """A human-edited field is ground truth: pin it to 1.0 and re-score the record."""
    scores = lead.confidence.as_dict()
    for name in edited_fields & set(LEAD_FIELDS):
        scores[name] = 1.0 if getattr(lead, name) else 0.0

    values = {name: getattr(lead, name) for name in LEAD_FIELDS}
    values["phone_e164"] = lead.phone_e164
    missing = ("no_name", "no_company", "no_email", "no_phone")
    flags = [f for f in lead.quality_flags if f not in missing]
    if not (values["first_name"] or values["last_name"]):
        flags.append("no_name")
    if not values["company"]:
        flags.append("no_company")
    if not values["email"]:
        flags.append("no_email")
    if not values["phone"]:
        flags.append("no_phone")

    overall = overall_confidence(scores, flags)
    status = lead.status
    if status in ("completed", "needs_review"):
        status = "completed" if overall >= threshold else "needs_review"
    return lead.model_copy(
        update={
            "confidence": Confidence(**{k: round(v, 4) for k, v in scores.items()}),
            "overall_confidence": overall,
            "quality_flags": list(dict.fromkeys(flags)),
            "status": status,
            "edited": True,
        }
    )
