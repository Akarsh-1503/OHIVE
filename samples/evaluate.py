#!/usr/bin/env python3
"""Score a LeadForge batch export against ``ground_truth.json``.

Accepts whatever the API already produces, so no glue code is needed:

    python evaluate.py batch.json          # GET /api/v1/batches/{id} response
    python evaluate.py export.csv          # GET /api/v1/batches/{id}/export.csv
    python evaluate.py export.xlsx         # GET /api/v1/batches/{id}/export.xlsx
    python evaluate.py --self-test         # synthesise predictions and score those

Cards are matched to ground truth by ``filename``. Comparison is normalised per field
type, not raw-string: the extractor should not be punished for printing a phone number
with different spacing than the card used.

    text     NFKC, case-folded, whitespace-collapsed, surrounding punctuation stripped
    email    case-folded, ``mailto:`` stripped
    phone    reduced to E.164 digits; a missing country code is tolerated
    website  scheme, leading ``www.`` and trailing slash stripped, case-folded

Only openpyxl (for .xlsx input) is optional; everything else is stdlib.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
DEFAULT_GT = HERE / "ground_truth.json"

FIELDS = ("first_name", "last_name", "job_title", "company",
          "location", "phone", "email", "website")

# Header aliases so a CSV/XLSX export with prettified column names still lines up.
ALIASES: dict[str, str] = {
    "file": "filename", "filename": "filename", "file_name": "filename",
    "image": "filename", "image_name": "filename", "card": "filename",
    "source_file": "filename",
    "first": "first_name", "first_name": "first_name", "firstname": "first_name",
    "given_name": "first_name",
    "last": "last_name", "last_name": "last_name", "lastname": "last_name",
    "surname": "last_name", "family_name": "last_name",
    "title": "job_title", "job_title": "job_title", "jobtitle": "job_title",
    "role": "job_title", "position": "job_title",
    "company": "company", "organisation": "company", "organization": "company",
    "employer": "company", "company_name": "company",
    "location": "location", "city": "location", "address": "location",
    "phone": "phone", "phone_number": "phone", "telephone": "phone",
    "tel": "phone", "mobile": "phone", "phone_e164": "phone",
    "email": "email", "email_address": "email", "e_mail": "email",
    "website": "website", "web": "website", "url": "website", "domain": "website",
}

_WS = re.compile(r"\s+")
_TRIM = re.compile(r"^[\s -⁯\"'`(\[.,;:\-–—]+|[\s -⁯\"'`)\].,;:]+$")


def _slug(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def norm_text(v: str) -> str:
    s = unicodedata.normalize("NFKC", v)
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("–", "-").replace("—", "-").replace("‑", "-")
    s = _WS.sub(" ", s)
    s = _TRIM.sub("", s)
    return s.casefold()


def norm_email(v: str) -> str:
    s = unicodedata.normalize("NFKC", v).strip()
    if s.lower().startswith("mailto:"):
        s = s[7:]
    return s.strip().casefold()


def phone_digits(v: str) -> str:
    s = unicodedata.normalize("NFKC", v)
    digits = re.sub(r"\D", "", s)
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def norm_website(v: str) -> str:
    s = unicodedata.normalize("NFKC", v).strip().casefold()
    s = re.sub(r"^[a-z]+://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


def match_text(gt: str, pred: str) -> bool:
    return norm_text(gt) == norm_text(pred)


def match_email(gt: str, pred: str) -> bool:
    return norm_email(gt) == norm_email(pred)


def match_website(gt: str, pred: str) -> bool:
    return norm_website(gt) == norm_website(pred)


def match_phone(gt: str, pred: str) -> bool:
    a, b = phone_digits(gt), phone_digits(pred)
    if not a or not b:
        return False
    if a == b:
        return True
    # One side may omit the country code (e.g. "020 7946 0132" for "+44 20 7946 0132").
    # A country code is 1-4 digits, and we require a long enough national part that the
    # suffix test cannot fire by accident.
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 7 and 0 < len(long_) - len(short) <= 4 and long_.endswith(short)


MATCHERS: dict[str, Callable[[str, str], bool]] = {
    "phone": match_phone, "email": match_email, "website": match_website,
}


def blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (
        isinstance(v, str) and v.strip().lower() in {"null", "none", "n/a", "-"})


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

def load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _rows_from_json(json.loads(path.read_text(encoding="utf-8")))
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return _map_headers(list(csv.DictReader(fh)))
    if suffix in (".xlsx", ".xlsm"):
        return _map_headers(_rows_from_xlsx(path))
    raise SystemExit(f"unsupported prediction format: {path.suffix}")


def _rows_from_json(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, dict):
        for key in ("leads", "results", "cards", "data"):
            if isinstance(doc.get(key), list):
                doc = doc[key]
                break
        else:
            raise SystemExit("JSON object has no 'leads' array")
    if not isinstance(doc, list):
        raise SystemExit("expected a Batch object or a list of Leads")
    return [{k: v for k, v in row.items()} for row in doc]


def _rows_from_xlsx(path: Path) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise SystemExit("reading .xlsx needs openpyxl: pip install openpyxl") from exc
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [str(c) if c is not None else "" for c in next(it)]
    return [dict(zip(header, row, strict=False)) for row in it]


def _map_headers(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        mapped: dict[str, Any] = {}
        for key, value in row.items():
            canon = ALIASES.get(_slug(str(key)))
            # phone_e164 must not clobber a real 'phone' column that came earlier.
            if canon and (canon not in mapped or blank(mapped[canon])):
                mapped[canon] = value
        out.append(mapped)
    return out


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------

class Tally:
    # `n` is counted separately from tp/fp/fn/tn because a wrong non-null prediction
    # increments both fp and fn, so those four would otherwise over-count the cards.
    __slots__ = ("tp", "fp", "fn", "tn", "n")

    def __init__(self) -> None:
        self.tp = self.fp = self.fn = self.tn = self.n = 0

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def precision(self) -> float | None:
        d = self.tp + self.fp
        return self.tp / d if d else None

    @property
    def recall(self) -> float | None:
        d = self.tp + self.fn
        return self.tp / d if d else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return 0.0 if (p is not None and r is not None) else None
        return 2 * p * r / (p + r)

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.n if self.n else 0.0


def score(gt_records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_file: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("filename")
        if name:
            by_file[str(name).strip()] = row
    # Tolerate an export that dropped the extension or prefixed a directory.
    by_stem = {Path(k).stem: v for k, v in by_file.items()}

    tallies = {f: Tally() for f in FIELDS}
    per_card: list[dict[str, Any]] = []
    missing: list[str] = []

    for gt in gt_records:
        filename = str(gt["filename"])
        pred = by_file.get(filename) or by_stem.get(Path(filename).stem)
        if pred is None:
            missing.append(filename)
            pred = {}
        card: dict[str, Any] = {"filename": filename, "difficulty": gt.get("difficulty"),
                                "fields": {}, "correct": 0, "scored": 0}
        for f in FIELDS:
            g, p = gt.get(f), pred.get(f)
            g_blank, p_blank = blank(g), blank(p)
            t = tallies[f]
            t.n += 1
            if g_blank and p_blank:
                outcome = "tn"
                t.tn += 1
            elif g_blank:
                outcome = "fp"
                t.fp += 1
            elif p_blank:
                outcome = "fn"
                t.fn += 1
            else:
                ok = MATCHERS.get(f, match_text)(str(g), str(p))
                if ok:
                    outcome = "tp"
                    t.tp += 1
                else:
                    # A wrong non-null value is simultaneously a miss and a false alarm.
                    outcome = "wrong"
                    t.fp += 1
                    t.fn += 1
            card["fields"][f] = {"expected": g, "predicted": p, "outcome": outcome}
            card["scored"] += 1
            card["correct"] += 1 if outcome in ("tp", "tn") else 0
        card["exact"] = card["correct"] == card["scored"]
        per_card.append(card)

    micro = Tally()
    for t in tallies.values():
        micro.tp += t.tp
        micro.fp += t.fp
        micro.fn += t.fn
        micro.tn += t.tn
        micro.n += t.n
    f1s = [t.f1 for t in tallies.values() if t.f1 is not None]

    by_difficulty: dict[str, dict[str, float]] = {}
    for tag in sorted({str(c["difficulty"]) for c in per_card}):
        group = [c for c in per_card if str(c["difficulty"]) == tag]
        by_difficulty[tag] = {
            "cards": len(group),
            "field_accuracy": sum(c["correct"] for c in group) / max(sum(c["scored"] for c in group), 1),
            "exact_card_rate": sum(1 for c in group if c["exact"]) / len(group),
        }

    return {
        "cards_in_ground_truth": len(gt_records),
        "cards_matched": len(gt_records) - len(missing),
        "missing_predictions": missing,
        "fields": {f: {"support": t.support, "precision": t.precision, "recall": t.recall,
                       "f1": t.f1, "accuracy": t.accuracy,
                       "tp": t.tp, "fp": t.fp, "fn": t.fn, "tn": t.tn}
                   for f, t in tallies.items()},
        "overall": {
            "micro_precision": micro.precision, "micro_recall": micro.recall,
            "micro_f1": micro.f1, "macro_f1": sum(f1s) / len(f1s) if f1s else 0.0,
            "field_accuracy": micro.accuracy,
            "exact_card_rate": sum(1 for c in per_card if c["exact"]) / max(len(per_card), 1),
        },
        "by_difficulty": by_difficulty,
        "per_card": per_card,
    }


def pct(v: float | None) -> str:
    return "   n/a" if v is None else f"{100 * v:5.1f}%"


def report(result: dict[str, Any], show_errors: bool) -> None:
    print(f"cards: {result['cards_matched']}/{result['cards_in_ground_truth']} matched")
    if result["missing_predictions"]:
        print(f"  no prediction for: {', '.join(result['missing_predictions'])}")
    print()
    print(f"{'field':<12}{'support':>8}{'prec':>8}{'recall':>8}{'F1':>8}{'acc':>8}"
          f"{'TP':>5}{'FP':>5}{'FN':>5}{'TN':>5}")
    print("-" * 80)
    for f in FIELDS:
        m = result["fields"][f]
        print(f"{f:<12}{m['support']:>8}{pct(m['precision']):>8}{pct(m['recall']):>8}"
              f"{pct(m['f1']):>8}{pct(m['accuracy']):>8}"
              f"{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}{m['tn']:>5}")
    print("-" * 80)
    o = result["overall"]
    print(f"{'MICRO':<12}{'':>8}{pct(o['micro_precision']):>8}{pct(o['micro_recall']):>8}"
          f"{pct(o['micro_f1']):>8}{pct(o['field_accuracy']):>8}")
    print(f"{'MACRO F1':<12}{'':>8}{'':>8}{'':>8}{pct(o['macro_f1']):>8}")
    print()
    print(f"exact-card rate (all 8 fields right): {pct(o['exact_card_rate'])}")
    print()
    print(f"{'difficulty':<12}{'cards':>8}{'field acc':>12}{'exact':>10}")
    for tag, m in result["by_difficulty"].items():
        print(f"{tag:<12}{m['cards']:>8}{pct(m['field_accuracy']):>12}{pct(m['exact_card_rate']):>10}")

    if show_errors:
        print("\nerrors")
        print("-" * 80)
        for card in result["per_card"]:
            bad = {f: v for f, v in card["fields"].items() if v["outcome"] not in ("tp", "tn")}
            if not bad:
                continue
            print(f"{card['filename']}  [{card['difficulty']}]")
            for f, v in bad.items():
                print(f"    {f:<11} {v['outcome']:<6} expected={v['expected']!r} got={v['predicted']!r}")


def self_test(gt_records: list[dict[str, Any]], seed: int = 11) -> list[dict[str, Any]]:
    """Synthesise a plausible imperfect prediction set: reformatted values, a few drops
    and a few outright errors. Used to prove the harness scores what it claims to."""
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for i, gt in enumerate(gt_records):
        row: dict[str, Any] = {"filename": gt["filename"]}
        for f in FIELDS:
            v = gt.get(f)
            if v is None:
                # Occasionally hallucinate a value into a field that should stay null.
                row[f] = "Acme Holdings" if (f == "company" and i % 7 == 3) else None
                continue
            r = rng.random()
            if r < 0.08:
                row[f] = None                                  # dropped
            elif r < 0.14:
                row[f] = str(v)[:-2] + "x"                     # misread
            elif f == "phone":
                row[f] = phone_digits(str(v))                  # bare E.164 digits
            elif f == "email":
                row[f] = str(v).upper()                        # case noise
            elif f == "website":
                row[f] = "https://www." + str(v) + "/"         # scheme + www + slash
            else:
                row[f] = "  " + _WS.sub("  ", str(v)) + " "    # whitespace noise
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("predictions", nargs="?", type=Path,
                    help="batch JSON, CSV or XLSX export")
    ap.add_argument("--ground-truth", type=Path, default=DEFAULT_GT)
    ap.add_argument("--self-test", action="store_true",
                    help="score a synthetic, deliberately imperfect prediction set")
    ap.add_argument("--errors", action="store_true", help="list every mismatch")
    ap.add_argument("--json", type=Path, help="also write the full result as JSON")
    args = ap.parse_args()

    gt_records = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    if args.self_test:
        rows = self_test(gt_records)
        print(f"self-test: scoring {len(rows)} synthetic predictions "
              f"against {args.ground_truth.name}\n")
    elif args.predictions:
        rows = load_rows(args.predictions)
    else:
        ap.error("give a predictions file or --self-test")

    result = score(gt_records, rows)
    report(result, args.errors)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
