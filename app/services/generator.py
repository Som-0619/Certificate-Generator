"""Renders a single certificate PDF from the one predefined template."""
import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas


class CertificateGenerationError(Exception):
    pass


PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8934a")


def _check_renderable(text: str) -> None:
    # Built-in PDF fonts only cover Latin-1; anything else would render as
    # blank/garbage glyphs, so fail loudly rather than issue a bad certificate.
    try:
        text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise CertificateGenerationError(
            f"text contains characters the certificate font cannot render: {text!r}"
        ) from exc


def _fit_font_size(c: canvas.Canvas, text: str, font: str, size: float, max_width: float) -> float:
    while size > 10 and c.stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(*, name: str, course_title: str, issued_by: str, issue_date: date) -> bytes:
    for value in (name, course_title, issued_by):
        _check_renderable(value)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {name}")
    cx = PAGE_W / 2

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.rect(38, 38, PAGE_W - 76, PAGE_H - 76)

    max_w = PAGE_W - 140
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 38)
    c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE OF COMPLETION")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 175, "This is to certify that")

    size = _fit_font_size(c, name, "Helvetica-Bold", 34, max_w)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(cx, PAGE_H - 235, name)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(cx - 200, PAGE_H - 248, cx + 200, PAGE_H - 248)

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 285, "has successfully completed")
    size = _fit_font_size(c, course_title, "Helvetica-Bold", 24, max_w)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(cx, PAGE_H - 325, course_title)

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 13)
    c.drawString(80, 90, f"Date: {issue_date.strftime('%d %B %Y')}")
    size = _fit_font_size(c, issued_by, "Helvetica", 13, PAGE_W / 2 - 80)
    c.setFont("Helvetica", size)
    c.drawRightString(PAGE_W - 80, 90, f"Issued by: {issued_by}")

    c.showPage()
    c.save()
    return buf.getvalue()
