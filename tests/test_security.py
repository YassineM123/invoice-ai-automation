import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import document_pipeline
from app.services.extractor import _fallback_extract_from_text

client = TestClient(app)


def test_documents_api_requires_the_key_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret-key")
    assert client.post("/documents/create-from-text", data={"text": "x"}).status_code == 401
    assert client.post("/documents/create-from-text", data={"text": "x"}, headers={"X-API-Key": "bad"}).status_code == 401


def test_generated_files_get_an_unguessable_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "output_dir", str(tmp_path))
    written = []
    monkeypatch.setattr(document_pipeline, "generate_pdf", lambda d, c, name: written.append(name) or tmp_path / name)
    monkeypatch.setattr(document_pipeline, "generate_docx", lambda d, c, name: written.append(name) or tmp_path / name)
    monkeypatch.setattr(document_pipeline, "save_json", lambda data, name: written.append(name) or tmp_path / name)
    result = document_pipeline.process_extracted_document(_fallback_extract_from_text("facture peinture 500 HT"))
    folder = Path(written[0]).parent.name
    assert len(folder) == 32 and all(Path(name).parent.name == folder for name in written)
    assert f"/outputs/{folder}/" in result.pdf_url


def test_counters_are_not_in_the_public_outputs_folder():
    assert settings.counter_file.parent != settings.output_path


def _signed_post(body: dict, secret: str):
    raw = json.dumps(body).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": signature})


def test_whatsapp_webhook_rejects_unsigned_or_badly_signed_calls(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "app-secret")
    body = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
    assert client.post("/whatsapp/webhook", json=body).status_code == 403
    assert _signed_post(body, "wrong-secret").status_code == 403
    assert _signed_post(body, "app-secret").json() == {"ok": True}


def test_whatsapp_webhook_is_closed_without_app_secret(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    assert _signed_post({"entry": []}, "").status_code == 403


def test_whatsapp_verification_refuses_the_default_token(monkeypatch):
    monkeypatch.setattr(settings, "verify_token", "change-me")
    params = {"hub.mode": "subscribe", "hub.verify_token": "change-me", "hub.challenge": "42"}
    assert client.get("/whatsapp/webhook", params=params).status_code == 403
    monkeypatch.setattr(settings, "verify_token", "my-real-token")
    params["hub.verify_token"] = "my-real-token"
    assert client.get("/whatsapp/webhook", params=params).text == "42"
