"""The 100 real-life WhatsApp / Telegram messages, run fully offline (no OpenAI call, no token used)."""
import math
import re
from pathlib import Path

import pytest

from app.bot.conversation import ChatState, ConversationManager
from app.services.extractor import _fallback_extract_from_text
from app.services.validator import complete_and_validate

MESSAGES_FILE = Path(__file__).parent / "data" / "invoice_automation_100_messages.txt"
MESSAGES = dict(re.findall(r"^(T\d{3}):\s*(.*)$", MESSAGES_FILE.read_text(encoding="utf-8"), flags=re.MULTILINE))


def _process(message_id: str):
    doc = _fallback_extract_from_text(MESSAGES[message_id])
    return complete_and_validate(doc)


def test_all_100_messages_are_loaded():
    assert len(MESSAGES) == 100


@pytest.mark.parametrize("message_id", sorted(MESSAGES))
def test_every_message_is_processed_without_crash_or_absurd_totals(message_id):
    doc, _, _ = _process(message_id)
    for value in (doc.totals.subtotal_ht, doc.totals.vat_total, doc.totals.total_ttc):
        assert value is None or (math.isfinite(value) and value >= 0)


# message id -> (document type, HT, TTC, quantities, unit prices HT)
EXPECTED_AMOUNTS = {
    "T001": ("facture", 700.0, 840.0, [2], [350]),
    "T002": ("devis", 135.0, 162.0, [3], [45]),
    "T003": ("facture", 1200.0, 1440.0, [1], [1200]),
    "T004": ("devis", 375.0, 450.0, [3, 1], [75, 150]),
    "T005": ("facture", 396.0, 475.2, [12, 2], [18, 90]),
    "T006": ("facture", 950.0, 1140.0, [1], [950]),
    "T007": ("devis", 900.0, 1080.0, [1], [1000]),
    "T008": ("facture", 360.0, 432.0, [7.5], [48]),
    "T009": ("facture", 245.0, 294.0, [1, 1], [230, 15]),
    "T010": ("facture", 180.0, 216.0, [1.5], [120]),
    "T013": ("devis", 1400.0, 1680.0, [40], [35]),
    "T014": ("devis", 3375.0, 4050.0, [125], [27]),
    "T016": ("facture", 500.0, 600.0, [2], [250]),
    "T018": ("devis", 2100.0, 2520.0, [6], [350]),
    "T021": ("devis", 375.0, 450.0, [3], [125]),
    "T022": ("facture", 379.05, 454.86, [10], [39.9]),
    "T023": ("facture", 195.0, 234.0, [5, 1], [25, 70]),
    "T026": ("facture", 1000.0, 1200.0, [1], [1000]),
    "T027": ("devis", 1234.56, 1481.47, [1], [1234.56]),
    "T028": ("facture", 1000.0, 1200.0, [1], [1000]),
    "T029": ("devis", 999.99, 1199.99, [3], [333.33]),
    "T030": ("facture", 720.0, 864.0, [1], [800]),
    "T032": ("devis", 50000.0, 52750.0, [1], [50000]),
    "T033": ("devis", 12000.0, 12660.0, [1], [12000]),
    "T036": ("facture", 850.0, 850.0, [1], [850]),
    "T037": ("facture", 2400.0, 2400.0, [1], [2400]),
    "T040": ("devis", 1100.0, 1210.0, [1, 1], [500, 600]),
    "T051": ("facture", 300.0, 360.0, [3], [100]),
    "T052": ("devis", 1500.0, 1800.0, [1], [1500]),
    "T055": ("devis", 300.0, 360.0, [4], [75]),
    "T056": ("devis", 791.67, 950.0, [1], [791.67]),
    "T058": ("devis", 1800.0, 2160.0, [40], [45]),
    "T064": ("devis", 330.0, 396.0, [1], [330]),
    "T069": ("devis", 1400.0, 1680.0, [1, 1], [500, 900]),
    "T091": ("facture", 3500.0, 4200.0, [1, 1], [1000, 2500]),
    "T093": ("devis", 12000.0, 14400.0, [10], [1200]),
    "T099": ("facture", 999.0, 1198.8, [1], [999]),
    "T100": ("facture", 1000.0, 1200.0, [1, 1], [450, 550]),
}


@pytest.mark.parametrize("message_id", sorted(EXPECTED_AMOUNTS))
def test_amounts_are_computed_correctly(message_id):
    doc_type, ht, ttc, quantities, prices = EXPECTED_AMOUNTS[message_id]
    doc, _, _ = _process(message_id)
    assert doc.document_type.value == doc_type
    assert [item.quantity for item in doc.items] == quantities
    assert [item.unit_price_ht for item in doc.items] == prices
    assert doc.totals.subtotal_ht == ht
    assert doc.totals.total_ttc == ttc


@pytest.mark.parametrize("message_id, client", [
    ("T001", "mr Martin"), ("T002", "madam legrand"), ("T003", "societé test alpha"), ("T004", "Sophie"),
    ("T006", "Ahmd"), ("T011", "karim"), ("T012", "Dupont"), ("T015", "Monsieur B"), ("T016", "Mr Ahmed"),
    ("T044", "M. Petit"), ("T045", "SARL BETA"), ("T070", "mme legrand"), ("T084", "Pierre"),
])
def test_client_name(message_id, client):
    doc, _, _ = _process(message_id)
    assert doc.client_info.name == client


@pytest.mark.parametrize("message_id", ["T028", "T029", "T032", "T035", "T060", "T065", "T079", "T080", "T089", "T096"])
def test_words_after_pour_or_client_are_not_taken_as_client_name(message_id):
    doc, _, _ = _process(message_id)
    assert doc.client_info.name == "à confirmer"


def test_discount_deposit_and_balance():
    doc, _, _ = _process("T030")  # 800 HT, remise 10%, deja payé 300 € TTC
    assert doc.totals.discount_ht == 80
    assert doc.totals.deposit_ttc == 300
    assert doc.totals.balance_due_ttc == 564


def test_deposit_invoice_is_flagged():
    doc, _, _ = _process("T075")
    assert doc.totals.deposit_ttc == 720
    assert any("acompte" in field for field in doc.uncertain_fields)


@pytest.mark.parametrize("message_id, mention", [("T036", "293 B"), ("T037", "Autoliquidation")])
def test_zero_vat_carries_the_legal_mention(message_id, mention):
    doc, corrections, _ = _process(message_id)
    assert all(item.vat_rate == 0 for item in doc.items)
    assert mention in doc.notes
    assert not any("sans mention" in c.error_found for c in corrections)


@pytest.mark.parametrize("message_id, field", [
    ("T014", "vat_rate"), ("T018", "vat_rate"), ("T038", "vat_rate"), ("T097", "vat_rate"), ("T098", "vat_rate"),
    ("T039", "TVA ligne 1"), ("T053", "unit_price_ht"), ("T057", "total_ttc"), ("T059", "date"),
    ("T069", "plusieurs documents demandes"), ("T094", "montant ou quantite invalide"),
    ("T095", "montant ou quantite invalide"), ("T096", "devise"), ("T099", "date invalide"), ("T025", "prix ligne 1"),
])
def test_doubtful_values_are_marked_to_confirm(message_id, field):
    doc, _, _ = _process(message_id)
    assert field in doc.uncertain_fields


@pytest.mark.parametrize("message_id, error", [
    ("T024", "Quantité invalide"), ("T025", "Prix négatif"), ("T026", "Total TTC incorrect"),
    ("T039", "Taux de TVA invalide"), ("T051", "Total HT incorrect ligne 1"), ("T052", "TVA totale incorrect"),
    ("T055", "Sous-total HT incorrect"), ("T058", "Total HT incorrect ligne 1"), ("T059", "Date invalide"),
    ("T100", "Total TTC incorrect"),
])
def test_wrong_values_are_reported_as_errors(message_id, error):
    _, corrections, _ = _process(message_id)
    assert any(error in c.error_found and c.severity == "error" for c in corrections)


@pytest.mark.parametrize("message_id", ["T041", "T042", "T047", "T057", "T059", "T090", "T094"])
def test_no_invented_prestation(message_id):
    """SIRET, phone numbers, penalties, dates, years or unreadable numbers never become a billed line."""
    doc, _, missing = _process(message_id)
    assert doc.items == []
    assert "prestations / lignes de facturation" in missing


def test_card_number_and_api_key_never_reach_the_document():
    for message_id in ("T085", "T089"):
        doc, _, _ = _process(message_id)
        dumped = doc.model_dump_json()
        assert "4111 1111 1111 1111" not in dumped
        assert "sk-FAUX-EXEMPLE" not in dumped


def test_unsafe_document_number_cannot_escape_output_folder(monkeypatch):
    from app.services import document_pipeline

    doc = _fallback_extract_from_text(MESSAGES["T086"])
    doc.number = "../../../../etc/passwd"
    output = document_pipeline.settings.output_path
    captured = {}
    monkeypatch.setattr(document_pipeline, "generate_pdf", lambda d, c, name: captured.setdefault("pdf", output / name))
    monkeypatch.setattr(document_pipeline, "generate_docx", lambda d, c, name: captured.setdefault("docx", output / name))
    monkeypatch.setattr(document_pipeline, "save_json", lambda data, name: captured.setdefault("json", output / name))
    document_pipeline.process_extracted_document(doc)
    for path in captured.values():
        assert ".." not in str(path) and path.resolve().parent.parent == output.resolve()
    captured["pdf"].parent.rmdir()


class _FakeApi:
    def health(self):
        return True

    def create_from_text(self, text):  # pragma: no cover - the cancel/greeting tests never reach the backend
        raise AssertionError("must not generate")


def test_bot_cancels_and_greets_without_generating():
    manager, state = ConversationManager(_FakeApi()), ChatState()
    manager.handle_text(MESSAGES["T001"], state)
    reply, files = manager.handle_text(MESSAGES["T065"], state)
    assert "annule" in reply and files == []
    assert state.data.get("items") == []
    reply, _ = manager.handle_text(MESSAGES["T061"], state)
    assert reply == manager.start_message()
