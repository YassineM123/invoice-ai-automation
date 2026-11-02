from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from app.models import CorrectedDocument
from app.services.document_pipeline import process_text, process_upload

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/correct", response_model=CorrectedDocument)
async def correct_document(file: UploadFile = File(...)) -> CorrectedDocument:
    return await process_upload(file)


@router.post("/create-from-text", response_model=CorrectedDocument)
def create_from_text(text: str = Form(...)) -> CorrectedDocument:
    return process_text(text)
