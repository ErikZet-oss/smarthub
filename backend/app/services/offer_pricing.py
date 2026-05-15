"""Výpočet predajnej ceny z nákupnej ceny a marže."""

from __future__ import annotations


def selling_unit_price(
    purchase_unit_price_eur: float | None,
    margin_percent: float,
    *,
    fallback_unit_price_eur: float = 0.0,
) -> float:
    purchase = float(purchase_unit_price_eur or 0)
    margin = float(margin_percent or 0)
    if purchase > 0:
        return round(purchase * (1.0 + margin / 100.0), 4)
    return round(float(fallback_unit_price_eur or 0), 4)
