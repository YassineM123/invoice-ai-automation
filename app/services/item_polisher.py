from __future__ import annotations

import json
import logging
import re
from typing import Callable

from openai import OpenAI

from app.bot.conversation import ROOM_WORDS
from app.config import settings
from app.models import DocumentItem

LOGGER = logging.getLogger("item_polisher")

ROOM_AT_START = re.compile(rf"^(?:sur|dans|pour)?\s*(?:les|la|le|l['’])?\s*({ROOM_WORDS})\b\s*(.*)$", re.IGNORECASE)
ROOM_ANYWHERE = re.compile(rf"\b({ROOM_WORDS})\b", re.IGNORECASE)

POLISH_PROMPT = """Tu mets en forme les lignes d'un devis de renovation BTP francais, dans le style de l'entreprise.
Pour chaque ligne recue, renvoie exactement une ligne, dans le meme ordre :
- group : la piece ou le lot ("Couloir d'entree", "Cuisine", "3 Chambres", "WC", "Electricite", "Boiseries (portes, plinthes, fenetres interieures)"...). Deux lignes consecutives de la meme piece gardent le meme group. null si aucune piece.
- title : sous-titre court quand une piece a plusieurs lignes ou une zone precise ("Murs et Plafonds", "Meubles", "Plafonds & Murs"), sinon null.
- details : 1 a 4 phrases courtes, professionnelles, sans fautes, une idee par phrase.
Regles :
- Ne jamais inventer de travaux, de prix, de quantites ou de materiaux qui ne sont pas dans les lignes.
- Si une consigne generale est donnee (ex: "travaux a emulsion acrylique aspect mat finition A, a la place de peinture satinee + mate"), applique-la a toutes les lignes de peinture concernees.
- Ne recopie pas les mots inutiles ("sur les", "deux couches de satinee" remplace par la consigne, etc.).

Exemple de style attendu :
{"group": "Cuisine", "title": "Murs et Plafonds", "details": ["Lavage, rebouchage, preparation des supports", "Travaux a l'emulsion acrylique - aspect mat finition A - travaux soignes"]}
{"group": "3 Chambres", "title": null, "details": ["Travaux a l'emulsion acrylique - aspect mat finition A - travaux soignes", "Nettoyage de la moquette chambre parentale", "Demontage de 3 radiateurs"]}

Reponds uniquement avec le JSON {"items": [...]}.
"""

POLISH_JSON_SCHEMA = {
    "name": "devis_lines_style",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "group": {"type": ["string", "null"]},
                        "title": {"type": ["string", "null"]},
                        "details": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["group", "title", "details"],
                },
            }
        },
        "required": ["items"],
    },
}


# Frequent misspellings in renovation messages (written fast, without accents) -> correct word.
SPELLING_WORDS = {
    "electricite": "électricité", "électricite": "électricité", "electricité": "électricité",
    "eléctricité": "électricité", "eléctricite": "électricité", "electrique": "électrique",
    "fenetre": "fenêtre", "fenetres": "fenêtres", "plinte": "plinthe", "plintes": "plinthes", "plinths": "plinthes",
    "interieur": "intérieur", "interieurs": "intérieurs", "exterieur": "extérieur", "exterieurs": "extérieurs",
    "emulsion": "émulsion", "acrylic": "acrylique", "securite": "sécurité", "sécurite": "sécurité",
    "logemant": "logement", "demontage": "démontage", "revision": "révision", "equipement": "équipement",
    "entree": "entrée", "peintur": "peinture", "plafont": "plafond", "plafons": "plafonds", "moquete": "moquette",
    "netoyage": "nettoyage", "preparation": "préparation", "reparation": "réparation", "renovation": "rénovation",
    "realisation": "réalisation", "deplacement": "déplacement", "satines": "satinées", "decorative": "décorative",
    "decoratives": "décoratives", "evacuation": "évacuation", "degats": "dégâts", "facade": "façade",
    "facades": "façades", "cuisinne": "cuisine", "chambe": "chambre", "chambes": "chambres",
}
SPELLING_PHRASES = [
    (r"\ba\s+(?:l['’]\s*)?émulsion\b", "à l'émulsion"),
    (r"\s+_\s+", " - "),
    (r"\btravaux soigné\b", "travaux soignés"),
    (r"\bpeinture (laqu|satin)é\b", r"peinture \1ée"),
    (r"\bface intérieur\b", "face intérieure"),
    (r"\b([2-9]|\d{2,})\s+(radiateur|spot|porte|fenêtre|prise|couche|chambre|plinthe)\b", r"\1 \2s"),
    (r"\ba la place\b", "à la place"),
]


def _keep_case(original: str, replacement: str) -> str:
    return replacement[:1].upper() + replacement[1:] if original[:1].isupper() else replacement


def fix_spelling(text: str) -> str:
    for wrong, right in SPELLING_WORDS.items():
        text = re.sub(rf"\b{wrong}\b", lambda m: _keep_case(m.group(0), right), text, flags=re.IGNORECASE)
    for pattern, replacement in SPELLING_PHRASES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _lot_from_title(title: str) -> tuple[str | None, str | None]:
    """'Cuisine plafond et mur' -> ('Cuisine', 'Plafond et mur'); 'Lot électricité' -> ('Électricité', None)."""
    lot = re.match(r"^lot\s+(.+)$", title, re.IGNORECASE)
    if lot:
        return _capitalize(lot.group(1).strip()), None
    room = ROOM_AT_START.match(title)
    if room:
        rest = room.group(2).strip(" -:,")
        return _capitalize(room.group(1)), _capitalize(rest) or None
    return None, title


def group_items_by_lot(items: list[DocumentItem]) -> list[DocumentItem]:
    """Local rules used when the AI is unavailable: put each line under its room / lot."""
    grouped: list[DocumentItem] = []
    previous_group: str | None = None
    for item in items:
        item = item.model_copy(update={
            "title": fix_spelling(item.title) if item.title else None,
            "description": _capitalize(fix_spelling(item.description)),
        })
        if item.title:
            item.group, item.title = _lot_from_title(item.title)
        if not item.group:
            # "Sur les meubles de la cuisine ..." continues the Cuisine lot.
            mentioned = ROOM_ANYWHERE.search(item.description)
            if previous_group and mentioned and mentioned.group(1).lower() in previous_group.lower():
                item.group = previous_group
        grouped.append(item)
        previous_group = item.group
    return grouped


def _openai_complete(prompt: str) -> str:
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": POLISH_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_schema", "json_schema": POLISH_JSON_SCHEMA},
        temperature=0.2,
    )
    return response.choices[0].message.content or "{}"


def polish_items(
    items: list[DocumentItem],
    complete: Callable[[str], str] | None = None,
) -> list[DocumentItem]:
    """Rewrite line texts in the company style. Prices and quantities are never sent to nor taken from the AI."""
    if not items:
        return items
    if complete is None:
        if not settings.openai_api_key:
            return group_items_by_lot(items)
        complete = _openai_complete

    lines = [{"title": it.title, "description": it.description} for it in items]
    try:
        answer = json.loads(complete(json.dumps({"lignes": lines}, ensure_ascii=False)))
        styled = answer["items"]
        if len(styled) != len(items) or any(not s.get("details") for s in styled):
            raise ValueError(f"AI returned {len(styled)} lines for {len(items)}, or empty details")
    except Exception as exc:
        LOGGER.warning("AI line styling unavailable (%s). Using local lot grouping.", exc)
        return group_items_by_lot(items)

    polished = []
    for item, style in zip(items, styled):
        polished.append(item.model_copy(update={
            "group": style.get("group") or None,
            "title": style.get("title") or None,
            "description": "\n".join(d.strip() for d in style["details"] if d.strip()),
        }))
    return polished
