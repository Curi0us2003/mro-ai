"""
==============================================================
AI Maintenance Voice Copilot
Reporting Layer - PDF Maintenance Report Generation
--------------------------------------------------------------

Purpose
-------
Turn a completed maintenance record (plus its conversation
transcript) into a professional PDF report technicians and
engineers can file, print, or attach to SAP.

Responsibilities
----------------
• Render a structured maintenance record as a formatted PDF
  using ReportLab
• Optionally include the raw technician/AI conversation as an
  appendix for traceability
• Save reports under REPORTS_FOLDER

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.

Example
-------
    from backend.pdf_service import generate_report_for_record

    path = generate_report_for_record(record_id)
    print(path)
==============================================================
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.config import (
    REPORTS_FOLDER,
    PDF_FONT,
    PDF_TITLE,
    LOG_LEVEL,
)
from backend.database import (
    get_maintenance_record,
    get_conversation,
    list_record_photos,
    get_record_photo,
)

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("mro_copilot.pdf_service")


class MaintenanceRecordNotFoundError(ValueError):
    """Raised when a report is requested for a record_id that doesn't exist."""


# ==========================================================
# Styles
# ==========================================================

def _build_styles() -> dict:
    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            fontName=f"{PDF_FONT}-Bold",
            fontSize=20,
            leading=24,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubtitle",
            fontName=PDF_FONT,
            fontSize=10,
            textColor=colors.grey,
            spaceAfter=18,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            fontName=f"{PDF_FONT}-Bold",
            fontSize=13,
            spaceBefore=16,
            spaceAfter=8,
            textColor=colors.HexColor("#1a1a2e"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyTextCustom",
            fontName=PDF_FONT,
            fontSize=10,
            leading=14,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ConversationTechnician",
            fontName=f"{PDF_FONT}-Bold",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#1a1a2e"),
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ConversationAssistant",
            fontName=PDF_FONT,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#333333"),
            spaceAfter=6,
            leftIndent=12,
        )
    )
    # Caption under a photographic evidence plate.
    styles.add(
        ParagraphStyle(
            name="PhotoCaption",
            fontName=PDF_FONT,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#666666"),
            spaceAfter=0,
        )
    )
    return styles


SEVERITY_COLORS = {
    "MINOR": colors.HexColor("#2e7d32"),
    "MAJOR": colors.HexColor("#e65100"),
    "CRITICAL": colors.HexColor("#c62828"),
    "AOG": colors.HexColor("#b71c1c"),
}


def _severity_color(severity: Optional[str]):
    if not severity:
        return colors.black
    return SEVERITY_COLORS.get(severity.strip().upper(), colors.black)


def _fmt(value) -> str:
    """Render a possibly-None field as readable text."""
    if value in (None, ""):
        return "Not recorded"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


# ==========================================================
# Report building blocks
# ==========================================================

def _build_header(styles, record: dict) -> list:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    elements = [
        Paragraph(PDF_TITLE, styles["ReportTitle"]),
        Paragraph(
            f"Record ID: {record.get('RECORD_ID', 'N/A')} &nbsp;|&nbsp; "
            f"Generated: {generated_at}",
            styles["ReportSubtitle"],
        ),
    ]
    return elements


def _build_details_table(styles, record: dict) -> Table:
    severity = record.get("SEVERITY")
    rows = [
        ["Aircraft Registration", _fmt(record.get("AIRCRAFT_REG"))],
        ["Component", _fmt(record.get("COMPONENT"))],
        ["Location", _fmt(record.get("LOCATION"))],
        ["Severity", _fmt(severity)],
        ["Status", _fmt(record.get("STATUS"))],
        ["Technician", _fmt(record.get("TECHNICIAN"))],
        ["Inspection Timestamp", _fmt(record.get("INSPECTION_TS"))],
    ]

    table = Table(
        [[Paragraph(f"<b>{label}</b>", styles["BodyTextCustom"]), Paragraph(value, styles["BodyTextCustom"])]
         for label, value in rows],
        colWidths=[1.8 * inch, 4.2 * inch],
    )

    style_commands = [
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f7")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d0d8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]

    # Highlight the severity row in its corresponding color.
    severity_row_index = 3
    style_commands.append(
        ("TEXTCOLOR", (1, severity_row_index), (1, severity_row_index), _severity_color(severity))
    )

    table.setStyle(TableStyle(style_commands))
    return table


def _build_conversation_section(styles, conversation: list[dict]) -> list:
    if not conversation:
        return []

    elements = [Paragraph("Conversation Transcript", styles["SectionHeading"])]

    role_labels = {
        "technician": "Technician",
        "assistant": "AI Copilot",
        "system": "System",
    }
    role_styles = {
        "technician": styles["ConversationTechnician"],
        "assistant": styles["ConversationAssistant"],
        "system": styles["ConversationAssistant"],
    }

    for turn in conversation:
        role = (turn.get("ROLE") or "").lower()
        label = role_labels.get(role, role.title() or "Unknown")
        style = role_styles.get(role, styles["BodyTextCustom"])
        message = _fmt(turn.get("MESSAGE"))
        elements.append(Paragraph(f"{label}: {message}", style))

    return elements


# ==========================================================
# Public API
# ==========================================================

def _build_photo_section(styles, photos: list[dict], max_width: float) -> list:
    """
    Reproduce the attached damage photos as evidence plates.

    Each plate is scaled to fit the text column and capped in height so a
    portrait phone photo cannot push a whole page of white space ahead of
    itself. KeepTogether stops a caption from being orphaned onto the next
    page away from its image.

    A photo that fails to load is reported in the PDF rather than silently
    dropped - a report that quietly omits evidence is worse than one that
    says a plate was unreadable.
    """
    story: list = [Paragraph("Photographic Evidence", styles["SectionHeading"])]

    max_height = 3.6 * inch
    total = len(photos)

    for index, meta in enumerate(photos, start=1):
        photo = get_record_photo(meta["PHOTO_ID"])

        if not photo or not photo.get("IMAGE_DATA"):
            story.append(
                Paragraph(
                    f"Photo {index} of {total} could not be read from storage.",
                    styles["BodyTextCustom"],
                )
            )
            continue

        # Decode NOW rather than handing raw bytes to Image(). reportlab
        # defers reading until doc.build(), so a corrupt blob would blow up
        # far from here - past any try/except around this loop - and take
        # the whole report with it. ImageReader parses eagerly, which both
        # surfaces the problem at a point we can report it and yields the
        # true pixel size instead of trusting the stored WIDTH/HEIGHT.
        try:
            width, height = ImageReader(io.BytesIO(photo["IMAGE_DATA"])).getSize()
        except Exception:  # noqa: BLE001 - Pillow/reportlab raise various types
            logger.exception("Could not decode photo %s for the report", meta.get("PHOTO_ID"))
            story.append(
                Paragraph(
                    f"Photo {index} of {total} is attached to this record but "
                    f"could not be rendered.",
                    styles["BodyTextCustom"],
                )
            )
            continue

        scale = min(max_width / width, max_height / height, 1.0)

        caption_bits = [f"Photo {index} of {total}"]
        if photo.get("CAPTION"):
            caption_bits.append(_fmt(photo["CAPTION"]))
        if photo.get("CREATED_AT"):
            caption_bits.append(f"attached {_fmt(photo['CREATED_AT'])}")

        # A fresh stream: Image() wants a file-like it can read itself, and
        # the one above has already been consumed by the validation decode.
        story.append(
            KeepTogether([
                Image(
                    io.BytesIO(photo["IMAGE_DATA"]),
                    width=width * scale,
                    height=height * scale,
                ),
                Spacer(1, 0.06 * inch),
                Paragraph(" &middot; ".join(caption_bits), styles["PhotoCaption"]),
                Spacer(1, 0.18 * inch),
            ])
        )

    return story


def generate_maintenance_report(
    record: dict,
    conversation: Optional[list[dict]] = None,
    output_path: Optional[Path] = None,
    photos: Optional[list[dict]] = None,
) -> Path:
    """
    Render a maintenance record (and optional conversation
    transcript) to a PDF file and return its path.

    `record` is expected to be a dict shaped like the rows
    returned by backend.database.get_maintenance_record().
    """
    record_id = record.get("RECORD_ID", "unknown")

    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = REPORTS_FOLDER / f"maintenance_report_{record_id}_{timestamp}.pdf"

    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        title=PDF_TITLE,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    story: list = []
    story.extend(_build_header(styles, record))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Inspection Details", styles["SectionHeading"]))
    story.append(_build_details_table(styles, record))

    story.append(Paragraph("Finding", styles["SectionHeading"]))
    story.append(Paragraph(_fmt(record.get("FINDING")), styles["BodyTextCustom"]))

    story.append(Paragraph("Recommended Action", styles["SectionHeading"]))
    story.append(Paragraph(_fmt(record.get("RECOMMENDED_ACTION")), styles["BodyTextCustom"]))

    # Evidence comes before the transcript: a reader wants to see the
    # damage next to the finding, not after pages of dialogue.
    if photos:
        story.append(Spacer(1, 0.1 * inch))
        story.extend(_build_photo_section(styles, photos, doc.width))

    if conversation:
        story.append(Spacer(1, 0.1 * inch))
        story.extend(_build_conversation_section(styles, conversation))

    doc.build(story)
    logger.info("Generated maintenance report at %s", output_path)
    return output_path


def generate_report_for_record(record_id: str, include_conversation: bool = True) -> Path:
    """
    Convenience wrapper: load a maintenance record (and its
    conversation) straight from the database and generate its
    PDF report.
    """
    record = get_maintenance_record(record_id)
    if not record:
        raise MaintenanceRecordNotFoundError(f"No maintenance record found with id '{record_id}'")

    conversation = get_conversation(record_id) if include_conversation else None

    # Metadata only here; the bytes are fetched per photo while the plates
    # are laid out, so a record with eight photos doesn't load them all
    # into memory at once.
    photos = list_record_photos(record_id)

    return generate_maintenance_report(
        record,
        conversation=conversation,
        photos=photos,
    )
