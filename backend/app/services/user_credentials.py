"""Zlúčenie šablóny dodávateľa s pobočkovými prihlasovacími údajmi."""

from __future__ import annotations

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models.entities import Supplier, UserSupplierCredential
from app.services.scraper_service import supplier_allows_empty_cart_config


def effective_supplier_for_user(
    session: Session,
    supplier: Supplier,
    user_id: int,
) -> Supplier:
    cred = session.exec(
        select(UserSupplierCredential).where(
            UserSupplierCredential.user_id == user_id,
            UserSupplierCredential.supplier_id == int(supplier.id),
        )
    ).first()
    if cred is None:
        return supplier
    return supplier.model_copy(
        update={
            "username": cred.username or "",
            "password": cred.password or "",
        }
    )


def get_supplier_for_automation(
    session: Session,
    supplier_id: int,
    user_id: int,
) -> Supplier:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    eff = effective_supplier_for_user(session, supplier, user_id)
    if not (eff.shop_url or "").strip():
        raise HTTPException(status_code=400, detail="Vyplň URL e-shopu u dodávateľa.")
    if not (eff.username or "").strip() or not (eff.password or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Vyplň prihlasovacie údaje u dodávateľa (pobočkové alebo šablónu u admina).",
        )
    if not (supplier.cart_config_json or "").strip():
        # `eff` má rovnaké name/shop_url ako šablóna (cred mení len login).
        if not supplier_allows_empty_cart_config(
            supplier
        ) and not supplier_allows_empty_cart_config(eff):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Chýba konfigurácia (cart_config_json). V sekcii Dodávatelia "
                    "vlož aspoň JSON pre prihlásenie; vyhľadávanie a košík môžeš doplniť neskôr."
                ),
            )
    return eff


def supplier_row_to_api_dict(
    session: Session,
    supplier: Supplier,
    user_id: int,
    *,
    logo_url: str | None,
) -> dict:
    eff = effective_supplier_for_user(session, supplier, user_id)
    return {
        "id": supplier.id,
        "name": supplier.name,
        "shop_url": supplier.shop_url,
        "username": eff.username,
        "password": eff.password,
        "is_connected": supplier.is_connected,
        "code_column": supplier.code_column,
        "cart_config_json": supplier.cart_config_json,
        "logo_url": logo_url,
        "free_shipping_threshold_eur": supplier.free_shipping_threshold_eur,
        "sort_order": supplier.sort_order,
    }
