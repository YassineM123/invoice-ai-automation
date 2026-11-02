from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.routers.documents import router as documents_router
from app.routers.whatsapp import router as whatsapp_router

app = FastAPI(title=settings.app_name, version="1.0.0")

app.include_router(documents_router)
app.include_router(whatsapp_router)
app.mount("/outputs", StaticFiles(directory=str(settings.output_path)), name="outputs")


@app.get("/")
def root():
    return {
        "app": settings.app_name,
        "status": "running",
        "endpoints": [
            "POST /documents/correct",
            "POST /documents/create-from-text",
            "GET/POST /whatsapp/webhook",
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok"}
