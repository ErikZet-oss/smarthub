from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional
from urllib.parse import quote

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models.entities import Product, ProductMapping, Supplier
from app.schemas.inquiry import (
    InquiryLineParsed,
    InquiryLineRunResult,
    InquiryRunTaskResult,
    InquiryScrapedOffer,
)
from app.services.inquiry.catalog_match import find_catalog_products
from app.services.inquiry.pricing import inquiry_line_total_eur
from app.services.scraper_service import ScraperProductNotFoundError, ScraperService, load_scraper_config
from app.services.supplier_logos import supplier_logo_public_url
from app.services.user_credentials import effective_supplier_for_user

ProgressCb = Callable[[int, int], None]

_SUPPLIER_SCRAPE_CONCURRENCY = 6

_FIELD_LABELS = {
    "norma": "norma",
    "surface": "povrch",
    "diameter": "priemer",
    "length": "dĺžka",
    "v_class": "class",
    "quantity": "množstvo",
}


def _row_prevalidation_error(row: InquiryLineParsed) -> tuple[str, str] | None:
    if row.parse_error:
        return "invalid_row", f"Chyba parsovania: {row.parse_error}"
    if row.catalog_warnings:
        detail = "; ".join(w.strip() for w in row.catalog_warnings if w and str(w).strip())
        if detail:
            return "catalog_mismatch", f"Nie je v katalógu: {detail}"
        return "catalog_mismatch", "Položka nie je v katalógu."
    missing = row.missing_required_fields()
    if missing:
        names = ", ".join(_FIELD_LABELS.get(field, field) for field in missing)
        return "invalid_row", f"Chýbajúce polia: {names}."
    return None


def _supplier_product_url(supplier: Supplier, supplier_code: str) -> str | None:
    code = (supplier_code or "").strip()
    if not code:
        return None
    raw_cfg = (supplier.cart_config_json or "").strip()
    if raw_cfg:
        try:
            cfg = json.loads(raw_cfg)
        except Exception:
            cfg = {}
        if isinstance(cfg, dict):
            template = cfg.get("search_via_url_template")
            if isinstance(template, str):
                tmpl = template.strip()
                if tmpl:
                    if "{code}" in tmpl:
                        return tmpl.replace("{code}", quote(code, safe=""))
                    sep = "&" if "?" in tmpl else "?"
                    return f"{tmpl}{sep}q={quote(code, safe='')}"
    base = (supplier.shop_url or "").strip()
    if not base:
        return None
    root = base.rstrip("/")
    sep = "&" if "?" in root else "?"
    return f"{root}{sep}q={quote(code, safe='')}"


def pick_best_offer(
    offers: list[InquiryScrapedOffer],
) -> tuple[InquiryScrapedOffer | None, bool]:
    """
    Vyberie najlacnejšiu ponuku so skladom > 0.
    Ak nikto nemá sklad, vráti najnižšiu cenu a no_stock=True.
    """
    priced = [
        o
        for o in offers
        if o.error is None and o.price_eur is not None and o.price_eur > 0
    ]
    if not priced:
        return None, False

    with_stock = [o for o in priced if (o.stock or 0) > 0]
    if with_stock:
        return min(with_stock, key=lambda o: o.price_eur or 0), False

    return min(priced, key=lambda o: o.price_eur or 0), True


def _line_from_parsed(row: InquiryLineParsed) -> InquiryLineRunResult:
    return InquiryLineRunResult(
        row_index=row.row_index,
        raw_text=row.raw_text,
        quantity=row.quantity,
        norma=row.norma,
        surface=row.surface,
        diameter=row.diameter,
        length=row.length,
        v_class=row.v_class,
    )


async def _scrape_supplier_offer(
    session: Session,
    *,
    supplier: Supplier,
    supplier_code: str,
    user_id: int,
    semaphore: asyncio.Semaphore,
) -> InquiryScrapedOffer:
    base = InquiryScrapedOffer(
        supplier_id=int(supplier.id),
        supplier_name=supplier.name,
        supplier_code=supplier_code,
        logo_url=supplier_logo_public_url(supplier.logo_path),
        supplier_product_url=_supplier_product_url(supplier, supplier_code),
    )
    eff = effective_supplier_for_user(session, supplier, user_id)
    if not (eff.username or "").strip() or not (eff.password or "").strip():
        return base.model_copy(update={"error": "Chýbajú prihlasovacie údaje."})
    try:
        config = load_scraper_config(eff)
    except Exception as exc:
        return base.model_copy(update={"error": f"Neplatná konfigurácia: {exc}"})

    async with semaphore:
        try:
            data = await ScraperService.get_supplier_data(
                eff,
                supplier_code,
                config,
                automation_user_id=user_id,
            )
        except ScraperProductNotFoundError as exc:
            return base.model_copy(update={"error": str(exc)})
        except Exception as exc:
            msg = str(exc).strip() or type(exc).__name__
            return base.model_copy(update={"error": msg})

    price_raw = data.get("price_eur")
    stock_raw = data.get("stock")
    price_unit_raw = data.get("price_unit")
    pack_qty_raw = data.get("pack_quantity")
    price_eur: float | None = None
    stock: int | None = None
    price_unit: str | None = None
    pack_quantity: int | None = None
    if price_raw is not None:
        try:
            price_eur = float(price_raw)
        except (TypeError, ValueError):
            price_eur = None
    if stock_raw is not None:
        try:
            stock = int(stock_raw)
        except (TypeError, ValueError):
            stock = None
    if isinstance(price_unit_raw, str) and price_unit_raw.strip():
        price_unit = price_unit_raw.strip()
    if pack_qty_raw is not None:
        try:
            pack_quantity = max(1, int(pack_qty_raw))
        except (TypeError, ValueError):
            pack_quantity = None

    logged_in = data.get("logged_in")
    return base.model_copy(
        update={
            "price_eur": price_eur,
            "price_unit": price_unit,
            "pack_quantity": pack_quantity,
            "stock": stock,
            "logged_in": logged_in if isinstance(logged_in, bool) else None,
            "error": None if price_eur and price_eur > 0 else "Cena nedostupná.",
        }
    )


async def _run_line_async(
    session: Session,
    *,
    row: InquiryLineParsed,
    supplier_ids: list[int],
    user_id: int,
    semaphore: asyncio.Semaphore,
) -> InquiryLineRunResult:
    result = _line_from_parsed(row)
    prevalidation = _row_prevalidation_error(row)
    if prevalidation is not None:
        status, message = prevalidation
        return result.model_copy(update={"status": status, "error": message})

    products = find_catalog_products(session, row, limit=1)
    if not products:
        return result.model_copy(update={"status": "no_product", "error": "Produkt v katalógu nenájdený."})

    product = products[0]
    result = result.model_copy(
        update={
            "product_id": product.id,
            "internal_code": product.internal_code,
        }
    )
    if product.id is None:
        return result.model_copy(update={"status": "error", "error": "Neplatný produkt v DB."})

    mappings = session.exec(
        select(ProductMapping).where(
            ProductMapping.product_id == product.id,
            ProductMapping.supplier_id.in_(supplier_ids),  # type: ignore[attr-defined]
        )
    ).all()
    if not mappings:
        return result.model_copy(
            update={"status": "no_mapping", "error": "Žiadne mapovanie u vybraných dodávateľov."},
        )

    supplier_rows = session.exec(
        select(Supplier).where(Supplier.id.in_(supplier_ids))  # type: ignore[attr-defined]
    ).all()
    supplier_by_id = {int(s.id): s for s in supplier_rows if s.id is not None}

    tasks = []
    for mp in mappings:
        sup = supplier_by_id.get(int(mp.supplier_id))
        if sup is None:
            continue
        code = (mp.supplier_code or "").strip()
        if not code:
            continue
        tasks.append(
            _scrape_supplier_offer(
                session,
                supplier=sup,
                supplier_code=code,
                user_id=user_id,
                semaphore=semaphore,
            )
        )

    if not tasks:
        return result.model_copy(
            update={"status": "no_mapping", "error": "Chýbajú kódy dodávateľov."},
        )

    offers = list(await asyncio.gather(*tasks))
    best, no_stock = pick_best_offer(offers)
    if best is None:
        return result.model_copy(
            update={
                "status": "no_price",
                "offers": offers,
                "error": "Nepodarilo sa načítať cenu u žiadneho dodávateľa.",
            },
        )

    qty = row.quantity or 1
    line_total = inquiry_line_total_eur(
        price_eur=best.price_eur or 0,
        quantity=qty,
        price_unit=best.price_unit,
        pack_quantity=best.pack_quantity,
        supplier_name=best.supplier_name,
    )
    return result.model_copy(
        update={
            "status": "no_stock" if no_stock else "ok",
            "no_stock": no_stock,
            "best_offer": best,
            "offers": offers,
            "line_total_eur": line_total,
            "error": "Nie je skladom." if no_stock else None,
        },
    )


async def run_inquiry_batch_async(
    session: Session,
    *,
    rows: list[InquiryLineParsed],
    supplier_ids: list[int],
    user_id: int,
    progress_cb: ProgressCb | None = None,
) -> list[InquiryLineRunResult]:
    if not supplier_ids:
        raise ValueError("Vyber aspoň jedného dodávateľa.")
    if not rows:
        raise ValueError("Dopyt neobsahuje žiadne riadky.")

    semaphore = asyncio.Semaphore(_SUPPLIER_SCRAPE_CONCURRENCY)
    results: list[InquiryLineRunResult] = []
    total = len(rows)
    for idx, row in enumerate(rows, start=1):
        results.append(
            await _run_line_async(
                session,
                row=row,
                supplier_ids=supplier_ids,
                user_id=user_id,
                semaphore=semaphore,
            )
        )
        if progress_cb:
            progress_cb(idx, total)
    return results


def run_inquiry_batch(
    session: Session,
    *,
    rows: list[InquiryLineParsed],
    supplier_ids: list[int],
    user_id: int,
    progress_cb: ProgressCb | None = None,
) -> InquiryRunTaskResult:
    line_results = asyncio.run(
        run_inquiry_batch_async(
            session,
            rows=rows,
            supplier_ids=supplier_ids,
            user_id=user_id,
            progress_cb=progress_cb,
        )
    )
    rows_with_offer = sum(
        1 for r in line_results if r.best_offer and r.status in ("ok", "no_stock")
    )
    rows_no_stock = sum(1 for r in line_results if r.status == "no_stock")
    rows_failed = sum(1 for r in line_results if r.status not in ("ok", "no_stock"))
    totals = [r.line_total_eur for r in line_results if r.line_total_eur is not None]
    total_eur = round(sum(totals), 4) if totals else None
    return InquiryRunTaskResult(
        rows=line_results,
        source_filename="",
        supplier_ids=supplier_ids,
        total_rows=len(line_results),
        rows_with_offer=rows_with_offer,
        rows_no_stock=rows_no_stock,
        rows_failed=rows_failed,
        total_eur=total_eur,
    )


def validate_run_request(
    rows: list[InquiryLineParsed],
    supplier_ids: list[int],
    *,
    ignore_errors: bool = False,
) -> None:
    if not supplier_ids:
        raise HTTPException(status_code=400, detail="Vyber aspoň jedného dodávateľa.")
    if not rows:
        raise HTTPException(status_code=400, detail="Dopyt neobsahuje žiadne riadky.")
    if ignore_errors:
        return
    invalid = [
        r.row_index
        for r in rows
        if not r.is_valid or (r.catalog_warnings and len(r.catalog_warnings) > 0)
    ]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Riadky {invalid[:5]} nie sú kompletné — doplň chýbajúce polia alebo zapni „Ignorovať chyby“.",
        )
