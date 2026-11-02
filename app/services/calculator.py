from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


def money(value: float | int | Decimal | None) -> float | None:
    if value is None:
        return None
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(amount)


def line_total_ht(quantity: float | int, unit_price_ht: float | int | None) -> float | None:
    if unit_price_ht is None:
        return None
    return money(Decimal(str(quantity)) * Decimal(str(unit_price_ht)))


def vat_amount(total_ht: float | int | None, vat_rate: float | int | None) -> float | None:
    if total_ht is None or vat_rate is None:
        return None
    return money(Decimal(str(total_ht)) * Decimal(str(vat_rate)) / Decimal("100"))


def total_ttc(total_ht: float | int | None, vat: float | int | None) -> float | None:
    if total_ht is None or vat is None:
        return None
    return money(Decimal(str(total_ht)) + Decimal(str(vat)))


def calculate_totals(items: Iterable[dict], discount_ht: float = 0, deposit_ttc: float = 0) -> dict:
    subtotal = Decimal("0")
    total_vat = Decimal("0")
    has_uncertain_price = False

    for item in items:
        qty = Decimal(str(item.get("quantity", 1)))
        price = item.get("unit_price_ht")
        rate = item.get("vat_rate")
        if price is None or rate is None:
            has_uncertain_price = True
            continue
        line_ht = Decimal(str(line_total_ht(qty, price)))
        line_vat = Decimal(str(vat_amount(line_ht, rate)))
        subtotal += line_ht
        total_vat += line_vat

    subtotal_after_discount = subtotal - Decimal(str(discount_ht or 0))
    if subtotal_after_discount < 0:
        # Negative lines or a discount larger than the total: nothing is due, VAT included.
        subtotal_after_discount = Decimal("0")
        total_vat = Decimal("0")
    # VAT is due on the discounted base: spread the discount across VAT rates pro rata.
    if subtotal > 0 and subtotal_after_discount != subtotal:
        total_vat = total_vat * subtotal_after_discount / subtotal

    ttc = subtotal_after_discount + total_vat
    balance = ttc - Decimal(str(deposit_ttc or 0))

    return {
        "subtotal_ht": None if has_uncertain_price and subtotal == 0 else money(subtotal_after_discount),
        "vat_total": None if has_uncertain_price and total_vat == 0 else money(total_vat),
        "total_ttc": None if has_uncertain_price and ttc == 0 else money(ttc),
        "balance_due_ttc": None if has_uncertain_price and ttc == 0 else money(balance),
    }
