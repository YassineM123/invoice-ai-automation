import json

import pytest

from app.bot.conversation import ChatState, create_document_payload, extract_session_data, merge_session_data
from app.models import DocumentItem
from app.services.extractor import _fallback_extract_from_text
from app.services.item_polisher import group_items_by_lot, polish_items
from test_whatsapp_conversation import MULTI_LINE_QUOTE


def _backend_items() -> list[DocumentItem]:
    state = ChatState()
    merge_session_data(state, extract_session_data(MULTI_LINE_QUOTE, state))
    return _fallback_extract_from_text(create_document_payload(state)).items


def test_rule_grouping_puts_each_line_under_its_room_lot():
    items = group_items_by_lot(_backend_items())

    assert [it.group for it in items] == [
        "Couloir d’entrée", "Cuisine", "Cuisine", "Couloirs", "3 Chambres", "WC", None, "Électricité", "Boiseries",
    ]
    assert items[0].title == "Peinture"
    assert items[1].title == "Plafond et mur"
    assert items[1].description.startswith("Sur les murs lavage")
    assert items[3].title is None
    assert items[6].description == "Révision équipement logement"
    assert sum(it.unit_price_ht for it in items) == 7100


def _fake_ai(response: dict):
    calls = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(response, ensure_ascii=False)

    complete.calls = calls
    return complete


def test_ai_rewrites_text_only_and_never_touches_prices():
    items = _backend_items()
    ai_items = [
        {"group": f"Lot {i}", "title": None, "details": [f"Detail {i}", "Travaux soignés"]}
        for i in range(len(items))
    ]
    complete = _fake_ai({"items": ai_items})

    polished = polish_items(items, complete=complete)

    assert polished[1].group == "Lot 1"
    assert polished[1].description == "Detail 1\nTravaux soignés"
    assert [it.unit_price_ht for it in polished] == [450, 750, 450, 1600, 2600, 350, 0, 450, 450]
    # Prices are not even sent to the AI.
    assert "2600" not in complete.calls[0]


@pytest.mark.parametrize("bad_response", [
    {"items": []},
    {"items": [{"group": "X", "title": None, "details": []}] * 9},
    {"wrong": "shape"},
])
def test_invalid_ai_answer_falls_back_to_rule_grouping(bad_response):
    polished = polish_items(_backend_items(), complete=_fake_ai(bad_response))

    assert polished[1].group == "Cuisine"
    assert polished[1].title == "Plafond et mur"


def test_ai_error_falls_back_to_rule_grouping():
    def failing(prompt: str) -> str:
        raise RuntimeError("429 no credits")

    polished = polish_items(_backend_items(), complete=failing)

    assert polished[2].group == "Cuisine"
