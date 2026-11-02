from __future__ import annotations

import math
import re
import logging
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.services.validator import parse_french_date


LOGGER = logging.getLogger("whatsapp_conversation")
UNKNOWN_VALUE = "\u00e0 confirmer"

WAITING_FOR_DOCUMENT_TYPE = "waiting_for_document_type"
COLLECTING_CLIENT_INFO = "collecting_client_info"
COLLECTING_ITEMS = "collecting_items"
CONFIRMING_SUMMARY = "confirming_summary"
GENERATING_DOCUMENT = "generating_document"
COMPLETED = "completed"

SESSION_STATES = {
    WAITING_FOR_DOCUMENT_TYPE,
    COLLECTING_CLIENT_INFO,
    COLLECTING_ITEMS,
    CONFIRMING_SUMMARY,
    GENERATING_DOCUMENT,
    COMPLETED,
}

REQUIRED_FIELDS = [
    "document_type",
    "client_name",
    "client_address",
    "worksite_address",
    "service_description",
    "quantity",
    "unit_price_ht",
]

FIELD_LABELS = {
    "document_type": "type de document (devis ou facture)",
    "client_name": "nom client",
    "client_address": "adresse client",
    "worksite_address": "adresse chantier",
    "service_description": "description de la prestation",
    "quantity": "quantite",
    "unit_price_ht": "prix unitaire HT",
    "vat_rate": "TVA si differente du taux par defaut",
    "discount_ht": "remise si applicable",
    "deposit_ttc": "acompte si applicable",
    "payment_method": "mode de paiement si different",
}

DEFAULT_SESSION_DATA: dict[str, Any] = {
    "document_type": "devis",
    "client_name": None,
    "client_address": None,
    "worksite_address": None,
    "items": [],
    "service_description": None,
    "quantity": None,
    "unit_price_ht": None,
    "vat_rate": None,
    "discount_ht": None,
    "deposit_ttc": None,
    "payment_method": None,
    "uncertain_fields": [],
}


class InvoiceApi(Protocol):
    def create_from_text(self, text: str) -> dict[str, Any]:
        ...

    def correct_file(self, path: Path) -> dict[str, Any]:
        ...

    def download_generated_file(self, url_or_path: str | None) -> Path | None:
        ...

    def health(self) -> bool:
        ...


@dataclass
class ChatState:
    processed_message_ids: set[str] = field(default_factory=set)
    step: str = WAITING_FOR_DOCUMENT_TYPE
    data: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SESSION_DATA))
    pending_text: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    generated_once: bool = False
    last_summary: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatState":
        session_data = dict(DEFAULT_SESSION_DATA)
        session_data.update(data.get("data") or data.get("document") or {})
        step = data.get("step") or WAITING_FOR_DOCUMENT_TYPE
        if step not in SESSION_STATES:
            step = WAITING_FOR_DOCUMENT_TYPE
        return cls(
            processed_message_ids=set(data.get("processed_message_ids", [])),
            step=step,
            data=session_data,
            pending_text=list(data.get("pending_text", [])),
            missing_fields=list(data.get("missing_fields", [])),
            generated_once=bool(data.get("generated_once", False)),
            last_summary=str(data.get("last_summary", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed_message_ids": sorted(self.processed_message_ids),
            "step": self.step,
            "data": self.data,
            "pending_text": self.pending_text,
            "missing_fields": self.missing_fields,
            "generated_once": self.generated_once,
            "last_summary": self.last_summary,
        }

    def reset(self) -> None:
        self.step = WAITING_FOR_DOCUMENT_TYPE
        self.data = dict(DEFAULT_SESSION_DATA)
        self.pending_text.clear()
        self.missing_fields.clear()
        self.generated_once = False
        self.last_summary = ""

    @property
    def combined_text(self) -> str:
        return "\n".join(part.strip() for part in self.pending_text if part.strip()).strip()


class ConversationManager:
    def __init__(self, api: InvoiceApi):
        self.api = api

    def handle_command(self, command: str, state: ChatState) -> str | None:
        command = command.strip().lower()
        if command == "/start":
            return self.start_message()
        if command == "/help":
            return self.help_message()
        if command == "/reset":
            state.reset()
            return "Conversation reinitialisee. Envoyez le type de document: devis ou facture."
        if command == "/status":
            api_status = "pret" if self.api.health() else "API indisponible"
            pending = ", ".join(FIELD_LABELS.get(field, field) for field in state.missing_fields) or "aucun"
            return f"Bot actif. Etape: {state.step}. API: {api_status}. Champs manquants: {pending}."
        return None

    @staticmethod
    def start_message() -> str:
        return (
            "Bonjour. Je peux creer ou corriger des factures et devis francais. "
            "Envoyez vos informations client/prestation ou un document a corriger."
        )

    @staticmethod
    def help_message() -> str:
        return (
            "Informations a envoyer:\n"
            "- Devis ou facture\n"
            "- Client name\n"
            "- Client address / worksite address\n"
            "- Service description\n"
            "- Quantity\n"
            "- Unit price HT\n"
            "- VAT rate if different\n"
            "- Deposit or discount if any"
        )

    def handle_text(self, text: str, state: ChatState) -> tuple[str, list[Path]]:
        command_response = self.handle_command(text, state)
        if command_response is not None:
            return command_response, []

        if _is_cancel(text):
            state.reset()
            return "C'est annule, rien n'a ete genere ni envoye. Envoyez une nouvelle demande quand vous voulez.", []
        if _is_greeting(text):
            return self.start_message(), []

        if state.step == COMPLETED:
            state.reset()

        if state.step == CONFIRMING_SUMMARY and _is_confirmation(text):
            state.step = GENERATING_DOCUMENT
            try:
                result = self.api.create_from_text(create_document_payload(state))
            except Exception as exc:
                LOGGER.exception("Backend create-from-text failed.")
                state.step = CONFIRMING_SUMMARY
                return backend_error_message(exc), []
            return self._response_from_result(result, state)

        state.pending_text.append(text)
        merge_session_data(state, extract_session_data(text, state))
        state.missing_fields = detect_missing_fields(state)
        state.step = next_step_for_state(state)

        if state.step == CONFIRMING_SUMMARY:
            state.last_summary = build_summary(state)
            return (
                "Voici le resume avant generation :\n"
                f"{state.last_summary}\n\n"
                "Repondez OUI pour generer, ou envoyez une correction.",
                [],
            )

        return ask_for_missing_fields(state), []

    def handle_file(self, path: Path, caption: str, state: ChatState) -> tuple[str, list[Path]]:
        if caption.strip():
            state.pending_text.append(caption.strip())
            merge_session_data(state, extract_session_data(caption, state))
        state.step = GENERATING_DOCUMENT
        try:
            result = self.api.correct_file(path)
        except Exception as exc:
            LOGGER.exception("Backend file correction failed for %s.", path)
            state.step = next_step_for_state(state)
            return backend_error_message(exc), []
        return self._response_from_result(result, state)

    def _response_from_result(self, result: dict[str, Any], state: ChatState) -> tuple[str, list[Path]]:
        state.generated_once = True
        state.missing_fields = list(result.get("missing_fields") or [])

        if state.missing_fields:
            state.step = next_step_for_state(state)
            return ask_for_missing_fields(state), []

        files = [
            path
            for path in (
                self.api.download_generated_file(result.get("pdf_url")),
                self.api.download_generated_file(result.get("docx_url")),
                self.api.download_generated_file(result.get("report_url")),
            )
            if path is not None
        ]
        state.step = COMPLETED
        status = result.get("status", "ready")
        message = (
            "Votre document est pret \u2705\n"
            f"Statut: {status}.\n"
            f"Les donnees incertaines restent marquees {UNKNOWN_VALUE}."
        )
        return message, files


BTP_SERVICE_WORDS = [
    "peinture",
    "pose",
    "carrelage",
    "parquet",
    "maconnerie",
    "maçonnerie",
    "plâtrerie",
    "platrerie",
    "isolation",
    "installation",
    "renovation",
    "rénovation",
    "reparation",
    "réparation",
    "nettoyage",
    "electricite",
    "électricité",
    "plomberie",
    "menuiserie",
    "cloison",
    "démolition",
    "demolition",
    "remplacement",
    "chape",
    "enduit",
]


# A number that is not glued to a date ("29/02/2026"), a reference ("FAC-2026") or a float exponent ("1e309").
# French thousands: "1 500", "1.234,56", "1 000,00".
AMOUNT = r'(?<![\d/.,])(?<!\w-)(?<!\d[eE])(?<![a-wyzA-WYZà-ÿ])(\d{1,3}(?:[   .]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)'
CURRENCY = r'(?:€|euros?|eur\b|e\s*ht\b|usd\b|\$|dollars?\b|ttc\b|ht\b|hors\s+tax(?:es?)?\b)'
UNIT_WORDS = r'(?:m2|m²|m3|m³|ml|h|heures?|jours?|u|unit[eé]s?|mois|pi[eè]ces?)(?![\w²³])'
PRICE_PATTERN = re.compile(
    AMOUNT + r'\s*(k)?\s*(' + CURRENCY + r'(?:\s*(?:ht|ttc|hors\s+tax(?:es?)?)\b)?)'
    r'(?:\s*(?:/|par\s+|le\s+|la\s+)\s*' + UNIT_WORDS + r'|\s+(?:heure|jour)\b)?'
    r'(?=[,.;:/\s|=)\]\'"!?]|$)',
    re.IGNORECASE,
)
# Typed fast: "800e", "200 E", or no currency at all at the end of the line ("peinture salon 800").
PRICE_E_PATTERN = re.compile(AMOUNT + r'\s*e(?=[,.;\s]|$)', re.IGNORECASE)
PRICE_TRAILING_PATTERN = re.compile(AMOUNT + r'\s*$')
# "6 fenetres 350 l une", "2 windows at 250 each", "40 m2 prix 35"
EACH_ALTERNATIVES = r"l\s*['’ ]?\s*une?|chacune?|chaque|chaqune|each|la\s+pi[eè]ce|pi[eè]ce|l['’]\s*unit[eé]"
EACH_WORDS = rf"(?:{EACH_ALTERNATIVES})\b"
PRICE_EACH_PATTERN = re.compile(AMOUNT + r"\s*(?:€|euros?)?\s*(?:ht\s+)?(?=" + EACH_WORDS + ")", re.IGNORECASE)
PRICE_LABEL_PATTERN = re.compile(r"\bprix\s+(?:unit\w*\s+)?(?:final\s+)?(?:ht\s+)?" + AMOUNT, re.IGNORECASE)
ADDRESS_WORDS = re.compile(r"\b(rue|avenue|av|bd|boulevard|chemin|all[eé]e|impasse|route|place|quai)\b", re.IGNORECASE)
MIN_PRICE_WITHOUT_EURO = 10

QTY_NUMBER = r"(?<![\w/.,-])(\d+/\d+|\d+(?:[.,]\d+)?)"
QTY_UNIT_PATTERN = re.compile(
    QTY_NUMBER + r"\s*(?:de\s+)?(m2|m²|m3|m³|m[eè]tres?\s+(?:cubes?|carr[ée]s?)|ml|heures?|journ[ée]e?s?|jours?|unit[eé]s?|u|pi[eè]ces?|passages?)(?![\w²³])",
    re.IGNORECASE,
)
HOURS_PATTERN = re.compile(r"(?<![\w/.,-])(\d+)\s*h\s*(\d{2})?(?![a-z])", re.IGNORECASE)
PLAIN_NUMBER_PATTERN = re.compile(r"(?<![\w/.,-])(\d+(?:[.,]\d+)?)(?![\d/])")
TIMES_BEFORE_PATTERN = re.compile(QTY_NUMBER + r"\s*(?:m2|m²|m3|m³|ml|h|heures?|jours?|u|unit[eé]s?)?\s*[x×*]\s*$", re.IGNORECASE)
TIMES_AFTER_PATTERN = re.compile(r"^\s*[x×*]\s*" + QTY_NUMBER, re.IGNORECASE)
# "3x100 HT=500", "40 m² x 45 = 18000 euro HT": quantity x unit price = stated line total.
TIMES_EQUALS_PATTERN = re.compile(
    QTY_NUMBER + r"\s*(?:m2|m²|m3|m³|ml|h|u|unit[eé]s?)?\s*[x×*]\s*" + AMOUNT
    + r"\s*(?:€|euros?)?\s*(?:ht)?\s*=\s*" + AMOUNT + r"\s*(?:€|euros?)?\s*(?:ht\b)?",
    re.IGNORECASE,
)
PREP_BEFORE_PRICE = re.compile(r"(?:\b(?:à|a|at)|@)\s*$", re.IGNORECASE)
PRICE_LABEL_BEFORE = re.compile(r"\bprix(?:\s+unit\w*)?\s*-?\s*$", re.IGNORECASE)

CLAUSE_SPLIT = re.compile(r"\s*[;|]\s*|,\s+|(?<![\s.][A-Za-z])\.\s+(?=[A-Z])")
SEGMENT_SPLIT = re.compile(r"(\s+(?:\+|plus|et|and|&)\s+)", re.IGNORECASE)
# "corrige ce texte OCR : ...", "facture: ...", "Devis : ..." -> the request part before the colon.
REQUEST_PREFIX = re.compile(r"^([^:]{0,80}):\s*")
REQUEST_PREFIX_WORDS = re.compile(r"\b(?:ocr|pdf|scan\w*|texte|document|corrig\w*|photo)\b", re.IGNORECASE)
NEGATED_AMOUNT = re.compile(r"\b(?:nn|non|pas|pa)\b\s+(?:\w+\s+){0,2}?\d", re.IGNORECASE)
INFO_CLAUSE_START = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:chanti?er|adresse|adr\b|clients?\b|clt\b|tva|taxe|(?:remise|r[ée]duction|rabais)\s*(?:globale?|de|:|=)?\s*\d|acompte|"
    r"d[ée]j[aà]\b|solde|siret|siren|iban|bic\b|total|montant|sous[\s-]?total|prix\b|pu\b|qt[ée]\b|quantit|"
    r"paiement|r[èe]glement|merci|date|[ée]ch[ée]ance|page\s*\d*|assurance|indemnit|p[ée]nalit|num[ée]ro|n°|"
    r"t[ée]l\b|email|mail\b|ttc\b|ht\s*=)",
    re.IGNORECASE,
)
INFO_CLAUSE_ANYWHERE = re.compile(r"\b(?:indemnit|p[ée]nalit|siret|iban|carte\s+bancaire)", re.IGNORECASE)
# "pose carrelage 45 m2, prix unitaire HT 35€/m2": the price clause completes the previous one.
PRICE_CLAUSE_START = re.compile(r"^\s*(?:prix|pu)\b", re.IGNORECASE)
INFO_WORDS_WITHOUT_PRICE = re.compile(r"\b(?:tva|taxe|remise|acompte|chantier)\b", re.IGNORECASE)
REQUEST_FILLER_WORDS = {
    "bjs", "bjr", "bonjour", "salam", "salut", "slt", "hello", "stp", "svp", "please", "merci", "urgent", "ok",
    "fait", "fais", "faites", "faire", "moi", "make", "create", "cree", "creer", "prepare", "preparer", "une", "un",
    "an", "a", "la", "le", "me", "nous", "ce", "cette", "sors", "envoie", "corrige", "corriger", "nouvelle",
    "nouveau", "new", "the", "my", "mon", "ma", "pour", "pr", "to", "au", "chez", "c", "cest", "demain",
    "invoice", "quote", "estimate", "de", "du", "des", "d", "l", "et",
}
# Words of the request or of the VAT regime that are not part of the prestation itself.
DESCRIPTION_NOISE = re.compile(
    r"\[[^\]]*\]|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:je\s+suis\s+)?(?:auto|micro)[\s-]?entrepreneur|sans\s+tva|293\s*b|auto[\s-]?liquidation|"
    r"sous[\s-]?traitant(?:\s+btp)?|(?:au\s+)?donneur\s+d\s*['’]?\s*ordre|"
    r"\b(?:tva|taxe)\s*(?:de|à|a|:|=)?\s*\d+(?:[.,]\d+)?\s*(?:%|pour\s*cent|pourcent)?|"
    r"\b\d+(?:[.,]\d+)?\s*(?:%|pour\s*cent|pourcent)?\s*(?:de\s+)?tva\b|"
    r"\b(?:moins|remise|r[ée]duction|rabais)\s*(?:globale?|de)?\s*\d+(?:[.,]\d+)?\s*(?:%|€|euros?)?(?:\s*(?:de\s+)?(?:remise|r[ée]duction|rabais)\b)?|"
    r"\bj\s*['’]?\s*ai\s+re[çc]u\b|\btva\b[^,.;\d]{0,15}\?|(?<!\S)(?:m2|m²|m3|m³|ml)(?!\S)|"
    r"\b\d+(?:[.,]\d+)?\s*%\s*(?:de\s+)?(?:remise|r[ée]duction|rabais)\b|"
    r"\bacompte\s*(?:de\s*)?\d+(?:[.,]\d+)?\s*%?|"
    r"(?:/|par\s+|le\s+|la\s+)\s*" + UNIT_WORDS + r"|"
    r"\bprix(?:\s+unit\w*)?(?:\s+final)?(?:\s+ht)?\b|"
    r"\b(?:hors\s+tax\w*|ht|ttc|forfait|ens|inclus|" + EACH_ALTERNATIVES + r")\b",
    re.IGNORECASE,
)
DESCRIPTION_TAIL = re.compile(
    r"\s(?:mais|parce\s+que|car|mets?|j\s*veux|je\s+veux|merci|stp|svp|pour\s+(?:un|le|la|ce)\s+chantier)\b.*$", re.IGNORECASE
)

# "Clients : X", "adrese ; Y", "Nom du clien - Z", "clt: W" ... label (1-3 words) + separator + value
LABEL_LINE_PATTERN = re.compile(r"^\s*([^\W\d_][^\W\d_'’ ]*(?:\s+[^\W\d_][^\W\d_'’]*){0,2})\s*[:;=\-]\s*(.+)$")
LABEL_ABBREVIATIONS = {"adr": "client_address", "addr": "client_address", "clt": "client_name", "cli": "client_name"}
LABEL_WORDS = [("chantier", "worksite_address"), ("adresse", "client_address"), ("client", "client_name")]
LABEL_FILLER_WORDS = {"nom", "du", "de", "la", "le", "l", "d"}
# How close a misspelled word must be to the real one (difflib ratio): "cliant", "adrese", "factur" pass, "Denis" does not.
LABEL_SIMILARITY = 0.75
# "factr", "fakture", "faqture" still mean facture.
DOCUMENT_WORD_SIMILARITY = 0.83
INVOICE_WORDS = {"fact", "invoice", "فاتورة"}
QUOTE_WORDS = {"devi", "dvis", "quote", "estimate"}
# A non-priced line right after an item that is this long is a detail of that item, not a new section title.
ITEM_DETAIL_MIN_LENGTH = 60


def _plain_word(word: str) -> str:
    word = unicodedata.normalize("NFKD", word.lower())
    return "".join(ch for ch in word if ch.isalpha())


def _similar(word: str, target: str, threshold: float) -> bool:
    return word == target or (len(word) >= 4 and SequenceMatcher(None, word, target).ratio() >= threshold)


def _label_field(label: str) -> str | None:
    words = [w for w in (_plain_word(w) for w in label.split()) if w and w not in LABEL_FILLER_WORDS]
    if not words:
        return None
    if len(words) == 1 and words[0] in LABEL_ABBREVIATIONS:
        return LABEL_ABBREVIATIONS[words[0]]
    if words == ["lieu"]:
        return "worksite_address"
    for target, field in LABEL_WORDS:
        if any(_similar(w.rstrip("s"), target, LABEL_SIMILARITY) or _similar(w, target, LABEL_SIMILARITY) for w in words):
            return field
    return None


def _split_label_line(line: str) -> tuple[str, str] | None:
    match = LABEL_LINE_PATTERN.match(line)
    # A line with a price is a one-line request ("Devis Client: X, adresse Y, peinture 1450€"), not a label.
    if not match or find_price(line):
        return None
    field = _label_field(match.group(1))
    return (field, match.group(2)) if field else None


def _is_document_word(word: str, target: str) -> bool:
    plain = _plain_word(word)
    if target == "facture":
        return plain in INVOICE_WORDS or word in INVOICE_WORDS or _similar(plain, "facture", DOCUMENT_WORD_SIMILARITY)
    return plain in QUOTE_WORDS or _similar(plain, "devis", DOCUMENT_WORD_SIMILARITY)


def detect_document_word(text: str) -> str | None:
    """'facture', 'devis' or None, tolerant to typos: 'factur', 'fakture', 'devi', 'dvis'."""
    words = [w for w in re.split(r"[\s,.;:!?/()]+", text.lower()) if w]
    if any(_is_document_word(w, "facture") for w in words):
        return "facture"
    if any(_is_document_word(w, "devis") for w in words):
        return "devis"
    return None


def parse_amount(raw: str) -> float:
    """'1 500' -> 1500, '1.234,56' -> 1234.56, '39,90' -> 39.9, '3/4' -> 0.75."""
    raw = re.sub(r"[\s  ]", "", raw)
    if "/" in raw:
        num, den = raw.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    return float(raw)


def _is_year(amount: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{2}", amount))


def find_price(line: str) -> re.Match | None:
    match = PRICE_PATTERN.search(line)
    if match:
        return match
    for pattern in (PRICE_EACH_PATTERN, PRICE_LABEL_PATTERN):
        match = pattern.search(line)
        if match and not _is_year(match.group(1)):
            return match
    for pattern in (PRICE_E_PATTERN, PRICE_TRAILING_PATTERN):
        match = pattern.search(line)
        if not match or not re.search(r"[^\W\d_]", line[:match.start()]):
            continue
        amount = match.group(1).replace(" ", "")
        # Not a postcode or a street address, not a year ("en 2026"), and not "2e couche".
        if re.fullmatch(r"\d{5}", amount) or _is_year(amount) or ADDRESS_WORDS.search(line):
            continue
        if parse_amount(amount) >= MIN_PRICE_WITHOUT_EURO:
            return match
    return None


def _strip_request_prefix(text: str) -> str:
    """'bjs devis pour madam legrand 3h netoyage' -> '3h netoyage'."""
    prefix = REQUEST_PREFIX.match(text)
    if prefix and (detect_document_word(prefix.group(1)) or REQUEST_PREFIX_WORDS.search(prefix.group(1))):
        text = text[prefix.end():]
    client = _find_client_name_match(text)
    if client:
        text = text[:client[1]] + " " + text[client[2]:]
    words = text.split()
    while words and (_plain_word(words[0]) in REQUEST_FILLER_WORDS or detect_document_word(words[0]) or not _plain_word(words[0])):
        if re.match(r"^[\d.,]", words[0]):
            break
        words.pop(0)
    return " ".join(words)


def _clean_description(text: str) -> str:
    text = DESCRIPTION_TAIL.sub("", " " + text)
    text = DESCRIPTION_NOISE.sub(" ", text)
    words = [w for w in text.split() if not (detect_document_word(w) and len(_plain_word(w)) >= 4)]
    text = " ".join(words)
    text = re.sub(r"^\s*lignes?\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:fait|faits|réalisés?|realises?)\s+le\s*$", "", text, flags=re.IGNORECASE)
    text = text.strip(" -:+,;.=/'\"")
    text = re.sub(r"(?:\s+(?:à|a|at|x|@|de|du|des|d['’]?|et|le|la|les|pour|sur))+\s*$", "", " " + text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:\s*(?:de|du|des|d['’]|le|la|les|l['’]|pour|et))+\s+", "", text.strip() + " ", flags=re.IGNORECASE)
    return text.strip(" -:+,;.=/'\"")


def _segment_vat_rate(segment: str) -> float | None:
    rates = find_vat_rates(segment)
    return rates[0] if rates else None


def _parse_priced_segment(segment: str) -> dict[str, Any] | None:
    """One prestation: '2 porte interieur a 350 euro HT chaqune' -> qty 2, unit price 350."""
    flags: list[str] = []
    stated_total = None
    equals = TIMES_EQUALS_PATTERN.search(segment)
    if equals:
        qty, price, stated_total = (parse_amount(equals.group(i)) for i in (1, 2, 3))
        basis = "ht"
        rest = _strip_request_prefix(segment[:equals.start()]) + " " + segment[equals.end():]
        description = _clean_description(rest)
    else:
        match = find_price(segment)
        if not match:
            return None
        price = parse_amount(match.group(1))
        price_text = match.group(0).lower()
        if match.re is PRICE_PATTERN and match.group(2):
            price *= 1000
        if re.search(r"(?:^|\s)-\s*$", segment[:match.start()]):
            price = -price
        if re.search(r"usd|\$|dollar", price_text):
            flags.append("devise")
        basis = "ttc" if "ttc" in price_text else "ht"

        before = _strip_request_prefix(re.sub(r"-\s*$", "", segment[:match.start()]))
        after = segment[match.end():]
        per_unit = bool(
            re.search(r"(?:/|par\s|le\s|la\s|heure|jour)", price_text[len(match.group(1)):])
            or PREP_BEFORE_PRICE.search(before)
            or PRICE_LABEL_BEFORE.search(before)
            or match.re in (PRICE_EACH_PATTERN, PRICE_LABEL_PATTERN)
            or re.match(r"\s*" + EACH_WORDS, after, re.IGNORECASE)
        )
        qty = 1.0
        times_before = TIMES_BEFORE_PATTERN.search(before)
        times_after = TIMES_AFTER_PATTERN.search(after)
        if times_before:
            qty = parse_amount(times_before.group(1))
            before = before[:times_before.start()] + " " + before[times_before.end():]
        elif times_after:
            qty = parse_amount(times_after.group(1))
            after = after[:times_after.start()] + " " + after[times_after.end():]
        elif per_unit:
            hours = list(HOURS_PATTERN.finditer(before))
            units = list(QTY_UNIT_PATTERN.finditer(before))
            numbers = list(PLAIN_NUMBER_PATTERN.finditer(before))
            if hours and hours[-1].group(2):
                found, qty = hours[-1], int(hours[-1].group(1)) + int(hours[-1].group(2)) / 60
            elif units:
                found, qty = units[-1], parse_amount(units[-1].group(1))
            elif hours:
                found, qty = hours[-1], float(hours[-1].group(1))
            elif numbers:
                found, qty = numbers[-1], parse_amount(numbers[-1].group(1))
            else:
                found = None
            if found:
                before = before[:found.start()] + " " + before[found.end():]
        else:
            # "5 prise elec 25HT": a count at the start of the line is the quantity.
            leading = re.match(r"\s*(\d+)\s+(?=[^\W\d_])", before)
            if leading:
                qty = float(leading.group(1))
                before = before[leading.end():]
        description = _clean_description(before + " " + after)

    # "le montant ressemble a 8O0,OO ou 8000,00": the user does not know the amount.
    if re.search(r"\bou\b", segment) and (len(re.findall(r"\d", segment)) > 6 or re.search(r"\d[oO]|[oO]\d", segment)):
        price, description = None, ""
        flags.append("unit_price_ht")
    item: dict[str, Any] = {
        "title": None,
        "description": description or UNKNOWN_VALUE,
        "quantity": round(qty, 4),
        "unit_price_ht": price,
        "vat_rate": _segment_vat_rate(segment),
    }
    if basis == "ttc":
        item["price_basis"] = "ttc"
    if stated_total is not None:
        item["total_ht"] = stated_total
    if flags:
        item["uncertain"] = flags
    return item


def _split_segments(clause: str) -> list[str]:
    """'12 sacs a 18€HT et pose 2h a 90eHT' -> two segments; 'mitigeur et électroménager 450€' stays one."""
    parts = SEGMENT_SPLIT.split(clause)
    segments: list[str] = []
    current, current_separator = "", ""
    for index in range(0, len(parts), 2):
        separator = parts[index - 1] if index else ""
        if current:
            current += separator + parts[index]
        else:
            # The "+" / "plus" / "et" between two prestations belongs to neither of them.
            current, current_separator = parts[index], separator
        if find_price(parts[index]) or TIMES_EQUALS_PATTERN.search(parts[index]):
            segments.append(current)
            current = ""
    if current.strip():
        if segments:
            segments[-1] += current_separator + current
        else:
            segments.append(current)
    return segments


def _is_info_clause(clause: str) -> bool:
    if _split_label_line(clause):
        return True
    prefix = REQUEST_PREFIX.match(clause)
    stripped = clause[prefix.end():] if prefix and (detect_document_word(prefix.group(1)) or REQUEST_PREFIX_WORDS.search(prefix.group(1))) else clause
    if INFO_CLAUSE_START.match(stripped) or INFO_CLAUSE_ANYWHERE.search(clause):
        return True
    has_price = find_price(clause) or TIMES_EQUALS_PATTERN.search(clause)
    return not has_price and bool(detect_document_word(clause) or INFO_WORDS_WITHOUT_PRICE.search(clause))


def _parse_priced_line(line: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    leading: list[str] = []
    for clause in CLAUSE_SPLIT.split(line):
        if not clause or not clause.strip():
            continue
        if PRICE_CLAUSE_START.match(clause) and PRICE_PATTERN.search(clause):
            clause, leading = ", ".join(leading + [clause]), []
        elif _is_info_clause(clause):
            # "facture nettoyage bureau, ..., total HT 700": keep the prestation words.
            rest = _strip_request_prefix(clause)
            if not items and rest and not find_price(clause) and _extract_service_fallback(rest):
                leading.append(rest)
            continue
        if NEGATED_AMOUNT.search(clause):
            continue
        new_items = [it for it in (_parse_priced_segment(seg) for seg in _split_segments(clause)) if it]
        if not new_items:
            rest = _strip_request_prefix(clause)
            if not items and rest:
                leading.append(rest)
            continue
        if leading and not items:
            first = new_items[0]
            joined = ", ".join(leading)
            first["description"] = joined if first["description"] == UNKNOWN_VALUE else f"{joined}, {first['description']}"
            leading = []
        items.extend(new_items)
    return items


def _is_info_line(line: str) -> bool:
    if _split_label_line(line):
        return True
    # "Fait moi un devis", "Devis dhaou", "factur pour X" ... : document request lines, never a prestation
    return detect_document_word(line) is not None and not find_price(line)


def _extract_labelled_line(text: str, field: str) -> str | None:
    for line in text.splitlines():
        label = _split_label_line(line)
        if label and label[0] == field:
            value = " ".join(label[1].split()).strip(" ,.;")
            if _mentions_unknown(value.lower()):
                return UNKNOWN_VALUE
            if value:
                return value
    return None


def parse_items_from_text(text: str) -> list[dict[str, Any]]:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    items: list[dict[str, Any]] = []
    pending_headers: list[str] = []
    first_header_is_detail = False

    for line in lines:
        lower_line = line.lower()
        if re.search(r'^\s*[-*•]?\s*(remise|discount|acompte|deposit)\b', lower_line):
            continue
        if _is_info_line(line):
            continue

        if find_price(line) or TIMES_EQUALS_PATTERN.search(line):
            line_items = _parse_priced_line(re.sub(r'^\s*[-*•]\s*', '', line))
            if not line_items:
                continue
            first = line_items[0]
            desc_part = "" if first["description"] == UNKNOWN_VALUE else first["description"]

            # The detail line belongs to the previous item unless this line has no description of its own.
            if first_header_is_detail and desc_part.strip():
                _append_item_detail(items[-1], pending_headers.pop(0))
            first_header_is_detail = False

            # The first non-priced line is the lot title ("Cuisine plafond et mur"), the rest are its details.
            title = pending_headers[0] if pending_headers else None
            full_desc = ' - '.join(pending_headers[1:] + [desc_part]) if desc_part else ' - '.join(pending_headers[1:])
            pending_headers = []

            full_desc = full_desc.strip(' -:+,')
            if not full_desc:
                title, full_desc = None, title or ''
            if not title:
                title, full_desc = _split_room_title(full_desc)

            first["title"] = title
            first["description"] = full_desc.strip() or UNKNOWN_VALUE
            items.extend(line_items)
        else:
            if any(marker in lower_line for marker in ["nom client", "client:", "adresse:", "devis pour", "facture pour"]):
                continue
            if items and not pending_headers and len(line) >= ITEM_DETAIL_MIN_LENGTH:
                first_header_is_detail = True
            pending_headers.append(line)

    if first_header_is_detail:
        _append_item_detail(items[-1], pending_headers[0])

    return items


ROOM_WORDS = (
    r"(?:\d+\s+)?(?:couloirs?\s+d['’]entr[ée]e|chambres?|cuisines?|couloirs?|wc|toilettes|salles?\s+de\s+bains?|"
    r"salles?\s+d['’]eau|salons?|s[ée]jours?|entr[ée]es?|bureaux?|escaliers?|garages?|caves?|balcons?|"
    r"terrasses?|fa[cç]ades?|boiseries|pi[eè]ces?)"
)
ROOM_TITLE_PATTERN = re.compile(
    rf"^(?:sur|dans|pour)\s+(?:les|la|le|l['’])\s*({ROOM_WORDS})\b\s*(.*)$",
    re.IGNORECASE,
)


def _split_room_title(description: str) -> tuple[str | None, str]:
    # "Dans les WC deux couches ..." -> ("WC", "Deux couches ...")
    match = ROOM_TITLE_PATTERN.match(description)
    if not match or not match.group(2):
        return None, description
    room, rest = match.group(1), match.group(2)
    return room[0].upper() + room[1:], rest[0].upper() + rest[1:]


def _append_item_detail(item: dict[str, Any], detail: str) -> None:
    # An untitled item with a detail line: its own text becomes the title, the detail its description.
    if item.get('title'):
        item['description'] += ' - ' + detail
    else:
        item['title'], item['description'] = item['description'], detail


CLIENT_TITLE = r"(?:m\.?\s+et\s+mme|mr\.?|mrs\.?|mme|mlle|madame|madam|monsieur|mademoiselle|m\.)"
CLIENT_MARKERS = [
    re.compile(rf"(?<![\w.]){CLIENT_TITLE}(?=\s)", re.IGNORECASE),  # the title itself starts the name
    re.compile(r"\bnom\s+(?:du\s+)?clients?\s*[:;\-]?\s*", re.IGNORECASE),
    re.compile(r"(?<!adresse\s)(?<!chantier\s)\bclients?\b\s*[:;\-]?\s*", re.IGNORECASE),
    re.compile(r"\bchez\s+", re.IGNORECASE),
    re.compile(r"\b(?:pour|pr|to|au\s+nom\s+de)\s+", re.IGNORECASE),
    re.compile(r"(?:لل|ال)عميل\s+"),
]
CLIENT_NAME_END = re.compile(
    r"[,;|\[\]()]|\s*:|\s+\d|\s+(?:et|mais|pour|avec|sur|le\s+chantier|adresse|chantier|prestation|travaux|tva|prix|"
    r"facture|devis|merci|stp|svp|qui|à|au|de\s+la|no|for|on|على|" + "|".join(BTP_SERVICE_WORDS) + r")\b",
    re.IGNORECASE,
)
CLIENT_REJECT_WORDS = {
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "ce", "cette", "cet", "mon", "ma", "mes", "son", "sa",
    "ses", "tous", "toutes", "tout", "cent", "pourcent", "refaire", "faire", "finalement", "maintenant", "mais", "et",
    "veut", "chantier", "site", "travaux", "particulier", "lundi", "mardi", "mercredi", "jeudi", "vendredi",
    "samedi", "dimanche", "demain", "hier", "moi", "toi", "nous", "vous", "lui", "qui", "que", "rien", "ca", "the",
    "an", "it", "me", "my", "en", "aux", "sur", "avec", "pour", "chaque", "autre", "same", "job", "devis", "facture",
}
MAX_CLIENT_NAME_WORDS = 5
BTP_PLAIN_WORDS = {_plain_word(word) for word in BTP_SERVICE_WORDS}

VAT_WORD_NUMBERS = {"vingt": 20.0, "dix": 10.0, "zero": 0.0, "zéro": 0.0, "cinq virgule cinq": 5.5}
VAT_AFTER_WORD = re.compile(
    r"\b(?:tva|taxe)\s*(?:de|à|a|:|=)?\s*(\d+(?:[.,]\d+)?|vingt|dix|z[ée]ro|cinq\s+virgule\s+cinq)\s*(%|pour\s*cent|pourcent)?",
    re.IGNORECASE,
)
VAT_BEFORE_WORD = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(%|pour\s*cent|pourcent)?\s*(?:de\s+)?(?:tva|taxe)\b", re.IGNORECASE)
VAT_UNKNOWN = re.compile(
    r"\btva\b[^,.;\d]{0,15}\?|\bpas\s+(?:le\s+)?(?:taux\s+)?(?:de\s+)?(?:la\s+)?tva\b|\btva\s+(?:à|a)\s+confirmer|"
    r"\b(?:tva|taxe)\s*\d+(?:[.,]\d+)?\s*%?\s*ou\s*\d|\bsi\s+\d+(?:[.,]\d+)?\s*%?\s+ou\s+\d+",
    re.IGNORECASE,
)
VAT_EXEMPT = re.compile(r"sans\s+tva|293\s*b|franchise\s+(?:en\s+)?(?:base|de\s+tva)|tva\s+non\s+applicable|(?:auto|micro)[\s-]?entrepr", re.IGNORECASE)
VAT_REVERSE_CHARGE = re.compile(r"auto[\s-]?liquidation", re.IGNORECASE)
VAT_EXEMPT_MENTION = "TVA non applicable, art. 293 B du CGI"
VAT_REVERSE_CHARGE_MENTION = "Autoliquidation : TVA due par le preneur, art. 283-2 nonies du CGI"

PERCENT = r"\s*(%|pour\s*cent|pourcent)"
DISCOUNT_PATTERNS = [
    re.compile(r"\b(?:remise|r[ée]duction|rabais|discount)\s*(?:ht\s*)?(?:globale?|de|:|=)?\s*" + AMOUNT + PERCENT + "?", re.IGNORECASE),
    re.compile(r"\bmoins\s+" + AMOUNT + PERCENT, re.IGNORECASE),
    re.compile(AMOUNT + r"\s*(%|€|euros?)\s*(?:de\s+)?(?:remise|r[ée]duction|rabais)\b", re.IGNORECASE),
]
DEPOSIT_PATTERNS = [
    re.compile(
        r"\b(?:acompte|deposit|d[ée]j[aà]\s+(?:pay[ée]|r[ée]gl[ée]|vers[ée])|vers[ée])\s*(?:ttc\s*)?(?:de|:|=)?\s*" + AMOUNT + r"\s*(%)?",
        re.IGNORECASE,
    ),
    re.compile(AMOUNT + r"\s*(%|€|euros?)?\s*(?:ttc\s*)?d['’]\s*acompte\b", re.IGNORECASE),
]
STATED_TOTAL_PATTERNS = {
    "total_ttc": [
        re.compile(r"(?<!acompte )\b(?:total\s+)?ttc\s*(?:[ée]crit\s*)?[=:]?\s*" + AMOUNT, re.IGNORECASE),
        re.compile(r"\btotal\s*(?:[ée]crit\s*)?[=:]?\s*" + AMOUNT + r"(?!\s*(?:€\s*)?(?:ht|hors))", re.IGNORECASE),
    ],
    "subtotal_ht": [
        re.compile(r"\b(?:montant|total|sous[\s-]?total)\s*ht\s*[=:]?\s*" + AMOUNT, re.IGNORECASE),
        re.compile(r"\b(?:montant|sous[\s-]?total)\s*[=:]?\s*" + AMOUNT, re.IGNORECASE),
        re.compile(r"\btotal\s*[=:]?\s*" + AMOUNT + r"\s*(?:€|euros?)?\s*(?:ht|hors\s+tax)", re.IGNORECASE),
        re.compile(r"\bht\s*=\s*" + AMOUNT, re.IGNORECASE),
    ],
    "vat_total": [
        re.compile(r"\b(?:tva|taxe)\s*\d+(?:[.,]\d+)?\s*%?\s*[=:]\s*" + AMOUNT, re.IGNORECASE),
        re.compile(r"\btva\s*\d+(?:[.,]\d+)?\s*%\s*" + AMOUNT + r"\s*(?:€|euros?)", re.IGNORECASE),
        re.compile(r"\bmontant\s+tva\s*[=:]?\s*" + AMOUNT, re.IGNORECASE),
    ],
}
DATE_LABEL_PATTERN = re.compile(
    r"\b(date|[ée]ch[ée]ance)\s*(?:de\s+\w+\s*)?[:=]?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", re.IGNORECASE
)
ANY_DATE_PATTERN = re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{2,4}(?![\d/])")
INVALID_NUMBER = re.compile(r"\d[eE][+-]?\d+|\bnan\b|\binf(?:ini(?:ty)?)?\b|quantit[ée]\s*[:=]?\s*\?", re.IGNORECASE)
MULTIPLE_DOCUMENTS = re.compile(r"\b(?:[2-9]|deux|trois)\s+(?:devis|factures)\b", re.IGNORECASE)
STREET_ADDRESS = re.compile(
    r"\b\d+\s*(?:bis|ter)?,?\s+(?:rue|avenue|av|bd|boulevard|chemin|all[eé]e|impasse|route|place|quai)\s+[^\W\d_]{2,}|\b\d{5}\s+(?-i:[A-Z][a-zé])",
    re.IGNORECASE,
)


def _find_client_name_match(text: str) -> tuple[str, int, int] | None:
    """(name, span start, span end) of 'pour Mr Martin', 'client Sophie', 'chez karim' ..."""
    for index, marker in enumerate(CLIENT_MARKERS):
        for match in marker.finditer(text):
            value_start = match.start() if index == 0 else match.end()
            rest = text[value_start:]
            head = ""
            if index == 0:
                title = re.match(CLIENT_TITLE + r"\s*", rest, re.IGNORECASE)
                head, rest = title.group(0), rest[title.end():]
            end = CLIENT_NAME_END.search(rest)
            raw_value = rest[:end.start()] if end else rest
            value = " ".join(raw_value.split()[:MAX_CLIENT_NAME_WORDS]).strip(" ,.;:-'\"")
            first_word = _plain_word(value.split()[0]) if value else ""
            if not value or value[0].isdigit() or first_word in CLIENT_REJECT_WORDS or first_word in BTP_PLAIN_WORDS:
                continue
            return (head + value).strip(), match.start(), value_start + len(head) + len(raw_value)
    return None


def find_client_name(text: str) -> str | None:
    found = _find_client_name_match(text)
    return found[0] if found else None


def find_vat_rates(text: str) -> list[float]:
    """All VAT rates in order: 'tva20%', 'TVA vingt', '20% TVA', 'taxe 20 pour cent', 'à 5,5 TVA'."""
    found: list[tuple[int, float]] = []
    for match in VAT_AFTER_WORD.finditer(text):
        raw = " ".join(match.group(1).lower().split())
        rate = VAT_WORD_NUMBERS.get(raw) if not raw[0].isdigit() else parse_amount(raw)
        if rate is not None:
            found.append((match.start(), rate))
    for match in VAT_BEFORE_WORD.finditer(text):
        rate = parse_amount(match.group(1))
        if match.group(2) or rate <= 30:
            found.append((match.start(), rate))
    return [rate for _, rate in sorted(found)]


def _find_amount(patterns: list[re.Pattern], text: str) -> tuple[float, bool] | None:
    """(amount, is_percent) of the first pattern that matches."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            unit = (match.group(2) or "") if match.re.groups >= 2 else ""
            return parse_amount(match.group(1)), unit.strip().startswith(("%", "pour"))
    return None


def find_stated_totals(text: str) -> tuple[dict[str, float], list[str]]:
    """Totals written by the user or read on an old document, to compare with the recalculated ones."""
    stated: dict[str, float] = {}
    conflicting: list[str] = []
    for field, patterns in STATED_TOTAL_PATTERNS.items():
        values = []
        for pattern in patterns:
            values += [parse_amount(m.group(1)) for m in pattern.finditer(text)]
        distinct = sorted(set(values))
        if len(distinct) == 1:
            stated[field] = distinct[0]
        elif distinct:
            conflicting.append(field)
    return stated, conflicting


def _finish_items(items: list[dict[str, Any]], text: str, data: dict[str, Any], uncertain: set[str]) -> None:
    """Give every item its VAT rate and an HT unit price, and collect what must be confirmed."""
    all_rates = find_vat_rates(text)
    unattached = list(all_rates)
    for item in items:
        if item.get("vat_rate") is not None and item["vat_rate"] in unattached:
            unattached.remove(item["vat_rate"])
    item_rates = {it["vat_rate"] for it in items if it.get("vat_rate") is not None}

    if VAT_REVERSE_CHARGE.search(text):
        rate, data["vat_mention"] = 0.0, VAT_REVERSE_CHARGE_MENTION
    elif VAT_EXEMPT.search(text):
        rate, data["vat_mention"] = 0.0, VAT_EXEMPT_MENTION
    elif unattached:
        rate = unattached[0]
    elif len(item_rates) == 1:
        rate = next(iter(item_rates))
    else:
        rate = None
    if rate is not None:
        data["vat_rate"] = rate
    if VAT_UNKNOWN.search(text) or len(set(unattached)) > 1:
        uncertain.add("vat_rate")

    for item in items:
        if data.get("vat_mention"):
            item["vat_rate"] = 0.0
        elif item.get("vat_rate") is None:
            item["vat_rate"] = rate if rate is not None else settings.default_vat_rate
        if item.pop("price_basis", None) == "ttc" and item.get("unit_price_ht") is not None:
            item["unit_price_ht"] = round(item["unit_price_ht"] / (1 + item["vat_rate"] / 100), 2)
        uncertain.update(item.pop("uncertain", []))
        if item.get("unit_price_ht") is None:
            uncertain.add("unit_price_ht")


def extract_session_data(text: str, state: ChatState | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    uncertain: set[str] = set()
    cleaned = " ".join(text.strip().split())
    lowered = cleaned.lower()
    current = state.data if state is not None else {}

    document_word = detect_document_word(cleaned)
    if document_word:
        data["document_type"] = document_word
    elif not current.get("document_type"):
        data["document_type"] = "devis"

    if _mentions_unknown(lowered):
        _apply_unknown_mentions(lowered, data)
    # An unknown number is kept out of the data (it would break the totals) and only marked to confirm.
    for field in ("quantity", "unit_price_ht", "vat_rate", "discount_ht", "deposit_ttc"):
        if data.get(field) == UNKNOWN_VALUE:
            del data[field]
            uncertain.add(field)

    address_stop_words = [
        "chantier", "adresse chantier", "prestation", "travaux", "service",
        "quantite", "qte", "prix", "pu", "tva", "remise", "acompte"
    ] + BTP_SERVICE_WORDS

    # Labelled lines ("Clients : X") are reliable; the inline search is a fallback for one-line messages.
    client_name = _extract_labelled_line(text, "client_name") or (
        _extract_text_after(cleaned, [r"\bnom (?:du )?clients?\b"], stop_words=["adresse", "chantier"] + BTP_SERVICE_WORDS)
        or find_client_name(cleaned)
    )
    if client_name:
        data["client_name"] = client_name

    client_address = _extract_labelled_line(text, "client_address") or _extract_text_after(
        cleaned,
        [r"\badresse client\b", r"\badresse\b(?!\s+(?:du\s+)?chanti?er)"],
        stop_words=address_stop_words + ["pose", "prix unitaire", "prix ht", "tva", "m2", "m²", "total", "ni"],
    )
    # Trim address at first comma followed by a service/quantity keyword
    if client_address:
        # Remove trailing service description that leaked into address (e.g. "10 rue X, pose de...")
        client_address = re.sub(
            r',\s*(?:pose|peinture|carrelage|parquet|maçonnerie|isolation|rénovation|réparation|nettoyage|électricité|plomberie|menuiserie|cloison|démolition|chape|enduit|prix|tva|quantit|forfait|lot|\d+\s*(?:m2|m²)).+$',
            '', client_address, flags=re.IGNORECASE
        ).strip(' ,;.')
    # "adresse ni TVA": a real address has a number or at least two words.
    if client_address and client_address != UNKNOWN_VALUE and not (re.search(r"\d", client_address) or len(client_address.split()) >= 2):
        client_address = None
    if client_address:
        data["client_address"] = client_address

    if re.search(r"\b(chantier|adresse chantier)?\s*(m[eê]me(\s+adresse)?|identique|pareil)\b", lowered):
        data["worksite_address"] = data.get("client_address") or current.get("client_address") or UNKNOWN_VALUE
    else:
        worksite = _extract_labelled_line(text, "worksite_address") or _extract_worksite(cleaned)
        if worksite:
            data["worksite_address"] = worksite
        elif not data.get("worksite_address") and not current.get("worksite_address"):
            # If an address was given but no separate worksite was mentioned, default worksite to the address
            if data.get("client_address"):
                data["worksite_address"] = data["client_address"]
            elif current.get("client_address") and not current.get("worksite_address"):
                data["worksite_address"] = current["client_address"]

    # If the user is answering a prompt for missing address and typed a direct street address
    if not data.get("worksite_address") and not data.get("client_address"):
        if STREET_ADDRESS.search(cleaned):
            if current.get("client_address") and not current.get("worksite_address"):
                data["worksite_address"] = cleaned
            elif not current.get("client_address"):
                data["client_address"] = cleaned
                data["worksite_address"] = cleaned

    service = _extract_text_after(
        cleaned,
        [r"\bprestation\b", r"\btravaux\b", r"\bservice\b"],
        stop_words=["quantite", "qte", "prix", "pu", "tva", "remise", "acompte", "client", "adresse"],
    )
    if not service:
        service = _extract_service_fallback(cleaned)

    quantity = _extract_number_after(lowered, [r"quantit[eé]", r"\bqte\b", r"\bqt[eé]\b"])
    labelled_quantity = quantity is not None
    if quantity is None:
        quantity = _extract_number_with_unit(lowered)
    if quantity is not None:
        data["quantity"] = quantity

    unit_price = _extract_number_after(lowered, [r"prix unitaire ht", r"prix unitaire", r"prix ht", r"pu ht", r"\bprix\b"])
    if unit_price is None:
        unit_price = _extract_ht_amount(lowered)
    if unit_price is not None:
        data["unit_price_ht"] = unit_price

    stated, conflicting = find_stated_totals(cleaned)
    uncertain.update(conflicting)
    if stated:
        data["stated_totals"] = stated

    items = parse_items_from_text(text)
    if not items:
        # "prix unitaire 75 ; qte 4 ; montant 200", "Montant HT 1 500 €", "total 950€ TTC": one line from the labels.
        description = service or UNKNOWN_VALUE
        if unit_price is not None and labelled_quantity and "subtotal_ht" in stated:
            items = [{"title": None, "description": description, "quantity": quantity, "unit_price_ht": unit_price, "vat_rate": None}]
            items[0]["total_ht"] = stated["subtotal_ht"]
        elif "subtotal_ht" in stated:
            items = [{"title": None, "description": description, "quantity": 1.0, "unit_price_ht": stated["subtotal_ht"], "vat_rate": None}]
        elif "total_ttc" in stated:
            items = [{"title": None, "description": description, "quantity": 1.0, "unit_price_ht": stated["total_ttc"], "vat_rate": None, "price_basis": "ttc"}]
    _finish_items(items, cleaned, data, uncertain)
    if items:
        data["items"] = items
        data["service_description"] = items[0]["description"]
        data["quantity"] = items[0]["quantity"]
        data["unit_price_ht"] = items[0]["unit_price_ht"]
    elif service:
        data["service_description"] = service

    discount = _find_amount(DISCOUNT_PATTERNS, cleaned)
    if discount is not None:
        data["discount_percent" if discount[1] else "discount_ht"] = discount[0]

    deposit = _find_amount(DEPOSIT_PATTERNS, cleaned)
    if deposit is not None:
        data["deposit_percent" if deposit[1] else "deposit_ttc"] = deposit[0]
        if deposit[1] and document_word == "facture":
            uncertain.add("facture d'acompte (montant a confirmer)")

    for match in DATE_LABEL_PATTERN.finditer(cleaned):
        data["due_date" if match.group(1).lower().startswith(("e", "é")) else "date"] = match.group(2)
    if any(parse_french_date(d) is None for d in ANY_DATE_PATTERN.findall(cleaned)):
        uncertain.add("date invalide")
    if INVALID_NUMBER.search(cleaned):
        uncertain.add("montant ou quantite invalide")
    if MULTIPLE_DOCUMENTS.search(cleaned):
        uncertain.add("plusieurs documents demandes")

    payment = _extract_payment_method(lowered)
    if payment:
        data["payment_method"] = payment

    if uncertain:
        data["uncertain_fields"] = sorted(uncertain)
    return data


def _extract_worksite(cleaned: str) -> str | None:
    """'chantier lyon' -> 'lyon'; 'chantier de lundi', 'pour chantier, SIRET ...' -> None."""
    match = re.search(r"\b(?:adresse\s+)?chanti?er\b\s*(?::\s*)?(.*)", cleaned, flags=re.IGNORECASE)
    if not match or not match.group(1) or match.group(1)[0] in ",.;":
        return None
    value = _trim_at_stop_word(
        re.split(r"[;|]", match.group(1))[0],
        ["prestation", "travaux", "service", "quantite", "qte", "prix", "tva", "remise", "acompte", "le reste", "c est", "urgent"],
    )
    if _mentions_unknown(value.lower()):
        return UNKNOWN_VALUE
    if not value or re.match(r"(?:de|du|en|pour|le|la|un|une)\b", value, flags=re.IGNORECASE):
        return None
    return value


def merge_session_data(state: ChatState, updates: dict[str, Any]) -> None:
    uncertain = set(state.data.get("uncertain_fields") or []) | set(updates.get("uncertain_fields") or [])
    for key, value in updates.items():
        if value is None or value == "" or key == "uncertain_fields":
            continue
        if key == "items" and isinstance(value, list) and value:
            state.data["items"] = value
        else:
            state.data[key] = value
        if value == UNKNOWN_VALUE:
            uncertain.add(key)
    state.data["uncertain_fields"] = sorted(uncertain)


def detect_missing_fields(state: ChatState) -> list[str]:
    missing: list[str] = []
    items = state.data.get("items") or []
    for field in REQUIRED_FIELDS:
        if items and field in {"service_description", "quantity"}:
            continue
        if items and field == "unit_price_ht":
            if any(_num(it.get("unit_price_ht")) is None for it in items):
                missing.append(field)
            continue
        value = state.data.get(field)
        if value is None or str(value).strip() == "":
            missing.append(field)
    return missing


def next_step_for_state(state: ChatState) -> str:
    missing = set(state.missing_fields)
    if "document_type" in missing:
        return WAITING_FOR_DOCUMENT_TYPE
    if missing.intersection({"client_name", "client_address", "worksite_address"}):
        return COLLECTING_CLIENT_INFO
    if missing.intersection({"service_description", "quantity", "unit_price_ht"}):
        return COLLECTING_ITEMS
    return CONFIRMING_SUMMARY


def ask_for_missing_fields(state: ChatState) -> str:
    if not state.missing_fields:
        state.step = CONFIRMING_SUMMARY
        state.last_summary = build_summary(state)
        return (
            "Voici le resume avant generation :\n"
            f"{state.last_summary}\n\n"
            "Repondez OUI pour generer, ou envoyez une correction."
        )

    readable = "\n".join(f"- {FIELD_LABELS[field]}" for field in state.missing_fields)
    items = state.data.get("items") or []
    if items:
        total_ht = _items_subtotal(items)
        return (
            f"J'ai bien enregistre vos {len(items)} prestations (Total HT estime: {total_ht:.2f} EUR).\n\n"
            f"Pour finaliser le document, merci de preciser uniquement :\n{readable}"
        )

    prefix = {
        WAITING_FOR_DOCUMENT_TYPE: "Quel type de document souhaitez-vous creer ?",
        COLLECTING_CLIENT_INFO: "Il me manque uniquement ces informations client/chantier:",
        COLLECTING_ITEMS: "Il me manque uniquement ces informations de prestation:",
    }.get(state.step, "Il me manque uniquement ces informations:")
    return f"{prefix}\n{readable}"


def backend_error_message(exc: Exception) -> str:
    return (
        "Erreur: le backend n'a pas pu traiter la demande. "
        "Verifiez que l'API FastAPI est demarree puis reessayez. "
        f"Detail: {exc.__class__.__name__}."
    )


def build_summary(state: ChatState) -> str:
    data = state.data
    items = data.get("items") or []
    lines = [
        f"Type: {_display(data.get('document_type'))}",
        f"Client: {_display(data.get('client_name'))}",
        f"Adresse: {_display(data.get('client_address'))}",
        f"Adresse chantier: {_display(data.get('worksite_address'))}",
    ]
    if len(items) > 1:
        lines.append(f"Prestations ({len(items)}) :")
        for i, it in enumerate(items, 1):
            desc = it['description'][:60] + ("..." if len(it['description']) > 60 else "")
            if it.get('title'):
                desc = f"{it['title'].upper()} : {desc}"
            lines.append(f"  {i}. {desc} : {_display_money(it.get('unit_price_ht'))}")
    else:
        lines.append(f"Prestation: {_display(data.get('service_description'))}")
        lines.append(f"Quantite: {_display(data.get('quantity'))}")
        lines.append(f"Prix HT: {_display_money(data.get('unit_price_ht'))}")

    amounts = _estimated_amounts(data)
    if amounts:
        lines.append(f"Sous-total HT: {amounts['subtotal_ht']:.2f} EUR")
    lines.append(f"TVA: {_display(data.get('vat_rate')) if data.get('vat_rate') is not None else '20% (par defaut)'}")
    if data.get("vat_mention"):
        lines.append(f"Mention TVA: {data['vat_mention']}")
    lines.append(f"Total TTC estime: {_display_money(amounts['total_ttc'] if amounts else None)}")
    lines.append(f"Remise: {_display_money(amounts['discount_ht']) if amounts and amounts['discount_ht'] else 'aucune'}")
    lines.append(f"Acompte: {_display_money(amounts['deposit_ttc']) if amounts and amounts['deposit_ttc'] else 'aucun'}")
    if data.get("uncertain_fields"):
        lines.append("A confirmer: " + ", ".join(FIELD_LABELS.get(f, f) for f in data["uncertain_fields"]))
    lines.append(f"Mode de paiement: {_display(data.get('payment_method')) if data.get('payment_method') else 'conditions par defaut'}")
    lines.append("Donnees entreprise: automatiques depuis company_profile.json")
    return "\n".join(lines)


def create_document_payload(state: ChatState) -> str:
    data = state.data
    items = data.get("items") or []
    payload_lines = [
        "Generer le document avec les donnees suivantes.",
        "Ne pas inventer les donnees legales, financieres ou client.",
        "Utiliser company_profile.json pour toutes les donnees entreprise.",
        f"Type de document: {_display(data.get('document_type'))}",
        f"Nom client: {_display(data.get('client_name'))}",
        f"Adresse client: {_display(data.get('client_address'))}",
        f"Adresse chantier: {_display(data.get('worksite_address'))}",
    ]
    if items:
        for i, it in enumerate(items, 1):
            title = f"[{it['title']}] " if it.get('title') else ""
            payload_lines.append(
                f"Ligne {i}: {title}{it['description']} | Quantite: {it.get('quantity', 1)} | Prix unitaire HT: {_display(it.get('unit_price_ht'))} | TVA: {_display(it.get('vat_rate', settings.default_vat_rate))}"
            )
    else:
        payload_lines.extend([
            f"Prestation: {_display(data.get('service_description'))}",
            f"Quantite: {_display(data.get('quantity'))}",
            f"Prix unitaire HT: {_display(data.get('unit_price_ht'))}",
            f"TVA: {_display(data.get('vat_rate')) if data.get('vat_rate') is not None else 'taux par defaut'}",
        ])
    amounts = _estimated_amounts(data)
    payload_lines.extend([
        f"Remise HT: {amounts['discount_ht'] if amounts else _num(data.get('discount_ht')) or 0}",
        f"Acompte TTC: {amounts['deposit_ttc'] if amounts else _num(data.get('deposit_ttc')) or 0}",
        f"Mode de paiement: {_display(data.get('payment_method')) if data.get('payment_method') else UNKNOWN_VALUE}",
    ])
    if data.get("vat_mention"):
        payload_lines.append(f"Mention TVA: {data['vat_mention']}")
    if data.get("uncertain_fields"):
        labels = ", ".join(FIELD_LABELS.get(field, field) for field in data["uncertain_fields"])
        payload_lines.append(f"Champs incertains a marquer {UNKNOWN_VALUE}: {labels}")
    return "\n".join(payload_lines)


def _is_cancel(text: str) -> bool:
    # "stop envoi rien au client finalement", "annule", "laisse tomber" -- but not "annule facture FAC-2026-0020",
    # an issued invoice is never deleted (it needs a credit note).
    if re.search(r"\b(?:FAC|DEV)-\d{4}", text, flags=re.IGNORECASE):
        return False
    return bool(re.match(r"^\s*(?:stop|annule[rz]?|annulation|laisse\s+tomber|cancel)\b", text, flags=re.IGNORECASE))


def _is_greeting(text: str) -> bool:
    return _plain_word(text) in {"slt", "salut", "bonjour", "bjr", "bjs", "salam", "hello", "coucou", "bonsoir"}


def _is_confirmation(text: str) -> bool:
    return text.strip().lower() in {"oui", "ok", "go", "confirme", "confirmer", "confirm", "yes", "valid\u00e9", "valide"}


def _num(value: Any) -> float | None:
    """A finite number, or None for missing / 'a confirmer' / NaN values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _items_subtotal(items: list[dict[str, Any]]) -> float:
    return sum((_num(it.get("quantity")) or 0) * (_num(it.get("unit_price_ht")) or 0) for it in items)


def _estimated_amounts(data: dict[str, Any]) -> dict[str, float] | None:
    """Subtotal HT after discount, discount, total TTC and deposit, with each line's own VAT rate."""
    default_rate = _num(data.get("vat_rate"))
    default_rate = settings.default_vat_rate if default_rate is None else default_rate
    items = data.get("items") or []
    if not items:
        quantity, unit_price = _num(data.get("quantity")), _num(data.get("unit_price_ht"))
        if quantity is None or unit_price is None:
            return None
        items = [{"quantity": quantity, "unit_price_ht": unit_price, "vat_rate": default_rate}]
    subtotal = _items_subtotal(items)
    vat = sum(
        (_num(it.get("quantity")) or 0) * (_num(it.get("unit_price_ht")) or 0)
        * (_num(it.get("vat_rate")) if _num(it.get("vat_rate")) is not None else default_rate) / 100
        for it in items
    )
    percent = _num(data.get("discount_percent"))
    discount = round(subtotal * percent / 100, 2) if percent is not None else (_num(data.get("discount_ht")) or 0)
    after_discount = max(0.0, subtotal - discount)
    if subtotal > 0:
        vat = vat * after_discount / subtotal
    total_ttc = round(after_discount + vat, 2)
    deposit_percent = _num(data.get("deposit_percent"))
    deposit = round(total_ttc * deposit_percent / 100, 2) if deposit_percent is not None else (_num(data.get("deposit_ttc")) or 0)
    return {"subtotal_ht": round(after_discount, 2), "discount_ht": discount, "total_ttc": total_ttc, "deposit_ttc": deposit}


def _estimated_total_ttc(data: dict[str, Any]) -> float | None:
    amounts = _estimated_amounts(data)
    return amounts["total_ttc"] if amounts else None


def _display(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return UNKNOWN_VALUE
    return str(value).strip()


def _display_money(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return UNKNOWN_VALUE
    if isinstance(value, int | float):
        return f"{value:g} EUR"
    return str(value)


def _mentions_unknown(lowered: str) -> bool:
    return any(token in lowered for token in [
        "a confirmer", "\u00e0 confirmer", "je ne sais pas", "je sais pas", "j sais pas", "jsp", "je connais pas",
        "j c pas", "inconnu", "plus tard",
    ])


def _apply_unknown_mentions(lowered: str, data: dict[str, Any]) -> None:
    mappings = {
        "client_name": ["nom client", "client"],
        "client_address": ["adresse client", "son adresse"],
        "worksite_address": ["adresse chantier", "chantier"],
        "service_description": ["prestation", "travaux", "service"],
        "quantity": ["quantite", "qte"],
        "unit_price_ht": ["prix", "pu ht"],
        "vat_rate": ["tva"],
        "discount_ht": ["remise"],
        "deposit_ttc": ["acompte"],
        "payment_method": ["paiement", "mode de paiement"],
    }
    for field, markers in mappings.items():
        if any(marker in lowered for marker in markers):
            data[field] = UNKNOWN_VALUE


def _extract_text_after(text: str, markers: list[str], stop_words: list[str]) -> str | None:
    for marker in markers:
        match = re.search(marker + r"\s*[:;\-]?\s*(.+)", text, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" ,.;")
        value = _trim_at_stop_word(value, stop_words)
        value = re.sub(r"\b(est|c'est|c est)\b\s*", "", value, flags=re.IGNORECASE).strip(" ,.;")
        if _mentions_unknown(value.lower()):
            return UNKNOWN_VALUE
        if value and value.lower() not in {"est", "c'est", "c est"}:
            return value
    return None


def _trim_at_stop_word(value: str, stop_words: list[str]) -> str:
    indexes = []
    for stop in stop_words:
        match = re.search(rf"\b{stop}\b", value, flags=re.IGNORECASE)
        if match:
            indexes.append(match.start())
    if indexes:
        value = value[: min(indexes)]
    return value.strip(" ,.;")


def _extract_number_after(lowered: str, markers: list[str]) -> float | None:
    for marker in markers:
        match = re.search(marker + r"\s*[:=\-]?\s*(\d+(?:[,.]\d+)?)", lowered)
        if match:
            return _parse_number(match.group(1))
    return None


def _extract_number_with_unit(lowered: str) -> float | None:
    match = re.search(r"\b(\d+(?:[,.]\d+)?)\s*(m2|m\u00b2|h|heure|heures|jour|jours|u|unite|unit\u00e9)", lowered)
    if match:
        return _parse_number(match.group(1))
    return None


def _extract_ht_amount(lowered: str) -> float | None:
    match = re.search(r"\b(\d+(?:[,.]\d+)?)\s*(eur|euro|euros|\u20ac)?\s*ht\b", lowered)
    if match:
        return _parse_number(match.group(1))
    return None


def _parse_number(raw: str) -> float:
    value = float(raw.replace(",", "."))
    return int(value) if value.is_integer() else value


def _extract_payment_method(lowered: str) -> str | None:
    methods = {
        "virement": "virement",
        "cheque": "cheque",
        "ch\u00e8que": "cheque",
        "carte": "carte bancaire",
        "especes": "especes",
        "esp\u00e8ces": "especes",
    }
    for marker, value in methods.items():
        if marker in lowered:
            return value
    return None


def _extract_service_fallback(text: str) -> str | None:
    lowered = text.lower()
    matches = []
    for word in BTP_SERVICE_WORDS:
        match = re.search(rf"\b{word}\b", lowered)
        if match:
            matches.append(match.start())
    if not matches:
        return None
    earliest_idx = min(matches)
    subtext = text[earliest_idx:]
    value = _trim_at_stop_word(
        subtext,
        ["quantite", "qte", "prix", "pu", "tva", "remise", "acompte"],
    )
    return value.strip(" ,.;") or None
