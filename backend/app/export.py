"""Excel and CSV export.

The spreadsheet is the deliverable most reviewers actually open, so it is built to look like
a product artefact: a branded title band, a frozen and filtered header, a colour scale on
confidence, tinted rows for duplicates and the review queue, working mailto:/tel: links, and
a Summary sheet that answers "how did this batch go?" without scrolling. A third Raw sheet
carries the model's own transcription and JSON so any number on sheet one can be audited.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.models import LEAD_FIELDS, Batch, Lead

BRAND_DARK = "1F2A44"
BRAND_ACCENT = "4F6BED"
HEADER_TEXT = "FFFFFF"
ROW_DUPLICATE = "FFF1DC"
ROW_NEEDS_REVIEW = "FFFBEA"
ROW_FAILED = "FDE8E8"
LINK_BLUE = "1155CC"
GRID = "E3E6ED"

COLUMNS: tuple[tuple[str, str], ...] = (
    ("#", "index"),
    ("Status", "status"),
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("Job Title", "job_title"),
    ("Company", "company"),
    ("Location", "location"),
    ("Phone", "phone"),
    ("Phone (E.164)", "phone_e164"),
    ("Email", "email"),
    ("Website", "website"),
    ("Confidence", "overall_confidence"),
    ("Quality Flags", "quality_flags"),
    ("Duplicate Of", "duplicate_of"),
    ("Source File", "filename"),
    ("Processing (ms)", "processing_ms"),
)

HEADER_ROW = 4
FIRST_DATA_ROW = HEADER_ROW + 1
COL_CONFIDENCE = next(i for i, (_, key) in enumerate(COLUMNS, 1) if key == "overall_confidence")
COL_PHONE_E164 = next(i for i, (_, key) in enumerate(COLUMNS, 1) if key == "phone_e164")
COL_EMAIL = next(i for i, (_, key) in enumerate(COLUMNS, 1) if key == "email")
COL_WEBSITE = next(i for i, (_, key) in enumerate(COLUMNS, 1) if key == "website")

_THIN = Side(style="thin", color=GRID)
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def short_id(batch_id: str) -> str:
    return batch_id.replace("-", "")[:8]


def export_filename(batch_id: str, extension: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"leadforge-{short_id(batch_id)}-{stamp}.{extension}"


def select_leads(
    batch: Batch, include_low_confidence: bool, include_duplicates: bool = True
) -> list[Lead]:
    """Apply the two export filters.

    `include_low_confidence=false` narrows the export to the clean, ready-to-import rows.
    `include_duplicates=false` drops rows that point at an earlier card. The Summary sheet
    deliberately does *not* apply the duplicate filter — see `scored_leads` — so that
    "Duplicates found" still reports what was actually in the batch.
    """
    leads = scored_leads(batch, include_low_confidence)
    if include_duplicates:
        return leads
    return [lead for lead in leads if lead.duplicate_of is None]


def scored_leads(batch: Batch, include_low_confidence: bool) -> list[Lead]:
    """The rows the Summary sheet reports on: confidence filter only, duplicates retained."""
    if include_low_confidence:
        return list(batch.leads)
    return [lead for lead in batch.leads if lead.status == "completed"]


def _cell_value(lead: Lead, key: str, index: int) -> Any:
    if key == "index":
        return index
    if key == "quality_flags":
        return ", ".join(lead.quality_flags)
    if key == "duplicate_of":
        return short_id(lead.duplicate_of) if lead.duplicate_of else ""
    value = getattr(lead, key)
    return "" if value is None else value


def _title_band(sheet: Worksheet, span: int, subtitle: str) -> None:
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    banner = "LeadForge — Business cards in. Pipeline-ready leads out."
    title = sheet.cell(row=1, column=1, value=banner)
    title.font = Font(name="Calibri", size=16, bold=True, color=HEADER_TEXT)
    title.fill = PatternFill("solid", fgColor=BRAND_DARK)
    title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    caption = sheet.cell(row=2, column=1, value=subtitle)
    caption.font = Font(name="Calibri", size=9, color=HEADER_TEXT)
    caption.fill = PatternFill("solid", fgColor=BRAND_ACCENT)
    caption.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    sheet.row_dimensions[1].height = 28
    sheet.row_dimensions[2].height = 18
    sheet.row_dimensions[3].height = 6


def _autofit(sheet: Worksheet, widths: dict[int, int], minimum: int = 9, maximum: int = 44) -> None:
    for column, width in widths.items():
        sheet.column_dimensions[get_column_letter(column)].width = min(
            maximum, max(minimum, width + 2)
        )


def _leads_sheet(
    sheet: Worksheet, batch: Batch, leads: list[Lead], provider: str, model: str
) -> None:
    span = len(COLUMNS)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    _title_band(
        sheet,
        span,
        f"Batch {short_id(batch.batch_id)}  ·  {batch.total} source files  ·  "
        f"{len(leads)} rows exported  ·  generated {generated}  ·  {model} via {provider}",
    )

    header_font = Font(name="Calibri", size=11, bold=True, color=HEADER_TEXT)
    header_fill = PatternFill("solid", fgColor=BRAND_DARK)
    widths = {i: len(title) for i, (title, _) in enumerate(COLUMNS, 1)}
    for column, (title, _) in enumerate(COLUMNS, 1):
        cell = sheet.cell(row=HEADER_ROW, column=column, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
        cell.border = _BORDER
    sheet.row_dimensions[HEADER_ROW].height = 22

    for offset, lead in enumerate(leads):
        row = FIRST_DATA_ROW + offset
        if lead.status == "failed":
            tint = ROW_FAILED
        elif lead.duplicate_of:
            tint = ROW_DUPLICATE
        elif lead.status == "needs_review":
            tint = ROW_NEEDS_REVIEW
        else:
            tint = None

        for column, (_, key) in enumerate(COLUMNS, 1):
            value = _cell_value(lead, key, offset + 1)
            cell = sheet.cell(row=row, column=column, value=value)
            cell.border = _BORDER
            cell.alignment = Alignment(vertical="center")
            if tint:
                cell.fill = PatternFill("solid", fgColor=tint)
            widths[column] = max(widths[column], len(str(value)))

        sheet.cell(row=row, column=COL_CONFIDENCE).number_format = "0.00"
        # Force text so Excel never eats the leading "+" of an E.164 number.
        e164 = sheet.cell(row=row, column=COL_PHONE_E164)
        e164.number_format = "@"
        if lead.phone_e164:
            e164.hyperlink = f"tel:{lead.phone_e164}"
            e164.font = Font(color=LINK_BLUE, underline="single")
        if lead.email:
            email_cell = sheet.cell(row=row, column=COL_EMAIL)
            email_cell.hyperlink = f"mailto:{lead.email}"
            email_cell.font = Font(color=LINK_BLUE, underline="single")
        if lead.website:
            site = sheet.cell(row=row, column=COL_WEBSITE)
            site.hyperlink = f"https://{lead.website}"
            site.font = Font(color=LINK_BLUE, underline="single")

    last_row = max(FIRST_DATA_ROW, FIRST_DATA_ROW + len(leads) - 1)
    last_column = get_column_letter(span)
    confidence_column = get_column_letter(COL_CONFIDENCE)
    if leads:
        sheet.conditional_formatting.add(
            f"{confidence_column}{FIRST_DATA_ROW}:{confidence_column}{last_row}",
            ColorScaleRule(
                start_type="num", start_value=0.0, start_color="F4777B",
                mid_type="num", mid_value=0.65, mid_color="FFD666",
                end_type="num", end_value=1.0, end_color="63BE7B",
            ),
        )
    sheet.auto_filter.ref = f"A{HEADER_ROW}:{last_column}{last_row}"
    sheet.freeze_panes = f"A{FIRST_DATA_ROW}"
    sheet.print_area = f"A1:{last_column}{last_row}"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    _autofit(sheet, widths)


def _summary_sheet(
    sheet: Worksheet,
    batch: Batch,
    exported: list[Lead],
    reported: list[Lead],
    provider: str,
    model: str,
) -> None:
    """`exported` is what reached the Leads sheet; `reported` is what the batch contained.

    They differ when `include_duplicates=false`: the duplicate rows are hidden from the
    Leads sheet but every count here is still computed over `reported`, so the summary
    never claims the batch was cleaner than it was.
    """
    _title_band(sheet, 4, f"Batch summary  ·  {short_id(batch.batch_id)}")

    scored = [lead for lead in reported if lead.status in ("completed", "needs_review")]
    mean_confidence = (
        round(sum(lead.overall_confidence for lead in scored) / len(scored), 4) if scored else 0.0
    )
    processing_total = sum(lead.processing_ms or 0 for lead in reported)
    duplicates = sum(1 for lead in reported if lead.duplicate_of)
    hidden = len(reported) - len(exported)

    rows: list[tuple[str, Any, str]] = [
        ("Batch ID", batch.batch_id, "@"),
        ("Batch status", batch.status, "@"),
        ("Created (UTC)", batch.created_at, "@"),
        ("Finished (UTC)", batch.finished_at or "—", "@"),
        ("Source files", batch.total, "0"),
        ("Rows exported", len(exported), "0"),
        ("Completed", sum(1 for lead in reported if lead.status == "completed"), "0"),
        ("Needs review", sum(1 for lead in reported if lead.status == "needs_review"), "0"),
        ("Failed", sum(1 for lead in reported if lead.status == "failed"), "0"),
        ("Duplicates found", duplicates, "0"),
        ("Duplicates hidden from Leads sheet", hidden, "0"),
        ("Mean confidence", mean_confidence, "0.00"),
        ("Batch elapsed (s)", round(batch.elapsed_ms / 1000, 2), "0.00"),
        ("Total processing time (s)", round(processing_total / 1000, 2), "0.00"),
        (
            "Mean per-card processing (ms)",
            round(processing_total / len(reported)) if reported else 0,
            "0",
        ),
        ("VLM provider", provider, "@"),
        ("VLM model", model, "@"),
    ]

    label_font = Font(name="Calibri", size=11, bold=True)
    widths = {1: 30, 2: 26, 3: 14, 4: 12}
    row_index = HEADER_ROW
    for label, value, number_format in rows:
        label_cell = sheet.cell(row=row_index, column=1, value=label)
        label_cell.font = label_font
        value_cell = sheet.cell(row=row_index, column=2, value=value)
        value_cell.number_format = number_format
        widths[2] = max(widths[2], len(str(value)))
        row_index += 1

    row_index += 1
    fill_header = row_index
    for column, title in enumerate(("Field", "Filled", "Of", "Fill rate"), 1):
        cell = sheet.cell(row=fill_header, column=column, value=title)
        cell.font = Font(name="Calibri", size=11, bold=True, color=HEADER_TEXT)
        cell.fill = PatternFill("solid", fgColor=BRAND_DARK)
    row_index += 1

    denominator = len(scored) or 1
    for name in LEAD_FIELDS:
        filled = sum(1 for lead in scored if getattr(lead, name))
        sheet.cell(row=row_index, column=1, value=name)
        sheet.cell(row=row_index, column=2, value=filled)
        sheet.cell(row=row_index, column=3, value=len(scored))
        rate = sheet.cell(row=row_index, column=4, value=round(filled / denominator, 4))
        rate.number_format = "0%"
        row_index += 1

    sheet.conditional_formatting.add(
        f"D{fill_header + 1}:D{row_index - 1}",
        ColorScaleRule(
            start_type="num", start_value=0.0, start_color="F4777B",
            mid_type="num", mid_value=0.5, mid_color="FFD666",
            end_type="num", end_value=1.0, end_color="63BE7B",
        ),
    )
    _autofit(sheet, widths)


def _raw_sheet(sheet: Worksheet, leads: list[Lead], raw: dict[str, dict[str, Any]]) -> None:
    headers = ("Card ID", "Source File", "Status", "Confidence", "Raw Text", "Raw JSON")
    _title_band(sheet, len(headers), "Model transcription and raw JSON, for audit")
    for column, title in enumerate(headers, 1):
        cell = sheet.cell(row=HEADER_ROW, column=column, value=title)
        cell.font = Font(name="Calibri", size=11, bold=True, color=HEADER_TEXT)
        cell.fill = PatternFill("solid", fgColor=BRAND_DARK)

    for offset, lead in enumerate(leads):
        row = FIRST_DATA_ROW + offset
        sheet.cell(row=row, column=1, value=lead.card_id)
        sheet.cell(row=row, column=2, value=lead.filename)
        sheet.cell(row=row, column=3, value=lead.status)
        confidence = sheet.cell(row=row, column=4, value=lead.overall_confidence)
        confidence.number_format = "0.00"
        text = sheet.cell(row=row, column=5, value=lead.raw_text or "")
        text.alignment = Alignment(wrap_text=True, vertical="top")
        payload = raw.get(lead.card_id)
        sheet.cell(
            row=row,
            column=6,
            value=json.dumps(payload, ensure_ascii=False) if payload else "",
        ).alignment = Alignment(wrap_text=False, vertical="top")

    sheet.freeze_panes = f"A{FIRST_DATA_ROW}"
    _autofit(sheet, {1: 34, 2: 24, 3: 14, 4: 12, 5: 60, 6: 60}, maximum=70)


def build_workbook(
    batch: Batch,
    raw: dict[str, dict[str, Any]],
    provider: str,
    model: str,
    include_low_confidence: bool = True,
    include_duplicates: bool = True,
) -> bytes:
    leads = select_leads(batch, include_low_confidence, include_duplicates)
    reported = scored_leads(batch, include_low_confidence)
    workbook = Workbook()
    _leads_sheet(workbook.active, batch, leads, provider, model)
    workbook.active.title = "Leads"
    _summary_sheet(workbook.create_sheet("Summary"), batch, leads, reported, provider, model)
    _raw_sheet(workbook.create_sheet("Raw"), leads, raw)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


CSV_COLUMNS: tuple[str, ...] = (
    "card_id", "filename", "status", *LEAD_FIELDS[:5], "phone", "phone_e164", "email",
    "website", "overall_confidence", *(f"confidence_{name}" for name in LEAD_FIELDS),
    "quality_flags", "duplicate_of", "edited", "processing_ms", "error", "created_at",
)


def build_csv(
    batch: Batch, include_low_confidence: bool = True, include_duplicates: bool = True
) -> bytes:
    leads = select_leads(batch, include_low_confidence, include_duplicates)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        record = lead.model_dump(mode="json")
        confidence = record.pop("confidence")
        record.update({f"confidence_{name}": confidence[name] for name in LEAD_FIELDS})
        record["quality_flags"] = ", ".join(lead.quality_flags)
        writer.writerow({key: record.get(key, "") for key in CSV_COLUMNS})
    # BOM so Excel opens UTF-8 CSVs without mangling accented names.
    return buffer.getvalue().encode("utf-8-sig")
