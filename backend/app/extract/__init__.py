"""Everything around the model call: image preparation, normalisation, confidence, dedup.

The VLM reads the card; this package is what turns a transcription into a lead a salesperson
can act on. Three things happen here that a naive "just call the model" pipeline skips:

1. **Quality flags are measured before the call**, so a blurry or under-exposed card is
   visible to the reviewer and discounts the confidence rather than silently producing
   plausible-looking garbage.
2. **Cross-field validation adjusts confidence.** The model scores each field in isolation;
   we score the *record*. An email whose local part matches the name and whose domain matches
   the website is far more likely to be right than either signal alone suggests.
3. **Validation failures floor confidence** instead of being dropped, so nothing disappears
   without a human seeing it.
"""

from __future__ import annotations

from app.extract.confidence import (
    QUALITY_MULTIPLIER_FLOOR,
    QUALITY_PENALTIES,
    build_lead,
    cross_validate,
    overall_confidence,
    quality_multiplier,
    recompute_after_edit,
)
from app.extract.dedup import find_duplicate
from app.extract.normalise import (
    WEBSITE_REPAIR_MAX_EDITS,
    EmailResult,
    clean_phone_text,
    email_local_key,
    normalise_email,
    normalise_location,
    normalise_phone,
    normalise_website,
    phone_region,
    region_from_location,
    reorder_name_from_email,
    repair_website_from_email,
    split_name,
    strip_address_detail,
    website_is_valid,
)
from app.extract.preprocess import (
    CONTRAST_RANGE_FLOOR,
    DARK_MEAN_CEILING,
    FOCUS_BLUR_RADIUS,
    FOCUS_SCORE_FLOOR,
    JPEG_QUALITY,
    MIN_USABLE_LONG_EDGE,
    TARGET_LONG_EDGE,
    THUMB_LONG_EDGE,
    PreparedImage,
    UnsupportedImageError,
    assess_quality,
    focus_score,
    make_thumbnail,
    make_web_jpeg,
    prepare_image,
)

__all__ = [
    "CONTRAST_RANGE_FLOOR",
    "DARK_MEAN_CEILING",
    "FOCUS_BLUR_RADIUS",
    "FOCUS_SCORE_FLOOR",
    "JPEG_QUALITY",
    "MIN_USABLE_LONG_EDGE",
    "QUALITY_MULTIPLIER_FLOOR",
    "QUALITY_PENALTIES",
    "TARGET_LONG_EDGE",
    "THUMB_LONG_EDGE",
    "WEBSITE_REPAIR_MAX_EDITS",
    "EmailResult",
    "PreparedImage",
    "UnsupportedImageError",
    "assess_quality",
    "build_lead",
    "clean_phone_text",
    "cross_validate",
    "email_local_key",
    "find_duplicate",
    "focus_score",
    "make_thumbnail",
    "make_web_jpeg",
    "normalise_email",
    "normalise_location",
    "normalise_phone",
    "normalise_website",
    "overall_confidence",
    "phone_region",
    "prepare_image",
    "quality_multiplier",
    "recompute_after_edit",
    "region_from_location",
    "reorder_name_from_email",
    "repair_website_from_email",
    "split_name",
    "strip_address_detail",
    "website_is_valid",
]
