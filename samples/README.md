# LeadForge sample corpus

20 synthetic business cards with exact ground truth, plus a scorer. Everything here is
generated — there is no scraped data, no real person and no real company anywhere in this
directory.

```
cards/                 20 rendered cards (8 clean captures, 12 degraded)
ground_truth.json      expected extraction for every card, keyed by filename
generate_cards.py      the renderer; regenerates cards/ and ground_truth.json
evaluate.py            scores a batch export against ground_truth.json
card_0{1,2,3}.png      unrelated GPU-benchmark fixtures, see infra/modal/README.md
```

The evaluation corpus is everything under `cards/`. The three loose `card_0N.png` files at
this level are the fixed inputs for the Modal cold-start/throughput benchmark and are not
part of it — `ground_truth.json` does not reference them.

## The corpus

Cards are 3.5 x 2 in at 300 dpi (1050 x 600 px) rasterised at 2x and downsampled, then
either laid flat as a scan or composited onto a surface and photographed. Nine layouts are
used: left-aligned, centred serif, split panel, logo block, minimalist, dense corporate,
portrait, QR block and bilingual. Countries represented: India, USA, UAE, UK, Japan,
Germany, Singapore.

`difficulty` describes the **capture**; `edge_cases` describes the **content**. They are
independent axes, so a card can be a clean capture of very hard content.

| file | px | difficulty | degradations | edge cases |
|---|---|---|---|---|
| `card_01_left_align_clean.png` | 1086x636 | clean | — | — |
| `card_02_centered_serif_clean.png` | 1086x636 | edge | — | hyphenated_surname, centred_layout |
| `card_03_dark_panel_clean.png` | 1086x636 | edge | — | honorific_prefix, post_nominal, dark_card, hyphenated_surname |
| `card_04_minimal_clean.webp` | 1086x636 | edge | — | minimal_name_email_only, mostly_null_fields |
| `card_05_bilingual_jp_clean.png` | 1086x636 | edge | — | bilingual_ja_en, non_latin_script |
| `card_06_title_wrap_clean.png` | 1086x636 | edge | — | job_title_wraps_two_lines |
| `card_07_two_phones_clean.png` | 1086x636 | edge | — | two_phone_numbers, fax_number, family_name_first |
| `card_08_no_email_clean.png` | 1086x636 | edge | — | no_email |
| `card_09_portrait_warp.jpg` | 852x1302 | degraded | warp, shadow | portrait_orientation |
| `card_10_qr_lowlight.jpg` | 1302x852 | degraded | lowlight | qr_code, honorific_prefix |
| `card_11_multiline_addr_blur.jpg` | 1302x852 | degraded | motion_blur | multiline_international_address |
| `card_12_suffix_jpeg.jpg` | 1302x852 | degraded | jpeg_hard | generational_suffix |
| `card_13_dense_skew_shadow.jpg` | 1302x852 | degraded | skew, hard_shadow | dense_layout, two_phone_numbers |
| `card_14_dup_of_02_blur.jpg` | 1302x852 | degraded | warp, motion_blur | duplicate_of_card_02, hyphenated_surname |
| `card_15_dark_warp.jpg` | 1302x852 | degraded | warp_hard, shadow | dark_card |
| `card_16_lowlight_grain.jpg` | 1302x852 | degraded | lowlight_hard, grain | — |
| `card_17_lowres_jpeg.jpg` | 1302x852 | degraded | lowres, jpeg_mid | non_ascii_diacritics |
| `card_18_glare_warp.jpg` | 1302x852 | degraded | warp, glare | bilingual_ja_en, non_latin_script, dark_card |
| `card_19_motionblur_dark.jpg` | 1302x852 | degraded | motion_blur_hard | dark_card |
| `card_20_skew_lowlight.jpg` | 1302x852 | degraded | skew, lowlight | hyphenated_surname, non_ascii_diacritics |

Three formats are present on purpose (`.png`, `.jpg`, `.webp`) so the upload path's
accepted-type handling is exercised by the corpus itself.

Notes on the deliberate traps:

- **`card_04`** carries a name and an email and nothing else. Six of the eight fields must
  come back `null`; a model that invents a company here is worse than one that returns
  nothing.
- **`card_07`** prints mobile, office and fax. The contract has one `phone` field, so the
  ground truth is the mobile.
- **`card_14`** is a second, blurrier capture of the same physical card as `card_02`. Both
  score independently, and together they exercise `duplicate_of`.
- **`card_10`**'s QR code is real (MECARD payload). It decodes with
  `cv2.QRCodeDetector` after auto-levelling — the frame as shipped is 1.6 stops
  under-exposed by design, so a raw decode on the unmodified pixels fails.
- **`card_20`** shares an employer with `card_02`/`card_14` but from a different office, so
  "same company" must not imply "same lead".

## Ground truth

`ground_truth.json` is an array, one object per card:

```jsonc
{
  "filename": "card_01_left_align_clean.png",
  "difficulty": "clean",                  // clean | edge | degraded  (capture quality)
  "edge_cases": [],                       // content-difficulty tags
  "degradations": [],                     // capture ops applied by the renderer
  "notes": "why this card is hard",
  "layout": "left_align",

  "first_name": "Aarav",                  // the eight scored fields; null means
  "last_name": "Deshmukh",                // "not present on the card"
  "job_title": "Head of Platform Engineering",
  "company": "Northwind Robotics",
  "location": "Bengaluru, KA, India",
  "phone": "+91 80 4555 0118",            // exactly as printed
  "phone_e164": "+918045550118",
  "email": "aarav.deshmukh@northwind.example",
  "website": "northwind.example",         // bare domain, see below

  "printed": { /* every string that was rasterised onto the card */ }
}
```

The `printed` block is the audit trail: `name`, `title_lines`, `company`,
`address_lines`, `phones`, `email`, `website`, `tagline` and `non_latin` are literally the
strings passed to the text renderer. Both the images and the ground truth are emitted from
one record in `generate_cards.py`, so they cannot drift apart.

Two conventions where "as printed" and "what the CRM field should hold" differ:

- **`website`** is the bare registrable domain. Cards that print `www.northwind.example`
  still have `"website": "northwind.example"`; the scorer strips scheme, `www.` and any
  trailing slash before comparing.
- **`location`** is the locality, not the full postal address. `card_11` prints four
  address lines but its `location` is `"Hamburg, Germany"`; the full printed block is in
  `printed.address_lines`.

## Regenerating

```bash
pip install pillow numpy opencv-python-headless qrcode
python generate_cards.py                       # all 20 + ground_truth.json
python generate_cards.py --only card_10_qr_lowlight
```

Deterministic: each card seeds its RNG from `crc32(filename)`, so re-running produces
byte-identical output on the same Pillow/OpenCV versions.

Fonts come from the macOS system set (Helvetica Neue, Avenir Next, Futura, Gill Sans,
Baskerville, Didot, Charter, Iowan Old Style, American Typewriter, Copperplate, DIN,
Georgia, Arial Narrow, and **Hiragino Sans** for the Japanese cards). They are referenced
by absolute path and are **not** redistributed with this repo. On a machine without them,
`generate_cards.py` will fail loudly at font-load time rather than silently substituting —
adjust the `FONTS` table to point at whatever faces are available, and make sure the
replacement for `jp`/`jp_bold` has kana and kanji coverage or `card_05`/`card_18` will
render tofu boxes.

## Scoring an extraction run

```bash
python evaluate.py batch.json            # GET /api/v1/batches/{id} response body
python evaluate.py export.csv            # GET /api/v1/batches/{id}/export.csv
python evaluate.py export.xlsx           # needs openpyxl
python evaluate.py --self-test           # synthetic imperfect predictions, proves the harness
python evaluate.py batch.json --errors --json result.json
```

Stdlib only (openpyxl is needed only for `.xlsx`). Cards are joined on `filename`; CSV and
XLSX headers are matched case- and punctuation-insensitively against a small alias table,
so `First Name`, `first_name` and `firstname` all resolve.

Per field, per card, the outcome is one of:

| outcome | ground truth | prediction | counts as |
|---|---|---|---|
| `tp` | present | present and equal | true positive |
| `tn` | null | null | true negative |
| `fn` | present | null | false negative |
| `fp` | null | present | false positive |
| `wrong` | present | present but different | false positive **and** false negative |

Normalisation before comparison: text is NFKC-normalised, case-folded,
whitespace-collapsed and stripped of surrounding punctuation; email is case-folded with
`mailto:` removed; phone is reduced to E.164 digits, tolerating a missing country code
(`020 7946 0132` matches `+44 20 7946 0132`); website has scheme, `www.` and trailing
slash removed.

The report gives precision / recall / F1 / accuracy per field, micro and macro aggregates,
an exact-card rate (all eight fields right), and a breakdown by `difficulty`. Example
output from `--self-test`:

```
field        support    prec  recall      F1     acc   TP   FP   FN   TN
--------------------------------------------------------------------------------
first_name        20   88.2%   75.0%   81.1%   75.0%   15    2    5    0
last_name         20  100.0%   95.0%   97.4%   95.0%   19    0    1    0
...
MICRO                  91.5%   83.8%   87.5%   83.8%
MACRO F1                               87.3%

exact-card rate (all 8 fields right):  30.0%
```

## Licensing and provenance

Every image in `cards/` was rendered by `generate_cards.py` in this repository. There is no
third-party imagery, no photograph of a real card, and no scraped or licensed dataset.

All names, job titles, companies and addresses are invented. Domains use the IANA-reserved
`.example` TLD (RFC 2606) and therefore cannot resolve. Phone numbers use ranges reserved
for fiction by the relevant regulators — NANP `555-01xx`, Ofcom `07700 900xxx` and
`0xxx 496 0xxx` — or otherwise unassigned blocks, so none of them can be dialled. Any
resemblance to a real person or organisation is coincidental.

The generated images and `ground_truth.json` are covered by this repository's licence. The
system fonts used during rendering are not redistributed here and remain under their own
licences.
