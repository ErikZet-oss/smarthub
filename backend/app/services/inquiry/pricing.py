from __future__ import annotations

import math


def _supplier_defaults_per_100(supplier_name: str | None) -> bool:
    """Dodávatelia, u ktorých scraper často neposiela price_unit, ale UI je / 100 ks."""
    name = (supplier_name or "").casefold()
    if not name:
        return True
    if "bmkco" in name or "bmk" in name:
        return False
    return True


def inquiry_line_total_eur(
    *,
    price_eur: float,
    quantity: int,
    price_unit: str | None = None,
    pack_quantity: int | None = None,
    supplier_name: str | None = None,
) -> float:
    """
    Celková cena riadku dopytu podľa jednotky ceny zo scrapu.

    ``price_eur`` zostáva zobrazená hodnota (typicky za 100 ks u Mekrs/Fabory).
    """
    qty = max(1, int(quantity))
    unit = (price_unit or "").strip().casefold()

    if unit == "per_1_ks":
        return round(price_eur * qty, 4)

    if unit == "per_sks":
        pack = max(1, int(pack_quantity or 1))
        packages = math.ceil(qty / pack)
        return round(price_eur * packages, 4)

    if unit in ("per_100_ks", "100") or (not unit and _supplier_defaults_per_100(supplier_name)):
        return round(price_eur * qty / 100.0, 4)

    return round(price_eur * qty / 100.0, 4)
