"""Cross-field validation, the confidence model, quality flags and duplicate detection."""

from __future__ import annotations

from PIL import ImageFilter

from app.extract import (
    FOCUS_SCORE_FLOOR,
    assess_quality,
    build_lead,
    cross_validate,
    find_duplicate,
    focus_score,
    overall_confidence,
    prepare_image,
    quality_multiplier,
    recompute_after_edit,
)
from app.models import LEAD_FIELDS, Confidence, Lead
from app.vlm import VlmResult
from tests.conftest import card_bytes, card_image

GOOD_CARD = {
    "first_name": "Priya",
    "last_name": "Raghavan",
    "job_title": "VP of Partnerships",
    "company": "Northwind Robotics",
    "location": "Bengaluru, KA, India",
    "phone": "+91 80 4718 2200",
    "email": "priya.raghavan@northwind.io",
    "website": "www.northwind.io",
}


def make_result(values: dict[str, str | None], score: float = 0.9, **over: float) -> VlmResult:
    confidences = {name: (score if values.get(name) else 0.0) for name in LEAD_FIELDS}
    confidences.update(over)
    return VlmResult(
        values={name: values.get(name) for name in LEAD_FIELDS},
        confidences=confidences,
        raw_text="Priya Raghavan\nNorthwind Robotics",
        latency_ms=120,
        attempts=1,
        repaired=False,
        model="stub",
    )


def blank_lead(card_id: str = "c1", batch_id: str = "b1") -> Lead:
    return Lead(card_id=card_id, batch_id=batch_id, filename=f"{card_id}.jpg")


# --------------------------------------------------------------------------- cross-validation


def test_matching_email_local_part_raises_name_and_email_confidence() -> None:
    values = dict(GOOD_CARD)
    scores = dict.fromkeys(LEAD_FIELDS, 0.8)
    flags: list[str] = []
    cross_validate(values, scores, flags)

    assert "name_email_match" in flags
    assert scores["first_name"] > 0.80
    assert scores["last_name"] > 0.80
    assert scores["email"] > 0.80


def test_email_domain_matching_website_raises_company_confidence() -> None:
    values = dict(GOOD_CARD) | {"website": "northwind.io"}
    scores = dict.fromkeys(LEAD_FIELDS, 0.8)
    flags: list[str] = []
    cross_validate(values, scores, flags)

    assert "domain_website_match" in flags
    assert scores["company"] > 0.80
    assert scores["website"] > 0.80


def test_email_domain_matching_company_name_raises_company_confidence() -> None:
    values = dict(GOOD_CARD) | {"website": None}
    scores = dict.fromkeys(LEAD_FIELDS, 0.8)
    flags: list[str] = []
    cross_validate(values, scores, flags)

    assert "domain_company_match" in flags
    assert scores["company"] > 0.80


def test_phone_country_disagreeing_with_location_lowers_both() -> None:
    values = dict(GOOD_CARD) | {"phone_e164": "+15125550147"}  # US number, Indian address
    scores = dict.fromkeys(LEAD_FIELDS, 0.9)
    flags: list[str] = []
    cross_validate(values, scores, flags)

    assert "phone_location_mismatch" in flags
    assert scores["phone"] < 0.90
    assert scores["location"] < 0.90


def test_job_title_copied_from_company_is_penalised() -> None:
    values = dict(GOOD_CARD) | {"job_title": "Northwind Robotics"}
    scores = dict.fromkeys(LEAD_FIELDS, 0.95)
    cross_validate(values, scores, [])
    assert scores["job_title"] <= 0.4
    assert scores["company"] <= 0.6


# --------------------------------------------------------------------------- scoring


def test_overall_confidence_is_a_weighted_mean_of_populated_fields() -> None:
    scores = dict.fromkeys(LEAD_FIELDS, 0.9)
    assert overall_confidence(scores, []) == 0.9


def test_missing_fields_are_excluded_from_the_mean_but_penalised_by_flag() -> None:
    scores = dict.fromkeys(LEAD_FIELDS, 0.9)
    scores["email"] = 0.0
    without_flag = overall_confidence(scores, [])
    with_flag = overall_confidence(scores, ["no_email"])
    assert without_flag == 0.9  # weighted mean over the fields that are present
    assert with_flag < without_flag  # the missing email still costs the record


def test_quality_multiplier_compounds_and_is_floored() -> None:
    assert quality_multiplier([]) == 1.0
    assert quality_multiplier(["blurry"]) == 0.9
    assert quality_multiplier(["blurry", "low_resolution"]) < 0.9
    assert quality_multiplier(["blurry", "low_resolution", "dark", "low_contrast"] * 4) == 0.55


def test_build_lead_produces_a_complete_high_confidence_record() -> None:
    lead = build_lead(blank_lead(), make_result(GOOD_CARD), [], 0.65)

    assert lead.status == "completed"
    assert (lead.first_name, lead.last_name) == ("Priya", "Raghavan")
    assert lead.email == "priya.raghavan@northwind.io"
    assert lead.phone_e164 == "+918047182200"
    assert lead.website == "northwind.io"
    assert lead.overall_confidence > 0.9
    assert lead.quality_flags == []
    assert lead.raw_text


def test_build_lead_floors_confidence_for_an_invalid_email() -> None:
    lead = build_lead(blank_lead(), make_result(dict(GOOD_CARD) | {"email": "priya(at)nw"}), [], 0.65)
    assert "invalid_email" in lead.quality_flags
    assert lead.confidence.email <= 0.30


def test_build_lead_floors_confidence_for_an_unparseable_phone() -> None:
    """A number-shaped string that is not a real number is kept, flagged and discounted."""
    misread = dict(GOOD_CARD) | {"phone": "+1 555 000 0000"}
    lead = build_lead(blank_lead(), make_result(misread), [], 0.65)
    assert lead.phone == "+1 555 000 0000"
    assert lead.phone_e164 is None
    assert "unparseable_phone" in lead.quality_flags
    assert lead.confidence.phone <= 0.35


def test_build_lead_drops_text_that_is_not_a_phone_number_at_all() -> None:
    lead = build_lead(blank_lead(), make_result(dict(GOOD_CARD) | {"phone": "call me"}), [], 0.65)
    assert lead.phone is None
    assert "no_phone" in lead.quality_flags


def test_build_lead_flags_missing_core_fields() -> None:
    sparse = {"first_name": "Priya", "last_name": "Raghavan"}
    lead = build_lead(blank_lead(), make_result(sparse), [], 0.65)
    assert {"no_company", "no_email", "no_phone"} <= set(lead.quality_flags)
    assert lead.email is None and lead.phone is None


def test_low_confidence_card_lands_in_the_review_queue() -> None:
    lead = build_lead(blank_lead(), make_result(GOOD_CARD, score=0.45), ["blurry"], 0.65)
    assert lead.status == "needs_review"
    assert lead.overall_confidence < 0.65
    assert "blurry" in lead.quality_flags


def test_image_quality_flags_reach_the_lead() -> None:
    lead = build_lead(blank_lead(), make_result(GOOD_CARD), ["blurry", "low_resolution"], 0.65)
    assert {"blurry", "low_resolution"} <= set(lead.quality_flags)
    assert lead.overall_confidence < 0.9


def test_informational_agreement_flags_are_not_shown_to_the_reviewer() -> None:
    lead = build_lead(blank_lead(), make_result(GOOD_CARD), [], 0.65)
    assert "name_email_match" not in lead.quality_flags
    assert "domain_website_match" not in lead.quality_flags


def test_editing_a_field_pins_its_confidence_to_one() -> None:
    lead = build_lead(blank_lead(), make_result(GOOD_CARD, score=0.5), [], 0.65)
    assert lead.status == "needs_review"

    edited = recompute_after_edit(
        lead.model_copy(update={"first_name": "Priyanka", "company": "Northwind Robotics Pvt"}),
        {"first_name", "company"},
        0.65,
    )
    assert edited.edited is True
    assert edited.confidence.first_name == 1.0
    assert edited.confidence.company == 1.0
    assert edited.overall_confidence > lead.overall_confidence


# --------------------------------------------------------------------------- quality flags


def test_assess_quality_separates_blur_from_exposure() -> None:
    assert assess_quality(card_image()) == []
    assert "blurry" in assess_quality(card_image(blur=3.0))
    assert "low_resolution" in assess_quality(card_image(width=400, height=240))

    dark = assess_quality(card_image(darken=0.78))
    assert "dark" in dark
    # A dark photo must not be reported as blurry just because its edges are faint.
    assert "blurry" not in assess_quality(card_image(darken=0.5))


SPARSE = ("Priya Raghavan", "Northwind Robotics", "priya@northwind.io")


def test_a_minimalist_card_is_not_mistaken_for_a_blurry_one() -> None:
    """Regression: a card that is 99% blank paper still has sharp edges where it counts."""
    sparse = card_image(lines=SPARSE, border=False)
    assert "blurry" not in assess_quality(sparse)
    assert "blurry" in assess_quality(card_image(lines=SPARSE, border=False, blur=2.5))


def test_focus_score_falls_monotonically_with_blur() -> None:
    def score(blur: float) -> float:
        image = card_image(blur=blur).convert("L")
        low, high = image.filter(ImageFilter.MedianFilter(3)).getextrema()
        return focus_score(image, high - low)

    scores = [score(blur) for blur in (0.0, 1.0, 2.0, 3.0)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > FOCUS_SCORE_FLOOR > scores[-1]


def test_prepare_image_downscales_and_reencodes() -> None:
    prepared = prepare_image(card_bytes(width=2400, height=1500), "card.jpg", "image/jpeg")
    assert max(prepared.width, prepared.height) == 1280
    assert (prepared.source_width, prepared.source_height) == (2400, 1500)
    assert prepared.jpeg_bytes[:2] == b"\xff\xd8"  # JPEG SOI marker


# --------------------------------------------------------------------------- duplicates


def completed(card_id: str, **over: object) -> Lead:
    base = {
        "first_name": "Priya",
        "last_name": "Raghavan",
        "company": "Northwind Robotics",
        "email": "priya.raghavan@northwind.io",
        "phone_e164": "+918047182200",
        "status": "completed",
        "confidence": Confidence(),
    }
    return blank_lead(card_id).model_copy(update=base | over)


def test_duplicate_matched_on_email() -> None:
    first = completed("a")
    second = completed("b", phone_e164=None)
    assert find_duplicate(second, [first], 90.0) == "a"


def test_duplicate_matched_on_e164_when_emails_differ() -> None:
    first = completed("a")
    second = completed("b", email="p.raghavan@northwind.io")
    assert find_duplicate(second, [first], 90.0) == "a"


def test_duplicate_matched_fuzzily_on_name_and_company() -> None:
    first = completed("a")
    second = completed(
        "b", email=None, phone_e164=None, company="Northwind Robotics Pvt Ltd"
    )
    assert find_duplicate(second, [first], 90.0) == "a"


def test_different_people_at_the_same_company_are_not_duplicates() -> None:
    first = completed("a")
    second = completed(
        "b",
        first_name="Marcus",
        last_name="Okonkwo",
        email="marcus@northwind.io",
        phone_e164="+918047182201",
    )
    assert find_duplicate(second, [first], 90.0) is None


def test_duplicate_chain_anchors_on_the_first_seen_card() -> None:
    first = completed("a")
    second = completed("b").model_copy(update={"duplicate_of": "a"})
    third = completed("c")
    assert find_duplicate(third, [first, second], 90.0) == "a"


def test_nameless_records_do_not_fuzzy_match_each_other() -> None:
    first = completed("a", first_name=None, last_name=None, company=None, email=None, phone_e164=None)
    second = completed("b", first_name=None, last_name=None, company=None, email=None, phone_e164=None)
    assert find_duplicate(second, [first], 90.0) is None


def test_family_name_first_card_is_corrected_end_to_end() -> None:
    """The model returns the whole printed name in first_name and repeats the surname."""
    card = {
        "first_name": "Tan Wei Ming",
        "last_name": "Ming",
        "job_title": "Regional Partnerships Lead",
        "company": "Kallang Harbour Capital",
        "location": "Singapore",
        "phone": "+65 8555 0132",
        "email": "weiming.tan@kallangharbour.example",
        "website": "kallangharbour.example",
    }
    lead = build_lead(blank_lead(), make_result(card), [], 0.65)
    assert (lead.first_name, lead.last_name) == ("Wei Ming", "Tan")
    assert "name_order_corrected" not in lead.quality_flags  # informational, not a defect


def test_repeated_surname_is_not_duplicated_into_the_name() -> None:
    card = dict(GOOD_CARD) | {"first_name": "Priya Raghavan", "last_name": "Raghavan"}
    lead = build_lead(blank_lead(), make_result(card), [], 0.65)
    assert (lead.first_name, lead.last_name) == ("Priya", "Raghavan")


def test_western_card_name_is_left_alone() -> None:
    lead = build_lead(blank_lead(), make_result(GOOD_CARD), [], 0.65)
    assert (lead.first_name, lead.last_name) == ("Priya", "Raghavan")
