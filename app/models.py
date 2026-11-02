from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


UNKNOWN = "à confirmer"


class DocumentType(str, Enum):
    invoice = "facture"
    quote = "devis"
    unknown = "à confirmer"


class DocumentStatus(str, Enum):
    draft = "draft"
    pending_confirmation = "pending_confirmation"
    ready = "ready"
    sent = "sent"


class CompanyProfile(BaseModel):
    name: str = UNKNOWN
    logo_path: str | None = None
    address: str = UNKNOWN
    siret: str = UNKNOWN
    vat_number: str = UNKNOWN
    email: str = UNKNOWN
    phone: str = UNKNOWN
    website: str = UNKNOWN
    iban: str = UNKNOWN
    insurance_decennale: str = UNKNOWN
    payment_terms: str = "Paiement à 30 jours sauf mention contraire."
    late_penalties: str = "Pénalités de retard applicables conformément aux conditions légales en vigueur."
    footer_note: str = 'Document généré automatiquement. Les champs marqués "à confirmer" doivent être vérifiés.'


class ClientInfo(BaseModel):
    name: str = UNKNOWN
    address: str = UNKNOWN
    email: str = UNKNOWN
    phone: str = UNKNOWN


class DocumentItem(BaseModel):
    group: str | None = None
    title: str | None = None
    description: str = UNKNOWN
    quantity: float = 1
    unit_price_ht: float | None = None
    vat_rate: float | None = None
    total_ht: float | None = None


class Totals(BaseModel):
    subtotal_ht: float | None = None
    vat_total: float | None = None
    total_ttc: float | None = None
    discount_ht: float = 0
    deposit_ttc: float = 0
    balance_due_ttc: float | None = None


class CorrectionItem(BaseModel):
    error_found: str
    old_value: str = UNKNOWN
    corrected_value: str = UNKNOWN
    explanation: str
    severity: Literal["info", "warning", "error"] = "warning"


class ExtractedDocument(BaseModel):
    document_type: DocumentType = DocumentType.unknown
    number: str = UNKNOWN
    date: str = UNKNOWN
    due_date: str = UNKNOWN
    company_info: CompanyProfile | None = None
    client_info: ClientInfo = Field(default_factory=ClientInfo)
    worksite_address: str = UNKNOWN
    items: list[DocumentItem] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    payment_method: str = UNKNOWN
    payment_terms: str = UNKNOWN
    notes: str = ""
    uncertain_fields: list[str] = Field(default_factory=list)
    raw_text_summary: str = ""


class CorrectedDocument(BaseModel):
    document: ExtractedDocument
    corrections: list[CorrectionItem]
    status: DocumentStatus
    pdf_url: str | None = None
    docx_url: str | None = None
    report_url: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class WhatsAppSession(BaseModel):
    phone: str
    step: str = "start"
    document: dict[str, Any] = Field(default_factory=dict)
