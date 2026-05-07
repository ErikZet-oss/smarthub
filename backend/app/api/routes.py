import asyncio
import json
import threading
import time
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlmodel import Session, select

from app.api.deps import AuthUserContext, get_current_user, require_admin
from app.db import engine, get_session
from app.models.entities import (
    FieldMapping,
    Product,
    ProductList,
    ProductListItem,
    ProductMapping,
    SmarthubUser,
    Supplier,
    UserSupplierCredential,
)
from app.schemas.common import ProductComparison, ProductSearchFilters, SupplierOffer
from app.services.automation import AutomationEngine
from app.services.excel_importer import import_gamechanger_excel, profile_excel_columns
from app.services.dev_run_log import (
    clear_dev_logs,
    get_dev_logs,
    get_step_screenshots_status,
    set_step_screenshots_override,
)
from app.services.scraper_service import (
    ScraperProductNotFoundError,
    ScraperService,
    load_scraper_config,
)
from app.services.haspl_http_client import supplier_shop_cart_url
from app.services.smarthub_bootstrap import (
    copy_supplier_credentials_for_new_user,
    ensure_credentials_for_supplier,
    hash_password,
    verify_password,
)
from app.services.supplier_logos import (
    remove_supplier_logo_files,
    save_supplier_logo_upload,
    supplier_logo_public_url,
)
from app.services.user_credentials import (
    effective_supplier_for_user,
    get_supplier_for_automation,
    supplier_row_to_api_dict,
)

router = APIRouter()
automation = AutomationEngine()
_IMPORT_TASKS: dict[str, dict[str, object]] = {}
_IMPORT_TASKS_LOCK = threading.Lock()


def _supplier_product_url(supplier: Supplier, supplier_code: str | None) -> str | None:
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


def _next_supplier_sort_order(session: Session) -> int:
    suppliers = session.exec(select(Supplier)).all()
    if not suppliers:
        return 0
    return max((s.sort_order or 0) for s in suppliers) + 10


def _normalize_supplier_cart_config_json(name: str, raw_cfg: str | None) -> str | None:
    cfg_text = (raw_cfg or "").strip()
    if not cfg_text:
        return None
    # Fabory na Render beží bez X servera; browser_channel=chrome spôsobí pád headed browsera.
    # Ak sa hodnota niekde obnoví zo šablóny, pri uložení ju tu odstránime.
    if "fabory" in (name or "").strip().lower():
        try:
            cfg = json.loads(cfg_text)
            if isinstance(cfg, dict) and "browser_channel" in cfg:
                cfg.pop("browser_channel", None)
                return json.dumps(cfg, ensure_ascii=False, indent=2)
        except Exception:
            return cfg_text
    return cfg_text


class SupplierUpsertPayload(BaseModel):
    id: int | None = None
    name: str
    shop_url: str
    username: str
    password: str
    code_column: str | None = None
    cart_config_json: str | None = None
    free_shipping_threshold_eur: float | None = None


class SupplierRemovePayload(BaseModel):
    supplier_id: int


class SupplierReorderPayload(BaseModel):
    ordered_supplier_ids: list[int]


class AddToCartPayload(BaseModel):
    supplier_id: int
    supplier_code: str
    quantity: int = 1
    # Mekrs / viac balení: index riadku v modale (0 = prvý variant).
    packaging_variant_index: int | None = None
    # Mekrs HTTP: UUID variantu z /api/product/.../variants (namiesto Playwright modal index).
    mekrs_product_variant_id: str | None = None
    # Hopefix HTTP: číselné ID z HTML / rozbaleného riadku pre POST /api/add_to_cart.
    hopefix_product_id: str | None = None
    hopefix_package_type: str | None = None
    # Haspl HTTP: kód variantu (code) z Sylius product-variants — POST košíka.
    haspl_variant_code: str | None = None
    # Inoxmare HTTP: Magento product ID a relatívna cesta PDP (z packaging_variants).
    inoxmare_product_id: str | None = None
    inoxmare_referer_path: str | None = None


class StepScreenshotsPayload(BaseModel):
    """None = riadiť sa len SCRAPER_STEP_SCREENSHOTS v prostredí."""

    override: bool | None = None


class SupplierScrapePayload(BaseModel):
    supplier_id: int
    supplier_code: str


class ImportExcelPayload(BaseModel):
    file_path: str
    # Rovnaký list ako pri náhľade stĺpcov; predvolene DIN.
    sheet_name: str | None = "DIN"


class ExcelProfilePayload(BaseModel):
    file_path: str
    sheet_name: str = "DIN"


class FieldMappingPayload(BaseModel):
    code: str | None = None
    norma: str | None = None
    surface: str | None = None
    diameter: str | None = None
    length: str | None = None
    v_class: str | None = None
    y_money_name: str | None = None
    image_filename: str | None = None


class SmarthubLoginPayload(BaseModel):
    username: str
    password: str


class AdminCreateUserPayload(BaseModel):
    username: str
    password: str
    display_label: str | None = None


class PatchMySupplierCredentialsPayload(BaseModel):
    supplier_id: int
    username: str
    password: str


class ProductListCreatePayload(BaseModel):
    name: str


class ProductListRenamePayload(BaseModel):
    name: str


class ProductListAddItemPayload(BaseModel):
    internal_code: str


def _get_user_product_list_or_404(
    session: Session, list_id: int, user_id: int
) -> ProductList:
    row = session.exec(
        select(ProductList).where(
            ProductList.id == list_id,
            ProductList.user_id == user_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Zoznam sa nenašiel.")
    return row


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/auth/smarthub-login")
def smarthub_login(
    payload: SmarthubLoginPayload,
    session: Session = Depends(get_session),
):
    name = payload.username.strip()
    if not name or not payload.password:
        raise HTTPException(status_code=400, detail="Vyplň meno a heslo.")
    user = session.exec(
        select(SmarthubUser).where(SmarthubUser.username == name)
    ).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Nesprávne prihlasovacie meno alebo heslo.",
        )
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
    }


@router.get("/admin/users")
def admin_list_users(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    users = session.exec(select(SmarthubUser).order_by(SmarthubUser.username)).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "display_label": u.display_label,
            "is_admin": u.is_admin,
        }
        for u in users
    ]


@router.post("/admin/users")
def admin_create_user(
    payload: AdminCreateUserPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    uname = payload.username.strip()
    if not uname or not payload.password:
        raise HTTPException(status_code=400, detail="Vyplň meno a heslo.")
    exists = session.exec(
        select(SmarthubUser).where(SmarthubUser.username == uname)
    ).first()
    if exists is not None:
        raise HTTPException(status_code=400, detail="Toto používateľské meno už existuje.")
    user = SmarthubUser(
        username=uname,
        password_hash=hash_password(payload.password),
        is_admin=False,
        display_label=(payload.display_label or "").strip() or None,
    )
    session.add(user)
    session.flush()
    copy_supplier_credentials_for_new_user(session, int(user.id))
    session.commit()
    session.refresh(user)
    return {
        "id": user.id,
        "username": user.username,
        "display_label": user.display_label,
        "is_admin": user.is_admin,
    }


@router.patch("/users/me/supplier-credentials")
def patch_my_supplier_credentials(
    payload: PatchMySupplierCredentialsPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    cred = session.exec(
        select(UserSupplierCredential).where(
            UserSupplierCredential.user_id == user.id,
            UserSupplierCredential.supplier_id == int(payload.supplier_id),
        )
    ).first()
    if cred is None:
        raise HTTPException(
            status_code=404,
            detail="Pre tohto dodávateľa nemáš záznam poverení — kontaktuj administrátora.",
        )
    cred.username = payload.username.strip()
    cred.password = payload.password
    session.add(cred)
    session.commit()
    ScraperService.invalidate_remote_cart_cache(
        int(payload.supplier_id), user_id=user.id
    )
    return {"ok": True}


@router.get("/dev/logs")
def dev_logs_get(
    limit: int = 2000,
    _: AuthUserContext = Depends(get_current_user),
):
    """Záznamy z Playwright (scrape / košík) pre ladenie vo frontende."""
    return {"logs": get_dev_logs(min(max(limit, 1), 8000))}


@router.delete("/dev/logs")
def dev_logs_clear(_: AuthUserContext = Depends(get_current_user)):
    clear_dev_logs()
    return {"ok": True}


@router.get("/dev/step-screenshots")
def dev_step_screenshots_get(_: AuthUserContext = Depends(get_current_user)):
    """Stav screenshotov krokov Playwright (predvolene vypnuté)."""
    return get_step_screenshots_status()


@router.put("/dev/step-screenshots")
def dev_step_screenshots_put(
    payload: StepScreenshotsPayload,
    _: AuthUserContext = Depends(get_current_user),
):
    set_step_screenshots_override(payload.override)
    return get_step_screenshots_status()


@router.post("/products/search", response_model=list[ProductComparison])
async def search_products(
    filters: ProductSearchFilters,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
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

    limit = min(max(filters.limit or 50, 1), 500)
    query = query.limit(limit)

    products = session.exec(query).all()
    suppliers = session.exec(
        select(Supplier).order_by(Supplier.sort_order, Supplier.id)
    ).all()

    response: list[ProductComparison] = []
    for product in products:
        offers: list[SupplierOffer] = []
        mapping_rows = session.exec(
            select(ProductMapping, Supplier)
            .join(Supplier, ProductMapping.supplier_id == Supplier.id)
            .where(ProductMapping.product_id == product.id)
        ).all()
        mapping_rows = sorted(
            mapping_rows,
            key=lambda pair: ((pair[1].sort_order or 0), pair[1].id or 0),
        )

        if mapping_rows:
            for mapping, supplier in mapping_rows:
                price_eur = 0.0
                stock = 0
                if filters.prefetch_live_prices:
                    auto_list = await automation.fetch_supplier_offers(
                        supplier.name, filters
                    )
                    auto = auto_list[0] if auto_list else None
                    price_eur = auto.price_eur if auto else 0.0
                    stock = auto.stock if auto else 0
                offers.append(
                    SupplierOffer(
                        supplier=supplier.name,
                        supplier_id=supplier.id,
                        supplier_code=mapping.supplier_code,
                        supplier_product_url=_supplier_product_url(
                            supplier, mapping.supplier_code
                        ),
                        price_eur=price_eur,
                        stock=stock,
                        logo_url=supplier_logo_public_url(supplier.logo_path),
                    )
                )
        elif filters.prefetch_live_prices:
            for supplier in suppliers:
                auto_list = await automation.fetch_supplier_offers(
                    supplier.name, filters
                )
                for auto in auto_list:
                    offers.append(
                        SupplierOffer(
                            supplier=auto.supplier,
                            supplier_id=supplier.id,
                            supplier_code=auto.supplier_code,
                            supplier_product_url=_supplier_product_url(
                                supplier, auto.supplier_code
                            ),
                            price_eur=auto.price_eur,
                            stock=auto.stock,
                            logo_url=supplier_logo_public_url(supplier.logo_path),
                        )
                    )

        response.append(
            ProductComparison(
                internal_code=product.internal_code,
                norma=product.norma,
                diameter=product.diameter,
                length=product.length,
                surface=product.surface,
                v_class=product.v_class,
                y_money_name=product.y_money_name,
                image_filename=product.image_filename,
                offers=offers,
            )
        )
    return response


FILTER_OPTION_VALUE_LIMIT = 20_000


def _distinct_strings(
    session: Session, column, max_values: int = FILTER_OPTION_VALUE_LIMIT
) -> list[str]:
    rows = session.exec(
        select(column).where(column.isnot(None)).where(column != "")
    ).all()
    values = sorted({str(v).strip() for v in rows if v is not None and str(v).strip()})
    return values[:max_values]


@router.get("/products/filter-options")
def product_filter_options(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    return {
        "norma": _distinct_strings(session, Product.norma),
        "surface": _distinct_strings(session, Product.surface),
        "diameter": _distinct_strings(session, Product.diameter),
        "length": _distinct_strings(session, Product.length),
        "v_class": _distinct_strings(session, Product.v_class),
        "y_money_name": _distinct_strings(session, Product.y_money_name),
    }


def _apply_search_filters(
    statement,
    filters: ProductSearchFilters,
    *,
    skip_code: bool = False,
    skip_norma: bool = False,
    skip_surface: bool = False,
    skip_diameter: bool = False,
    skip_length: bool = False,
    skip_v_class: bool = False,
    skip_y_money_name: bool = False,
):
    if not skip_code and filters.code:
        statement = statement.where(Product.internal_code.contains(filters.code))
    if not skip_norma and filters.norma:
        statement = statement.where(Product.norma == filters.norma)
    if not skip_surface and filters.surface:
        statement = statement.where(Product.surface == filters.surface)
    if not skip_diameter and filters.diameter:
        statement = statement.where(Product.diameter == filters.diameter)
    if not skip_length and filters.length:
        statement = statement.where(Product.length == filters.length)
    if not skip_v_class and filters.v_class:
        statement = statement.where(Product.v_class == filters.v_class)
    if not skip_y_money_name and filters.y_money_name:
        statement = statement.where(Product.y_money_name == filters.y_money_name)
    return statement


def _distinct_conditional(
    session: Session,
    filters: ProductSearchFilters,
    *,
    skip_code: bool,
    skip_norma: bool,
    skip_surface: bool,
    skip_diameter: bool,
    skip_length: bool,
    skip_v_class: bool,
    skip_y_money_name: bool,
    column,
    max_values: int = FILTER_OPTION_VALUE_LIMIT,
) -> list[str]:
    stmt = select(column).distinct()
    stmt = _apply_search_filters(
        stmt,
        filters,
        skip_code=skip_code,
        skip_norma=skip_norma,
        skip_surface=skip_surface,
        skip_diameter=skip_diameter,
        skip_length=skip_length,
        skip_v_class=skip_v_class,
        skip_y_money_name=skip_y_money_name,
    )
    stmt = stmt.where(column.isnot(None)).where(column != "")
    rows = session.exec(stmt).all()
    values = sorted({str(v).strip() for v in rows if v is not None and str(v).strip()})
    return values[:max_values]


@router.post("/products/filter-options/conditional")
def product_filter_options_conditional(
    filters: ProductSearchFilters,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    """Možnosti pre každý filter podľa aktuálne zúženého súboru (kaskáda)."""
    return {
        "norma": _distinct_conditional(
            session,
            filters,
            skip_code=False,
            skip_norma=True,
            skip_surface=False,
            skip_diameter=False,
            skip_length=False,
            skip_v_class=False,
            skip_y_money_name=False,
            column=Product.norma,
        ),
        "surface": _distinct_conditional(
            session,
            filters,
            skip_code=False,
            skip_norma=False,
            skip_surface=True,
            skip_diameter=False,
            skip_length=False,
            skip_v_class=False,
            skip_y_money_name=False,
            column=Product.surface,
        ),
        "diameter": _distinct_conditional(
            session,
            filters,
            skip_code=False,
            skip_norma=False,
            skip_surface=False,
            skip_diameter=True,
            skip_length=False,
            skip_v_class=False,
            skip_y_money_name=False,
            column=Product.diameter,
        ),
        "length": _distinct_conditional(
            session,
            filters,
            skip_code=False,
            skip_norma=False,
            skip_surface=False,
            skip_diameter=False,
            skip_length=True,
            skip_v_class=False,
            skip_y_money_name=False,
            column=Product.length,
        ),
        "v_class": _distinct_conditional(
            session,
            filters,
            skip_code=False,
            skip_norma=False,
            skip_surface=False,
            skip_diameter=False,
            skip_length=False,
            skip_v_class=True,
            skip_y_money_name=False,
            column=Product.v_class,
        ),
        "y_money_name": _distinct_conditional(
            session,
            filters,
            skip_code=False,
            skip_norma=False,
            skip_surface=False,
            skip_diameter=False,
            skip_length=False,
            skip_v_class=False,
            skip_y_money_name=True,
            column=Product.y_money_name,
        ),
    }


@router.get("/mapping/fields")
def get_field_mapping(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    fm = session.get(FieldMapping, 1)
    if fm is None:
        return {
            "code": None,
            "norma": None,
            "surface": None,
            "diameter": None,
            "length": None,
            "v_class": None,
            "y_money_name": None,
            "image_filename": None,
        }
    return {
        "code": fm.code_column,
        "norma": fm.norma_column,
        "surface": fm.surface_column,
        "diameter": fm.diameter_column,
        "length": fm.length_column,
        "v_class": fm.v_class_column,
        "y_money_name": fm.y_money_name_column,
        "image_filename": fm.image_filename_column,
    }


@router.post("/mapping/fields")
def save_field_mapping(
    payload: FieldMappingPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    fm = session.get(FieldMapping, 1)
    if fm is None:
        fm = FieldMapping(id=1)
        session.add(fm)
    fm.code_column = payload.code.strip() if payload.code else None
    fm.norma_column = payload.norma.strip() if payload.norma else None
    fm.surface_column = payload.surface.strip() if payload.surface else None
    fm.diameter_column = payload.diameter.strip() if payload.diameter else None
    fm.length_column = payload.length.strip() if payload.length else None
    fm.v_class_column = payload.v_class.strip() if payload.v_class else None
    fm.y_money_name_column = (
        payload.y_money_name.strip() if payload.y_money_name else None
    )
    fm.image_filename_column = (
        payload.image_filename.strip() if payload.image_filename else None
    )
    session.commit()
    return {"ok": True}


@router.get("/lists")
def lists_get(
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    out: list[dict] = []
    rows = session.exec(
        select(ProductList)
        .where(ProductList.user_id == user.id)
        .order_by(ProductList.created_at.desc())
    ).all()
    for row in rows:
        cnt = session.exec(
            select(ProductListItem).where(ProductListItem.list_id == row.id)
        ).all()
        out.append(
            {
                "id": row.id,
                "name": row.name,
                "created_at": row.created_at.isoformat(),
                "item_count": len(cnt),
            }
        )
    return {"lists": out}


@router.post("/lists")
def lists_create(
    payload: ProductListCreatePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Zadaj názov zoznamu.")
    row = ProductList(user_id=user.id, name=name[:120])
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "name": row.name}


@router.patch("/lists/{list_id}")
def lists_rename(
    list_id: int,
    payload: ProductListRenamePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    row = _get_user_product_list_or_404(session, list_id, user.id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Zadaj názov zoznamu.")
    row.name = name[:120]
    session.add(row)
    session.commit()
    return {"ok": True}


@router.delete("/lists/{list_id}")
def lists_delete(
    list_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    row = _get_user_product_list_or_404(session, list_id, user.id)
    items = session.exec(
        select(ProductListItem).where(ProductListItem.list_id == row.id)
    ).all()
    for it in items:
        session.delete(it)
    session.delete(row)
    session.commit()
    return {"ok": True}


@router.get("/lists/{list_id}")
def lists_detail(
    list_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    row = _get_user_product_list_or_404(session, list_id, user.id)
    items = session.exec(
        select(ProductListItem, Product)
        .join(Product, Product.id == ProductListItem.product_id)
        .where(ProductListItem.list_id == row.id)
        .order_by(ProductListItem.created_at.desc())
    ).all()
    out: list[dict] = []
    for item, prod in items:
        out.append(
            {
                "product_id": prod.id,
                "internal_code": prod.internal_code,
                "norma": prod.norma,
                "diameter": prod.diameter,
                "length": prod.length,
                "surface": prod.surface,
                "v_class": prod.v_class,
                "y_money_name": prod.y_money_name,
                "image_filename": prod.image_filename,
                "added_at": item.created_at.isoformat(),
            }
        )
    return {"id": row.id, "name": row.name, "items": out}


@router.post("/lists/{list_id}/items")
def lists_add_item(
    list_id: int,
    payload: ProductListAddItemPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    row = _get_user_product_list_or_404(session, list_id, user.id)
    code = payload.internal_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Prázdny kód produktu.")
    prod = session.exec(select(Product).where(Product.internal_code == code)).first()
    if prod is None:
        raise HTTPException(status_code=404, detail="Produkt sa nenašiel.")
    existing = session.exec(
        select(ProductListItem).where(
            ProductListItem.list_id == row.id,
            ProductListItem.product_id == prod.id,
        )
    ).first()
    if existing is None:
        session.add(ProductListItem(list_id=row.id, product_id=prod.id))
        session.commit()
    return {"ok": True}


@router.delete("/lists/{list_id}/items/{product_id}")
def lists_remove_item(
    list_id: int,
    product_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    row = _get_user_product_list_or_404(session, list_id, user.id)
    existing = session.exec(
        select(ProductListItem).where(
            ProductListItem.list_id == row.id,
            ProductListItem.product_id == product_id,
        )
    ).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="Položka v zozname sa nenašla.")
    session.delete(existing)
    session.commit()
    return {"ok": True}


@router.post("/scraper/supplier-data")
async def scraper_supplier_data(
    payload: SupplierScrapePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    """Playwright: prihlásenie, vyhľadanie produktu, čítanie ceny a skladu podľa selektorov v JSON."""
    supplier = get_supplier_for_automation(session, payload.supplier_id, user.id)
    try:
        config = load_scraper_config(supplier)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Neplatný cart_config_json: {exc}",
        ) from exc
    code = payload.supplier_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Prázdny kód dodávateľa.")
    try:
        data = await ScraperService.get_supplier_data(
            supplier, code, config, automation_user_id=user.id
        )
    except ScraperProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc).strip() or f"{type(exc).__name__}"
        raise HTTPException(
            status_code=502,
            detail=f"Playwright / e-shop: {msg}",
        ) from exc
    return data


@router.post("/cart/add")
async def cart_add(
    payload: AddToCartPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    supplier = get_supplier_for_automation(session, payload.supplier_id, user.id)
    try:
        config = load_scraper_config(supplier)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Neplatný cart_config_json: {exc}",
        ) from exc
    code = payload.supplier_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Prázdny kód dodávateľa.")
    if payload.quantity < 1:
        raise HTTPException(status_code=400, detail="Množstvo musí byť aspoň 1.")

    try:
        await ScraperService.add_to_cart(
            supplier,
            code,
            payload.quantity,
            config,
            packaging_variant_index=payload.packaging_variant_index,
            mekrs_product_variant_id=payload.mekrs_product_variant_id,
            hopefix_product_id=payload.hopefix_product_id,
            hopefix_package_type=payload.hopefix_package_type,
            haspl_variant_code=payload.haspl_variant_code,
            inoxmare_product_id=payload.inoxmare_product_id,
            inoxmare_referer_path=payload.inoxmare_referer_path,
            automation_user_id=user.id,
        )
        ScraperService.invalidate_remote_cart_cache(
            payload.supplier_id, user_id=user.id
        )
    except ScraperProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc).strip() or f"{type(exc).__name__}"
        raise HTTPException(
            status_code=502,
            detail=f"Playwright / e-shop: {msg}",
        ) from exc
    return {"ok": True}


@router.get("/cart/remote")
async def cart_remote_overview(
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    """Súhrn košíkov u dodávateľov (HTTP: Haspl, Mekrs)."""
    suppliers = session.exec(
        select(Supplier).order_by(Supplier.sort_order, Supplier.id)
    ).all()

    async def _row(s: Supplier) -> dict:
        try:
            eff = effective_supplier_for_user(session, s, user.id)
            return await ScraperService.fetch_remote_cart_overview_row(
                eff, automation_user_id=user.id
            )
        except Exception as exc:
            return {
                "supplier_id": s.id,
                "name": s.name,
                "logo_url": supplier_logo_public_url(s.logo_path),
                "remote_supported": False,
                "logged_in": False,
                "total_eur": None,
                "line_count": 0,
                "message": str(exc).strip() or type(exc).__name__,
                "web_cart_url": supplier_shop_cart_url(s.shop_url or ""),
                "free_shipping_threshold_eur": s.free_shipping_threshold_eur,
            }

    rows = await asyncio.gather(*[_row(s) for s in suppliers])
    return {"suppliers": list(rows)}


@router.get("/cart/remote/{supplier_id}")
async def cart_remote_detail(
    supplier_id: int,
    refresh: bool = Query(
        False,
        description="Zahodiť cache pre tohto dodávateľa a načítať košík znova.",
    ),
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    if refresh:
        ScraperService.invalidate_remote_cart_cache(supplier_id, user_id=user.id)
    eff = effective_supplier_for_user(session, supplier, user.id)
    return await ScraperService.fetch_remote_cart_lines(
        eff, automation_user_id=user.id
    )


@router.post("/suppliers")
def upsert_supplier(
    payload: SupplierUpsertPayload,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    if not payload.shop_url.startswith("http://") and not payload.shop_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="shop_url must start with http:// or https://")

    normalized_cart_cfg = _normalize_supplier_cart_config_json(
        payload.name, payload.cart_config_json
    )

    supplier = None
    if payload.id is not None:
        supplier = session.get(Supplier, payload.id)
        if supplier is None:
            raise HTTPException(status_code=404, detail="Supplier not found.")
    else:
        supplier = session.exec(select(Supplier).where(Supplier.name == payload.name)).first()

    if supplier is None:
        supplier = Supplier(
            name=payload.name,
            shop_url=payload.shop_url,
            username=payload.username,
            password=payload.password,
            is_connected=True,
            code_column=payload.code_column or None,
            cart_config_json=normalized_cart_cfg,
            free_shipping_threshold_eur=payload.free_shipping_threshold_eur,
            sort_order=_next_supplier_sort_order(session),
        )
        session.add(supplier)
    else:
        supplier.name = payload.name
        supplier.shop_url = payload.shop_url
        supplier.username = payload.username
        supplier.password = payload.password
        supplier.is_connected = True
        supplier.code_column = payload.code_column or None
        supplier.cart_config_json = normalized_cart_cfg
        supplier.free_shipping_threshold_eur = payload.free_shipping_threshold_eur

    session.commit()
    session.refresh(supplier)
    ensure_credentials_for_supplier(session, int(supplier.id))

    # U admin účtu okamžite zosúlaď pobočkové credentials so šablónou.
    # Inak môže scraper čítať staré UserSupplierCredential a login padá, hoci admin v UI práve uložil nové údaje.
    admin_cred = session.exec(
        select(UserSupplierCredential).where(
            UserSupplierCredential.user_id == admin.id,
            UserSupplierCredential.supplier_id == int(supplier.id),
        )
    ).first()
    if admin_cred is None:
        admin_cred = UserSupplierCredential(
            user_id=admin.id,
            supplier_id=int(supplier.id),
            username=payload.username.strip(),
            password=payload.password,
        )
        session.add(admin_cred)
    else:
        admin_cred.username = payload.username.strip()
        admin_cred.password = payload.password
    session.commit()
    return {
        "id": supplier.id,
        "name": supplier.name,
        "shop_url": supplier.shop_url,
        "is_connected": supplier.is_connected,
        "code_column": supplier.code_column,
        "cart_config_json": supplier.cart_config_json,
        "logo_url": supplier_logo_public_url(supplier.logo_path),
        "free_shipping_threshold_eur": supplier.free_shipping_threshold_eur,
        "sort_order": supplier.sort_order,
    }


@router.get("/suppliers")
def list_suppliers(
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    suppliers = session.exec(
        select(Supplier).order_by(Supplier.sort_order, Supplier.id)
    ).all()
    return [
        supplier_row_to_api_dict(
            session,
            supplier,
            user.id,
            logo_url=supplier_logo_public_url(supplier.logo_path),
        )
        for supplier in suppliers
    ]


@router.post("/suppliers/reorder")
def reorder_suppliers(
    payload: SupplierReorderPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    """Nastaví poradie dodávateľov (vyhľadávanie, košík, zoznam)."""
    all_suppliers = session.exec(select(Supplier)).all()
    expected = {int(s.id) for s in all_suppliers}
    received = payload.ordered_supplier_ids
    if len(received) != len(expected) or set(received) != expected:
        raise HTTPException(
            status_code=400,
            detail="Zoznam musí obsahovať presne všetkých dodávateľov, každého raz.",
        )
    for i, sid in enumerate(received):
        row = session.get(Supplier, sid)
        if row is None:
            raise HTTPException(status_code=400, detail="Neplatné ID dodávateľa.")
        row.sort_order = i * 10
    session.commit()
    return {"ok": True}


def _delete_supplier_by_id(session: Session, supplier_id: int) -> None:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    creds = session.exec(
        select(UserSupplierCredential).where(
            UserSupplierCredential.supplier_id == supplier_id
        )
    ).all()
    for c in creds:
        session.delete(c)
    mappings = session.exec(
        select(ProductMapping).where(ProductMapping.supplier_id == supplier_id)
    ).all()
    for mapping in mappings:
        session.delete(mapping)
    remove_supplier_logo_files(supplier_id)
    ScraperService.invalidate_remote_cart_cache(supplier_id, user_id=None)
    session.delete(supplier)
    session.commit()


@router.post("/suppliers/remove")
def remove_supplier(
    payload: SupplierRemovePayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    """Odstránenie dodávateľa (POST — rovnaká logika ako DELETE; spoľahlivejšie cez proxy / staré klienty)."""
    _delete_supplier_by_id(session, payload.supplier_id)
    return {"ok": True}


@router.post("/supplier/remove")
def remove_supplier_alias_path(
    payload: SupplierRemovePayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    """Rovnaké ako ``POST /suppliers/remove`` — keď proxy alebo starý routing vracia 404 na cesty pod ``/suppliers/…``."""
    _delete_supplier_by_id(session, payload.supplier_id)
    return {"ok": True}


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(
    supplier_id: int,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    _delete_supplier_by_id(session, supplier_id)
    return {"ok": True}


@router.post("/suppliers/{supplier_id}/logo")
async def upload_supplier_logo(
    supplier_id: int,
    session: Session = Depends(get_session),
    file: UploadFile = File(...),
    _: AuthUserContext = Depends(require_admin),
):
    """Nahratie loga (PNG, JPEG, WebP, GIF, max 2 MB)."""
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    data = await file.read()
    try:
        basename = save_supplier_logo_upload(
            supplier_id, file.content_type, data
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    supplier.logo_path = basename
    session.add(supplier)
    session.commit()
    session.refresh(supplier)
    return {
        "ok": True,
        "logo_url": supplier_logo_public_url(supplier.logo_path),
    }


@router.delete("/suppliers/{supplier_id}/logo")
def delete_supplier_logo(
    supplier_id: int,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    remove_supplier_logo_files(supplier_id)
    supplier.logo_path = None
    session.add(supplier)
    session.commit()
    return {"ok": True, "logo_url": None}


def _import_task_snapshot(task_id: str) -> dict[str, object] | None:
    with _IMPORT_TASKS_LOCK:
        task = _IMPORT_TASKS.get(task_id)
        if task is None:
            return None
        return dict(task)


def _import_task_update(task_id: str, **patch: object) -> None:
    with _IMPORT_TASKS_LOCK:
        task = _IMPORT_TASKS.get(task_id)
        if task is None:
            return
        task.update(patch)
        task["updated_at"] = time.time()


def _run_excel_import_task(task_id: str, file_path: str, sheet_name: str) -> None:
    _import_task_update(task_id, state="running")
    try:
        with Session(engine) as session:
            result = import_gamechanger_excel(
                file_path,
                session,
                sheet_name=sheet_name,
                progress_cb=lambda scanned, total: _import_task_update(
                    task_id, rows_scanned=scanned, total_rows=total
                ),
            )
            for sup in session.exec(select(Supplier)).all():
                ensure_credentials_for_supplier(session, int(sup.id))
            session.commit()
        _import_task_update(
            task_id,
            state="done",
            result={
                "products_upserted": result.products_upserted,
                "suppliers_upserted": result.suppliers_upserted,
                "mappings_upserted": result.mappings_upserted,
                "rows_scanned": result.rows_scanned,
                "total_rows": result.total_rows,
                "warnings": result.warnings,
            },
            rows_scanned=result.rows_scanned,
            total_rows=result.total_rows,
            finished_at=time.time(),
        )
    except FileNotFoundError as exc:
        _import_task_update(
            task_id, state="error", error=str(exc), error_code=404, finished_at=time.time()
        )
    except ValueError as exc:
        _import_task_update(
            task_id, state="error", error=str(exc), error_code=400, finished_at=time.time()
        )
    except Exception as exc:
        _import_task_update(
            task_id, state="error", error=str(exc), error_code=500, finished_at=time.time()
        )


@router.post("/import/excel/start")
def import_excel_start(
    payload: ImportExcelPayload,
    _: AuthUserContext = Depends(require_admin),
):
    sheet = (payload.sheet_name or "DIN").strip() or "DIN"
    task_id = uuid4().hex
    with _IMPORT_TASKS_LOCK:
        _IMPORT_TASKS[task_id] = {
            "task_id": task_id,
            "state": "queued",
            "rows_scanned": 0,
            "total_rows": 0,
            "result": None,
            "error": None,
            "error_code": None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": None,
        }
    thread = threading.Thread(
        target=_run_excel_import_task,
        args=(task_id, payload.file_path, sheet),
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id, "state": "queued"}


@router.get("/import/excel/{task_id}")
def import_excel_status(
    task_id: str,
    _: AuthUserContext = Depends(require_admin),
):
    task = _import_task_snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Import task neexistuje.")
    total_rows = int(task.get("total_rows") or 0)
    rows_scanned = int(task.get("rows_scanned") or 0)
    progress_pct = 0
    if total_rows > 0:
        progress_pct = min(100, int((rows_scanned / total_rows) * 100))
    return {
        "task_id": task_id,
        "state": task.get("state"),
        "rows_scanned": rows_scanned,
        "total_rows": total_rows,
        "progress_pct": progress_pct,
        "result": task.get("result"),
        "error": task.get("error"),
        "error_code": task.get("error_code"),
    }


@router.post("/import/excel")
def import_excel(
    payload: ImportExcelPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    try:
        sheet = (payload.sheet_name or "DIN").strip() or "DIN"
        result = import_gamechanger_excel(
            payload.file_path, session, sheet_name=sheet
        )
        for sup in session.exec(select(Supplier)).all():
            ensure_credentials_for_supplier(session, int(sup.id))
        session.commit()
        return {
            "products_upserted": result.products_upserted,
            "suppliers_upserted": result.suppliers_upserted,
            "mappings_upserted": result.mappings_upserted,
            "rows_scanned": result.rows_scanned,
            "total_rows": result.total_rows,
            "warnings": result.warnings,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mapping/profile")
def mapping_profile(
    payload: ExcelProfilePayload,
    _: AuthUserContext = Depends(get_current_user),
):
    try:
        result = profile_excel_columns(payload.file_path, payload.sheet_name)
        return {
            "sheet": result.sheet,
            "columns": result.columns,
            "preview_rows": result.preview_rows,
            "unique_values": result.unique_values,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
