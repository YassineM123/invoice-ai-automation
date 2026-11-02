from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from app.config import settings
from app.models import CompanyProfile, ExtractedDocument, UNKNOWN


def euro(value) -> str:
    if value is None:
        return UNKNOWN
    return f"{value:,.2f} EUR".replace(",", " ").replace(".", ",")


def generate_docx(doc: ExtractedDocument, company: CompanyProfile, filename: str) -> Path:
    output_path = settings.output_path / filename
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    style = document.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(10)

    header = document.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = header.add_run(company.name)
    run.bold = True
    run.font.size = Pt(18)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run(f"{doc.document_type.value.upper()} No {doc.number}")
    title_run.bold = True
    title_run.font.size = Pt(15)

    document.add_paragraph(f"Date: {doc.date}")
    document.add_paragraph(f"Echeance: {doc.due_date}")

    table_info = document.add_table(rows=1, cols=2)
    table_info.style = "Table Grid"
    left, right = table_info.rows[0].cells
    left.text = (
        f"Emetteur\n{company.name}\n{company.address}\n"
        f"SIRET: {company.siret}\nTVA: {company.vat_number}\n{company.email} - {company.phone}"
    )
    right.text = (
        f"Client\n{doc.client_info.name}\n{doc.client_info.address}\n"
        f"{doc.client_info.email}\n{doc.client_info.phone}\nChantier: {doc.worksite_address}"
    )

    document.add_paragraph("")
    items_table = document.add_table(rows=1, cols=5)
    items_table.style = "Table Grid"
    headers = ["Description", "Qte", "PU HT", "TVA", "Total HT"]
    for idx, text in enumerate(headers):
        items_table.rows[0].cells[idx].text = text

    previous_group = None
    for item in doc.items:
        row = items_table.add_row().cells
        cell = row[0].paragraphs[0]
        if item.group and item.group != previous_group:
            lot = cell.add_run(f"{item.group}\n")
            lot.bold = True
            lot.font.size = Pt(13)
        previous_group = item.group
        if item.title:
            cell.add_run(f"{item.title}\n").bold = True
        cell.add_run(item.description).font.size = Pt(9)
        row[1].text = str(item.quantity)
        row[2].text = euro(item.unit_price_ht)
        row[3].text = f"{item.vat_rate}%" if item.vat_rate is not None else UNKNOWN
        row[4].text = euro(item.total_ht)

    document.add_paragraph("")
    totals = document.add_paragraph()
    totals.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    totals.add_run(f"Total HT: {euro(doc.totals.subtotal_ht)}\n")
    totals.add_run(f"TVA: {euro(doc.totals.vat_total)}\n")
    totals.add_run(f"Total TTC: {euro(doc.totals.total_ttc)}\n").bold = True
    totals.add_run(f"Reste a payer: {euro(doc.totals.balance_due_ttc)}")

    if doc.notes:
        document.add_paragraph(doc.notes)
    document.add_paragraph(f"Mode de paiement: {doc.payment_method}")
    document.add_paragraph(f"Conditions: {doc.payment_terms if doc.payment_terms != UNKNOWN else company.payment_terms}")
    document.add_paragraph(company.late_penalties)
    document.add_paragraph(company.footer_note)

    document.save(str(output_path))
    return output_path
