from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from app.config import settings
from app.models import CorrectedDocument
from app.services.document_pipeline import process_text, process_upload


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and not secrets.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")


router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_key)])


@router.post("/correct", response_model=CorrectedDocument)
async def correct_document(file: UploadFile = File(...)) -> CorrectedDocument:
    return await process_upload(file)


@router.post("/create-from-text", response_model=CorrectedDocument)
def create_from_text(text: str = Form(...)) -> CorrectedDocument:
    return process_text(text)
