from app.services.calculator import calculate_totals, line_total_ht, money, total_ttc, vat_amount


def test_line_total_ht():
    assert line_total_ht(2, 300) == 600.0


def test_vat_amount():
    assert vat_amount(600, 20) == 120.0


def test_total_ttc():
    assert total_ttc(600, 120) == 720.0


def test_rounding_money():
    assert money(10.005) == 10.01


def test_calculate_totals():
    result = calculate_totals([
        {"quantity": 2, "unit_price_ht": 300, "vat_rate": 20},
        {"quantity": 1, "unit_price_ht": 100, "vat_rate": 10},
    ])
    assert result["subtotal_ht"] == 700.0
    assert result["vat_total"] == 130.0
    assert result["total_ttc"] == 830.0
    assert result["balance_due_ttc"] == 830.0


def test_deposit_balance():
    result = calculate_totals([
        {"quantity": 1, "unit_price_ht": 1000, "vat_rate": 20},
    ], deposit_ttc=200)
    assert result["total_ttc"] == 1200.0
    assert result["balance_due_ttc"] == 1000.0


def test_discount_reduces_vat_base():
    result = calculate_totals([
        {"quantity": 1, "unit_price_ht": 2500, "vat_rate": 20},
    ], discount_ht=200)
    assert result["subtotal_ht"] == 2300.0
    assert result["vat_total"] == 460.0
    assert result["total_ttc"] == 2760.0
