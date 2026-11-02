from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.services.document_pipeline import process_text
from app.services.whatsapp import send_welcome, send_whatsapp_text

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    try:
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
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}
