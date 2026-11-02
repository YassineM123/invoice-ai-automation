from app.models import DocumentItem, DocumentType, ExtractedDocument, Totals
from app.services.validator import complete_and_validate


def test_validator_completes_totals_and_dates():
    doc = ExtractedDocument(
        document_type=DocumentType.invoice,
        items=[DocumentItem(description="Peinture murale", quantity=2, unit_price_ht=300, vat_rate=20)],
        totals=Totals(),
    )
    corrected, corrections, missing = complete_and_validate(doc)
    assert corrected.number.startswith("FAC-")
    assert corrected.totals.subtotal_ht == 600.0
    assert corrected.totals.vat_total == 120.0
    assert corrected.totals.total_ttc == 720.0
    assert "nom client" in missing
    assert len(corrections) >= 1
