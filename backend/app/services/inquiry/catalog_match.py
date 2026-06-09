from __future__ import annotations

from sqlmodel import Session, select

from app.models.entities import Product
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.normalize import (
    norm_display_candidates,
    normalize_diameter,
    normalize_length_mm,
    search_key,
)


def find_catalog_products(
    session: Session,
    parsed: InquiryLineParsed,
    *,
    limit: int = 10,
) -> list[Product]:
    """
    Nájde produkty v katalógu podľa parsovaného riadku.
    Najprv presný match (viac variant normy), potom normalizovaný fallback.
    """
    if parsed.parse_error:
        return []

    diameter = normalize_diameter(parsed.diameter)
    length = normalize_length_mm(parsed.length)
    v_class = (parsed.v_class or "").strip() or None
    surface = (parsed.surface or "").strip() or None

    for norm in norm_display_candidates(parsed):
        products = _query_products(
            session,
            norma=norm,
            diameter=diameter,
            length=length,
            v_class=v_class,
            surface=surface,
            limit=limit,
        )
        if products:
            return products

    target_norm = search_key(parsed.norma)
    if not target_norm:
        return []

    return _normalized_norm_fallback(
        session,
        target_norm_key=target_norm,
        diameter=diameter,
        length=length,
        v_class=v_class,
        surface=surface,
        limit=limit,
    )


def _query_products(
    session: Session,
    *,
    norma: str | None,
    diameter: str | None,
    length: str | None,
    v_class: str | None,
    surface: str | None,
    limit: int,
) -> list[Product]:
    query = select(Product)
    if norma:
        query = query.where(Product.norma == norma)
    if diameter:
        query = query.where(Product.diameter == diameter)
    if length:
        query = query.where(Product.length == length)
    if v_class:
        query = query.where(Product.v_class == v_class)
    if surface:
        query = query.where(Product.surface == surface)
    return list(session.exec(query.limit(limit)).all())


def _normalized_norm_fallback(
    session: Session,
    *,
    target_norm_key: str,
    diameter: str | None,
    length: str | None,
    v_class: str | None,
    surface: str | None,
    limit: int,
) -> list[Product]:
    query = select(Product).where(Product.norma.is_not(None))  # type: ignore[union-attr]
    if diameter:
        query = query.where(Product.diameter == diameter)
    if length:
        query = query.where(Product.length == length)
    if v_class:
        query = query.where(Product.v_class == v_class)
    if surface:
        query = query.where(Product.surface == surface)

    batch = list(session.exec(query.limit(3000)).all())
    matched = [p for p in batch if search_key(p.norma) == target_norm_key]
    return matched[:limit]
