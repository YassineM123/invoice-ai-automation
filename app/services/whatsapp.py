from __future__ import annotations

import requests

from app.config import settings

GRAPH_BASE = "https://graph.facebook.com/v20.0"


def send_whatsapp_text(to_phone: str, text: str) -> None:
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        return
    url = f"{GRAPH_BASE}/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}", "Content-Type": "application/json"}
    requests.post(url, json=payload, headers=headers, timeout=20).raise_for_status()


def send_welcome(to_phone: str) -> None:
    send_whatsapp_text(
        to_phone,
        (
            "Bonjour.\n"
            "Je peux creer automatiquement votre devis ou facture.\n\n"
            "Envoyez simplement:\n"
            "1. Devis ou facture\n"
            "2. Nom client\n"
            "3. Adresse client / chantier\n"
            "4. Prestation\n"
            "5. Quantite\n"
            "6. Prix HT\n"
            "7. TVA si differente\n\n"
            "Le reste est genere automatiquement: numero, date, calculs, PDF et Word."
        ),
    )


def download_media(media_id: str) -> bytes:
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    meta = requests.get(f"{GRAPH_BASE}/{media_id}", headers=headers, timeout=20).json()
    media_url = meta.get("url")
    if not media_url:
        raise ValueError("Impossible de recuperer le media WhatsApp.")
    media = requests.get(media_url, headers=headers, timeout=30)
    media.raise_for_status()
    return media.content
