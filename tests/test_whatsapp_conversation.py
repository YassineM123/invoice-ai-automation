from pathlib import Path

from app.bot.conversation import (
    COLLECTING_CLIENT_INFO,
    COLLECTING_ITEMS,
    COMPLETED,
    CONFIRMING_SUMMARY,
    UNKNOWN_VALUE,
    ChatState,
    ConversationManager,
    create_document_payload,
    detect_missing_fields,
    extract_session_data,
    merge_session_data,
)
from app.bot.whatsapp_web_bot import AllowedChatPolicy, looks_like_group_header


class FakeInvoiceApi:
    def __init__(self):
        self.created_payloads: list[str] = []

    def create_from_text(self, text: str) -> dict:
        self.created_payloads.append(text)
        return {
            "status": "ready",
            "pdf_url": "http://localhost:8000/outputs/doc.pdf",
            "docx_url": "http://localhost:8000/outputs/doc.docx",
            "report_url": "http://localhost:8000/outputs/report.json",
            "missing_fields": [],
        }

    def correct_file(self, path: Path) -> dict:
        return {
            "status": "ready",
            "pdf_url": None,
            "docx_url": None,
            "report_url": None,
            "missing_fields": [],
        }

    def download_generated_file(self, url_or_path: str | None) -> Path | None:
        if not url_or_path:
            return None
        return Path(url_or_path).name

    def health(self) -> bool:
        return True


class FailingInvoiceApi(FakeInvoiceApi):
    def create_from_text(self, text: str) -> dict:
        raise RuntimeError("backend down")

    def correct_file(self, path: Path) -> dict:
        raise RuntimeError("backend down")


def test_missing_field_detection_only_dynamic_data():
    state = ChatState()
    merge_session_data(state, extract_session_data("Je veux un devis pour client Ahmed"))

    missing = detect_missing_fields(state)

    assert "client_address" in missing
    assert "worksite_address" in missing
    assert "service_description" in missing
    assert "quantity" in missing
    assert "unit_price_ht" in missing
    assert "vat_rate" not in missing
    assert "payment_method" not in missing
    assert "siret" not in missing
    assert "iban" not in missing


def test_session_state_transitions_until_summary_confirmation():
    manager = ConversationManager(FakeInvoiceApi())
    state = ChatState()

    response, files = manager.handle_text("Bonjour, je veux un devis", state)
    assert files == []
    assert state.step == COLLECTING_CLIENT_INFO
    assert "nom client" in response

    response, files = manager.handle_text(
        "Client Ahmed Ben Ali, adresse client 12 rue Victor Hugo Paris, chantier 15 rue de Lyon",
        state,
    )
    assert files == []
    assert state.step == COLLECTING_ITEMS
    assert "prestation" in response

    response, files = manager.handle_text("Prestation peinture murs, quantite 2, prix HT 300", state)
    assert files == []
    assert state.step == CONFIRMING_SUMMARY
    assert "Voici le resume avant generation" in response
    assert "Repondez OUI" in response


def test_confirmation_flow_generates_document_and_returns_files():
    api = FakeInvoiceApi()
    manager = ConversationManager(api)
    state = ChatState()

    manager.handle_text(
        "Devis pour client Ahmed, adresse client 12 rue A, chantier 20 rue B, "
        "prestation peinture, quantite 2, prix HT 300",
        state,
    )
    response, files = manager.handle_text("OUI", state)

    assert state.step == COMPLETED
    assert state.generated_once is True
    assert len(api.created_payloads) == 1
    assert "Votre document est pret" in response
    assert files == ["doc.pdf", "doc.docx", "report.json"]


def test_a_confirmer_fallback_for_unclear_data():
    state = ChatState()
    updates = extract_session_data("Adresse chantier a confirmer", state)
    merge_session_data(state, updates)

    assert state.data["worksite_address"] == UNKNOWN_VALUE
    assert "worksite_address" in state.data["uncertain_fields"]


def test_document_payload_creation_uses_company_profile_and_no_legal_prompts():
    state = ChatState()
    merge_session_data(
        state,
        extract_session_data(
            "Facture pour client Sarah, adresse client 8 avenue Nord, chantier identique, "
            "travaux pose carrelage, quantite 5, prix HT 120, tva 10, acompte 100, virement",
            state,
        ),
    )

    payload = create_document_payload(state)

    assert "Utiliser company_profile.json" in payload
    assert "Type de document: facture" in payload
    assert "Nom client: Sarah" in payload
    assert "Prestation: pose carrelage" in payload
    assert "Quantite: 5" in payload
    assert "Prix unitaire HT: 120" in payload
    assert "TVA: 10" in payload
    assert "Acompte TTC: 100" in payload
    assert "Mode de paiement: virement" in payload
    assert "SIRET:" not in payload
    assert "IBAN:" not in payload


def test_allowed_chat_policy_requires_all_configured_identifiers():
    policy = AllowedChatPolicy(allowed_chat_name="Client A", allowed_phone="+33 6 00 00 00 00")

    assert policy.matches_header("Client A +33 6 00 00 00 00")
    assert not policy.matches_header("Client A +33 7 00 00 00 00")
    assert not policy.matches_header("Other Client +33 6 00 00 00 00")
    assert not policy.matches_text("Client A")
    assert not policy.matches_text("+33 6 00 00 00 00")


def test_allowed_chat_policy_blocks_groups_by_default():
    policy = AllowedChatPolicy(allowed_chat_name="Client A")

    assert looks_like_group_header("Client A, Client B, Client C")
    assert not policy.matches_header("Client A, Client B, Client C")
    assert AllowedChatPolicy(allowed_chat_name="Client A", allow_groups=True).matches_header(
        "Client A, Client B, Client C"
    )


def test_command_detection_start_help_status_reset():
    manager = ConversationManager(FakeInvoiceApi())
    state = ChatState()

    start_response, _ = manager.handle_text("/start", state)
    help_response, _ = manager.handle_text("/help", state)
    status_response, _ = manager.handle_text("/status", state)
    reset_response, _ = manager.handle_text("/reset", state)

    assert "factures et devis" in start_response
    assert "Unit price HT" in help_response
    assert "Bot actif" in status_response
    assert "Conversation reinitialisee" in reset_response


def test_backend_error_keeps_confirmation_state_and_returns_clear_message():
    manager = ConversationManager(FailingInvoiceApi())
    state = ChatState()

    manager.handle_text(
        "Devis pour client Ahmed, adresse client 12 rue A, chantier 20 rue B, "
        "prestation peinture, quantite 2, prix HT 300",
        state,
    )
    response, files = manager.handle_text("OUI", state)

    assert files == []
    assert state.step == CONFIRMING_SUMMARY
    assert "backend n'a pas pu traiter" in response
    assert "RuntimeError" in response


MULTI_LINE_QUOTE = r"""Fait moi un devis
Devis dhaou
Adresse ; 15 rue de Chartres Neuilly
Clients : Nathalie Pousin
Couloir  d’entrée peinture 450€ ens
Travaux a emulsion acrylique _ aspect mat finition (à) travaux soigné ( a la place de peinture satiné+ mate)
Cuisine plafond et mur
Sur les murs lavage \ rebouchage / deux couches satinée|  sur les plafonds deux couches mate forfait 750€
Sur les meubles de la cuisine deux couches peinture laqué 450€
Sur les couloirs sur les plafond et mur deux couches de satinée + pose des moulures 1600€ forfait
Sur les 3 Chambres plafond et mur peinture plafond et mur sur les plafonds deux couches mate et sur les murs deux couches satines + nettoyage de moquette de chambre parentale + démontage de 3 radiateurs 2600€
Dans les WC deux couches de peinture + pose de moulures 350€
Révision équipement logement 0€
Lot électricité
Fourniture et pose de spot + mise en sécurité de logement 450€
Sur les boiseries
Porte plinthe fenêtre face intérieur
Peinture sur ouvrage bois 450€"""


def test_multi_line_quote_reads_labelled_client_and_address_lines():
    state = ChatState()
    merge_session_data(state, extract_session_data(MULTI_LINE_QUOTE, state))

    assert state.data["client_name"] == "Nathalie Pousin"
    assert state.data["client_address"] == "15 rue de Chartres Neuilly"
    assert state.data["worksite_address"] == "15 rue de Chartres Neuilly"
    assert detect_missing_fields(state) == []


def test_multi_line_quote_items_keep_header_lines_out_and_details_with_their_item():
    items = extract_session_data(MULTI_LINE_QUOTE)["items"]

    assert len(items) == 9
    assert sum(it["unit_price_ht"] for it in items) == 7100
    for it in items:
        full_text = f"{it.get('title') or ''} {it['description']}"
        assert "Nathalie" not in full_text
        assert "Chartres" not in full_text
        assert "Fait moi" not in full_text


def test_multi_line_quote_splits_each_lot_into_title_and_details():
    items = extract_session_data(MULTI_LINE_QUOTE)["items"]

    # A lot line ("Cuisine plafond et mur") becomes the title, the priced line its details.
    assert items[0]["title"] == "Couloir d’entrée peinture"
    assert items[0]["description"].startswith("Travaux a emulsion acrylique")
    assert items[1]["title"] == "Cuisine plafond et mur"
    assert items[1]["description"].startswith("Sur les murs lavage")
    assert items[2]["title"] is None
    assert items[2]["description"] == "Sur les meubles de la cuisine deux couches peinture laqué"
    # "Sur les couloirs ..." / "Dans les WC ...": the room at the start of the line is the lot title.
    assert items[3]["title"] == "Couloirs"
    assert items[3]["description"] == "Sur les plafond et mur deux couches de satinée + pose des moulures"
    assert items[4]["title"] == "3 Chambres"
    assert items[4]["description"].startswith("Plafond et mur peinture")
    assert items[5]["title"] == "WC"
    assert items[5]["description"] == "Deux couches de peinture + pose de moulures"
    assert items[6]["title"] is None
    assert items[7]["title"] == "Lot électricité"
    assert items[7]["description"] == "Fourniture et pose de spot + mise en sécurité de logement"
    assert items[8]["title"] == "Sur les boiseries"
    assert items[8]["description"] == "Porte plinthe fenêtre face intérieur - Peinture sur ouvrage bois"


def test_item_titles_survive_payload_round_trip_to_backend():
    from app.services.extractor import _fallback_extract_from_text

    state = ChatState()
    merge_session_data(state, extract_session_data(MULTI_LINE_QUOTE, state))
    doc = _fallback_extract_from_text(create_document_payload(state))

    assert len(doc.items) == 9
    assert doc.items[1].title == "Cuisine plafond et mur"
    assert doc.items[1].description.startswith("Sur les murs lavage")
    assert doc.items[2].title is None
    assert doc.client_info.name == "Nathalie Pousin"


def test_quote_with_complete_info_goes_straight_to_summary():
    manager = ConversationManager(FakeInvoiceApi())
    state = ChatState()

    reply, _ = manager.handle_text(MULTI_LINE_QUOTE, state)

    assert state.step == CONFIRMING_SUMMARY
    assert "Client: Nathalie Pousin" in reply
