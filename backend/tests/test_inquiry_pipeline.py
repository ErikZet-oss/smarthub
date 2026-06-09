from __future__ import annotations

from app.schemas.inquiry import InquiryScrapedOffer
from app.services.inquiry.pipeline import pick_best_offer


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
