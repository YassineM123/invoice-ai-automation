import pytest

from app.bot.conversation import extract_session_data
from app.models import DocumentItem
from app.services.item_polisher import fix_spelling, group_items_by_lot


@pytest.mark.parametrize("text, client, address", [
    ("fais moi un devi\nadrese : 15 rue de chartres neuilly\ncliant : nathalie pousin\ncuisine peinture 450€",
     "nathalie pousin", "15 rue de chartres neuilly"),
    ("Devis\nAddresse: 3 rue Victor Hugo Paris\nNom du clien: Mr Dupont\nPeinture salon 800 €",
     "Mr Dupont", "3 rue Victor Hugo Paris"),
    ("devis\nadr; 3 rue Victor Hugo\nclt: Dupont\npeinture salon 800",
     "Dupont", "3 rue Victor Hugo"),
    ("Devis\nAdress - 8 avenue Foch\nClinet - Mme Martin\nPeinture 300€",
     "Mme Martin", "8 avenue Foch"),
])
def test_misspelled_labels_are_understood(text, client, address):
    data = extract_session_data(text)

    assert data["client_name"] == client
    assert data["client_address"] == address
    assert len(data["items"]) == 1
    for word in ("devi", "adr", "cli", "clt"):
        assert not data["items"][0]["description"].lower().startswith(word)


@pytest.mark.parametrize("text, doc_type", [
    ("factur pour Dupont, adresse 3 rue X\nPeinture 500€", "facture"),
    ("facure\nclient: Dupont\nadresse: 3 rue X\nPeinture 500€", "facture"),
    ("fait moi un dvis\nclient: Dupont\nadresse: 3 rue X\nPeinture 500€", "devis"),
])
def test_misspelled_document_type_is_understood(text, doc_type):
    data = extract_session_data(text)

    assert data["document_type"] == doc_type
    assert len(data["items"]) == 1
    assert data["items"][0]["description"] == "Peinture"


def test_misspelled_or_missing_currency_still_reads_each_price():
    text = (
        "Devis client : Dupont adresse : 3 rue X\n"
        "Peinture salon 800e\n"
        "Peinture cuisine 450 euro\n"
        "Peinture wc 300 eur\n"
        "Peinture chambre 1 200€\n"
        "Peinture couloir 350,50 €\n"
        "Peinture entree 200 E\n"
        "Peinture bureau 900"
    )
    items = extract_session_data(text)["items"]

    assert [it["unit_price_ht"] for it in items] == [800, 450, 300, 1200, 350.5, 200, 900]
    assert items[1]["description"] == "Peinture cuisine"


def test_spelling_is_corrected_in_the_devis_text():
    assert fix_spelling("Travaux a emulsion acrylique _ aspect mat travaux soigné") == (
        "Travaux à l'émulsion acrylique - aspect mat travaux soignés"
    )
    assert fix_spelling("Lot electricite") == "Lot électricité"
    assert fix_spelling("Porte plinte fenetre face interieur") == "Porte plinthe fenêtre face intérieure"
    assert fix_spelling("deux couches peinture laqué + demontage de 3 radiateur") == (
        "deux couches peinture laquée + démontage de 3 radiateurs"
    )
    # Correct words are left alone.
    assert fix_spelling("Peinture plafond et murs") == "Peinture plafond et murs"


def test_local_lot_grouping_fixes_spelling():
    items = group_items_by_lot([
        DocumentItem(title="Lot electricite", description="mise en securite du logemant", unit_price_ht=450),
    ])

    assert items[0].group == "Électricité"
    assert items[0].description == "Mise en sécurité du logement"
