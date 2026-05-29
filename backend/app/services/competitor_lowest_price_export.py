"""Hromadný export najnižších cien konkurencie podľa filtrov vyhľadávania."""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import dataclass
from typing import Callable, Optional

from sqlmodel import Session, select

from app.models.entities import Competitor, CompetitorProductMapping, Product
from app.schemas.common import ProductSearchFilters
from app.services.competitor_scraper_service import fetch_competitor_public_price

MAX_EXPORT_PRODUCTS = 500
SCRAPE_CONCURRENCY = 5


@dataclass(frozen=True)
class LowestPriceExportRow:
    internal_code: str
    y_money_name: Optional[str]
    competitor_name: Optional[str]
    price_eur: Optional[float]


@dataclass(frozen=True)
class _ExportProduct:
    internal_code: str
    y_money_name: Optional[str]
    mappings: list[tuple[int, str, str, str | None]]  # id, name, code, scrape_config_json


def export_filters_active(filters: ProductSearchFilters) -> bool:
    return any(
        [
            (filters.code or "").strip(),
            (filters.norma or "").strip(),
            (filters.surface or "").strip(),
            (filters.diameter or "").strip(),
            (filters.length or "").strip(),
            (filters.v_class or "").strip(),
            (filters.y_money_name or "").strip(),
            (filters.image_filename or "").strip(),
        ]
    )


def build_product_query(filters: ProductSearchFilters):
    query = select(Product)
    if filters.code:
        query = query.where(Product.internal_code.contains(filters.code))
    if filters.norma:
        query = query.where(Product.norma == filters.norma)
    if filters.diameter:
        query = query.where(Product.diameter == filters.diameter)
    if filters.length:
        query = query.where(Product.length == filters.length)
    if filters.surface:
        query = query.where(Product.surface == filters.surface)
    if filters.v_class:
        query = query.where(Product.v_class == filters.v_class)
    if filters.y_money_name:
        query = query.where(Product.y_money_name == filters.y_money_name)
    if filters.image_filename:
        query = query.where(Product.image_filename == filters.image_filename)
    return query


def count_products_for_export(session: Session, filters: ProductSearchFilters) -> int:
    from sqlalchemy import func as sa_func

    query = build_product_query(filters)
    count_query = select(sa_func.count()).select_from(query.subquery())
    return int(session.exec(count_query).one())


def load_export_products(
    session: Session, filters: ProductSearchFilters
) -> list[_ExportProduct]:
    query = build_product_query(filters).limit(MAX_EXPORT_PRODUCTS)
    products = session.exec(query).all()
    if not products:
        return []

    product_ids = [p.id for p in products if p.id is not None]
    competitors = session.exec(
        select(Competitor)
        .where(Competitor.is_active == True)  # noqa: E712
        .order_by(Competitor.sort_order, Competitor.id)
    ).all()
    competitor_by_id: dict[int, Competitor] = {
        c.id: c for c in competitors if c.id is not None
    }

    mappings_by_product: dict[int, list[tuple[int, str, str, str | None]]] = {}
    if product_ids:
        mapping_rows = session.exec(
            select(CompetitorProductMapping).where(
                CompetitorProductMapping.product_id.in_(product_ids)  # type: ignore[attr-defined]
            )
        ).all()
        for mp in mapping_rows:
            comp = competitor_by_id.get(mp.competitor_id)
            if comp is None:
                continue
            code = (mp.competitor_code or "").strip()
            if not code:
                continue
            mappings_by_product.setdefault(mp.product_id, []).append(
                (
                    int(comp.id),
                    (comp.name or "").strip(),
                    code,
                    comp.scrape_config_json,
                )
            )

    out: list[_ExportProduct] = []
    for product in products:
        if product.id is None:
            continue
        out.append(
            _ExportProduct(
                internal_code=product.internal_code,
                y_money_name=product.y_money_name,
                mappings=mappings_by_product.get(product.id, []),
            )
        )
    return out


def _fmt_price_eur(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def build_lowest_price_csv(rows: list[LowestPriceExportRow]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["Katalógové číslo", "Money názov", "Konkurencia", "Cena EUR"])
    for row in rows:
        writer.writerow(
            [
                row.internal_code,
                row.y_money_name or "",
                row.competitor_name or "",
                _fmt_price_eur(row.price_eur) if row.price_eur is not None else "",
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


async def _fetch_one_price(
    *,
    competitor_id: int,
    shop_url: str,
    competitor_code: str,
    scrape_config_json: str | None,
    sem: asyncio.Semaphore,
) -> tuple[str, Optional[float]]:
    async with sem:
        try:
            data = await fetch_competitor_public_price(
                competitor_id=competitor_id,
                shop_url=shop_url,
                competitor_code=competitor_code,
                scrape_config_json=scrape_config_json,
            )
            price = data.get("price_eur")
            if price is None:
                return competitor_code, None
            return competitor_code, float(price)
        except Exception:
            return competitor_code, None


async def _lowest_for_product(
    product: _ExportProduct,
    competitor_meta: dict[int, tuple[str, str | None]],
    sem: asyncio.Semaphore,
) -> LowestPriceExportRow:
    if not product.mappings:
        return LowestPriceExportRow(
            internal_code=product.internal_code,
            y_money_name=product.y_money_name,
            competitor_name=None,
            price_eur=None,
        )

    async def _one(mapping: tuple[int, str, str, str | None]) -> tuple[str, Optional[float]]:
        comp_id, comp_name, code, scrape_cfg = mapping
        shop_url, _ = competitor_meta.get(comp_id, ("", None))
        _, price = await _fetch_one_price(
            competitor_id=comp_id,
            shop_url=shop_url,
            competitor_code=code,
            scrape_config_json=scrape_cfg,
            sem=sem,
        )
        return comp_name, price

    results = await asyncio.gather(*[_one(m) for m in product.mappings])
    best_name: Optional[str] = None
    best_price: Optional[float] = None
    for comp_name, price in results:
        if price is None:
            continue
        if best_price is None or price < best_price:
            best_price = price
            best_name = comp_name
    return LowestPriceExportRow(
        internal_code=product.internal_code,
        y_money_name=product.y_money_name,
        competitor_name=best_name,
        price_eur=best_price,
    )


async def run_lowest_price_export(
    session: Session,
    filters: ProductSearchFilters,
    progress_cb: Callable[[int, int, int], None] | None = None,
) -> tuple[list[LowestPriceExportRow], int]:
    """
    Načíta produkty podľa filtrov, pre každý zistí najnižšiu live cenu konkurencie.
    Vráti riadky exportu a počet produktov spolu (aj keď > MAX — pre validáciu).
    """
    total = count_products_for_export(session, filters)
    products = load_export_products(session, filters)
    competitors = session.exec(select(Competitor)).all()
    competitor_meta: dict[int, tuple[str, str | None]] = {
        int(c.id): ((c.shop_url or "").strip(), c.scrape_config_json)
        for c in competitors
        if c.id is not None
    }

    sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    rows: list[LowestPriceExportRow] = []
    errors = 0
    for idx, product in enumerate(products, start=1):
        row = await _lowest_for_product(product, competitor_meta, sem)
        if row.price_eur is None and product.mappings:
            errors += 1
        rows.append(row)
        if progress_cb is not None:
            progress_cb(idx, len(products), errors)
    return rows, total
