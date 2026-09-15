#!/usr/bin/env python3
"""Render the LeadForge business-card evaluation corpus.

Produces 20 synthetic cards in ``cards/`` plus ``ground_truth.json``. Every string that
ends up on a card is derived from the same record that seeds the ground truth, so the two
artefacts cannot drift apart.

All people, companies, domains and phone numbers are invented. Domains use the IANA
reserved ``.example`` TLD (RFC 2606); phone numbers use the drama/fiction ranges reserved
by the respective regulators (NANP 555-01xx, Ofcom 07700 900xxx / 0xx 496 0xxx) or an
otherwise unassigned block.

Usage:
    python generate_cards.py            # writes cards/ and ground_truth.json
    python generate_cards.py --only card_05_bilingual_jp_clean
"""

from __future__ import annotations

import argparse
import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
CARDS_DIR = HERE / "cards"
GT_PATH = HERE / "ground_truth.json"

# 3.5in x 2in at 300 dpi is the physical standard; we rasterise at 2x and downsample so
# hairlines and logo marks get proper antialiasing instead of stair-stepping.
CARD_W, CARD_H = 1050, 600
SS = 2

RGB = tuple[int, int, int]

FONTS: dict[str, tuple[str, int]] = {
    "helv": ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
    "helv_bold": ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    "helv_light": ("/System/Library/Fonts/HelveticaNeue.ttc", 7),
    "helv_med": ("/System/Library/Fonts/HelveticaNeue.ttc", 10),
    "avenir": ("/System/Library/Fonts/Avenir Next.ttc", 7),
    "avenir_med": ("/System/Library/Fonts/Avenir Next.ttc", 5),
    "avenir_demi": ("/System/Library/Fonts/Avenir Next.ttc", 2),
    "avenir_bold": ("/System/Library/Fonts/Avenir Next.ttc", 0),
    "futura": ("/System/Library/Fonts/Supplemental/Futura.ttc", 0),
    "futura_bold": ("/System/Library/Fonts/Supplemental/Futura.ttc", 2),
    "gill": ("/System/Library/Fonts/Supplemental/GillSans.ttc", 0),
    "gill_semi": ("/System/Library/Fonts/Supplemental/GillSans.ttc", 4),
    "gill_bold": ("/System/Library/Fonts/Supplemental/GillSans.ttc", 1),
    "gill_light": ("/System/Library/Fonts/Supplemental/GillSans.ttc", 7),
    "bask": ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 0),
    "bask_bold": ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 1),
    "bask_semi": ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 4),
    "didot": ("/System/Library/Fonts/Supplemental/Didot.ttc", 0),
    "didot_bold": ("/System/Library/Fonts/Supplemental/Didot.ttc", 2),
    "charter": ("/System/Library/Fonts/Supplemental/Charter.ttc", 0),
    "charter_bold": ("/System/Library/Fonts/Supplemental/Charter.ttc", 3),
    "iowan": ("/System/Library/Fonts/Supplemental/Iowan Old Style.ttc", 0),
    "iowan_bold": ("/System/Library/Fonts/Supplemental/Iowan Old Style.ttc", 1),
    "typewriter": ("/System/Library/Fonts/Supplemental/AmericanTypewriter.ttc", 0),
    "typewriter_bold": ("/System/Library/Fonts/Supplemental/AmericanTypewriter.ttc", 2),
    "copper": ("/System/Library/Fonts/Supplemental/Copperplate.ttc", 0),
    "copper_bold": ("/System/Library/Fonts/Supplemental/Copperplate.ttc", 2),
    "din": ("/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf", 0),
    "din_cond": ("/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf", 0),
    "georgia": ("/System/Library/Fonts/Supplemental/Georgia.ttf", 0),
    "georgia_bold": ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 0),
    "arialn": ("/System/Library/Fonts/Supplemental/Arial Narrow.ttf", 0),
    "arialn_bold": ("/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf", 0),
    "menlo": ("/System/Library/Fonts/Menlo.ttc", 0),
    # Hiragino Sans is the only system face here with full kana + jouyou kanji coverage.
    "jp": ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", 0),
    "jp_bold": ("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", 0),
}

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(key: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a cached face. ``size`` is in final-card pixels; supersampling is applied here."""
    px = int(round(size * SS))
    cache_key = (key, px)
    if cache_key not in _FONT_CACHE:
        path, index = FONTS[key]
        _FONT_CACHE[cache_key] = ImageFont.truetype(path, px, index=index)
    return _FONT_CACHE[cache_key]


def fit_font(key: str, text: str, max_w: float, size: float, floor: float = 6.0) -> ImageFont.FreeTypeFont:
    """Largest size <= ``size`` at which ``text`` fits ``max_w`` final-card pixels."""
    limit = max_w * SS
    while size > floor:
        f = font(key, int(round(size)))
        if f.getlength(text) <= limit:
            return f
        size -= 0.5
    return font(key, int(round(floor)))


def track(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
          f: ImageFont.FreeTypeFont, fill: RGB, spacing: float = 0.0,
          anchor: str = "ls") -> float:
    """Draw ``text`` with manual letter-spacing. Returns the advance width in device px.

    PIL has no tracking control, so small-caps labels are drawn glyph by glyph; without
    this the 'D I R E C T O R' style labels real cards use are impossible.
    """
    x, y = xy
    total = sum(f.getlength(c) for c in text) + spacing * SS * max(len(text) - 1, 0)
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    va = anchor[1]
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill, anchor="l" + va)
        x += f.getlength(ch) + spacing * SS
    return total


def hairline(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
             fill: RGB, w: float = 1.0) -> None:
    draw.line([(x0 * SS, y0 * SS), (x1 * SS, y1 * SS)], fill=fill, width=max(1, int(round(w * SS))))


def rect(draw: ImageDraw.ImageDraw, box: Sequence[float], fill: RGB | None = None,
         outline: RGB | None = None, w: float = 1.0) -> None:
    draw.rectangle([box[0] * SS, box[1] * SS, box[2] * SS, box[3] * SS],
                   fill=fill, outline=outline, width=max(1, int(round(w * SS))))


# --------------------------------------------------------------------------------------
# Logo marks. Drawn on a 4x supersampled RGBA tile then downsampled, so the curves and
# mitres stay clean after the card itself is resampled.
# --------------------------------------------------------------------------------------

LOGO_SS = 4


def _poly(n: int, cx: float, cy: float, r: float, rot: float) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(rot + 2 * math.pi * i / n),
             cy + r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]


def _mark_hex_ring(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    d.polygon(_poly(6, s / 2, s / 2, s * 0.46, -math.pi / 2), outline=c, width=int(s * 0.075))
    d.polygon(_poly(6, s / 2, s / 2, s * 0.22, -math.pi / 2), fill=c2)


def _mark_arc_stack(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    for i, r in enumerate((0.46, 0.32, 0.18)):
        col = c if i % 2 == 0 else c2
        d.arc([s / 2 - s * r, s / 2 - s * r, s / 2 + s * r, s / 2 + s * r],
              200, 340 if i % 2 == 0 else 380, fill=col, width=int(s * 0.09))
    d.ellipse([s * 0.44, s * 0.44, s * 0.56, s * 0.56], fill=c)


def _mark_tri_stack(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    d.polygon([(s * 0.5, s * 0.06), (s * 0.94, s * 0.62), (s * 0.06, s * 0.62)], fill=c)
    d.polygon([(s * 0.5, s * 0.38), (s * 0.94, s * 0.94), (s * 0.06, s * 0.94)], fill=c2)


def _mark_chevron(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    for i, col in ((0, c), (1, c2)):
        off = i * s * 0.26
        d.line([(s * 0.14, s * 0.30 + off), (s * 0.5, s * 0.60 + off), (s * 0.86, s * 0.30 + off)],
               fill=col, width=int(s * 0.11), joint="curve")


def _mark_wave(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    for i in range(3):
        pts = [(s * 0.08 + s * 0.84 * t / 40,
                s * (0.30 + 0.20 * i) + s * 0.10 * math.sin(2 * math.pi * t / 20))
               for t in range(41)]
        d.line(pts, fill=c if i != 1 else c2, width=int(s * 0.07), joint="curve")


def _mark_dot_grid(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    for r in range(4):
        for col in range(4):
            rad = s * (0.030 + 0.016 * ((r + col) % 3))
            cx, cy = s * (0.20 + 0.20 * col), s * (0.20 + 0.20 * r)
            d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=c if (r + col) % 2 else c2)


def _mark_diamond(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    d.polygon(_poly(4, s / 2, s / 2, s * 0.48, 0), outline=c, width=int(s * 0.07))
    d.polygon(_poly(4, s / 2, s / 2, s * 0.26, 0), fill=c2)
    d.line([(s * 0.5, s * 0.02), (s * 0.5, s * 0.98)], fill=c, width=int(s * 0.035))


def _mark_split_circle(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    d.pieslice([s * 0.04, s * 0.04, s * 0.96, s * 0.96], 90, 270, fill=c)
    d.pieslice([s * 0.04, s * 0.04, s * 0.96, s * 0.96], 270, 90, fill=c2)
    d.ellipse([s * 0.36, s * 0.36, s * 0.64, s * 0.64], fill=(255, 255, 255))


def _mark_bars(d: ImageDraw.ImageDraw, s: float, c: RGB, c2: RGB) -> None:
    heights = (0.30, 0.55, 0.80, 0.45)
    for i, h in enumerate(heights):
        x = s * (0.10 + 0.22 * i)
        d.rectangle([x, s * (0.92 - h), x + s * 0.13, s * 0.92], fill=c if i % 2 else c2)


MARKS: dict[str, Callable[[ImageDraw.ImageDraw, float, RGB, RGB], None]] = {
    "hex": _mark_hex_ring, "arc": _mark_arc_stack, "tri": _mark_tri_stack,
    "chevron": _mark_chevron, "wave": _mark_wave, "dots": _mark_dot_grid,
    "diamond": _mark_diamond, "split": _mark_split_circle, "bars": _mark_bars,
}


def logo(img: Image.Image, kind: str, cx: float, cy: float, size: float,
         c: RGB, c2: RGB, monogram: str | None = None) -> None:
    """Paste a drawn mark centred on (cx, cy) in final-card coordinates."""
    s = size * SS * LOGO_SS
    tile = Image.new("RGBA", (int(s), int(s)), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    if monogram is not None:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=c)
        path, index = FONTS["futura_bold"]
        f = ImageFont.truetype(path, int(s * 0.46), index=index)
        d.text((s / 2, s / 2 + s * 0.02), monogram, font=f, fill=c2, anchor="mm")
    else:
        MARKS[kind](d, s, c, c2)
    tile = tile.resize((int(size * SS), int(size * SS)), Image.LANCZOS)
    img.alpha_composite(tile, (int((cx - size / 2) * SS), int((cy - size / 2) * SS)))


# --------------------------------------------------------------------------------------
# Card records
# --------------------------------------------------------------------------------------

@dataclass
class Card:
    filename: str
    layout: str
    difficulty: str
    notes: str
    # Ground-truth fields (None means "not present on the card").
    first_name: str | None
    last_name: str | None
    job_title: str | None
    company: str | None
    location: str | None
    phone: str | None
    email: str | None
    website: str | None
    # `difficulty` describes the capture (clean | degraded); `edge_cases` describes the
    # content. They are orthogonal, and a harness usually wants to slice on both.
    edge_cases: tuple[str, ...] = ()
    # Presentation.
    palette: str = "ink_teal"
    fonts: tuple[str, str, str] = ("avenir_bold", "avenir_med", "avenir")
    mark: str = "hex"
    monogram: str | None = None
    name_display: str | None = None
    title_lines: tuple[str, ...] = ()
    company_display: str | None = None
    address_lines: tuple[str, ...] = ()
    phones: tuple[tuple[str, str], ...] = ()
    website_display: str | None = None
    tagline: str | None = None
    jp: dict[str, str] = field(default_factory=dict)
    degrade: tuple[str, ...] = ()
    fmt: str = "png"

    def printed_name(self) -> str:
        if self.name_display:
            return self.name_display
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    def printed_titles(self) -> tuple[str, ...]:
        if self.title_lines:
            return self.title_lines
        return (self.job_title,) if self.job_title else ()

    def printed_company(self) -> str | None:
        return self.company_display or self.company

    def printed_address(self) -> tuple[str, ...]:
        if self.address_lines:
            return self.address_lines
        return (self.location,) if self.location else ()

    def printed_phones(self) -> tuple[tuple[str, str], ...]:
        if self.phones:
            return self.phones
        return (("T", self.phone),) if self.phone else ()

    def printed_website(self) -> str | None:
        if self.website_display is not None:
            return self.website_display
        return self.website


PALETTES: dict[str, dict[str, RGB]] = {
    "ink_teal":   {"bg": (252, 251, 248), "ink": (26, 32, 38),  "mut": (108, 118, 126), "acc": (0, 122, 122), "acc2": (196, 226, 222)},
    "navy_gold":  {"bg": (250, 249, 245), "ink": (20, 33, 61),  "mut": (104, 114, 134), "acc": (176, 137, 48), "acc2": (232, 216, 176)},
    "dark_slate": {"bg": (26, 29, 33),    "ink": (242, 243, 244), "mut": (150, 157, 165), "acc": (0, 176, 155), "acc2": (58, 66, 74)},
    "dark_plum":  {"bg": (33, 25, 38),    "ink": (243, 239, 246), "mut": (163, 152, 173), "acc": (214, 158, 90), "acc2": (62, 48, 72)},
    "crimson":    {"bg": (253, 251, 250), "ink": (33, 28, 28),  "mut": (120, 110, 108), "acc": (168, 42, 48), "acc2": (240, 214, 210)},
    "forest":     {"bg": (248, 250, 246), "ink": (24, 38, 30),  "mut": (104, 122, 110), "acc": (44, 106, 70), "acc2": (206, 228, 212)},
    "mono":       {"bg": (255, 255, 255), "ink": (18, 18, 18),  "mut": (135, 135, 135), "acc": (18, 18, 18), "acc2": (226, 226, 226)},
    "indigo":     {"bg": (249, 250, 253), "ink": (27, 31, 59),  "mut": (112, 118, 146), "acc": (62, 78, 186), "acc2": (214, 220, 248)},
    "sand":       {"bg": (245, 240, 231), "ink": (46, 40, 32),  "mut": (128, 118, 104), "acc": (166, 98, 48), "acc2": (226, 210, 188)},
    "dark_navy":  {"bg": (18, 28, 46),    "ink": (238, 241, 247), "mut": (148, 160, 180), "acc": (226, 178, 92), "acc2": (40, 54, 78)},
}


CARDS: list[Card] = [
    Card(
        filename="card_01_left_align_clean", layout="left_align", difficulty="clean",
        notes="Baseline left-aligned card, high contrast, single phone. The control case: "
              "anything the pipeline gets wrong here is a systematic bug, not a hard input.",
        first_name="Aarav", last_name="Deshmukh",
        job_title="Head of Platform Engineering", company="Northwind Robotics",
        location="Bengaluru, KA, India", phone="+91 80 4555 0118",
        email="aarav.deshmukh@northwind.example", website="northwind.example",
        palette="ink_teal", fonts=("avenir_bold", "avenir_med", "avenir"), mark="hex",
        address_lines=("Level 7, Ashwin Tech Park", "Bengaluru, KA 560103, India"),
        website_display="www.northwind.example",
    ),
    Card(
        filename="card_02_centered_serif_clean", layout="centered", difficulty="edge",
        notes="Hyphenated compound surname ('Kowalczyk-Reyes') that must survive into "
              "last_name intact; centred serif layout with no left anchor for the parser.",
        first_name="Marta", last_name="Kowalczyk-Reyes",
        job_title="Director, Clinical Operations", company="Helix Meridian Labs",
        location="Boston, MA, USA", phone="+1 (617) 555 0142",
        email="m.kowalczyk-reyes@helixmeridian.example", website="helixmeridian.example",
        edge_cases=("hyphenated_surname", "centred_layout"),
        palette="navy_gold", fonts=("didot_bold", "bask_semi", "bask"), mark="diamond",
        address_lines=("41 Harborline Street, Suite 900", "Boston, MA 02210, USA"),
    ),
    Card(
        filename="card_03_dark_panel_clean", layout="split_panel", difficulty="edge",
        notes="Dark card with light text plus an honorific prefix and a post-nominal "
              "('Dr. ... , PhD') that must not leak into first_name/last_name.",
        first_name="Yusuf", last_name="Al-Hakim",
        job_title="Chief Scientific Officer", company="Qanat Desalination Group",
        location="Dubai, United Arab Emirates", phone="+971 4 555 0173",
        email="y.alhakim@qanatgroup.example", website="qanatgroup.example",
        edge_cases=("honorific_prefix", "post_nominal", "dark_card", "hyphenated_surname"),
        palette="dark_slate", fonts=("gill_bold", "gill_semi", "gill"), mark="wave",
        name_display="Dr. Yusuf Al-Hakim, PhD",
        address_lines=("Marina Quarter, Tower 2, Level 18", "Dubai, United Arab Emirates"),
    ),
    Card(
        filename="card_04_minimal_clean", layout="minimal", difficulty="edge",
        notes="Deliberately minimal: name and email only. Every other field must come back "
              "null rather than hallucinated, and quality_flags should note the gaps.",
        first_name="Sophie", last_name="Brennan",
        job_title=None, company=None, location=None, phone=None,
        email="sophie@larkfield.example", website=None,
        edge_cases=("minimal_name_email_only", "mostly_null_fields"),
        palette="mono", fonts=("helv_light", "helv", "helv_light"), mark="dots",
        fmt="webp",
    ),
    Card(
        filename="card_05_bilingual_jp_clean", layout="bilingual", difficulty="edge",
        notes="Japanese/English bilingual card. The Latin block carries the ground truth; "
              "the model must not splice kanji into the Latin fields or swap the JP "
              "family-name-first ordering into first_name/last_name.",
        first_name="Kenji", last_name="Watanabe",
        job_title="Senior Solutions Architect", company="Tsubaki Mobility K.K.",
        location="Minato-ku, Tokyo, Japan", phone="+81 3 5555 0164",
        email="k.watanabe@tsubaki-mobility.example", website="tsubaki-mobility.example",
        edge_cases=("bilingual_ja_en", "non_latin_script"),
        palette="crimson", fonts=("helv_bold", "helv_med", "helv"), mark="split",
        address_lines=("2-14-6 Akasaka, Minato-ku", "Tokyo 107-0052, Japan"),
        jp={
            "name": "渡辺 健二",
            "title": "シニアソリューションアーキテクト",
            "company": "椿モビリティ株式会社",
            "addr1": "東京都港区赤坂2-14-6",
            "addr2": "電話：03-5555-0164",
        },
    ),
    Card(
        filename="card_06_title_wrap_clean", layout="logo_block", difficulty="edge",
        notes="Job title is too long for one line and wraps across two; the extracted "
              "job_title must be the rejoined single-space string, not just line one.",
        first_name="Lena", last_name="Hoffmann",
        job_title="Vice President, Industrial Automation & Digital Manufacturing Systems",
        company="Rheinwerk Systeme GmbH", location="Stuttgart, Germany",
        phone="+49 711 5550 0129", email="l.hoffmann@rheinwerk.example",
        website="rheinwerk.example",
        edge_cases=("job_title_wraps_two_lines",),
        palette="indigo", fonts=("din", "avenir_demi", "avenir"), mark="bars",
        title_lines=("Vice President, Industrial Automation",
                     "& Digital Manufacturing Systems"),
        address_lines=("Kernerstraße 52", "70182 Stuttgart, Germany"),
        website_display="rheinwerk.example",
    ),
    Card(
        filename="card_07_two_phones_clean", layout="dense_corporate", difficulty="edge",
        notes="Two labelled numbers (mobile + office) plus a fax. The contract has a single "
              "phone field, so the extractor must pick the mobile and not concatenate.",
        first_name="Wei Ming", last_name="Tan",
        job_title="Regional Partnerships Lead", company="Kallang Harbour Capital",
        location="Singapore", phone="+65 8555 0132",
        email="weiming.tan@kallangharbour.example", website="kallangharbour.example",
        edge_cases=("two_phone_numbers", "fax_number", "family_name_first"),
        palette="forest", fonts=("copper_bold", "helv_med", "helv"), mark="arc",
        name_display="Tan Wei Ming",
        phones=(("M", "+65 8555 0132"), ("T", "+65 6555 0117"), ("F", "+65 6555 0118")),
        address_lines=("18 Kallang Quay, #22-03", "Singapore 339158"),
        tagline="Private Credit · Infrastructure · Real Assets",
    ),
    Card(
        filename="card_08_no_email_clean", layout="left_align", difficulty="edge",
        notes="No email address anywhere on the card — only a phone and a website. "
              "email must be null and the no_email quality flag should fire.",
        first_name="Ravi", last_name="Iyer",
        job_title="Field Service Manager", company="Sundara Instruments Pvt Ltd",
        location="Pune, MH, India", phone="+91 20 4555 0166",
        email=None, website="sundarainstruments.example",
        edge_cases=("no_email",),
        palette="sand", fonts=("typewriter_bold", "typewriter", "helv"), mark="chevron",
        address_lines=("Plot 12, Hinjawadi Phase II", "Pune, MH 411057, India"),
        website_display="www.sundarainstruments.example",
        tagline="Calibration · Repair · Validation",
    ),
    Card(
        filename="card_09_portrait_warp", layout="portrait", difficulty="degraded",
        notes="Portrait orientation photographed at an angle: perspective warp plus a soft "
              "drop shadow. Tests both the unusual aspect ratio and geometric robustness.",
        first_name="Nadia", last_name="Farouk",
        job_title="Head of Brand", company="Caravelle Studio",
        location="London, United Kingdom", phone="+44 20 7946 0132",
        email="nadia@caravellestudio.example", website="caravellestudio.example",
        edge_cases=("portrait_orientation",),
        palette="dark_plum", fonts=("futura_bold", "futura", "helv"), mark="tri",
        address_lines=("9 Ravenscroft Yard", "London EC2A 4QT, United Kingdom"),
        degrade=("warp", "shadow"), fmt="jpg",
    ),
    Card(
        filename="card_10_qr_lowlight", layout="qr_block", difficulty="degraded",
        notes="Real scannable QR code (MECARD payload) next to the text block, captured "
              "under-exposed. The QR must not be read as text, and the honorific 'Dr.' "
              "must stay out of first_name.",
        first_name="Elena", last_name="Vasquez",
        job_title="Managing Partner", company="Tidewater Advisory",
        location="San Francisco, CA, USA", phone="+1 (415) 555 0188",
        email="elena.vasquez@tidewater.example", website="tidewater.example",
        edge_cases=("qr_code", "honorific_prefix"),
        palette="mono", fonts=("helv_bold", "helv_med", "helv"), mark="diamond",
        name_display="Dr. Elena Vasquez",
        address_lines=("500 Bayfront Plaza, Floor 12", "San Francisco, CA 94105, USA"),
        degrade=("lowlight",), fmt="jpg",
    ),
    Card(
        filename="card_11_multiline_addr_blur", layout="dense_corporate", difficulty="degraded",
        notes="Four-line international postal address (street, floor, postcode/city, "
              "country) shot with motion blur. location must collapse to the locality, not "
              "swallow the street line.",
        first_name="Ingrid", last_name="Lindqvist",
        job_title="Global Logistics Coordinator", company="Meridian Freight Alliance",
        location="Hamburg, Germany", phone="+49 40 5550 0147",
        email="i.lindqvist@meridianfreight.example", website="meridianfreight.example",
        edge_cases=("multiline_international_address",),
        palette="navy_gold", fonts=("charter_bold", "charter", "helv"), mark="wave",
        address_lines=("Hafenstraße 14", "4. Obergeschoss", "20359 Hamburg", "Germany"),
        tagline="Member: FIATA · IATA CASS · AEO-F DE 4471 0022",
        degrade=("motion_blur",), fmt="jpg",
    ),
    Card(
        filename="card_12_suffix_jpeg", layout="centered", difficulty="degraded",
        notes="Generational suffix 'Jr.' after the surname, plus aggressive JPEG "
              "quantisation (q=16) so 8x8 block ringing sits on every glyph edge.",
        first_name="James", last_name="Okonkwo",
        job_title="Account Executive", company="BrightSpar Analytics",
        location="Austin, TX, USA", phone="+1 (512) 555 0109",
        email="j.okonkwo@brightspar.example", website="brightspar.example",
        edge_cases=("generational_suffix",),
        palette="crimson", fonts=("iowan_bold", "iowan", "helv"), mark="dots",
        name_display="James Okonkwo Jr.",
        address_lines=("2200 Congress Avenue, Suite 410", "Austin, TX 78701, USA"),
        degrade=("jpeg_hard",), fmt="jpg",
    ),
    Card(
        filename="card_13_dense_skew_shadow", layout="dense_corporate", difficulty="degraded",
        notes="Dense 11-line corporate layout (registration number, two offices) captured "
              "skewed with a hard shadow edge crossing the contact block.",
        first_name="Hannah", last_name="Mbeki",
        job_title="Operations Director", company="Copperleaf Foods Ltd",
        location="Manchester, United Kingdom", phone="+44 161 496 0155",
        email="h.mbeki@copperleaffoods.example", website="copperleaffoods.example",
        edge_cases=("dense_layout", "two_phone_numbers"),
        palette="forest", fonts=("gill_bold", "gill_semi", "arialn"), mark="bars",
        address_lines=("Unit 4, Northgate Works", "Ancoats", "Manchester M4 5JT",
                       "United Kingdom"),
        phones=(("T", "+44 161 496 0155"), ("M", "+44 7700 900412")),
        tagline="Registered in England & Wales No. 09 552 118",
        degrade=("skew", "hard_shadow"), fmt="jpg",
    ),
    Card(
        filename="card_14_dup_of_02_blur", layout="centered", difficulty="degraded",
        notes="Second capture of the SAME card as card_02 — blurred and warped. Exists to "
              "exercise duplicate detection: the lead should be flagged duplicate_of the "
              "card_02 lead while still extracting cleanly.",
        first_name="Marta", last_name="Kowalczyk-Reyes",
        job_title="Director, Clinical Operations", company="Helix Meridian Labs",
        location="Boston, MA, USA", phone="+1 (617) 555 0142",
        email="m.kowalczyk-reyes@helixmeridian.example", website="helixmeridian.example",
        edge_cases=("duplicate_of_card_02", "hyphenated_surname"),
        palette="navy_gold", fonts=("didot_bold", "bask_semi", "bask"), mark="diamond",
        address_lines=("41 Harborline Street, Suite 900", "Boston, MA 02210, USA"),
        degrade=("warp", "motion_blur"), fmt="jpg",
    ),
    Card(
        filename="card_15_dark_warp", layout="split_panel", difficulty="degraded",
        notes="Dark card at a steep angle: light-on-dark text plus perspective foreshortening "
              "that shrinks the right-hand contact block to about half its true height.",
        first_name="Rahul", last_name="Menon",
        job_title="Director of Engineering", company="Vantara Aerosystems",
        location="Hyderabad, TS, India", phone="+91 40 4555 0121",
        email="rahul.menon@vantara-aero.example", website="vantara-aero.example",
        edge_cases=("dark_card",),
        palette="dark_navy", fonts=("avenir_bold", "avenir_med", "avenir"), mark="tri",
        address_lines=("Gachibowli Financial District", "Hyderabad, TS 500032, India"),
        degrade=("warp_hard", "shadow"), fmt="jpg",
    ),
    Card(
        filename="card_16_lowlight_grain", layout="logo_block", difficulty="degraded",
        notes="Under-exposed handheld capture with sensor grain — roughly 2.5 stops down. "
              "Low-contrast muted secondary text is the first thing to disappear.",
        first_name="Claire", last_name="Beaumont",
        job_title="Head of People Operations", company="Orrery Health",
        location="Bristol, United Kingdom", phone="+44 117 496 0155",
        email="c.beaumont@orreryhealth.example", website="orreryhealth.example",
        palette="ink_teal", fonts=("gill_bold", "gill_semi", "gill"), mark="arc",
        address_lines=("The Old Dispensary, Redcliff Street", "Bristol BS1 6NL, United Kingdom"),
        degrade=("lowlight_hard", "grain"), fmt="jpg",
    ),
    Card(
        filename="card_17_lowres_jpeg", layout="left_align", difficulty="degraded",
        notes="Low-resolution capture (long edge 620 px) re-encoded at q=28 — simulates a "
              "phone photo taken too far away and then messaged. Umlaut in the city name.",
        first_name="Tobias", last_name="Herrmann",
        job_title="Procurement Manager", company="Nordlicht Verpackung GmbH",
        location="Düsseldorf, Germany", phone="+49 211 5550 0193",
        email="t.herrmann@nordlicht-vp.example", website="nordlicht-vp.example",
        edge_cases=("non_ascii_diacritics",),
        palette="indigo", fonts=("din_cond", "avenir_demi", "avenir"), mark="hex",
        address_lines=("Rheinuferweg 8", "40213 Düsseldorf, Germany"),
        degrade=("lowres", "jpeg_mid"), fmt="jpg",
    ),
    Card(
        filename="card_18_glare_warp", layout="bilingual", difficulty="degraded",
        notes="Bilingual JP/EN card under a specular highlight from an overhead light, "
              "warped. The glare sits over the company line on the English half.",
        first_name="Aiko", last_name="Nakamura",
        job_title="Investor Relations Manager", company="Kaiyo Marine Holdings",
        location="Yokohama, Kanagawa, Japan", phone="+81 45 555 0187",
        email="a.nakamura@kaiyo-marine.example", website="kaiyo-marine.example",
        edge_cases=("bilingual_ja_en", "non_latin_script", "dark_card"),
        palette="dark_slate", fonts=("helv_bold", "helv_med", "helv"), mark="wave",
        address_lines=("3-2-1 Minatomirai, Nishi-ku", "Yokohama, Kanagawa 220-0012, Japan"),
        jp={
            "name": "中村 愛子",
            "title": "インベスター・リレーションズマネージャー",
            "company": "海洋マリンホールディングス株式会社",
            "addr1": "神奈川県横浜市西区みなとみらい3-2-1",
            "addr2": "電話：045-555-0187",
        },
        degrade=("warp", "glare"), fmt="jpg",
    ),
    Card(
        filename="card_19_motionblur_dark", layout="centered", difficulty="degraded",
        notes="Dark centred card with directional motion blur at ~20 degrees — the worst "
              "combination, because light-on-dark glyph strokes smear into the background.",
        first_name="Marcus", last_name="Oyelaran",
        job_title="Chief Revenue Officer", company="Silverpine Logistics",
        location="Chicago, IL, USA", phone="+1 (312) 555 0164",
        email="m.oyelaran@silverpine.example", website="silverpine.example",
        edge_cases=("dark_card",),
        palette="dark_plum", fonts=("futura_bold", "futura", "helv"), mark="split",
        address_lines=("310 W Kinzie Street, Floor 6", "Chicago, IL 60654, USA"),
        degrade=("motion_blur_hard",), fmt="jpg",
    ),
    Card(
        filename="card_20_skew_lowlight", layout="left_align", difficulty="degraded",
        notes="Compound surname with a locale twist (Indian given name, German married "
              "name), skewed 6 degrees and under-exposed. Also shares its employer with "
              "card_02/card_14 but from a different office.",
        first_name="Priyanka", last_name="Sharma-Vogel",
        job_title="Lead UX Researcher", company="Helix Meridian Labs",
        location="Berlin, Germany", phone="+49 30 5550 0178",
        email="p.sharma-vogel@helixmeridian.example", website="helixmeridian.example",
        edge_cases=("hyphenated_surname", "non_ascii_diacritics"),
        palette="navy_gold", fonts=("avenir_bold", "avenir_med", "avenir"), mark="diamond",
        address_lines=("Chausseestraße 117", "10115 Berlin, Germany"),
        degrade=("skew", "lowlight"), fmt="jpg",
    ),
]


# --------------------------------------------------------------------------------------
# Layout renderers. Each returns an RGBA card image at CARD_W*SS x CARD_H*SS.
# --------------------------------------------------------------------------------------

def _new_card(pal: dict[str, RGB], w: int = CARD_W, h: int = CARD_H) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (w * SS, h * SS), (*pal["bg"], 255))
    return img, ImageDraw.Draw(img)


def _paper(img: Image.Image, seed: int) -> Image.Image:
    """Add paper grain and a faint uneven-ink cast so the render is not vector-flat."""
    rng = np.random.default_rng(seed)
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    grain = rng.normal(0.0, 2.0, (h, w, 1)).astype(np.float32)
    low = rng.normal(0.0, 1.0, (h // 12 + 2, w // 12 + 2, 1)).astype(np.float32)
    low = cv2.resize(low, (w, h), interpolation=cv2.INTER_CUBIC)[:, :, None] * 1.1
    arr = np.clip(arr + grain + low, 0, 255)
    return Image.fromarray(arr.astype(np.uint8)).convert("RGBA")


def layout_left_align(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    rect(d, (0, 0, 14, CARD_H), fill=pal["acc"])
    logo(img, c.mark, CARD_W - 104, 102, 88, pal["acc"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)

    comp = c.printed_company()
    if comp:
        f = fit_font(fb, comp.upper(), 600, 17)
        track(d, (72 * SS, 108 * SS), comp.upper(), f, pal["ink"], spacing=1.6)
    if c.tagline:
        d.text((72 * SS, 132 * SS), c.tagline, font=font(fr, 13), fill=pal["mut"], anchor="ls")

    name = c.printed_name()
    d.text((72 * SS, 268 * SS), name, font=fit_font(fb, name, 640, 42), fill=pal["ink"], anchor="ls")
    y = 302
    for line in c.printed_titles():
        f = fit_font(fm, line, 620, 19)
        d.text((72 * SS, y * SS), line, font=f, fill=pal["acc"], anchor="ls")
        y += 26
    hairline(d, 72, y + 14, 240, y + 14, pal["acc2"], 2)

    y = CARD_H - 130
    for label, num in c.printed_phones():
        track(d, (72 * SS, y * SS), label, font(fb, 11), pal["acc"], spacing=0.6)
        d.text((96 * SS, y * SS), num, font=font(fr, 15), fill=pal["ink"], anchor="ls")
        y += 24
    if c.email:
        track(d, (72 * SS, y * SS), "E", font(fb, 11), pal["acc"], spacing=0.6)
        d.text((96 * SS, y * SS), c.email, font=fit_font(fr, c.email, 430, 15),
               fill=pal["ink"], anchor="ls")

    rx, ry = CARD_W - 72, CARD_H - 130
    for line in c.printed_address():
        d.text((rx * SS, ry * SS), line, font=fit_font(fr, line, 420, 14),
               fill=pal["mut"], anchor="rs")
        ry += 21
    web = c.printed_website()
    if web:
        d.text((rx * SS, (ry + 16) * SS), web, font=fit_font(fm, web, 420, 15),
               fill=pal["acc"], anchor="rs")
    return img


def layout_centered(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    mid = CARD_W / 2
    rect(d, (46, 40, CARD_W - 46, CARD_H - 40), outline=pal["acc2"], w=1.5)
    logo(img, c.mark, mid, 108, 70, pal["acc"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)

    name = c.printed_name()
    d.text((mid * SS, 196 * SS), name, font=fit_font(fb, name, 720, 38), fill=pal["ink"], anchor="ms")
    y = 226
    for line in c.printed_titles():
        track(d, (mid * SS, y * SS), line.upper(), fit_font(fm, line.upper(), 660, 13),
              pal["acc"], spacing=2.4, anchor="ms")
        y += 22
    hairline(d, mid - 46, y + 12, mid + 46, y + 12, pal["acc"], 1.5)
    y += 44

    comp = c.printed_company()
    if comp:
        d.text((mid * SS, y * SS), comp, font=fit_font(fb, comp, 700, 21), fill=pal["ink"], anchor="ms")
        y += 30
    for line in c.printed_address():
        d.text((mid * SS, y * SS), line, font=fit_font(fr, line, 720, 13), fill=pal["mut"], anchor="ms")
        y += 19

    parts = [n for _, n in c.printed_phones()]
    if c.email:
        parts.append(c.email)
    line = "   ·   ".join(parts)
    d.text((mid * SS, (CARD_H - 108) * SS), line, font=fit_font(fr, line, 830, 14),
           fill=pal["ink"], anchor="ms")
    web = c.printed_website()
    if web:
        track(d, (mid * SS, (CARD_H - 84) * SS), web.upper(), font(fm, 12), pal["acc"],
              spacing=2.0, anchor="ms")
    return img


def layout_split_panel(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    panel_w = 396
    rect(d, (0, 0, panel_w, CARD_H), fill=pal["acc2"])
    logo(img, c.mark, panel_w / 2, 190, 122, pal["acc"], pal["bg"], c.monogram)
    d = ImageDraw.Draw(img)
    comp = c.printed_company() or ""
    words = comp.split()
    lines = [" ".join(words[:2]), " ".join(words[2:])] if len(words) > 2 else [comp]
    y = 300
    for line in [l for l in lines if l]:
        d.text((panel_w / 2 * SS, y * SS), line, font=fit_font(fb, line, panel_w - 50, 23),
               fill=pal["ink"], anchor="ms")
        y += 32
    web = c.printed_website()
    if web:
        track(d, (panel_w / 2 * SS, (CARD_H - 56) * SS), web.upper(),
              fit_font(fm, web.upper(), panel_w - 60, 11), pal["acc"], spacing=1.8, anchor="ms")

    x = panel_w + 56
    name = c.printed_name()
    d.text((x * SS, 196 * SS), name, font=fit_font(fb, name, CARD_W - x - 46, 34),
           fill=pal["ink"], anchor="ls")
    y = 230
    for line in c.printed_titles():
        d.text((x * SS, y * SS), line, font=fit_font(fm, line, CARD_W - x - 46, 16),
               fill=pal["acc"], anchor="ls")
        y += 23
    hairline(d, x, y + 16, x + 70, y + 16, pal["acc"], 2)
    y += 56
    for label, num in c.printed_phones():
        track(d, (x * SS, y * SS), label, font(fb, 10), pal["mut"], spacing=0.5)
        d.text(((x + 24) * SS, y * SS), num, font=font(fr, 14), fill=pal["ink"], anchor="ls")
        y += 24
    if c.email:
        track(d, (x * SS, y * SS), "E", font(fb, 10), pal["mut"], spacing=0.5)
        d.text(((x + 24) * SS, y * SS), c.email,
               font=fit_font(fr, c.email, CARD_W - x - 70, 14), fill=pal["ink"], anchor="ls")
        y += 24
    y += 6
    for line in c.printed_address():
        d.text((x * SS, y * SS), line, font=fit_font(fr, line, CARD_W - x - 46, 12),
               fill=pal["mut"], anchor="ls")
        y += 18
    return img


def layout_logo_block(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    rect(d, (0, 0, CARD_W, 118), fill=pal["acc"])
    logo(img, c.mark, 82, 59, 62, pal["bg"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)
    comp = c.printed_company()
    if comp:
        track(d, (140 * SS, 68 * SS), comp.upper(), fit_font(fb, comp.upper(), 700, 22),
              pal["bg"], spacing=1.4)
    if c.tagline:
        d.text((140 * SS, 92 * SS), c.tagline, font=font(fr, 12), fill=pal["acc2"], anchor="ls")

    name = c.printed_name()
    d.text(((CARD_W - 64) * SS, 208 * SS), name, font=fit_font(fb, name, 620, 36),
           fill=pal["ink"], anchor="rs")
    y = 240
    for line in c.printed_titles():
        d.text(((CARD_W - 64) * SS, y * SS), line, font=fit_font(fm, line, 640, 17),
               fill=pal["acc"], anchor="rs")
        y += 24
    hairline(d, CARD_W - 64 - 90, y + 14, CARD_W - 64, y + 14, pal["acc2"], 2)

    y = CARD_H - 142
    rows: list[tuple[str, str]] = list(c.printed_phones())
    if c.email:
        rows.append(("E", c.email))
    web = c.printed_website()
    if web:
        rows.append(("W", web))
    for label, val in rows:
        track(d, (64 * SS, y * SS), label, font(fb, 11), pal["acc"], spacing=0.6)
        d.text((92 * SS, y * SS), val, font=fit_font(fr, val, 420, 14), fill=pal["ink"], anchor="ls")
        y += 24
    y = CARD_H - 142
    for line in c.printed_address():
        d.text(((CARD_W - 64) * SS, y * SS), line, font=fit_font(fr, line, 420, 13),
               fill=pal["mut"], anchor="rs")
        y += 20
    return img


def layout_minimal(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    logo(img, c.mark, 100, 100, 48, pal["ink"], pal["mut"], c.monogram)
    d = ImageDraw.Draw(img)
    name = c.printed_name()
    d.text((92 * SS, 322 * SS), name, font=fit_font(fb, name, 700, 40), fill=pal["ink"], anchor="ls")
    hairline(d, 92, 352, 168, 352, pal["ink"], 1.5)
    if c.email:
        d.text((92 * SS, 394 * SS), c.email, font=fit_font(fr, c.email, 700, 17),
               fill=pal["mut"], anchor="ls")
    return img


def layout_dense_corporate(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    rect(d, (0, 0, CARD_W, 8), fill=pal["acc"])
    logo(img, c.mark, 74, 74, 56, pal["acc"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)
    comp = c.printed_company()
    if comp:
        track(d, (120 * SS, 72 * SS), comp.upper(), fit_font(fb, comp.upper(), 620, 20),
              pal["ink"], spacing=1.2)
    hairline(d, 48, 108, CARD_W - 48, 108, pal["acc2"], 1)

    name = c.printed_name()
    d.text((48 * SS, 196 * SS), name, font=fit_font(fb, name, 560, 32), fill=pal["ink"], anchor="ls")
    y = 224
    for line in c.printed_titles():
        track(d, (48 * SS, y * SS), line.upper(), fit_font(fm, line.upper(), 560, 12),
              pal["acc"], spacing=2.0)
        y += 20

    y = 300
    rows: list[tuple[str, str]] = list(c.printed_phones())
    if c.email:
        rows.append(("E", c.email))
    web = c.printed_website()
    if web:
        rows.append(("W", web))
    labels = {"T": "Tel", "M": "Mob", "F": "Fax", "E": "Email", "W": "Web"}
    for label, val in rows:
        d.text((48 * SS, y * SS), labels.get(label, label), font=font(fm, 12),
               fill=pal["mut"], anchor="ls")
        d.text((110 * SS, y * SS), val, font=fit_font(fr, val, 420, 14), fill=pal["ink"], anchor="ls")
        y += 27

    ax, ay = CARD_W - 48, 304
    track(d, (ax * SS, (ay - 24) * SS), "OFFICE", font(fb, 10), pal["acc"], spacing=2.0, anchor="rs")
    for line in c.printed_address():
        d.text((ax * SS, ay * SS), line, font=fit_font(fr, line, 380, 13), fill=pal["mut"], anchor="rs")
        ay += 21
    if c.tagline:
        hairline(d, 48, CARD_H - 52, CARD_W - 48, CARD_H - 52, pal["acc2"], 1)
        d.text((48 * SS, (CARD_H - 28) * SS), c.tagline, font=font(fr, 11), fill=pal["mut"], anchor="ls")
    return img


def layout_portrait(c: Card, pal: dict[str, RGB]) -> Image.Image:
    w, h = CARD_H, CARD_W
    img, d = _new_card(pal, w, h)
    fb, fm, fr = c.fonts
    mid = w / 2
    rect(d, (0, 0, w, 6), fill=pal["acc"])
    rect(d, (0, h - 6, w, h), fill=pal["acc"])
    logo(img, c.mark, mid, 156, 116, pal["acc"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)
    comp = c.printed_company()
    if comp:
        track(d, (mid * SS, 286 * SS), comp.upper(), fit_font(fb, comp.upper(), w - 70, 19),
              pal["ink"], spacing=2.6, anchor="ms")
    hairline(d, mid - 40, 318, mid + 40, 318, pal["acc"], 2)

    name = c.printed_name()
    d.text((mid * SS, 452 * SS), name, font=fit_font(fb, name, w - 60, 34), fill=pal["ink"], anchor="ms")
    y = 486
    for line in c.printed_titles():
        d.text((mid * SS, y * SS), line, font=fit_font(fm, line, w - 60, 16), fill=pal["acc"], anchor="ms")
        y += 24

    y = 700
    for _, num in c.printed_phones():
        d.text((mid * SS, y * SS), num, font=font(fr, 15), fill=pal["ink"], anchor="ms")
        y += 27
    if c.email:
        d.text((mid * SS, y * SS), c.email, font=fit_font(fr, c.email, w - 56, 15),
               fill=pal["ink"], anchor="ms")
        y += 27
    web = c.printed_website()
    if web:
        d.text((mid * SS, y * SS), web, font=fit_font(fm, web, w - 56, 15), fill=pal["acc"], anchor="ms")
        y += 27
    hairline(d, mid - 60, y + 14, mid + 60, y + 14, pal["acc2"], 1)
    y += 52
    for line in c.printed_address():
        d.text((mid * SS, y * SS), line, font=fit_font(fr, line, w - 50, 12), fill=pal["mut"], anchor="ms")
        y += 20
    return img


def layout_qr_block(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    rect(d, (0, 0, CARD_W, CARD_H), outline=pal["acc2"], w=1)
    logo(img, c.mark, 76, 78, 54, pal["acc"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)

    payload = (f"MECARD:N:{c.last_name},{c.first_name};"
               f"TEL:{''.join(ch for ch in (c.phone or '') if ch.isdigit() or ch == '+')};"
               f"EMAIL:{c.email};URL:https://{c.website};;")
    qr = qrcode.QRCode(version=None, box_size=1, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=pal["ink"], back_color=pal["bg"]).convert("RGBA")
    # Integer module scaling only: a fractional resize smears module edges and the code
    # stops decoding once JPEG/low-light degradation is layered on top.
    scale = max(1, round(216 * SS / qr_img.width))
    qs = qr_img.width * scale
    qr_img = qr_img.resize((qs, qs), Image.NEAREST)
    qx, qy = int((CARD_W - 76) * SS) - qs, int(168 * SS)
    img.alpha_composite(qr_img, (qx, qy))
    d = ImageDraw.Draw(img)
    track(d, ((qx + qs / 2), (qy + qs + 28 * SS)), "SCAN TO SAVE", font(fm, 10),
          pal["mut"], spacing=2.2, anchor="ms")

    name = c.printed_name()
    d.text((76 * SS, 196 * SS), name, font=fit_font(fb, name, 600, 34), fill=pal["ink"], anchor="ls")
    y = 226
    for line in c.printed_titles():
        d.text((76 * SS, y * SS), line, font=fit_font(fm, line, 600, 16), fill=pal["acc"], anchor="ls")
        y += 23
    comp = c.printed_company()
    if comp:
        track(d, (76 * SS, (y + 14) * SS), comp.upper(), fit_font(fb, comp.upper(), 600, 15),
              pal["ink"], spacing=1.8)
        y += 40
    y = CARD_H - 152
    for _, num in c.printed_phones():
        d.text((76 * SS, y * SS), num, font=font(fr, 14), fill=pal["ink"], anchor="ls")
        y += 22
    if c.email:
        d.text((76 * SS, y * SS), c.email, font=fit_font(fr, c.email, 560, 14), fill=pal["ink"], anchor="ls")
        y += 22
    web = c.printed_website()
    if web:
        d.text((76 * SS, y * SS), web, font=font(fm, 14), fill=pal["acc"], anchor="ls")
        y += 22
    for line in c.printed_address():
        d.text((76 * SS, y * SS), line, font=fit_font(fr, line, 560, 12), fill=pal["mut"], anchor="ls")
        y += 18
    return img


def layout_bilingual(c: Card, pal: dict[str, RGB]) -> Image.Image:
    img, d = _new_card(pal)
    fb, fm, fr = c.fonts
    rect(d, (0, 0, CARD_W, CARD_H), fill=pal["bg"])
    rect(d, (CARD_W - 10, 0, CARD_W, CARD_H), fill=pal["acc"])
    hairline(d, 54, 300, CARD_W - 54, 300, pal["acc2"], 1)
    logo(img, c.mark, 88, 78, 56, pal["acc"], pal["acc2"], c.monogram)
    d = ImageDraw.Draw(img)

    comp = c.printed_company()
    if comp:
        track(d, (136 * SS, 76 * SS), comp.upper(), fit_font(fb, comp.upper(), 640, 17),
              pal["ink"], spacing=1.6)
    name = c.printed_name()
    d.text((54 * SS, 176 * SS), name, font=fit_font(fb, name, 520, 32), fill=pal["ink"], anchor="ls")
    y = 206
    for line in c.printed_titles():
        d.text((54 * SS, y * SS), line, font=fit_font(fm, line, 520, 15), fill=pal["acc"], anchor="ls")
        y += 22
    ay = 150
    for line in c.printed_address():
        d.text(((CARD_W - 54) * SS, ay * SS), line, font=fit_font(fr, line, 430, 12),
               fill=pal["mut"], anchor="rs")
        ay += 19
    for label, num in c.printed_phones():
        d.text(((CARD_W - 54) * SS, ay * SS), f"{label}  {num}", font=font(fr, 13),
               fill=pal["ink"], anchor="rs")
        ay += 20
    if c.email:
        d.text(((CARD_W - 54) * SS, ay * SS), c.email, font=fit_font(fr, c.email, 430, 13),
               fill=pal["ink"], anchor="rs")
        ay += 20
    web = c.printed_website()
    if web:
        d.text(((CARD_W - 54) * SS, ay * SS), web, font=font(fm, 13), fill=pal["acc"], anchor="rs")

    jp = c.jp
    d.text((54 * SS, 392 * SS), jp["company"], font=fit_font("jp_bold", jp["company"], 520, 17),
           fill=pal["ink"], anchor="ls")
    d.text((54 * SS, 458 * SS), jp["name"], font=fit_font("jp_bold", jp["name"], 380, 30),
           fill=pal["ink"], anchor="ls")
    d.text((54 * SS, 496 * SS), jp["title"], font=fit_font("jp", jp["title"], 520, 14),
           fill=pal["acc"], anchor="ls")
    d.text(((CARD_W - 54) * SS, 428 * SS), jp["addr1"], font=fit_font("jp", jp["addr1"], 420, 13),
           fill=pal["mut"], anchor="rs")
    d.text(((CARD_W - 54) * SS, 456 * SS), jp["addr2"], font=fit_font("jp", jp["addr2"], 420, 13),
           fill=pal["mut"], anchor="rs")
    return img


LAYOUTS: dict[str, Callable[[Card, dict[str, RGB]], Image.Image]] = {
    "left_align": layout_left_align,
    "centered": layout_centered,
    "split_panel": layout_split_panel,
    "logo_block": layout_logo_block,
    "minimal": layout_minimal,
    "dense_corporate": layout_dense_corporate,
    "portrait": layout_portrait,
    "qr_block": layout_qr_block,
    "bilingual": layout_bilingual,
}


# --------------------------------------------------------------------------------------
# Capture simulation
# --------------------------------------------------------------------------------------

SURFACES: tuple[RGB, ...] = ((214, 210, 202), (188, 180, 168), (122, 124, 128),
                             (166, 150, 130), (232, 230, 226))


def _surface(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    base = np.array(SURFACES[int(rng.integers(0, len(SURFACES)))], dtype=np.float32)
    img = np.repeat(np.repeat(base[None, None, :], h, 0), w, 1)
    coarse = rng.normal(0, 1, (h // 24 + 2, w // 24 + 2)).astype(np.float32)
    coarse = cv2.resize(coarse, (w, h), interpolation=cv2.INTER_CUBIC)[:, :, None] * 7.0
    fine = rng.normal(0, 3.0, (h, w, 1)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    grad = ((xx / w) * 0.10 + (yy / h) * 0.06 - 0.08)[:, :, None] * 255.0
    return np.clip(img + coarse + fine + grad, 0, 255)


def _mount(card: np.ndarray, rng: np.random.Generator, ops: Iterable[str]) -> np.ndarray:
    """Place the flat card on a surface, optionally warping/skewing it as a photo would."""
    ops = set(ops)
    ch, cw = card.shape[:2]
    pad = int(max(cw, ch) * 0.12)
    W, H = cw + 2 * pad, ch + 2 * pad
    bg = _surface(W, H, rng)

    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    if "warp_hard" in ops:
        j = 0.16
    elif "warp" in ops:
        j = 0.085
    else:
        j = 0.0
    dst = np.float32([[pad, pad], [pad + cw, pad], [pad + cw, pad + ch], [pad, pad + ch]])
    if j > 0:
        # Bias the jitter so one edge recedes: that is what a hand-held oblique shot does,
        # rather than the symmetric wobble you get from independent per-corner noise.
        tilt = rng.choice([0, 1, 2, 3])
        for i in range(4):
            amp = j if (i in ((0, 1), (1, 2), (2, 3), (3, 0))[tilt]) else j * 0.35
            dst[i, 0] += rng.normal(0, amp) * cw * 0.55
            dst[i, 1] += rng.normal(0, amp) * ch * 0.55
    if "skew" in ops:
        ang = math.radians(rng.uniform(4.5, 8.0) * rng.choice([-1, 1]))
        cx, cy = W / 2, H / 2
        ca, sa = math.cos(ang), math.sin(ang)
        dst = np.float32([[cx + (x - cx) * ca - (y - cy) * sa,
                           cy + (x - cx) * sa + (y - cy) * ca] for x, y in dst])

    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(card, M, (W, H), flags=cv2.INTER_LANCZOS4,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    mask = cv2.warpPerspective(np.ones((ch, cw), np.float32), M, (W, H), flags=cv2.INTER_LINEAR)
    mask = np.clip(mask, 0, 1)[:, :, None]

    if "shadow" in ops or "warp" in ops or "warp_hard" in ops or "skew" in ops:
        off = int(max(W, H) * 0.012)
        sh = cv2.GaussianBlur(mask[:, :, 0], (0, 0), max(W, H) * 0.012)
        sh = np.roll(np.roll(sh, off, axis=0), off, axis=1)[:, :, None]
        bg = bg * (1.0 - 0.45 * sh)

    out = bg * (1 - mask) + warped * mask
    if "hard_shadow" in ops:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        edge = 0.38 * W + rng.uniform(-0.05, 0.05) * W
        slope = math.tan(math.radians(rng.uniform(60, 78)))
        t = np.clip((xx + (yy - H / 2) / slope - edge) / (W * 0.05), 0, 1)[:, :, None]
        out = out * (1.0 - 0.42 * t)
    return np.clip(out, 0, 255)


def _degrade(img: np.ndarray, rng: np.random.Generator, ops: Iterable[str]) -> np.ndarray:
    out = img.astype(np.float32)
    for op in ops:
        if op == "motion_blur" or op == "motion_blur_hard":
            # Kernels much above ~13 px at this raster size destroy the glyphs outright,
            # which tests nothing useful: the target is "hard but recoverable".
            k = 9 if op == "motion_blur" else 13
            ang = rng.uniform(0, 180) if op == "motion_blur" else 20.0
            kern = np.zeros((k, k), np.float32)
            kern[k // 2, :] = 1.0
            R = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), ang, 1.0)
            kern = cv2.warpAffine(kern, R, (k, k))
            kern /= kern.sum()
            out = cv2.filter2D(out, -1, kern)
        elif op in ("lowlight", "lowlight_hard"):
            stops = 1.6 if op == "lowlight" else 2.5
            gain = 2.0 ** -stops
            out = out * gain
            out = 255.0 * np.power(np.clip(out / 255.0, 0, 1), 0.78)  # in-camera tone curve lift
            out[:, :, 2] *= 1.06                                      # tungsten-ish cast
            out[:, :, 0] *= 0.97
            out = out + rng.normal(0, 3.5 if op == "lowlight" else 6.0, out.shape)
        elif op == "grain":
            lum = out.mean(axis=2, keepdims=True) / 255.0
            out = out + rng.normal(0, 1, out.shape) * (3.0 + 9.0 * (1.0 - lum))
        elif op == "glare":
            h, w = out.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            gx, gy = w * rng.uniform(0.30, 0.50), h * rng.uniform(0.30, 0.45)
            r2 = ((xx - gx) / (w * 0.30)) ** 2 + ((yy - gy) / (h * 0.16)) ** 2
            out = out + (np.exp(-r2 * 2.2) * 165.0)[:, :, None]
        elif op == "lowres":
            h, w = out.shape[:2]
            s = 620.0 / max(h, w)
            small = cv2.resize(out, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            out = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(out, 0, 255)


JPEG_Q = {"jpeg_hard": 16, "jpeg_mid": 28}


def render(c: Card) -> tuple[Image.Image, dict[str, object]]:
    seed = zlib.crc32(c.filename.encode())  # stable across interpreter runs, unlike hash()
    rng = np.random.default_rng(seed)
    pal = PALETTES[c.palette]
    card = LAYOUTS[c.layout](c, pal)
    card = _paper(card, seed)
    card = card.convert("RGB").resize(
        (card.width // SS, card.height // SS), Image.LANCZOS)
    arr = np.asarray(card, dtype=np.float32)

    ops = set(c.degrade)
    if ops & {"warp", "warp_hard", "skew", "shadow", "hard_shadow"} or c.fmt == "jpg":
        arr = _mount(arr, rng, ops)
    else:
        # "Flat scan" presentation: a thin neutral border, as a sheet-fed scanner produces.
        pad = 18
        bg = np.full((arr.shape[0] + 2 * pad, arr.shape[1] + 2 * pad, 3), 246.0, np.float32)
        bg[pad:pad + arr.shape[0], pad:pad + arr.shape[1]] = arr
        arr = bg
    arr = _degrade(arr, rng, c.degrade)

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    ext = c.fmt
    save_kwargs: dict[str, object] = {}
    if ext == "jpg":
        q = 84 if "grain" in c.degrade else 92
        for op in c.degrade:
            if op in JPEG_Q:
                q = JPEG_Q[op]
        save_kwargs = {"quality": q, "subsampling": 2 if q < 40 else 0}
    elif ext == "webp":
        save_kwargs = {"quality": 95}
    return img, {"ext": ext, "save": save_kwargs}


# --------------------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------------------

def e164(printed: str | None) -> str | None:
    if not printed:
        return None
    digits = "".join(ch for ch in printed if ch.isdigit())
    return "+" + digits if printed.strip().startswith("+") else digits


def ground_truth_entry(c: Card, filename: str) -> dict[str, object]:
    return {
        "filename": filename,
        "difficulty": c.difficulty,
        "edge_cases": list(c.edge_cases),
        "degradations": list(c.degrade),
        "notes": c.notes,
        "layout": c.layout,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "job_title": c.job_title,
        "company": c.company,
        "location": c.location,
        "phone": c.phone,
        "phone_e164": e164(c.phone),
        "email": c.email,
        "website": c.website,
        "printed": {
            "name": c.printed_name(),
            "title_lines": list(c.printed_titles()),
            "company": c.printed_company(),
            "address_lines": list(c.printed_address()),
            "phones": [list(p) for p in c.printed_phones()],
            "email": c.email,
            "website": c.printed_website(),
            "tagline": c.tagline,
            "non_latin": c.jp or None,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", help="render only these filenames (no extension)")
    args = ap.parse_args()

    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for c in CARDS:
        filename = f"{c.filename}.{c.fmt}"
        entries.append(ground_truth_entry(c, filename))
        if args.only and c.filename not in args.only:
            continue
        img, meta = render(c)
        out = CARDS_DIR / filename
        img.save(out, **meta["save"])
        print(f"{out.name:38s} {img.width:4d}x{img.height:<4d} "
              f"{out.stat().st_size / 1024:7.1f} KB  {c.difficulty}")

    GT_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {GT_PATH.relative_to(HERE.parent)} ({len(entries)} records)")


if __name__ == "__main__":
    main()
