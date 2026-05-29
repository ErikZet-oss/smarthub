import asyncio
import json
import re
import threading
import time
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import AuthUserContext, get_current_user, require_admin, require_sections_unlock
from app.db import engine, get_session
from app.models.entities import (
    CompanySettings,
    Competitor,
    CompetitorProductMapping,
    FieldMapping,
    Offer,
    OfferLine,
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
from app.services.inoxmare_login_session import (
    capture_inoxmare_cookies_via_playwright,
    complete_inoxmare_login_session,
    inoxmare_login_url_for_supplier,
    merge_inoxmare_cookie_into_cart_config,
    refresh_inoxmare_login_captcha,
    start_inoxmare_login_session,
    verify_inoxmare_cookie_header,
)
from app.services.sections_unlock import issue_sections_unlock_token, verify_sections_unlock_token
from app.services.haspl_http_client import supplier_shop_cart_url
from app.services.smarthub_bootstrap import (
    copy_supplier_credentials_for_new_user,
    delete_smarthub_user_cascade,
    ensure_credentials_for_supplier,
    hash_password,
    verify_password,
)
from app.services.competitor_logos import (
    competitor_logo_public_url,
    remove_competitor_logo_files,
    save_competitor_logo_upload,
)
from app.services.competitor_lowest_price_export import (
    MAX_EXPORT_PRODUCTS,
    build_lowest_price_csv,
    count_products_for_export,
    export_filters_active,
    run_lowest_price_export,
)
from app.services.competitor_scraper_service import (
    competitor_product_url,
    fetch_competitor_public_price,
    invalidate_competitor_price_cache,
    load_competitor_scrape_config,
    normalize_scrape_config_json_for_shop,
    resolve_competitor_scrape_config,
)
from app.services.company_logos import (
    company_logo_public_url,
    remove_company_logo_files,
    save_company_logo_upload,
)
from app.services.offer_export import (
    build_offer_csv,
    build_offer_pdf,
    offer_lines_subtotal,
)
from app.services.offer_pricing import selling_unit_price
from app.services.scraper_service import (
    ScraperProductNotFoundError,
    ScraperService,
    _playwright_fallback_disabled,
    _supplier_is_inoxmare,
    load_scraper_config,
    sanitize_cart_config_json_text,
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
_COMPETITOR_EXPORT_TASKS: dict[str, dict[str, object]] = {}
_COMPETITOR_EXPORT_TASKS_LOCK = threading.Lock()

# Krátka TTL cache na conditional filter-options. Bez aktívnych filtrov (typický prvý
# request po otvorení sekcie Vyhľadávanie) ide o najpomalší endpoint — 6× SELECT DISTINCT.
# 60 s je dosť nato, aby sa pri otvorení/zatvorení stránky znova nepočítalo, a zároveň
# málo, aby import Excelu rýchlo pretiekol do UI.
_FILTER_OPTS_CACHE: dict[str, tuple[float, dict[str, list[str]]]] = {}
_FILTER_OPTS_CACHE_TTL_SEC = 60.0
_FILTER_OPTS_CACHE_LOCK = threading.Lock()


def _filter_opts_cache_key(filters: "ProductSearchFilters") -> str:
    return json.dumps(
        {
            "code": (filters.code or "").strip().lower(),
            "norma": filters.norma or "",
            "surface": filters.surface or "",
            "diameter": filters.diameter or "",
            "length": filters.length or "",
            "v_class": filters.v_class or "",
            "y_money_name": filters.y_money_name or "",
            "image_filename": filters.image_filename or "",
        },
        sort_keys=True,
    )


def _filter_opts_cache_get(key: str) -> dict[str, list[str]] | None:
    with _FILTER_OPTS_CACHE_LOCK:
        hit = _FILTER_OPTS_CACHE.get(key)
        if hit is None:
            return None
        ts, value = hit
        if (time.monotonic() - ts) > _FILTER_OPTS_CACHE_TTL_SEC:
            _FILTER_OPTS_CACHE.pop(key, None)
            return None
        return value


def _filter_opts_cache_set(key: str, value: dict[str, list[str]]) -> None:
    with _FILTER_OPTS_CACHE_LOCK:
        _FILTER_OPTS_CACHE[key] = (time.monotonic(), value)
        # Odstráň najstaršie záznamy, aby cache nerástla bez kontroly.
        if len(_FILTER_OPTS_CACHE) > 64:
            oldest = sorted(_FILTER_OPTS_CACHE.items(), key=lambda kv: kv[1][0])
            for k, _ in oldest[:32]:
                _FILTER_OPTS_CACHE.pop(k, None)


def _filter_opts_cache_invalidate() -> None:
    with _FILTER_OPTS_CACHE_LOCK:
        _FILTER_OPTS_CACHE.clear()


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


def _next_competitor_sort_order(session: Session) -> int:
    rows = session.exec(select(Competitor)).all()
    if not rows:
        return 0
    return max((c.sort_order or 0) for c in rows) + 10


def _competitor_product_url(competitor: Competitor, competitor_code: str | None) -> str | None:
    code = (competitor_code or "").strip()
    if not code:
        return None
    cfg = resolve_competitor_scrape_config(competitor.shop_url or "", competitor.scrape_config_json)
    url = competitor_product_url(competitor.shop_url or "", code, cfg)
    if url:
        return url
    base = (competitor.shop_url or "").strip().rstrip("/")
    if not base:
        return None
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}q={quote(code, safe='')}"


def _competitor_row_to_api_dict(competitor: Competitor) -> dict:
    return {
        "id": competitor.id,
        "name": competitor.name,
        "shop_url": competitor.shop_url,
        "code_column": competitor.code_column,
        "scrape_config_json": competitor.scrape_config_json,
        "logo_url": competitor_logo_public_url(competitor.logo_path),
        "sort_order": competitor.sort_order,
        "is_active": competitor.is_active,
    }


def _strip_json_trailing_commas(text: str) -> str:
    """JSON5-friendly: ostrá `json` knižnica padá na „, }" / „, ]"."""
    import re as _re

    return _re.sub(r",(\s*[}\]])", r"\1", text)


def _normalize_supplier_cart_config_json(name: str, raw_cfg: str | None) -> str | None:
    cfg_text = sanitize_cart_config_json_text((raw_cfg or "").strip())
    if not cfg_text:
        return None
    parsed: dict | None = None
    try:
        parsed = json.loads(cfg_text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_strip_json_trailing_commas(cfg_text))
        except Exception:
            parsed = None
    if isinstance(parsed, dict):
        # Fabory na Render beží bez X servera; browser_channel=chrome spôsobí pád headed browsera.
        # Ak sa hodnota niekde obnoví zo šablóny, pri uložení ju tu odstránime.
        if "fabory" in (name or "").strip().lower():
            parsed.pop("browser_channel", None)
        ck = parsed.get("inoxmare_session_cookie_header")
        if isinstance(ck, str):
            parsed["inoxmare_session_cookie_header"] = re.sub(
                r"[\r\n\t\x00-\x1f]+", " ", ck
            ).strip()
        return json.dumps(parsed, ensure_ascii=False, indent=2)
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
    is_connected: bool = True


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
    # Hopefix HTTP: relatívna cesta katalógu pre Referer pri POST /api/add_to_cart (nájdená pri scrape).
    hopefix_referer_path: str | None = None
    # Hopefix HTTP: ks v jednom zvolenom balení (packaging_variants.pack_quantity) — UI posiela kusy, API počítá balenia.
    hopefix_pack_quantity: int | None = None
    # Haspl HTTP: kód variantu (code) z Sylius product-variants — POST košíka.
    haspl_variant_code: str | None = None
    # Inoxmare HTTP: Magento product ID a relatívna cesta PDP (z packaging_variants).
    inoxmare_product_id: str | None = None
    inoxmare_referer_path: str | None = None
    # Schäfer-Peters HTTP: numerické item_id z PDP (hidden input). Bez neho
    # backend musí najprv získať PDP cez search a item_id si vytiahnuť sám.
    schaef_item_id: str | None = None
    schaef_referer_path: str | None = None


class StepScreenshotsPayload(BaseModel):
    """None = riadiť sa len SCRAPER_STEP_SCREENSHOTS v prostredí."""

    override: bool | None = None


class SupplierScrapePayload(BaseModel):
    supplier_id: int
    supplier_code: str


class CompetitorUpsertPayload(BaseModel):
    id: int | None = None
    name: str
    shop_url: str = ""
    code_column: str | None = None
    scrape_config_json: str | None = None
    is_active: bool = True


class CompetitorRemovePayload(BaseModel):
    competitor_id: int


class CompetitorReorderPayload(BaseModel):
    ordered_competitor_ids: list[int]


class CompetitorPricePayload(BaseModel):
    competitor_id: int
    competitor_code: str


class CompetitorLowestPriceExportPayload(BaseModel):
    code: str | None = None
    norma: str | None = None
    surface: str | None = None
    diameter: str | None = None
    length: str | None = None
    v_class: str | None = None
    y_money_name: str | None = None
    image_filename: str | None = None


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


class AdminPatchUserPasswordPayload(BaseModel):
    password: str


class SectionsUnlockPasswordPayload(BaseModel):
    password: str


class AdminSetSectionsUnlockPasswordPayload(BaseModel):
    password: str | None = None


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


class CompanySettingsPayload(BaseModel):
    company_name: str | None = None
    street: str | None = None
    city: str | None = None
    zip_code: str | None = None
    country: str | None = None
    ico: str | None = None
    dic: str | None = None
    ic_dph: str | None = None
    email: str | None = None
    phone: str | None = None
    web: str | None = None
    iban: str | None = None
    bank_name: str | None = None
    pdf_accent_color: str | None = None
    offer_footer_note: str | None = None


class OfferCreatePayload(BaseModel):
    title: str | None = None
    client_name: str
    client_street: str | None = None
    client_city: str | None = None
    client_zip: str | None = None
    client_country: str | None = None
    client_ico: str | None = None
    client_dic: str | None = None
    client_ic_dph: str | None = None
    client_contact: str | None = None
    client_email: str | None = None
    client_phone: str | None = None
    notes_client: str | None = None
    notes_internal: str | None = None
    valid_until: str | None = None
    default_margin_percent: float | None = None


class OfferUpdatePayload(BaseModel):
    title: str | None = None
    status: str | None = None
    client_name: str | None = None
    client_street: str | None = None
    client_city: str | None = None
    client_zip: str | None = None
    client_country: str | None = None
    client_ico: str | None = None
    client_dic: str | None = None
    client_ic_dph: str | None = None
    client_contact: str | None = None
    client_email: str | None = None
    client_phone: str | None = None
    notes_client: str | None = None
    notes_internal: str | None = None
    valid_until: str | None = None
    default_margin_percent: float | None = None
    apply_margin_to_all_lines: bool = False


class OfferLinePayload(BaseModel):
    description: str
    quantity: float = 1.0
    unit: str = "ks"
    unit_price_eur: float = 0.0
    discount_percent: float = 0.0
    position: int | None = None
    purchase_unit_price_eur: float | None = None
    margin_percent: float | None = None
    supplier_id: int | None = None
    supplier_name: str | None = None
    supplier_code: str | None = None
    product_id: int | None = None


class OfferLineFromCatalogPayload(BaseModel):
    internal_code: str
    supplier_id: int
    supplier_name: str | None = None
    supplier_code: str | None = None
    purchase_price_eur: float
    quantity: float = 1.0
    description: str | None = None
    product_id: int | None = None


class OfferLineUpdatePayload(BaseModel):
    description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price_eur: float | None = None
    discount_percent: float | None = None
    position: int | None = None
    purchase_unit_price_eur: float | None = None
    margin_percent: float | None = None


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


@router.patch("/admin/users/{user_id}")
def admin_patch_user_password(
    user_id: int,
    payload: AdminPatchUserPasswordPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    if not payload.password:
        raise HTTPException(status_code=400, detail="Vyplň heslo.")
    user = session.get(SmarthubUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Používateľ sa nenašiel.")
    if user.is_admin:
        raise HTTPException(
            status_code=400,
            detail="Heslo admin účtu tu nemožno meniť.",
        )
    user.password_hash = hash_password(payload.password)
    session.add(user)
    session.commit()
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Nemôžeš vymazať vlastný účet.")
    user = session.get(SmarthubUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Používateľ sa nenašiel.")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Admin účet nemožno vymazať.")
    delete_smarthub_user_cascade(session, user_id)
    session.commit()
    return {"ok": True}


def _sections_unlock_hash(session: Session) -> str | None:
    row = _ensure_company_settings(session)
    h = (row.sections_unlock_password_hash or "").strip()
    return h or None


@router.get("/admin/sections-unlock")
def admin_get_sections_unlock(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    return {"configured": _sections_unlock_hash(session) is not None}


@router.patch("/admin/sections-unlock")
def admin_set_sections_unlock_password(
    payload: AdminSetSectionsUnlockPasswordPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    row = _ensure_company_settings(session)
    pwd = (payload.password or "").strip()
    if not pwd:
        row.sections_unlock_password_hash = None
    else:
        row.sections_unlock_password_hash = hash_password(pwd)
    session.add(row)
    session.commit()
    return {"configured": row.sections_unlock_password_hash is not None}


@router.get("/auth/sections-unlock/status")
def sections_unlock_status(
    request: Request,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    configured = _sections_unlock_hash(session) is not None
    unlocked = user.is_admin
    if not unlocked:
        raw = (request.headers.get("x-sections-unlock") or "").strip()
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
        unlocked = verify_sections_unlock_token(token, user.id)
    return {"configured": configured, "unlocked": unlocked}


@router.post("/auth/sections-unlock")
def sections_unlock_verify(
    payload: SectionsUnlockPasswordPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    if user.is_admin:
        return {"unlock_token": None, "expires_at": None, "unlocked": True}
    stored = _sections_unlock_hash(session)
    if stored is None:
        raise HTTPException(
            status_code=400,
            detail="Administrátor ešte nenastavil heslo pre odomknutie sekcií.",
        )
    if not payload.password or not verify_password(payload.password, stored):
        raise HTTPException(status_code=401, detail="Nesprávne heslo.")
    token, exp = issue_sections_unlock_token(user.id)
    return {
        "unlock_token": token,
        "expires_at": exp,
        "unlocked": True,
    }


@router.patch("/users/me/supplier-credentials")
def patch_my_supplier_credentials(
    payload: PatchMySupplierCredentialsPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(require_sections_unlock),
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
    # Cached HTTP klient pre tohto používateľa drží staré meno/heslo — zhodiť.
    from app.services.scraper_service import (
        invalidate_supplier_http_session_sync,
        invalidate_supplier_price_cache_sync,
    )

    invalidate_supplier_http_session_sync(
        int(payload.supplier_id), user_id=user.id
    )
    invalidate_supplier_price_cache_sync(
        int(payload.supplier_id), user_id=user.id
    )
    return {"ok": True}


@router.get("/dev/logs")
def dev_logs_get(
    limit: int = 2000,
    _: AuthUserContext = Depends(require_sections_unlock),
):
    """Záznamy z Playwright (scrape / košík) pre ladenie vo frontende."""
    return {"logs": get_dev_logs(min(max(limit, 1), 8000))}


@router.delete("/dev/logs")
def dev_logs_clear(_: AuthUserContext = Depends(require_sections_unlock)):
    clear_dev_logs()
    return {"ok": True}


@router.get("/dev/step-screenshots")
def dev_step_screenshots_get(_: AuthUserContext = Depends(require_sections_unlock)):
    """Stav screenshotov krokov Playwright (predvolene vypnuté)."""
    return get_step_screenshots_status()


@router.put("/dev/step-screenshots")
def dev_step_screenshots_put(
    payload: StepScreenshotsPayload,
    _: AuthUserContext = Depends(require_sections_unlock),
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
    if filters.image_filename:
        query = query.where(Product.image_filename == filters.image_filename)

    limit = min(max(filters.limit or 50, 1), 500)
    query = query.limit(limit)

    products = session.exec(query).all()
    product_ids = [p.id for p in products if p.id is not None]
    use_competitors = (filters.price_source or "suppliers") == "competitors"

    if use_competitors:
        competitors = session.exec(
            select(Competitor)
            .where(Competitor.is_active == True)  # noqa: E712
            .order_by(Competitor.sort_order, Competitor.id)
        ).all()
        competitor_by_id: dict[int, Competitor] = {
            c.id: c for c in competitors if c.id is not None
        }
        mappings_by_product: dict[int, list[tuple[CompetitorProductMapping, Competitor]]] = {}
        if product_ids:
            mapping_rows_all = session.exec(
                select(CompetitorProductMapping).where(
                    CompetitorProductMapping.product_id.in_(product_ids)  # type: ignore[attr-defined]
                )
            ).all()
            for mp in mapping_rows_all:
                comp = competitor_by_id.get(mp.competitor_id)
                if comp is None:
                    continue
                mappings_by_product.setdefault(mp.product_id, []).append((mp, comp))
            for k, lst in mappings_by_product.items():
                lst.sort(key=lambda pair: ((pair[1].sort_order or 0), pair[1].id or 0))

        response: list[ProductComparison] = []
        for product in products:
            offers: list[SupplierOffer] = []
            mapping_rows = mappings_by_product.get(product.id, []) if product.id else []
            for mapping, competitor in mapping_rows:
                offers.append(
                    SupplierOffer(
                        supplier=competitor.name,
                        supplier_id=competitor.id,
                        supplier_code=mapping.competitor_code,
                        supplier_product_url=_competitor_product_url(
                            competitor, mapping.competitor_code
                        ),
                        price_eur=0.0,
                        stock=0,
                        logo_url=competitor_logo_public_url(competitor.logo_path),
                    )
                )
            response.append(
                ProductComparison(
                    product_id=product.id,
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

    suppliers = session.exec(
        select(Supplier)
        .where(Supplier.is_connected == True)  # noqa: E712
        .order_by(Supplier.sort_order, Supplier.id)
    ).all()
    supplier_by_id: dict[int, Supplier] = {s.id: s for s in suppliers if s.id is not None}

    # N+1 fix: namiesto SELECT pre každý produkt načítaj všetky mappingy naraz.
    mappings_by_product: dict[int, list[tuple[ProductMapping, Supplier]]] = {}
    if product_ids:
        mapping_rows_all = session.exec(
            select(ProductMapping)
            .where(ProductMapping.product_id.in_(product_ids))  # type: ignore[attr-defined]
        ).all()
        for mp in mapping_rows_all:
            sup = supplier_by_id.get(mp.supplier_id)
            if sup is None:
                continue
            mappings_by_product.setdefault(mp.product_id, []).append((mp, sup))
        for k, lst in mappings_by_product.items():
            lst.sort(key=lambda pair: ((pair[1].sort_order or 0), pair[1].id or 0))

    response: list[ProductComparison] = []
    for product in products:
        offers: list[SupplierOffer] = []
        mapping_rows = mappings_by_product.get(product.id, []) if product.id else []

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
                product_id=product.id,
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
    skip_image_filename: bool = False,
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
    if not skip_image_filename and filters.image_filename:
        statement = statement.where(Product.image_filename == filters.image_filename)
    return statement


IMAGE_FILTER_OPTION_LIMIT = 200


def _build_image_filter_options(
    session: Session, filters: ProductSearchFilters
) -> list[dict[str, object]]:
    """Distinct obrázky pre lazy image filter — rešpektuje ostatné aktívne filtre."""
    stmt = select(Product.image_filename, func.count(Product.id))  # type: ignore[arg-type]
    stmt = stmt.where(Product.image_filename.isnot(None)).where(Product.image_filename != "")  # type: ignore[union-attr]
    stmt = _apply_search_filters(stmt, filters, skip_image_filename=True)
    stmt = stmt.group_by(Product.image_filename).order_by(func.count(Product.id).desc())  # type: ignore[arg-type]
    rows = session.exec(stmt).all()
    out: list[dict[str, object]] = []
    for row in rows[:IMAGE_FILTER_OPTION_LIMIT]:
        filename = str(row[0] or "").strip()
        if not filename:
            continue
        out.append({"filename": filename, "count": int(row[1] or 0)})
    return out


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


def _build_conditional_filter_options(
    session: Session, filters: ProductSearchFilters
) -> dict[str, list[str]]:
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


@router.post("/products/filter-options/conditional")
def product_filter_options_conditional(
    filters: ProductSearchFilters,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    """Možnosti pre každý filter podľa aktuálne zúženého súboru (kaskáda)."""
    cache_key = _filter_opts_cache_key(filters)
    cached = _filter_opts_cache_get(cache_key)
    if cached is not None:
        return cached
    result = _build_conditional_filter_options(session, filters)
    _filter_opts_cache_set(cache_key, result)
    return result


@router.post("/products/filter-options/images")
def product_filter_options_images(
    filters: ProductSearchFilters,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    """Lazy zoznam obrázkov pre image filter — volá sa až po otvorení panelu."""
    return _build_image_filter_options(session, filters)


@router.get("/health")
def health_check() -> dict[str, str]:
    """Bezauthový endpoint pre uptime monitor (UptimeRobot, BetterStack, GitHub Action).
    Pravidelný ping (každé 4 min) drží Render dyno hore a odstráni 10–15 s cold start.
    """
    return {"status": "ok"}


class BootstrapPayload(BaseModel):
    code: str | None = None
    limit: int = 25
    price_source: str = "suppliers"


@router.post("/bootstrap/search")
async def bootstrap_search(
    payload: BootstrapPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    """
    Spojí 3 prvotné requesty (filter-options + search + suppliers info) do jednej odpovede,
    aby sa UI prvý render dotiahlo jedným round-tripom namiesto sekvenčných 2–3.
    """
    filters = ProductSearchFilters(code=(payload.code or "").strip() or None)
    cache_key = _filter_opts_cache_key(filters)
    cached_opts = _filter_opts_cache_get(cache_key)
    if cached_opts is None:
        cached_opts = _build_conditional_filter_options(session, filters)
        _filter_opts_cache_set(cache_key, cached_opts)

    limit = min(max(payload.limit or 25, 1), 100)
    ps = (payload.price_source or "suppliers").strip().lower()
    if ps not in ("suppliers", "competitors"):
        ps = "suppliers"
    search_filters = ProductSearchFilters(
        code=filters.code,
        limit=limit,
        prefetch_live_prices=False,
        price_source=ps,  # type: ignore[arg-type]
    )
    products = await search_products(  # type: ignore[func-returns-value]
        search_filters, session=session, _=user
    )
    return {
        "filter_options": cached_opts,
        "products": products,
        "limit": limit,
        "is_admin": bool(user.is_admin),
    }


@router.get("/mapping/fields")
def get_field_mapping(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_sections_unlock),
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
    _: AuthUserContext = Depends(require_sections_unlock),
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
            hopefix_referer_path=payload.hopefix_referer_path,
            hopefix_pack_quantity=payload.hopefix_pack_quantity,
            haspl_variant_code=payload.haspl_variant_code,
            inoxmare_product_id=payload.inoxmare_product_id,
            inoxmare_referer_path=payload.inoxmare_referer_path,
            schaef_item_id=payload.schaef_item_id,
            schaef_referer_path=payload.schaef_referer_path,
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
    """Súhrn košíkov u dodávateľov (HTTP: Haspl, Mekrs, Argip)."""
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


@router.get("/cart/remote/{supplier_id}/overview")
async def cart_remote_overview_single(
    supplier_id: int,
    refresh: bool = Query(
        False,
        description="Zahodiť cache pre tohto dodávateľa a načítať súhrn košíka znova.",
    ),
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    """Súhrn košíka jedného dodávateľa — pre postupné načítanie v UI."""
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    if refresh:
        ScraperService.invalidate_remote_cart_cache(supplier_id, user_id=user.id)
    try:
        eff = effective_supplier_for_user(session, supplier, user.id)
        return await ScraperService.fetch_remote_cart_overview_row(
            eff, automation_user_id=user.id
        )
    except Exception as exc:
        return {
            "supplier_id": supplier.id,
            "name": supplier.name,
            "logo_url": supplier_logo_public_url(supplier.logo_path),
            "remote_supported": False,
            "logged_in": False,
            "total_eur": None,
            "line_count": 0,
            "message": str(exc).strip() or type(exc).__name__,
            "web_cart_url": supplier_shop_cart_url(supplier.shop_url or ""),
            "free_shipping_threshold_eur": supplier.free_shipping_threshold_eur,
        }


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
            is_connected=bool(payload.is_connected),
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
        supplier.is_connected = bool(payload.is_connected)
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

    # Zmena šablóny/credentials → cachovaný HTTP klient drží starú reláciu.
    # Zhodíme aj price+stock cache, aby UI hneď ukázalo aktuálne ceny.
    from app.services.scraper_service import (
        invalidate_supplier_http_session_sync,
        invalidate_supplier_price_cache_sync,
    )

    invalidate_supplier_http_session_sync(int(supplier.id))
    invalidate_supplier_price_cache_sync(int(supplier.id))
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


class InoxmareSessionCompletePayload(BaseModel):
    session_token: str
    captcha: str | None = None


class InoxmareSessionImportPayload(BaseModel):
    cookie_header: str


def _require_inoxmare_supplier(session: Session, supplier_id: int) -> Supplier:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Dodávateľ neexistuje.")
    if not _supplier_is_inoxmare(supplier):
        raise HTTPException(status_code=400, detail="Táto akcia je len pre Inox (inoxmare.com).")
    return supplier


def _save_inoxmare_cookie_header(
    session: Session, supplier: Supplier, cookie_header: str
) -> str:
    ck = (cookie_header or "").strip()
    if not ck:
        raise HTTPException(status_code=400, detail="Prázdna hlavička Cookie.")
    merged = merge_inoxmare_cookie_into_cart_config(supplier.cart_config_json, ck)
    normalized = _normalize_supplier_cart_config_json(supplier.name, merged)
    supplier.cart_config_json = normalized
    session.commit()
    session.refresh(supplier)
    from app.services.scraper_service import (
        invalidate_supplier_http_session_sync,
        invalidate_supplier_price_cache_sync,
    )

    invalidate_supplier_http_session_sync(int(supplier.id))
    invalidate_supplier_price_cache_sync(int(supplier.id))
    return normalized or merged


@router.post("/suppliers/{supplier_id}/inoxmare/session/start")
async def inoxmare_session_start(
    supplier_id: int,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    """Začne obnovu relácie — vráti CAPTCHA alebo hneď cookies (ak CAPTCHA nie je potrebná)."""
    supplier = _require_inoxmare_supplier(session, supplier_id)
    eff = effective_supplier_for_user(session, supplier, admin.id)
    if not (eff.username or "").strip() or not (eff.password or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Vyplň prihlasovacie údaje u Inox dodávateľa.",
        )
    try:
        cfg = load_scraper_config(supplier)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Neplatný cart_config_json: {exc}") from exc
    try:
        result = await start_inoxmare_login_session(
            supplier.shop_url or "",
            cfg.inoxmare_store_path,
            eff.username,
            eff.password,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("auto_completed") and result.get("cookie_header"):
        _save_inoxmare_cookie_header(session, supplier, str(result["cookie_header"]))
        result["saved"] = True
    else:
        result["saved"] = False
    result["login_url"] = inoxmare_login_url_for_supplier(eff)
    return result


@router.post("/suppliers/{supplier_id}/inoxmare/session/complete")
async def inoxmare_session_complete(
    supplier_id: int,
    payload: InoxmareSessionCompletePayload,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    supplier = _require_inoxmare_supplier(session, supplier_id)
    try:
        cookie_header = await complete_inoxmare_login_session(
            payload.session_token.strip(),
            captcha_text=payload.captcha,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized = _save_inoxmare_cookie_header(session, supplier, cookie_header)
    return {"ok": True, "cart_config_json": normalized}


@router.post("/suppliers/{supplier_id}/inoxmare/session/refresh-captcha")
async def inoxmare_session_refresh_captcha(
    supplier_id: int,
    payload: InoxmareSessionCompletePayload,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    _require_inoxmare_supplier(session, supplier_id)
    try:
        mime, b64 = await refresh_inoxmare_login_captcha(payload.session_token.strip())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"captcha_mime": mime, "captcha_image_base64": b64}


@router.post("/suppliers/{supplier_id}/inoxmare/session/import")
async def inoxmare_session_import(
    supplier_id: int,
    payload: InoxmareSessionImportPayload,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    """Po ručnom prihlásení v Chrome — vlož celú hlavičku Cookie."""
    supplier = _require_inoxmare_supplier(session, supplier_id)
    try:
        cfg = load_scraper_config(supplier)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Neplatný cart_config_json: {exc}") from exc
    ck = sanitize_cart_config_json_text(payload.cookie_header)
    ok = await verify_inoxmare_cookie_header(
        supplier.shop_url or "",
        cfg.inoxmare_store_path,
        ck,
    )
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Cookies nie sú platné — prihlás sa na inoxmare.com a skopíruj celú hlavičku Cookie.",
        )
    normalized = _save_inoxmare_cookie_header(session, supplier, ck)
    return {"ok": True, "cart_config_json": normalized}


@router.get("/suppliers/{supplier_id}/inoxmare/session/status")
async def inoxmare_session_status(
    supplier_id: int,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    supplier = _require_inoxmare_supplier(session, supplier_id)
    try:
        cfg = load_scraper_config(supplier)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Neplatný cart_config_json: {exc}") from exc
    ck = (cfg.inoxmare_session_cookie_header or "").strip()
    if not ck:
        return {"valid": False, "reason": "missing"}
    valid = await verify_inoxmare_cookie_header(
        supplier.shop_url or "",
        cfg.inoxmare_store_path,
        ck,
    )
    return {
        "valid": valid,
        "reason": None if valid else "expired",
        "login_url": inoxmare_login_url_for_supplier(supplier),
    }


@router.post("/suppliers/{supplier_id}/inoxmare/session/browser")
async def inoxmare_session_browser(
    supplier_id: int,
    session: Session = Depends(get_session),
    admin: AuthUserContext = Depends(require_admin),
):
    """
    Otvorí Chrome s predvyplneným loginom (len lokálny backend s Playwright).
    Na Renderi nie je k dispozícii — použite session/start s CAPTCHA v aplikácii.
    """
    if _playwright_fallback_disabled():
        raise HTTPException(
            status_code=501,
            detail=(
                "Prihlásenie cez prehliadač na serveri nie je na Renderi dostupné. "
                "Použi „Obnoviť reláciu“ s CAPTCHA v aplikácii."
            ),
        )
    supplier = _require_inoxmare_supplier(session, supplier_id)
    eff = effective_supplier_for_user(session, supplier, admin.id)
    try:
        cfg = load_scraper_config(supplier)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Neplatný cart_config_json: {exc}") from exc
    try:
        cookie_header = await capture_inoxmare_cookies_via_playwright(eff, cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized = _save_inoxmare_cookie_header(session, supplier, cookie_header)
    return {"ok": True, "cart_config_json": normalized}


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
            supplier_id, file.content_type, data, file.filename
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


def _delete_competitor_by_id(session: Session, competitor_id: int) -> None:
    competitor = session.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status_code=404, detail="Konkurencia neexistuje.")
    mappings = session.exec(
        select(CompetitorProductMapping).where(
            CompetitorProductMapping.competitor_id == competitor_id
        )
    ).all()
    for mapping in mappings:
        session.delete(mapping)
    remove_competitor_logo_files(competitor_id)
    invalidate_competitor_price_cache(competitor_id)
    session.delete(competitor)
    session.commit()


@router.post("/competitors")
def upsert_competitor(
    payload: CompetitorUpsertPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    shop_url = (payload.shop_url or "").strip()
    if shop_url and not shop_url.startswith(("http://", "https://")):
        shop_url = f"https://{shop_url.lstrip('/')}"
    if shop_url and not shop_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="shop_url must start with http:// or https://")

    competitor = None
    if payload.id is not None:
        competitor = session.get(Competitor, payload.id)
        if competitor is None:
            raise HTTPException(status_code=404, detail="Konkurencia neexistuje.")
    else:
        competitor = session.exec(
            select(Competitor).where(Competitor.name == payload.name)
        ).first()

    scrape_cfg = normalize_scrape_config_json_for_shop(
        shop_url, (payload.scrape_config_json or "").strip() or None
    )
    if competitor is None:
        competitor = Competitor(
            name=payload.name.strip(),
            shop_url=shop_url,
            code_column=(payload.code_column or "").strip() or None,
            scrape_config_json=scrape_cfg,
            is_active=bool(payload.is_active),
            sort_order=_next_competitor_sort_order(session),
        )
        session.add(competitor)
    else:
        competitor.name = payload.name.strip()
        competitor.shop_url = shop_url
        competitor.code_column = (payload.code_column or "").strip() or None
        competitor.scrape_config_json = scrape_cfg
        competitor.is_active = bool(payload.is_active)

    session.commit()
    session.refresh(competitor)
    invalidate_competitor_price_cache(int(competitor.id))
    return _competitor_row_to_api_dict(competitor)


@router.get("/competitors")
def list_competitors(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    rows = session.exec(
        select(Competitor).order_by(Competitor.sort_order, Competitor.id)
    ).all()
    return [_competitor_row_to_api_dict(c) for c in rows]


@router.post("/competitors/reorder")
def reorder_competitors(
    payload: CompetitorReorderPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    order = [int(x) for x in payload.ordered_competitor_ids]
    for idx, cid in enumerate(order):
        comp = session.get(Competitor, cid)
        if comp is not None:
            comp.sort_order = idx * 10
            session.add(comp)
    session.commit()
    return {"ok": True}


@router.post("/competitors/remove")
def remove_competitor(
    payload: CompetitorRemovePayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    _delete_competitor_by_id(session, payload.competitor_id)
    return {"ok": True}


@router.delete("/competitors/{competitor_id}")
def delete_competitor(
    competitor_id: int,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    _delete_competitor_by_id(session, competitor_id)
    return {"ok": True}


@router.post("/competitors/{competitor_id}/logo")
async def upload_competitor_logo(
    competitor_id: int,
    session: Session = Depends(get_session),
    file: UploadFile = File(...),
    _: AuthUserContext = Depends(require_admin),
):
    competitor = session.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status_code=404, detail="Konkurencia neexistuje.")
    data = await file.read()
    try:
        basename = save_competitor_logo_upload(
            competitor_id, file.content_type, data, file.filename
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    competitor.logo_path = basename
    session.add(competitor)
    session.commit()
    session.refresh(competitor)
    return {
        "ok": True,
        "logo_url": competitor_logo_public_url(competitor.logo_path),
    }


@router.delete("/competitors/{competitor_id}/logo")
def delete_competitor_logo(
    competitor_id: int,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    competitor = session.get(Competitor, competitor_id)
    if competitor is None:
        raise HTTPException(status_code=404, detail="Konkurencia neexistuje.")
    remove_competitor_logo_files(competitor_id)
    competitor.logo_path = None
    session.add(competitor)
    session.commit()
    return {"ok": True, "logo_url": None}


@router.post("/competitors/price")
async def competitor_price(
    payload: CompetitorPricePayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    """Verejná cena z e-shopu konkurencie (HTTP, bez prihlásenia)."""
    competitor = session.get(Competitor, payload.competitor_id)
    if competitor is None:
        raise HTTPException(status_code=404, detail="Konkurencia neexistuje.")
    code = payload.competitor_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Prázdny kód konkurencie.")
    try:
        data = await fetch_competitor_public_price(
            competitor_id=int(competitor.id),
            shop_url=competitor.shop_url or "",
            competitor_code=code,
            scrape_config_json=competitor.scrape_config_json,
        )
    except Exception as exc:
        msg = str(exc).strip() or f"{type(exc).__name__}"
        raise HTTPException(status_code=502, detail=f"Konkurencia / e-shop: {msg}") from exc
    data["competitor_product_url"] = data.get("competitor_product_url") or _competitor_product_url(
        competitor, code
    )
    if data.get("competitor_product_url"):
        data["supplier_product_url"] = data["competitor_product_url"]
    return data


def _competitor_export_filters(payload: CompetitorLowestPriceExportPayload) -> ProductSearchFilters:
    return ProductSearchFilters(
        code=(payload.code or "").strip() or None,
        norma=(payload.norma or "").strip() or None,
        surface=(payload.surface or "").strip() or None,
        diameter=(payload.diameter or "").strip() or None,
        length=(payload.length or "").strip() or None,
        v_class=(payload.v_class or "").strip() or None,
        y_money_name=(payload.y_money_name or "").strip() or None,
        image_filename=(payload.image_filename or "").strip() or None,
    )


def _competitor_export_task_snapshot(task_id: str) -> dict[str, object] | None:
    with _COMPETITOR_EXPORT_TASKS_LOCK:
        task = _COMPETITOR_EXPORT_TASKS.get(task_id)
        if task is None:
            return None
        return dict(task)


def _competitor_export_task_update(task_id: str, **patch: object) -> None:
    with _COMPETITOR_EXPORT_TASKS_LOCK:
        task = _COMPETITOR_EXPORT_TASKS.get(task_id)
        if task is None:
            return
        task.update(patch)
        task["updated_at"] = time.time()


def _run_competitor_lowest_export_task(
    task_id: str, filters: ProductSearchFilters, total_products: int
) -> None:
    _competitor_export_task_update(task_id, state="running", total=total_products)

    def _progress(processed: int, total: int, errors: int) -> None:
        _competitor_export_task_update(
            task_id, processed=processed, total=total, errors=errors
        )

    try:
        with Session(engine) as session:
            rows, _ = asyncio.run(
                run_lowest_price_export(session, filters, progress_cb=_progress)
            )
        csv_bytes = build_lowest_price_csv(rows)
        filename_bits = ["najnizsie-ceny-konkurencie"]
        if filters.norma:
            safe_norma = re.sub(r"[^\w\-]+", "-", filters.norma.strip())[:40]
            if safe_norma:
                filename_bits.append(safe_norma)
        filename_bits.append(datetime.now().strftime("%Y%m%d"))
        filename = "-".join(filename_bits) + ".csv"
        _competitor_export_task_update(
            task_id,
            state="done",
            csv_bytes=csv_bytes,
            filename=filename,
            row_count=len(rows),
            finished_at=time.time(),
        )
    except Exception as exc:
        _competitor_export_task_update(
            task_id, state="error", error=str(exc), finished_at=time.time()
        )


@router.post("/competitors/export-lowest-prices/start")
def competitor_lowest_export_start(
    payload: CompetitorLowestPriceExportPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    filters = _competitor_export_filters(payload)
    if not export_filters_active(filters):
        raise HTTPException(
            status_code=400,
            detail="Nastav aspoň jeden filter (napr. normu), aby export neobsahoval celú databázu.",
        )
    total = count_products_for_export(session, filters)
    if total == 0:
        raise HTTPException(status_code=400, detail="Podľa filtrov sa nenašiel žiadny produkt.")
    if total > MAX_EXPORT_PRODUCTS:
        raise HTTPException(
            status_code=400,
            detail=f"Filter zodpovedá {total} produktom (max. {MAX_EXPORT_PRODUCTS}). Spresni filter.",
        )

    task_id = uuid4().hex
    with _COMPETITOR_EXPORT_TASKS_LOCK:
        _COMPETITOR_EXPORT_TASKS[task_id] = {
            "task_id": task_id,
            "state": "queued",
            "processed": 0,
            "total": total,
            "errors": 0,
            "row_count": 0,
            "filename": None,
            "csv_bytes": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": None,
        }
    thread = threading.Thread(
        target=_run_competitor_lowest_export_task,
        args=(task_id, filters, total),
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id, "state": "queued", "total": total}


@router.get("/competitors/export-lowest-prices/{task_id}")
def competitor_lowest_export_status(
    task_id: str,
    _: AuthUserContext = Depends(get_current_user),
):
    task = _competitor_export_task_snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Export task neexistuje.")
    total = int(task.get("total") or 0)
    processed = int(task.get("processed") or 0)
    progress_pct = 0
    if total > 0:
        progress_pct = min(100, int((processed / total) * 100))
    return {
        "task_id": task_id,
        "state": task.get("state"),
        "processed": processed,
        "total": total,
        "errors": int(task.get("errors") or 0),
        "row_count": int(task.get("row_count") or 0),
        "progress_pct": progress_pct,
        "filename": task.get("filename"),
        "error": task.get("error"),
        "finished_at": task.get("finished_at"),
    }


@router.get("/competitors/export-lowest-prices/{task_id}/download")
def competitor_lowest_export_download(
    task_id: str,
    _: AuthUserContext = Depends(get_current_user),
):
    with _COMPETITOR_EXPORT_TASKS_LOCK:
        task = _COMPETITOR_EXPORT_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Export task neexistuje.")
        if task.get("state") != "done":
            raise HTTPException(status_code=409, detail="Export ešte nie je dokončený.")
        csv_bytes = task.get("csv_bytes")
        filename = str(task.get("filename") or "najnizsie-ceny-konkurencie.csv")
    if not isinstance(csv_bytes, (bytes, bytearray)) or not csv_bytes:
        raise HTTPException(status_code=500, detail="Chýba súbor exportu.")
    return Response(
        content=bytes(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        # filter-options cache musí vidieť nové normy/povrchy/priemery z importu.
        _filter_opts_cache_invalidate()
        _import_task_update(
            task_id,
            state="done",
            result={
                "products_upserted": result.products_upserted,
                "products_legacy_removed": result.products_legacy_removed,
                "suppliers_upserted": result.suppliers_upserted,
                "mappings_upserted": result.mappings_upserted,
                "rows_scanned": result.rows_scanned,
                "total_rows": result.total_rows,
                "file_resolved": result.file_resolved,
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
    _: AuthUserContext = Depends(require_sections_unlock),
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
    _: AuthUserContext = Depends(require_sections_unlock),
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
    _: AuthUserContext = Depends(require_sections_unlock),
):
    try:
        sheet = (payload.sheet_name or "DIN").strip() or "DIN"
        result = import_gamechanger_excel(
            payload.file_path, session, sheet_name=sheet
        )
        for sup in session.exec(select(Supplier)).all():
            ensure_credentials_for_supplier(session, int(sup.id))
        session.commit()
        _filter_opts_cache_invalidate()
        return {
            "products_upserted": result.products_upserted,
            "products_legacy_removed": result.products_legacy_removed,
            "suppliers_upserted": result.suppliers_upserted,
            "mappings_upserted": result.mappings_upserted,
            "rows_scanned": result.rows_scanned,
            "total_rows": result.total_rows,
            "file_resolved": result.file_resolved,
            "warnings": result.warnings,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mapping/profile")
def mapping_profile(
    payload: ExcelProfilePayload,
    _: AuthUserContext = Depends(require_sections_unlock),
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


def _ensure_company_settings(session: Session) -> CompanySettings:
    row = session.get(CompanySettings, 1)
    if row is None:
        row = CompanySettings(id=1)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _company_settings_api(row: CompanySettings) -> dict:
    return {
        "company_name": row.company_name or "",
        "street": row.street,
        "city": row.city,
        "zip_code": row.zip_code,
        "country": row.country,
        "ico": row.ico,
        "dic": row.dic,
        "ic_dph": row.ic_dph,
        "email": row.email,
        "phone": row.phone,
        "web": row.web,
        "iban": row.iban,
        "bank_name": row.bank_name,
        "logo_url": company_logo_public_url(row.logo_path),
        "pdf_accent_color": row.pdf_accent_color or "#0284c7",
        "offer_footer_note": row.offer_footer_note,
    }


def _parse_optional_datetime(raw: str | None) -> datetime | None:
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Neplatný formát dátumu (očakávané ISO)."
        ) from exc


def _next_offer_number(session: Session) -> str:
    year = datetime.utcnow().year
    prefix = f"PON-{year}-"
    offers = session.exec(select(Offer)).all()
    max_n = 0
    for offer in offers:
        num = offer.offer_number or ""
        if not num.startswith(prefix):
            continue
        try:
            max_n = max(max_n, int(num.split("-")[-1]))
        except ValueError:
            pass
    return f"{prefix}{max_n + 1:04d}"


def _get_user_offer_or_404(session: Session, offer_id: int, user_id: int) -> Offer:
    row = session.exec(
        select(Offer).where(Offer.id == offer_id, Offer.user_id == user_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Ponuka sa nenašla.")
    return row


def _offer_line_total(line: OfferLine) -> float:
    qty = float(line.quantity or 0)
    price = float(line.unit_price_eur or 0)
    disc = float(line.discount_percent or 0)
    return round(qty * price * (1.0 - disc / 100.0), 2)


def _offer_line_api(line: OfferLine) -> dict:
    return {
        "id": line.id,
        "position": line.position,
        "description": line.description,
        "quantity": line.quantity,
        "unit": line.unit,
        "unit_price_eur": line.unit_price_eur,
        "purchase_unit_price_eur": line.purchase_unit_price_eur,
        "margin_percent": line.margin_percent,
        "discount_percent": line.discount_percent,
        "line_total_eur": _offer_line_total(line),
        "product_id": line.product_id,
        "supplier_id": line.supplier_id,
        "supplier_name": line.supplier_name,
        "supplier_code": line.supplier_code,
    }


def _apply_line_margin(line: OfferLine, margin_percent: float | None = None) -> None:
    if margin_percent is not None:
        line.margin_percent = float(margin_percent)
    line.unit_price_eur = selling_unit_price(
        line.purchase_unit_price_eur,
        line.margin_percent,
        fallback_unit_price_eur=line.unit_price_eur,
    )


def _apply_margin_to_all_lines(session: Session, offer: Offer) -> None:
    lines = session.exec(select(OfferLine).where(OfferLine.offer_id == offer.id)).all()
    for line in lines:
        if line.purchase_unit_price_eur is not None and line.purchase_unit_price_eur > 0:
            line.margin_percent = float(offer.default_margin_percent or 0)
            _apply_line_margin(line)
            session.add(line)


def _catalog_line_description(
    product: Product,
    supplier_name: str,
    supplier_code: str | None,
) -> str:
    parts = [product.internal_code]
    if product.y_money_name:
        parts.append(product.y_money_name)
    elif product.norma:
        parts.append(product.norma)
    desc = " · ".join(parts)
    code = (supplier_code or "").strip()
    if code:
        return f"{desc} ({supplier_name}, {code})"
    return f"{desc} ({supplier_name})"


def _offer_api(offer: Offer, lines: list[OfferLine] | None = None) -> dict:
    sorted_lines = (
        sorted(lines, key=lambda x: (x.position, x.id or 0)) if lines is not None else None
    )
    subtotal = offer_lines_subtotal(sorted_lines) if sorted_lines is not None else None
    payload = {
        "id": offer.id,
        "offer_number": offer.offer_number,
        "title": offer.title,
        "status": offer.status,
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
        "client_name": offer.client_name,
        "client_street": offer.client_street,
        "client_city": offer.client_city,
        "client_zip": offer.client_zip,
        "client_country": offer.client_country,
        "client_ico": offer.client_ico,
        "client_dic": offer.client_dic,
        "client_ic_dph": offer.client_ic_dph,
        "client_contact": offer.client_contact,
        "client_email": offer.client_email,
        "client_phone": offer.client_phone,
        "notes_client": offer.notes_client,
        "notes_internal": offer.notes_internal,
        "default_margin_percent": float(offer.default_margin_percent or 0),
        "created_at": offer.created_at.isoformat() if offer.created_at else None,
        "updated_at": offer.updated_at.isoformat() if offer.updated_at else None,
    }
    if sorted_lines is not None:
        payload["lines"] = [_offer_line_api(ln) for ln in sorted_lines]
        payload["subtotal_eur"] = subtotal
        payload["vat_eur"] = round((subtotal or 0) * 0.21, 2)
        payload["total_eur"] = round((subtotal or 0) * 1.21, 2)
    return payload


@router.get("/company-settings")
def get_company_settings(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    return _company_settings_api(_ensure_company_settings(session))


@router.patch("/admin/company-settings")
def patch_company_settings(
    payload: CompanySettingsPayload,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    row = _ensure_company_settings(session)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None and isinstance(value, str):
            value = value.strip() or None
        setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _company_settings_api(row)


@router.post("/admin/company-settings/logo")
async def upload_company_logo(
    session: Session = Depends(get_session),
    file: UploadFile = File(...),
    _: AuthUserContext = Depends(require_admin),
):
    data = await file.read()
    try:
        basename = save_company_logo_upload(file.content_type, data, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = _ensure_company_settings(session)
    row.logo_path = basename
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"ok": True, "logo_url": company_logo_public_url(row.logo_path)}


@router.delete("/admin/company-settings/logo")
def delete_company_logo(
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(require_admin),
):
    remove_company_logo_files()
    row = _ensure_company_settings(session)
    row.logo_path = None
    session.add(row)
    session.commit()
    return {"ok": True}


@router.get("/offers")
def list_offers(
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offers = session.exec(
        select(Offer)
        .where(Offer.user_id == user.id)
        .order_by(Offer.updated_at.desc())
    ).all()
    return [
        {
            "id": o.id,
            "offer_number": o.offer_number,
            "title": o.title,
            "status": o.status,
            "client_name": o.client_name,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "updated_at": o.updated_at.isoformat() if o.updated_at else None,
        }
        for o in offers
    ]


@router.post("/offers")
def create_offer(
    payload: OfferCreatePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    name = (payload.client_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Vyplň názov firmy odberateľa.")
    now = datetime.utcnow()
    offer = Offer(
        user_id=user.id,
        offer_number=_next_offer_number(session),
        title=(payload.title or "").strip() or None,
        status="draft",
        client_name=name,
        client_street=(payload.client_street or "").strip() or None,
        client_city=(payload.client_city or "").strip() or None,
        client_zip=(payload.client_zip or "").strip() or None,
        client_country=(payload.client_country or "").strip() or None,
        client_ico=(payload.client_ico or "").strip() or None,
        client_dic=(payload.client_dic or "").strip() or None,
        client_ic_dph=(payload.client_ic_dph or "").strip() or None,
        client_contact=(payload.client_contact or "").strip() or None,
        client_email=(payload.client_email or "").strip() or None,
        client_phone=(payload.client_phone or "").strip() or None,
        notes_client=(payload.notes_client or "").strip() or None,
        notes_internal=(payload.notes_internal or "").strip() or None,
        valid_until=_parse_optional_datetime(payload.valid_until),
        default_margin_percent=float(payload.default_margin_percent or 0),
        created_at=now,
        updated_at=now,
    )
    session.add(offer)
    session.commit()
    session.refresh(offer)
    return _offer_api(offer, [])


@router.get("/offers/{offer_id}")
def get_offer(
    offer_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    lines = session.exec(
        select(OfferLine).where(OfferLine.offer_id == offer_id)
    ).all()
    return _offer_api(offer, list(lines))


@router.patch("/offers/{offer_id}")
def patch_offer(
    offer_id: int,
    payload: OfferUpdatePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    apply_all = bool(data.pop("apply_margin_to_all_lines", False))
    if "valid_until" in data:
        data["valid_until"] = _parse_optional_datetime(data.pop("valid_until"))
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip() or (None if key != "client_name" else "")
        setattr(offer, key, value)
    if not (offer.client_name or "").strip():
        raise HTTPException(status_code=400, detail="Vyplň názov firmy odberateľa.")
    offer.updated_at = datetime.utcnow()
    session.add(offer)
    if apply_all:
        _apply_margin_to_all_lines(session, offer)
    session.commit()
    session.refresh(offer)
    lines = session.exec(
        select(OfferLine).where(OfferLine.offer_id == offer_id)
    ).all()
    return _offer_api(offer, list(lines))


@router.delete("/offers/{offer_id}")
def delete_offer(
    offer_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    for line in session.exec(
        select(OfferLine).where(OfferLine.offer_id == offer_id)
    ).all():
        session.delete(line)
    session.delete(offer)
    session.commit()
    return {"ok": True}


@router.post("/offers/{offer_id}/lines")
def add_offer_line(
    offer_id: int,
    payload: OfferLinePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    desc = (payload.description or "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="Vyplň popis položky.")
    existing = session.exec(
        select(OfferLine).where(OfferLine.offer_id == offer_id)
    ).all()
    position = payload.position
    if position is None:
        position = max((ln.position for ln in existing), default=0) + 1
    purchase = payload.purchase_unit_price_eur
    margin = (
        float(payload.margin_percent)
        if payload.margin_percent is not None
        else float(offer.default_margin_percent or 0)
    )
    unit = selling_unit_price(
        purchase,
        margin,
        fallback_unit_price_eur=float(payload.unit_price_eur),
    )
    line = OfferLine(
        offer_id=offer_id,
        position=position,
        description=desc,
        quantity=float(payload.quantity),
        unit=(payload.unit or "ks").strip() or "ks",
        unit_price_eur=unit,
        purchase_unit_price_eur=purchase,
        margin_percent=margin,
        discount_percent=float(payload.discount_percent),
        product_id=payload.product_id,
        supplier_id=payload.supplier_id,
        supplier_name=(payload.supplier_name or "").strip() or None,
        supplier_code=(payload.supplier_code or "").strip() or None,
    )
    session.add(line)
    offer.updated_at = datetime.utcnow()
    session.add(offer)
    session.commit()
    session.refresh(line)
    return _offer_line_api(line)


@router.post("/offers/{offer_id}/lines/from-catalog")
def add_offer_line_from_catalog(
    offer_id: int,
    payload: OfferLineFromCatalogPayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    try:
        return _add_offer_line_from_catalog_impl(session, offer_id, user.id, payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pridanie do ponuky zlyhalo: {exc}",
        ) from exc


def _add_offer_line_from_catalog_impl(
    session: Session,
    offer_id: int,
    user_id: int,
    payload: OfferLineFromCatalogPayload,
) -> dict:
    offer = _get_user_offer_or_404(session, offer_id, user_id)
    purchase = float(payload.purchase_price_eur)
    if purchase < 0:
        raise HTTPException(status_code=400, detail="Neplatná nákupná cena.")
    code = (payload.internal_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Chýba interný kód produktu.")
    prod = None
    if payload.product_id:
        prod = session.get(Product, payload.product_id)
    if prod is None:
        prod = session.exec(
            select(Product).where(Product.internal_code == code)
        ).first()
    if prod is None:
        raise HTTPException(status_code=404, detail="Produkt sa nenašiel v katalógu.")
    supplier = session.get(Supplier, payload.supplier_id)
    supplier_name = (
        (payload.supplier_name or "").strip()
        or (supplier.name if supplier else "")
        or "Dodávateľ"
    )
    desc = (payload.description or "").strip() or _catalog_line_description(
        prod,
        supplier_name,
        payload.supplier_code,
    )
    margin = float(offer.default_margin_percent or 0)
    unit = selling_unit_price(purchase, margin, fallback_unit_price_eur=purchase)
    existing = session.exec(
        select(OfferLine).where(OfferLine.offer_id == offer_id)
    ).all()
    position = max((ln.position for ln in existing), default=0) + 1
    line = OfferLine(
        offer_id=offer_id,
        position=position,
        description=desc,
        quantity=float(payload.quantity or 1),
        unit="ks",
        unit_price_eur=unit,
        purchase_unit_price_eur=purchase,
        margin_percent=margin,
        product_id=prod.id,
        supplier_id=payload.supplier_id,
        supplier_name=supplier_name,
        supplier_code=(payload.supplier_code or "").strip() or None,
    )
    session.add(line)
    offer.updated_at = datetime.utcnow()
    session.add(offer)
    session.commit()
    session.refresh(line)
    return _offer_line_api(line)


@router.patch("/offers/{offer_id}/lines/{line_id}")
def patch_offer_line(
    offer_id: int,
    line_id: int,
    payload: OfferLineUpdatePayload,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    line = session.get(OfferLine, line_id)
    if line is None or line.offer_id != offer_id:
        raise HTTPException(status_code=404, detail="Položka sa nenašla.")
    data = payload.model_dump(exclude_unset=True)
    recalc_margin = "margin_percent" in data or "purchase_unit_price_eur" in data
    for key, value in data.items():
        if key == "description" and isinstance(value, str):
            value = value.strip()
            if not value:
                raise HTTPException(status_code=400, detail="Vyplň popis položky.")
        setattr(line, key, value)
    if recalc_margin:
        _apply_line_margin(
            line,
            data.get("margin_percent") if "margin_percent" in data else None,
        )
    offer.updated_at = datetime.utcnow()
    session.add(line)
    session.add(offer)
    session.commit()
    session.refresh(line)
    return _offer_line_api(line)


@router.delete("/offers/{offer_id}/lines/{line_id}")
def delete_offer_line(
    offer_id: int,
    line_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    line = session.get(OfferLine, line_id)
    if line is None or line.offer_id != offer_id:
        raise HTTPException(status_code=404, detail="Položka sa nenašla.")
    session.delete(line)
    offer.updated_at = datetime.utcnow()
    session.add(offer)
    session.commit()
    return {"ok": True}


@router.get("/offers/{offer_id}/export/pdf")
def export_offer_pdf(
    offer_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    lines = list(
        session.exec(select(OfferLine).where(OfferLine.offer_id == offer_id)).all()
    )
    company = _ensure_company_settings(session)
    pdf = build_offer_pdf(offer, lines, company)
    safe_num = offer.offer_number.replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ponuka-{safe_num}.pdf"'
        },
    )


@router.get("/offers/{offer_id}/export/csv")
def export_offer_csv(
    offer_id: int,
    session: Session = Depends(get_session),
    user: AuthUserContext = Depends(get_current_user),
):
    offer = _get_user_offer_or_404(session, offer_id, user.id)
    lines = list(
        session.exec(select(OfferLine).where(OfferLine.offer_id == offer_id)).all()
    )
    company = _ensure_company_settings(session)
    csv_bytes = build_offer_csv(offer, lines, company)
    safe_num = offer.offer_number.replace("/", "-")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ponuka-{safe_num}.csv"'
        },
    )
