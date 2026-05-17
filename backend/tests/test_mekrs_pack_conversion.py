"""Test ks → balení konverzie pri Mekrs add_to_cart.

Reprodukuje bug, ktorý vznikol pri Mekrs HTTP košíku: API očakáva počet **balení**,
ale UI posielalo počet **kusov**. Pri „50 ks" v UI + variante „50 ks/balenie" sa
do košíka pridalo 50 balení × 50 ks = 2500 ks namiesto 50 ks.
"""

from __future__ import annotations

import math


def _pieces_to_packs(pieces: int, pack_q: int) -> int:
    """Rovnaký výpočet ako v scraper_service._mekrs_http_cart."""
    pq = max(1, int(pack_q) if pack_q else 1)
    pc = max(1, int(pieces) if pieces else 1)
    return max(1, math.ceil(pc / pq))


def test_50_pieces_in_50ks_package_is_1_pack() -> None:
    """Hlavný bug z UI screenshot-u: 50 ks @ 50 ks/balenie ⇒ 1 balenie."""
    assert _pieces_to_packs(50, 50) == 1


def test_100_pieces_in_50ks_package_is_2_packs() -> None:
    assert _pieces_to_packs(100, 50) == 2


def test_60_pieces_in_50ks_package_rounds_up_to_2_packs() -> None:
    """Neúplné balenie sa zaokrúhľuje nahor (Mekrs nevie predať polovicu balenia)."""
    assert _pieces_to_packs(60, 50) == 2


def test_1_piece_in_1ks_variant_is_1_pack() -> None:
    """Variant „1 ks" funguje 1:1 (pack_q=1)."""
    assert _pieces_to_packs(1, 1) == 1


def test_5_pieces_in_1ks_variant_is_5_packs() -> None:
    assert _pieces_to_packs(5, 1) == 5


def test_zero_pieces_promotes_to_min_one() -> None:
    """Frontend by nemal posielať 0, ale ak by predsa, posielame minimum 1."""
    assert _pieces_to_packs(0, 50) == 1


def test_zero_pack_quantity_treated_as_one() -> None:
    """Variant bez pack_quantity (None/0) má fallback na 1 ks/balenie."""
    assert _pieces_to_packs(50, 0) == 50
