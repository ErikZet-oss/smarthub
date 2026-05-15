"""Export ponúk do PDF a CSV."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.entities import CompanySettings, Offer, OfferLine
from app.services.company_logos import company_logo_file_path


def _line_total(line: OfferLine) -> float:
    qty = float(line.quantity or 0)
    price = float(line.unit_price_eur or 0)
    disc = float(line.discount_percent or 0)
    return round(qty * price * (1.0 - disc / 100.0), 2)


def offer_lines_subtotal(lines: list[OfferLine]) -> float:
    return round(sum(_line_total(ln) for ln in lines), 2)


def _fmt_eur(value: float) -> str:
    return f"{value:,.2f} €".replace(",", " ").replace(".", ",")


def _fmt_date(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y")
    return str(dt)


def _hex_to_rgb(hex_color: str) -> colors.Color:
    h = (hex_color or "#0284c7").strip().lstrip("#")
    if len(h) != 6:
        h = "0284c7"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 2, 132, 199
    return colors.Color(r / 255.0, g / 255.0, b / 255.0)


def build_offer_csv(
    offer: Offer,
    lines: list[OfferLine],
    company: CompanySettings,
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Číslo ponuky", offer.offer_number])
    writer.writerow(["Názov", offer.title or ""])
    writer.writerow(["Klient", offer.client_name])
    writer.writerow(["Dátum", _fmt_date(offer.created_at)])
    writer.writerow([])
    writer.writerow(
        [
            "Poz.",
            "Popis",
            "Množstvo",
            "MJ",
            "Nákup EUR",
            "Marža %",
            "Predaj EUR",
            "Zľava %",
            "Spolu EUR",
        ]
    )
    for ln in sorted(lines, key=lambda x: (x.position, x.id or 0)):
        writer.writerow(
            [
                ln.position,
                ln.description,
                ln.quantity,
                ln.unit,
                ln.purchase_unit_price_eur if ln.purchase_unit_price_eur is not None else "",
                ln.margin_percent,
                ln.unit_price_eur,
                ln.discount_percent,
                _line_total(ln),
            ]
        )
    subtotal = offer_lines_subtotal(lines)
    vat = round(subtotal * 0.21, 2)
    total = round(subtotal + vat, 2)
    writer.writerow([])
    writer.writerow(["Medzisúčet bez DPH", subtotal])
    writer.writerow(["DPH 21 %", vat])
    writer.writerow(["Celkom s DPH", total])
    if company.company_name:
        writer.writerow([])
        writer.writerow(["Dodávateľ", company.company_name])
    return buf.getvalue().encode("utf-8-sig")


def build_offer_pdf(
    offer: Offer,
    lines: list[OfferLine],
    company: CompanySettings,
) -> bytes:
    accent = _hex_to_rgb(company.pdf_accent_color or "#0284c7")
    accent_light = colors.Color(accent.red, accent.green, accent.blue, alpha=0.12)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.6 * cm,
        title=f"Ponuka {offer.offer_number}",
    )
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "OfferTitle",
        parent=styles["Heading1"],
        fontSize=20,
        textColor=accent,
        spaceAfter=4,
        fontName="Helvetica-Bold",
    )
    style_h2 = ParagraphStyle(
        "OfferH2",
        parent=styles["Heading2"],
        fontSize=11,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=8,
        spaceAfter=4,
        fontName="Helvetica-Bold",
    )
    style_body = ParagraphStyle(
        "OfferBody",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )
    style_small = ParagraphStyle(
        "OfferSmall",
        parent=style_body,
        fontSize=8,
        textColor=colors.HexColor("#64748b"),
    )

    story: list[Any] = []

    # Header row: logo + company
    logo_path = company_logo_file_path(company.logo_path)
    logo_cell: Any = ""
    if logo_path:
        try:
            img = Image(logo_path, width=4.2 * cm, height=2.2 * cm, kind="proportional")
            logo_cell = img
        except Exception:
            logo_cell = ""

    company_lines = []
    if company.company_name:
        company_lines.append(f"<b>{company.company_name}</b>")
    addr_parts = [p for p in [company.street, company.zip_code, company.city] if p]
    if addr_parts:
        company_lines.append(", ".join(addr_parts))
    if company.country:
        company_lines.append(company.country)
    ids = []
    if company.ico:
        ids.append(f"IČO: {company.ico}")
    if company.dic:
        ids.append(f"DIČ: {company.dic}")
    if company.ic_dph:
        ids.append(f"IČ DPH: {company.ic_dph}")
    if ids:
        company_lines.append(" · ".join(ids))
    contact = []
    if company.email:
        contact.append(company.email)
    if company.phone:
        contact.append(company.phone)
    if company.web:
        contact.append(company.web)
    if contact:
        company_lines.append(" · ".join(contact))

    header_data = [
        [
            logo_cell,
            Paragraph("<br/>".join(company_lines) or "—", style_body),
        ]
    ]
    header_table = Table(header_data, colWidths=[5 * cm, doc.width - 5 * cm])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(header_table)

    # Accent bar + title
    bar = Table([[""]], colWidths=[doc.width], rowHeights=[3 * mm])
    bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), accent),
                ("LINEBELOW", (0, 0), (-1, -1), 0, colors.white),
            ]
        )
    )
    story.append(bar)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("CENOVÁ PONUKA", style_title))
    meta_bits = [
        f"<b>Číslo:</b> {offer.offer_number}",
        f"<b>Dátum:</b> {_fmt_date(offer.created_at)}",
    ]
    if offer.valid_until:
        meta_bits.append(f"<b>Platnosť do:</b> {_fmt_date(offer.valid_until)}")
    if offer.title:
        meta_bits.append(f"<b>Predmet:</b> {offer.title}")
    story.append(Paragraph(" &nbsp;&nbsp;|&nbsp;&nbsp; ".join(meta_bits), style_small))
    story.append(Spacer(1, 5 * mm))

    # Client box
    story.append(Paragraph("Odberateľ", style_h2))
    client_lines = [f"<b>{offer.client_name or '—'}</b>"]
    c_addr = [
        p
        for p in [
            offer.client_street,
            " ".join(x for x in [offer.client_zip, offer.client_city] if x),
            offer.client_country,
        ]
        if p
    ]
    if c_addr:
        client_lines.extend(c_addr)
    c_ids = []
    if offer.client_ico:
        c_ids.append(f"IČO: {offer.client_ico}")
    if offer.client_dic:
        c_ids.append(f"DIČ: {offer.client_dic}")
    if offer.client_ic_dph:
        c_ids.append(f"IČ DPH: {offer.client_ic_dph}")
    if c_ids:
        client_lines.append(" · ".join(c_ids))
    if offer.client_contact:
        client_lines.append(f"Kontakt: {offer.client_contact}")
    if offer.client_email or offer.client_phone:
        client_lines.append(
            " · ".join(x for x in [offer.client_email, offer.client_phone] if x)
        )

    client_box = Table(
        [[Paragraph("<br/>".join(client_lines), style_body)]],
        colWidths=[doc.width],
    )
    client_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), accent_light),
                ("BOX", (0, 0), (-1, -1), 0.5, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(client_box)
    story.append(Spacer(1, 6 * mm))

    # Lines table
    story.append(Paragraph("Položky", style_h2))
    table_header = ["#", "Popis", "Množ.", "MJ", "J. cena", "Zľava", "Spolu"]
    table_rows: list[list[Any]] = [table_header]
    sorted_lines = sorted(lines, key=lambda x: (x.position, x.id or 0))
    for ln in sorted_lines:
        table_rows.append(
            [
                str(ln.position),
                Paragraph(ln.description.replace("\n", "<br/>"), style_body),
                f"{ln.quantity:g}",
                ln.unit or "ks",
                _fmt_eur(float(ln.unit_price_eur or 0)),
                f"{ln.discount_percent:g} %" if ln.discount_percent else "—",
                _fmt_eur(_line_total(ln)),
            ]
        )
    if len(table_rows) == 1:
        table_rows.append(["", "Žiadne položky", "", "", "", "", ""])

    col_widths = [0.8 * cm, doc.width - 9.5 * cm, 1.4 * cm, 1.1 * cm, 2.2 * cm, 1.4 * cm, 2.6 * cm]
    items_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(items_table)

    subtotal = offer_lines_subtotal(sorted_lines)
    vat = round(subtotal * 0.21, 2)
    total = round(subtotal + vat, 2)

    totals_data = [
        ["Medzisúčet bez DPH", _fmt_eur(subtotal)],
        ["DPH 21 %", _fmt_eur(vat)],
        ["Celkom s DPH", _fmt_eur(total)],
    ]
    totals_table = Table(
        totals_data,
        colWidths=[doc.width - 4 * cm, 4 * cm],
        hAlign="RIGHT",
    )
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, -1), (-1, -1), accent),
                ("LINEABOVE", (0, -1), (-1, -1), 1, accent),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(totals_table)

    if offer.notes_client:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Poznámka", style_h2))
        story.append(Paragraph(offer.notes_client.replace("\n", "<br/>"), style_body))

    footer_bits = []
    if company.iban:
        footer_bits.append(f"IBAN: {company.iban}")
    if company.bank_name:
        footer_bits.append(company.bank_name)
    if company.offer_footer_note:
        footer_bits.append(company.offer_footer_note)
    if footer_bits:
        story.append(Spacer(1, 8 * mm))
        story.append(
            Paragraph("<br/>".join(footer_bits), style_small),
        )

    doc.build(story)
    return buf.getvalue()
