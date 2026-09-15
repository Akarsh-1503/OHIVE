"""Image preparation: EXIF orientation, HEIC/PDF decoding, downscaling and quality flags."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

import pypdfium2
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat
from pillow_heif import register_heif_opener

register_heif_opener()

# Pillow refuses very large images as a decompression-bomb guard. Business-card scans are
# small; raise it just enough to accept a 100 MP phone panorama someone uploads by mistake.
Image.MAX_IMAGE_PIXELS = 120_000_000

TARGET_LONG_EDGE = 1280
JPEG_QUALITY = 88
THUMB_LONG_EDGE = 480

# Heuristic thresholds, calibrated on synthetic and photographed cards. Each is deliberately
# loose: a flag costs a few points of confidence, it never rejects a card.
# Reference values from the calibration set (see assess_quality):
#   in focus 38-59 / 1.5 px Gaussian blur 13-23 / 3 px blur 8-9  -> FOCUS_SCORE_FLOOR
#   normal exposure 230+ / dark 51 / washed out 46               -> CONTRAST_RANGE_FLOOR
MIN_USABLE_LONG_EDGE = 800
FOCUS_SCORE_FLOOR = 30.0
FOCUS_BLUR_RADIUS = 2.0
DARK_MEAN_CEILING = 70.0
CONTRAST_RANGE_FLOOR = 90.0


class UnsupportedImageError(ValueError):
    """The upload could not be decoded as an image or PDF."""


@dataclass
class PreparedImage:
    jpeg_bytes: bytes
    width: int
    height: int
    source_width: int
    source_height: int
    quality_flags: list[str] = field(default_factory=list)
    #: CPU time spent inside prepare_image, excluding any wait for a worker thread.
    duration_ms: int = 0


def _load(raw: bytes, content_type: str, filename: str) -> Image.Image:
    is_pdf = content_type == "application/pdf" or raw[:5] == b"%PDF-"
    if is_pdf:
        try:
            document = pypdfium2.PdfDocument(raw)
            page = document[0]
            width, height = page.get_size()
            # Render at roughly 1600 px on the long edge so the downscale to 1280 resamples
            # down rather than up; get_size() is in points, render scale is multiples of 72dpi.
            scale = min(4.0, max(1.0, 1600.0 / max(width, height, 1.0)))
            image = page.render(scale=scale).to_pil()
            page.close()
            document.close()
            return image
        except Exception as exc:  # pypdfium2 raises bare PdfiumError subclasses
            raise UnsupportedImageError(f"could not render PDF {filename!r}: {exc}") from exc
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
        return image
    except Exception as exc:
        raise UnsupportedImageError(f"could not decode image {filename!r}: {exc}") from exc


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def _percentile(histogram: list[int], fraction: float) -> int:
    total = sum(histogram)
    if total == 0:
        return 0
    target = total * fraction
    running = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= target:
            return value
    return 255


def focus_score(gray: Image.Image, contrast: int) -> float:
    """Peak high-frequency response as a percentage of the card's ink-to-paper contrast.

    Variance of the Laplacian is the textbook focus measure, but it averages over the whole
    frame — and a business card is 95% blank paper, so a crisp minimalist card scores lower
    than a genuinely blurred busy one. Taking the *peak* response instead of the mean, and
    dividing by the ink-to-paper range, makes the measure independent of both how much of
    the frame carries text and how the photo was exposed: a sharp edge always produces a
    high-pass response that is a fixed fraction of its own amplitude, and blur destroys it.
    """
    if contrast < 10:
        return 0.0
    high_frequency = ImageChops.difference(
        gray, gray.filter(ImageFilter.GaussianBlur(FOCUS_BLUR_RADIUS))
    )
    return 100.0 * _percentile(high_frequency.histogram(), 0.999) / contrast


def assess_quality(image: Image.Image, source_long_edge: int | None = None) -> list[str]:
    """Measure the card's legibility *before* the VLM sees it.

    The metrics are deliberately chosen to be independent of one another, so an
    under-exposed but sharp photo is reported as `dark` and not also as `blurry`.
    Thresholds were calibrated against the reference values noted with the constants above.
    `source_long_edge` is the *original* upload's long edge: only `low_resolution` describes
    what the user sent, the rest describe the image the model will actually read.
    """
    gray = image.convert("L")
    if max(gray.size) > 1024:
        gray = ImageOps.contain(gray, (1024, 1024), Image.Resampling.BILINEAR)

    # A business card is mostly paper, so percentile-based contrast is dominated by the
    # background. The extrema of a median-filtered copy measure ink-to-paper separation
    # directly and do not care what fraction of the frame the text occupies. The median
    # filter is what stops a single hot pixel from claiming the card has full contrast.
    low, high = gray.filter(ImageFilter.MedianFilter(3)).getextrema()
    contrast = high - low

    flags: list[str] = []
    if (source_long_edge or max(image.size)) < MIN_USABLE_LONG_EDGE:
        flags.append("low_resolution")
    if focus_score(gray, contrast) < FOCUS_SCORE_FLOOR:
        flags.append("blurry")
    if ImageStat.Stat(gray).mean[0] < DARK_MEAN_CEILING:
        flags.append("dark")
    if contrast < CONTRAST_RANGE_FLOOR:
        flags.append("low_contrast")
    return flags


def prepare_image(raw: bytes, filename: str, content_type: str) -> PreparedImage:
    """EXIF-orient, downscale to 1280 px, measure quality and re-encode as JPEG q=88.

    CPU-bound; call it from a worker thread.
    """
    started = time.monotonic()
    image = _load(raw, content_type, filename)
    image = ImageOps.exif_transpose(image) or image
    image = _to_rgb(image)
    source_width, source_height = image.size

    if max(image.size) > TARGET_LONG_EDGE:
        image = ImageOps.contain(
            image, (TARGET_LONG_EDGE, TARGET_LONG_EDGE), Image.Resampling.LANCZOS
        )

    # Quality is judged on the downscaled copy, because that is precisely the image the
    # model will read — "is this legible?" is a question about what the model gets, not
    # about the 12 MP original. The focus score is scale-invariant, so the thresholds hold.
    flags = assess_quality(image, source_long_edge=max(source_width, source_height))

    # Light normalisation only: the VLM is trained on natural photos, so aggressive
    # binarisation of the kind classical OCR wants actively hurts it here.
    if "low_contrast" in flags or "dark" in flags:
        image = ImageOps.autocontrast(image, cutoff=1)
    if "dark" in flags:
        image = ImageEnhance.Brightness(image).enhance(1.25)
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Sharpness(image).enhance(1.6 if "blurry" in flags else 1.15)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return PreparedImage(
        jpeg_bytes=buffer.getvalue(),
        width=image.width,
        height=image.height,
        source_width=source_width,
        source_height=source_height,
        quality_flags=flags,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _render(raw: bytes, content_type: str, filename: str, long_edge: int) -> Image.Image:
    image = _load(raw, content_type, filename)
    image = ImageOps.exif_transpose(image) or image
    image = _to_rgb(image)
    if max(image.size) > long_edge:
        image = ImageOps.contain(image, (long_edge, long_edge), Image.Resampling.LANCZOS)
    return image


def make_thumbnail(raw: bytes, content_type: str, filename: str) -> bytes:
    """480 px WebP for the review grid."""
    buffer = io.BytesIO()
    _render(raw, content_type, filename, THUMB_LONG_EDGE).save(
        buffer, format="WEBP", quality=82, method=4
    )
    return buffer.getvalue()


def make_web_jpeg(raw: bytes, content_type: str, filename: str, long_edge: int = 1600) -> bytes:
    """Browser-renderable copy of an upload a browser cannot display itself (HEIC, PDF)."""
    buffer = io.BytesIO()
    _render(raw, content_type, filename, long_edge).save(
        buffer, format="JPEG", quality=90, optimize=True
    )
    return buffer.getvalue()
