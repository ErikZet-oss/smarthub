from __future__ import annotations

from sqlmodel import Session, select

from app.models.entities import Product, ProductMapping
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import (
    CatalogSnapCache,
    _WASHER_BOLT_TO_INNER,
    _is_washer_norm,
    _row_norma_candidates,
    resolve_catalog_norma,
    resolve_washer_inner_diameter,
)
from app.services.inquiry.normalize import (
    norm_display_candidates,
    normalize_diameter,
    normalize_length_mm,
    search_key,
)


def _resolve_inquiry_diameter(
    session: Session,
    parsed: InquiryLineParsed,
    *,
    norma: str | None,
    surface: str | None,
    v_class: str | None,
    length: str | None,
    cache: CatalogSnapCache,
) -> str | None:
    """Podložky DIN 125: M3 → 3.2 (vnútorný priemer), nie veľkosť skrutky."""
    diameter = normalize_diameter(parsed.diameter)
    if not diameter or not _is_washer_norm(norma, parsed.raw_text):
        return diameter

    inner = _WASHER_BOLT_TO_INNER.get(diameter)
    filters = ProductSearchFilters(
        norma=norma,
        surface=surface,
        diameter=diameter,
        length=length,
        v_class=v_class,
    )
    diam_opts = cache.filter_options(session, filters).get("diameter", [])
    # Novší katalóg drží podložky podľa veľkosti skrutky (M3 → „3"); ak je
    # bolt-size priemer v katalógu, použijeme ho. Inak fallback na vnútorný
    # priemer (starší formát, M3 → „3.2").
    if diameter in diam_opts:
        return diameter
    resolved = resolve_washer_inner_diameter(diameter, diam_opts)
    if resolved:
        return resolved
    if inner and (not diam_opts or inner in diam_opts):
        return inner
    return diameter


def _prefer_products_with_mappings(
    session: Session,
    products: list[Product],
    supplier_ids: list[int] | None,
) -> list[Product]:
    if not products or not supplier_ids:
        return products

    def selected_mapping_count(product: Product) -> int:
        if product.id is None:
            return 0
        rows = session.exec(
            select(ProductMapping).where(
                ProductMapping.product_id == product.id,
                ProductMapping.supplier_id.in_(supplier_ids),  # type: ignore[attr-defined]
            )
        ).all()
        return len(rows)

    return sorted(
        products,
        key=lambda p: (-selected_mapping_count(p), p.internal_code or ""),
    )


def find_catalog_products(
    session: Session,
    parsed: InquiryLineParsed,
    *,
    limit: int = 10,
    supplier_ids: list[int] | None = None,
) -> list[Product]:
    """
    Nájde produkty v katalógu podľa parsovaného riadku.
    Najprv presný match (viac variant normy), potom normalizovaný fallback.
    Pri viacerých zhodách uprednostní produkt s mapovaním u vybraných dodávateľov.
    """
    if parsed.parse_error:
        return []

    code = (parsed.internal_code or "").strip()
    if code:
        direct = session.exec(select(Product).where(Product.internal_code == code)).first()
        if direct is not None:
            return _prefer_products_with_mappings(session, [direct], supplier_ids)[:limit]
        return []

    cache = CatalogSnapCache.load(session)
    catalog_norma = resolve_catalog_norma(parsed.norma, known=cache.norma_values) or parsed.norma
    catalog_norma = cache.canonical_norma(catalog_norma) or catalog_norma
    norm_row = parsed.model_copy(update={"norma": catalog_norma})

    length = normalize_length_mm(parsed.length)
    surface = (parsed.surface or "").strip() or None
    v_class = (parsed.v_class or "").strip() or None
    if _is_washer_norm(catalog_norma, parsed.raw_text):
        # Katalóg drží pri podložkách triedu ako tvrdosť (140HV, 200HV) alebo
        # materiál (A2-50), čo sa z dopytu spoľahlivo odvodiť nedá — preto pri
        # podložkách podľa class nefiltrujeme a rozlíšime povrchom + názvom.
        v_class = None

    diameter = _resolve_inquiry_diameter(
        session,
        norm_row,
        norma=catalog_norma,
        surface=surface,
        v_class=v_class,
        length=length,
        cache=cache,
    )

    norm_keys = _row_norma_candidates(norm_row, known=cache.norma_values, cache=cache)
    seen_norm_queries: set[str] = set()
    for base_norm in norm_keys:
        candidate = norm_row.model_copy(update={"norma": base_norm})
        for norm in norm_display_candidates(candidate):
            if norm in seen_norm_queries:
                continue
            seen_norm_queries.add(norm)
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
                return _prefer_products_with_mappings(session, products, supplier_ids)[:limit]

    target_norm = search_key(catalog_norma)
    if not target_norm:
        return []

    products = _normalized_norm_fallback(
        session,
        target_norm_key=target_norm,
        diameter=diameter,
        length=length,
        v_class=v_class,
        surface=surface,
        limit=limit,
    )
    return _prefer_products_with_mappings(session, products, supplier_ids)[:limit]


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
