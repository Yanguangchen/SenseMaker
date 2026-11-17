"""Fixed Oyoshi invoice template and globally unique, persisted invoice numbers."""

from __future__ import annotations

import io
import re
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.api_core.exceptions import AlreadyExists
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from modules.saved_links import library_owner_id

TEMPLATE_PATH = Path(__file__).parent / "invoice_assets" / "invoice-template.pdf"
DESCRIPTION = "CrisisRadar with Browserbase Pro - Oyoshi - Monthly"
AMOUNT_CENTS = 8500
CURRENCY = "SGD"
CUSTOMER = "MSA MARKETING SOLUTIONS ASIA LTD."
NUMBER_COLLECTION = "sensemaker_invoice_numbers"
MAX_NUMBER_ATTEMPTS = 32
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def current_invoice_date() -> date:
    return datetime.now(ZoneInfo("Asia/Singapore")).date()


def invoice_number(code: str, invoice_date: date, append_date: bool = False) -> str:
    if not isinstance(code, str) or not re.fullmatch(r"[0-9A-F]{5}", code):
        raise ValueError("Invoice code must contain five uppercase hexadecimal characters.")
    if type(invoice_date) is not date:
        raise ValueError("Choose a valid invoice date.")
    if type(append_date) is not bool:
        raise ValueError("Choose whether to append the date.")
    return f"{code}-{invoice_date:%Y%m%d}" if append_date else code


def render_invoice_pdf(code: str, invoice_date: date, append_date: bool = False) -> bytes:
    """Preserve the supplied artwork; replace only number, date, description and amounts."""
    number = invoice_number(code, invoice_date, append_date)
    writer = PdfWriter(clone_from=TEMPLATE_PATH)
    page = writer.pages[0]
    overlay = io.BytesIO()
    pdf = canvas.Canvas(overlay, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))

    # Coordinates are in PDF points, measured against 85-updated.pdf. The source
    # is raster artwork, so all changed fields must be fully covered, including
    # all three occurrences of the previous $400.00 amount.
    pdf.setFillColorRGB(1, 1, 1)
    for x, top, width, height in (
        (360, 138, 218, 22),       # Invoice number
        (16, 199, 562, 29),       # Date
        (17, 282, 561, 29),       # Description and line amount
        (480, 335, 97, 18),       # Subtotal
        (480, 357, 97, 17),       # Total
    ):
        pdf.rect(x, float(page.mediabox.height) - top - height, width, height, fill=1, stroke=0)

    pdf.setFillColorRGB(0.36, 0.44, 0.53)
    pdf.setFont("Helvetica", 14)
    pdf.drawRightString(575, 688, number)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(17, 623.5, "DATE:")
    pdf.setFont("Helvetica", 18)
    pdf.drawString(78, 623.5, f"{invoice_date.day} {MONTHS[invoice_date.month - 1]} {invoice_date.year}")
    pdf.setFillColorRGB(0.2, 0.2, 0.2)
    pdf.setFont("Helvetica", 14)
    pdf.drawString(25, 541, DESCRIPTION)
    pdf.setFont("Helvetica", 10)
    amount = f"${AMOUNT_CENTS / 100:.2f} {CURRENCY}"
    pdf.drawRightString(570, 542, amount)
    pdf.drawRightString(572, 493.3, amount)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawRightString(572, 472, amount)
    pdf.save()
    overlay.seek(0)
    page.merge_page(PdfReader(overlay).pages[0])
    writer.add_metadata({"/Title": f"Invoice {number}", "/Author": "WEB WIZARDS SG"})
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def create_invoice(db: Any, email: str, invoice_date: date, append_date: bool = False) -> dict:
    """Return a PDF only after an atomic create reserves its five-character code.

    The registry is global to this Firestore database, across users and date
    suffixes. Never delete its entries or fall back to an unreserved number.
    Rendering before reservation also avoids consuming codes for invalid PDFs.
    """
    owner = library_owner_id(email)
    for _ in range(MAX_NUMBER_ATTEMPTS):
        code = secrets.token_hex(3)[:5].upper()
        number = invoice_number(code, invoice_date, append_date)
        document = render_invoice_pdf(code, invoice_date, append_date)
        record = {
            "code": code, "number": number, "date": invoice_date.isoformat(),
            "append_date": append_date, "owner": owner,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "description": DESCRIPTION, "amount_cents": AMOUNT_CENTS,
            "currency": CURRENCY, "template_version": 1,
        }
        try:
            # create() has an exists=false precondition. A prior read followed by
            # set() would race when two users draw the same random code.
            db.collection(NUMBER_COLLECTION).document(code).create(record, timeout=15, retry=None)
        except AlreadyExists:
            continue
        return {**record, "pdf": document}
    raise RuntimeError("Could not reserve a unique invoice number. Please try again.")
