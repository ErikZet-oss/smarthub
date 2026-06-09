from __future__ import annotations

from app.services.inquiry.pricing import inquiry_line_total_eur


def test_inquiry_line_total_per_100_ks() -> None:
    assert inquiry_line_total_eur(
        price_eur=0.0851,
        quantity=200,
        price_unit="per_100_ks",
    ) == 0.1702


def test_inquiry_line_total_mekrs_default_without_unit() -> None:
    assert inquiry_line_total_eur(
        price_eur=1.49,
        quantity=500,
        supplier_name="Mekrs",
    ) == 7.45


def test_inquiry_line_total_per_1_ks() -> None:
    assert inquiry_line_total_eur(
        price_eur=2.5,
        quantity=10,
        price_unit="per_1_ks",
    ) == 25.0


def test_inquiry_line_total_per_sks_packages() -> None:
    assert inquiry_line_total_eur(
        price_eur=9.5,
        quantity=150,
        price_unit="per_sks",
        pack_quantity=100,
    ) == 19.0
