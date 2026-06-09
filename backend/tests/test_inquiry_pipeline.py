from __future__ import annotations

from app.schemas.inquiry import InquiryLineParsed, InquiryScrapedOffer
from app.services.inquiry.pipeline import _row_prevalidation_error, pick_best_offer


def test_pick_best_offer_prefers_stock() -> None:
    offers = [
        InquiryScrapedOffer(
            supplier_id=1,
            supplier_name="A",
            supplier_code="x",
            price_eur=0.5,
            stock=0,
        ),
        InquiryScrapedOffer(
            supplier_id=2,
            supplier_name="B",
            supplier_code="y",
            price_eur=0.6,
            stock=10,
        ),
    ]
    best, no_stock = pick_best_offer(offers)
    assert best is not None
    assert best.supplier_id == 2
    assert no_stock is False


def test_pick_best_offer_cheapest_with_stock() -> None:
    offers = [
        InquiryScrapedOffer(
            supplier_id=1,
            supplier_name="A",
            supplier_code="x",
            price_eur=0.8,
            stock=5,
        ),
        InquiryScrapedOffer(
            supplier_id=2,
            supplier_name="B",
            supplier_code="y",
            price_eur=0.4,
            stock=3,
        ),
    ]
    best, no_stock = pick_best_offer(offers)
    assert best is not None
    assert best.supplier_id == 2
    assert no_stock is False


def test_pick_best_offer_no_stock_fallback() -> None:
    offers = [
        InquiryScrapedOffer(
            supplier_id=1,
            supplier_name="A",
            supplier_code="x",
            price_eur=0.9,
            stock=0,
        ),
        InquiryScrapedOffer(
            supplier_id=2,
            supplier_name="B",
            supplier_code="y",
            price_eur=0.7,
            stock=0,
        ),
    ]
    best, no_stock = pick_best_offer(offers)
    assert best is not None
    assert best.supplier_id == 2
    assert no_stock is True


def test_pick_best_offer_skips_errors() -> None:
    offers = [
        InquiryScrapedOffer(
            supplier_id=1,
            supplier_name="A",
            supplier_code="x",
            error="timeout",
        ),
        InquiryScrapedOffer(
            supplier_id=2,
            supplier_name="B",
            supplier_code="y",
            price_eur=1.2,
            stock=1,
        ),
    ]
    best, no_stock = pick_best_offer(offers)
    assert best is not None
    assert best.supplier_id == 2
    assert no_stock is False


def test_row_prevalidation_catalog_mismatch() -> None:
    row = InquiryLineParsed(
        row_index=1,
        raw_text="matica M99",
        norma="934",
        diameter="99",
        surface="pozink",
        quantity=1,
        catalog_warnings=["Priemer M99 nie je v katalógu."],
    )
    result = _row_prevalidation_error(row)
    assert result is not None
    status, msg = result
    assert status == "catalog_mismatch"
    assert "Nie je v katalógu" in msg


def test_row_prevalidation_missing_fields() -> None:
    row = InquiryLineParsed(row_index=2, raw_text="matica", norma="934", quantity=1)
    result = _row_prevalidation_error(row)
    assert result is not None
    status, msg = result
    assert status == "invalid_row"
    assert "Chýbajúce polia" in msg
