from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from fastapi import UploadFile
from app.config import settings
from app.models import CorrectedDocument, DocumentStatus, ExtractedDocument
from app.services.extractor import extract_document, extract_from_free_text
from app.services.generator_docx import generate_docx
from app.services.generator_pdf import generate_pdf
from app.services.item_polisher import polish_items
from app.services.validator import complete_and_validate, validate_legal_company_fields
from app.storage import load_company_profile, public_url, save_json


def _safe_filename(original: str) -> str:
    suffix = Path(original).suffix.lower() or ".txt"
    return f"upload_{uuid.uuid4().hex}{suffix}"


async def save_upload(file: UploadFile) -> Path:
    filename = _safe_filename(file.filename or "document")
    destination = settings.upload_path / filename
    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return destination


def process_extracted_document(doc: ExtractedDocument) -> CorrectedDocument:
    company = load_company_profile()
    doc.company_info = company
    doc, corrections, missing_fields = complete_and_validate(doc)
    corrections.extend(validate_legal_company_fields(company))

    status = DocumentStatus.pending_confirmation if missing_fields or doc.uncertain_fields else DocumentStatus.ready
    # The number may come from the user or the AI ("../../etc/passwd"): keep only safe filename characters.
    base_name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{doc.document_type.value}_{doc.number}").strip("_") or "document"

    pdf_path = generate_pdf(doc, company, f"{base_name}.pdf")
    docx_path = generate_docx(doc, company, f"{base_name}.docx")

    report_data = {
        "status": status.value,
        "missing_fields": missing_fields,
        "uncertain_fields": doc.uncertain_fields,
        "corrections": [c.model_dump() for c in corrections],
        "document": doc.model_dump(mode="json"),
    }
    report_path = save_json(report_data, f"{base_name}_correction_report.json")

    return CorrectedDocument(
        document=doc,
        corrections=corrections,
        status=status,
        pdf_url=public_url(pdf_path),
        docx_url=public_url(docx_path),
        report_url=public_url(report_path),
        missing_fields=missing_fields,
    )


async def process_upload(file: UploadFile) -> CorrectedDocument:
    path = await save_upload(file)
    doc = extract_document(path)
    return process_extracted_document(doc)


def process_text(text: str) -> CorrectedDocument:
    doc = extract_from_free_text(text)
    doc.items = polish_items(doc.items)
    return process_extracted_document(doc)
