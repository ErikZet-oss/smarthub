"""Testy exportu najnižších cien konkurencie."""

from app.schemas.common import ProductSearchFilters
from app.services.competitor_lowest_price_export import (
    MAX_EXPORT_PRODUCTS,
    LowestPriceExportRow,
    build_lowest_price_csv,
    export_filters_active,
)


def test_export_filters_active_requires_at_least_one() -> None:
    empty = ProductSearchFilters()
    assert export_filters_active(empty) is False
    assert export_filters_active(ProductSearchFilters(norma="DIN 975")) is True
    assert export_filters_active(ProductSearchFilters(code="ABC")) is True


def test_build_lowest_price_csv_sk_format() -> None:
    rows = [
        LowestPriceExportRow(
            internal_code="DIN975-M10-100",
            y_money_name="Závitová tyč M10",
            competitor_name="Feva",
            price_eur=1.12532,
        ),
        LowestPriceExportRow(
            internal_code="DIN975-M12-100",
            y_money_name="Závitová tyč M12",
            competitor_name=None,
            price_eur=None,
        ),
    ]
    raw = build_lowest_price_csv(rows)
    text = raw.decode("utf-8-sig")
    lines = text.strip().split("\n")
    assert lines[0] == "Katalógové číslo;Money názov;Konkurencia;Cena EUR"
    assert "Feva" in lines[1]
    assert "1,1253" in lines[1]
    assert lines[2].endswith(";;")


def test_max_export_products_is_500() -> None:
    assert MAX_EXPORT_PRODUCTS == 500
