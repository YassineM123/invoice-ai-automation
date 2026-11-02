from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.services.document_pipeline import process_text
from app.services.whatsapp import send_welcome, send_whatsapp_text

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
LOGGER = logging.getLogger("whatsapp_webhook")
INSECURE_VERIFY_TOKENS = {"", "change-me"}


@router.get("/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if settings.verify_token in INSECURE_VERIFY_TOKENS:
        LOGGER.error("VERIFY_TOKEN is not configured: webhook verification refused.")
        return Response(status_code=403)
    if mode == "subscribe" and secrets.compare_digest(token or "", settings.verify_token):
        return Response(content=challenge or "", media_type="text/plain")
    return Response(status_code=403)


def _valid_signature(body: bytes, signature: str | None) -> bool:
    """Meta signs every webhook call with the app secret (X-Hub-Signature-256: sha256=<hex>)."""
    if not settings.whatsapp_app_secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


@router.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.body()
    if not _valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        LOGGER.warning("Rejected WhatsApp webhook call with a missing or invalid signature.")
        return Response(status_code=403)
    try:
        payload = json.loads(body)
        value = payload["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:
            return {"ok": True}

        message = messages[0]
        from_phone = message["from"]
        message_type = message.get("type")

        if message_type == "text":
            text = message["text"]["body"].strip()
            if text.lower() in {"bonjour", "salut", "hello", "hi", "start", "/start"}:
                send_welcome(from_phone)
            else:
                result = process_text(text)
                send_whatsapp_text(
                    from_phone,
                    (
                        "Votre document est pret.\n\n"
                        f"Statut: {result.status.value}\n"
                        f"PDF: {result.pdf_url}\n"
                        f"Word: {result.docx_url}\n"
                        f"Rapport: {result.report_url}\n\n"
                        'Les champs incertains sont marques "a confirmer".'
                    ),
                )
        else:
            send_whatsapp_text(
                from_phone,
                "Document recu. Pour cette version MVP, envoyez le fichier via l'API ou envoyez les informations en texte.",
            )
    except Exception:
        LOGGER.exception("WhatsApp webhook processing failed.")
        return {"ok": False}
    return {"ok": True}
