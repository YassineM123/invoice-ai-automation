from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from app.config import settings
from app.models import CorrectionItem, ExtractedDocument, UNKNOWN
from app.services.calculator import calculate_totals, line_total_ht, money, vat_amount, total_ttc
from app.services.numbering import next_document_number


# Taux de TVA applicables en France metropolitaine.
VALID_VAT_RATES = {0.0, 2.1, 5.5, 10.0, 20.0}
REDUCED_VAT_RATES = {5.5, 10.0}
VAT_ZERO_MENTIONS = re.compile(r"293\s*b|autoliquidation|exon[ée]r|non applicable", re.IGNORECASE)
MAX_ITEMS = 200
MAX_DESCRIPTION_LENGTH = 1000
NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\s*$|^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
SECRET_TOKEN = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}")


def parse_french_date(value: str) -> datetime | None:
    """'31/12/2026', '31-12-26' or '2026-12-31' -> datetime; None when the date does not exist (31/02, 29/02/2026)."""
    match = NUMERIC_DATE.match(value or "")
    if not match:
        return None
    if match.group(4):
        year, month, day = (int(match.group(i)) for i in (4, 5, 6))
    else:
        day, month, year = (int(match.group(i)) for i in (1, 2, 3))
        year += 2000 if year < 100 else 0
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char) * (2 if index % 2 else 1)
        total += number - 9 if number > 9 else number
    return total % 10 == 0


def redact_sensitive(text: str) -> str:
    """Never print a bank card number or an API key on a document."""
    def card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "[carte bancaire masquée]" if 13 <= len(digits) <= 19 and _luhn_valid(digits) else match.group(0)
    return SECRET_TOKEN.sub("[clé masquée]", CARD_NUMBER.sub(card, text))


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _missing(value: object) -> bool:
    normalized = str(value).strip().lower() if value is not None else ""
    return value is None or normalized in {"", UNKNOWN.lower(), "a confirmer", "None".lower(), "null"}


def _today_fr() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _due_date_fr(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%d/%m/%Y")


def complete_and_validate(doc: ExtractedDocument) -> tuple[ExtractedDocument, list[CorrectionItem], list[str]]:
    corrections: list[CorrectionItem] = []
    missing_fields: list[str] = []

    doc.notes = redact_sensitive(doc.notes)
    doc.raw_text_summary = redact_sensitive(doc.raw_text_summary)
    doc.client_info.name = redact_sensitive(doc.client_info.name)
    doc.client_info.address = redact_sensitive(doc.client_info.address)
    for item in doc.items:
        item.description = redact_sensitive(item.description)

    if len(doc.items) > MAX_ITEMS:
        corrections.append(CorrectionItem(
            error_found="Trop de lignes",
            old_value=str(len(doc.items)),
            corrected_value=str(MAX_ITEMS),
            explanation=f"Le document est limité à {MAX_ITEMS} lignes. Les lignes suivantes ont été ignorées.",
            severity="error",
        ))
        doc.items = doc.items[:MAX_ITEMS]
        doc.uncertain_fields.append("nombre de lignes")

    if _missing(doc.document_type.value):
        missing_fields.append("type de document")

    if _missing(doc.number) or doc.number == UNKNOWN:
        old = doc.number
        doc.number = next_document_number(doc.document_type.value if doc.document_type.value != UNKNOWN else "devis")
        corrections.append(CorrectionItem(
            error_found="Numéro de document manquant",
            old_value=str(old),
            corrected_value=doc.number,
            explanation="Un numéro automatique a été généré avec le compteur interne.",
            severity="info",
        ))

    invalid_dates: set[str] = set()
    for field, label in (("date", "Date"), ("due_date", "Date d’échéance")):
        value = getattr(doc, field)
        if not _missing(value) and NUMERIC_DATE.match(value) and parse_french_date(value) is None:
            corrections.append(CorrectionItem(
                error_found=f"{label} invalide",
                old_value=value,
                corrected_value=UNKNOWN,
                explanation="Cette date n’existe pas dans le calendrier. Elle doit être confirmée.",
                severity="error",
            ))
            setattr(doc, field, UNKNOWN)
            doc.uncertain_fields.append(field)
            invalid_dates.add(field)

    if _missing(doc.date) and "date" not in invalid_dates:
        doc.date = _today_fr()
        corrections.append(CorrectionItem(
            error_found="Date manquante",
            old_value=UNKNOWN,
            corrected_value=doc.date,
            explanation="La date du jour a été utilisée automatiquement.",
            severity="info",
        ))

    if _missing(doc.due_date) and "due_date" not in invalid_dates:
        doc.due_date = _due_date_fr(settings.default_payment_days)
        corrections.append(CorrectionItem(
            error_found="Date d’échéance manquante",
            old_value=UNKNOWN,
            corrected_value=doc.due_date,
            explanation=f"Échéance calculée automatiquement à J+{settings.default_payment_days}.",
            severity="info",
        ))

    issue_date, due_date = parse_french_date(doc.date), parse_french_date(doc.due_date)
    if issue_date and due_date and due_date < issue_date:
        corrections.append(CorrectionItem(
            error_found="Échéance antérieure à la date du document",
            old_value=doc.due_date,
            corrected_value=UNKNOWN,
            explanation="La date d’échéance ne peut pas précéder la date d’émission.",
            severity="error",
        ))
        doc.uncertain_fields.append("due_date")

    if not doc.items:
        missing_fields.append("prestations / lignes de facturation")
        doc.uncertain_fields.append("items")

    for index, item in enumerate(doc.items, start=1):
        if len(item.description) > MAX_DESCRIPTION_LENGTH:
            item.description = item.description[:MAX_DESCRIPTION_LENGTH] + "…"
            doc.uncertain_fields.append(f"description ligne {index}")
        if _missing(item.description):
            missing_fields.append(f"description ligne {index}")
        if not _finite(item.quantity) or item.quantity <= 0:
            corrections.append(CorrectionItem(
                error_found=f"Quantité invalide ligne {index}",
                old_value=str(item.quantity),
                corrected_value=UNKNOWN,
                explanation="La quantité doit être un nombre supérieur à zéro.",
                severity="error",
            ))
            missing_fields.append(f"quantité ligne {index}")
            item.quantity = item.quantity if _finite(item.quantity) else 0
        if item.unit_price_ht is not None and not _finite(item.unit_price_ht):
            corrections.append(CorrectionItem(
                error_found=f"Prix invalide ligne {index}",
                old_value=str(item.unit_price_ht),
                corrected_value=UNKNOWN,
                explanation="Le prix n’est pas un nombre utilisable.",
                severity="error",
            ))
            item.unit_price_ht = None
        if item.unit_price_ht is not None and item.unit_price_ht < 0:
            corrections.append(CorrectionItem(
                error_found=f"Prix négatif ligne {index}",
                old_value=str(item.unit_price_ht),
                corrected_value=UNKNOWN,
                explanation="Un prix unitaire négatif est à confirmer (remise ou erreur de saisie ?).",
                severity="error",
            ))
            doc.uncertain_fields.append(f"prix ligne {index}")
        if item.total_ht is not None and not _finite(item.total_ht):
            item.total_ht = None
        if item.unit_price_ht is None:
            missing_fields.append(f"prix HT ligne {index}")
            doc.uncertain_fields.append(f"unit_price_ht ligne {index}")
        if item.vat_rate is None:
            item.vat_rate = settings.default_vat_rate
            corrections.append(CorrectionItem(
                error_found=f"TVA manquante ligne {index}",
                old_value=UNKNOWN,
                corrected_value=f"{settings.default_vat_rate}%",
                explanation="Le taux TVA par défaut a été appliqué. À vérifier si un taux réduit s’applique.",
                severity="warning",
            ))
        elif not _finite(item.vat_rate) or item.vat_rate not in VALID_VAT_RATES:
            corrections.append(CorrectionItem(
                error_found=f"Taux de TVA invalide ligne {index}",
                old_value=f"{item.vat_rate}%",
                corrected_value=UNKNOWN,
                explanation="Les taux français sont 20 %, 10 %, 5,5 %, 2,1 % ou 0 % (exonération).",
                severity="error",
            ))
            doc.uncertain_fields.append(f"TVA ligne {index}")
            if not _finite(item.vat_rate):
                item.vat_rate = settings.default_vat_rate
        elif item.vat_rate in REDUCED_VAT_RATES:
            corrections.append(CorrectionItem(
                error_found=f"Taux réduit ligne {index}",
                old_value=f"{item.vat_rate}%",
                corrected_value=f"{item.vat_rate}%",
                explanation="Taux réduit réservé aux travaux dans un logement achevé depuis plus de 2 ans (attestation du client). À vérifier.",
                severity="warning",
            ))
        elif item.vat_rate == 0 and not VAT_ZERO_MENTIONS.search(doc.notes):
            corrections.append(CorrectionItem(
                error_found=f"TVA à 0 % sans mention légale ligne {index}",
                old_value="0%",
                corrected_value=UNKNOWN,
                explanation="Une TVA à 0 % exige une mention (art. 293 B du CGI ou autoliquidation).",
                severity="error",
            ))
            doc.uncertain_fields.append("mention TVA")
        expected_line_ht = line_total_ht(item.quantity, item.unit_price_ht)
        if expected_line_ht is not None and item.total_ht != expected_line_ht:
            corrections.append(CorrectionItem(
                error_found=f"Total HT incorrect ligne {index}",
                old_value=str(item.total_ht),
                corrected_value=str(expected_line_ht),
                explanation="Total HT recalculé: quantité × prix unitaire HT.",
                severity="error" if item.total_ht is not None else "info",
            ))
            item.total_ht = expected_line_ht

    computed = calculate_totals(
        [i.model_dump() for i in doc.items],
        discount_ht=doc.totals.discount_ht,
        deposit_ttc=doc.totals.deposit_ttc,
    )

    for field, label in [
        ("subtotal_ht", "Sous-total HT"),
        ("vat_total", "TVA totale"),
        ("total_ttc", "Total TTC"),
        ("balance_due_ttc", "Reste à payer TTC"),
    ]:
        old = getattr(doc.totals, field)
        new = computed[field]
        if new is not None and money(old) != new:
            corrections.append(CorrectionItem(
                error_found=f"{label} incorrect ou manquant",
                old_value=str(old) if old is not None else UNKNOWN,
                corrected_value=str(new),
                explanation=f"{label} recalculé automatiquement à partir des lignes du document.",
                severity="error" if old is not None else "info",
            ))
            setattr(doc.totals, field, new)

    required_client_fields = {
        "nom client": doc.client_info.name,
        "adresse client": doc.client_info.address,
    }
    for label, value in required_client_fields.items():
        if _missing(value):
            missing_fields.append(label)
            doc.uncertain_fields.append(label)

    if _missing(doc.worksite_address):
        doc.worksite_address = UNKNOWN
        doc.uncertain_fields.append("adresse chantier")

    if _missing(doc.payment_terms):
        doc.payment_terms = settings.company_profile_path and UNKNOWN

    return doc, corrections, sorted(set(missing_fields))


def validate_legal_company_fields(company) -> list[CorrectionItem]:
    checks = {
        "nom de l’entreprise": company.name,
        "adresse entreprise": company.address,
        "SIRET": company.siret,
        "TVA intracommunautaire": company.vat_number,
        "email": company.email,
        "téléphone": company.phone,
        "assurance décennale": company.insurance_decennale,
    }
    warnings: list[CorrectionItem] = []
    for label, value in checks.items():
        if _missing(value):
            warnings.append(CorrectionItem(
                error_found=f"Mention entreprise manquante: {label}",
                old_value=UNKNOWN,
                corrected_value=UNKNOWN,
                explanation="Cette donnée doit être renseignée dans le profil entreprise. Le système ne l’invente pas.",
                severity="warning",
            ))
    return warnings
