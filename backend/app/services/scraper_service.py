"""Playwright ScraperService: prihlásenie, vyhľadanie produktu, čítanie ceny/skladu, pridanie do košíka.

Konfigurácia: `Supplier.cart_config_json` podľa modelu `ScraperConfig` (selektory z HTML e-shopu).
"""

from __future__ import annotations

import asyncio
import copy
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field
from playwright.async_api import BrowserContext, Locator, Page, Route, async_playwright

from app.models.entities import Supplier
from app.services.hopefix_http_client import (
    HopefixHttpClient,
    build_hopefix_catalog_url,
    find_hopefix_row,
    hopefix_norm_code,
    hopefix_raw_suggests_oos,
    hopefix_row_is_oos,
    parse_hopefix_rows,
)
from app.services.bmkco_http_client import (
    BmkcoHttpClient,
    bmkco_base_url,
    bmkco_norm_code,
)
from app.services.halfmann_http_client import (
    HalfmannHttpClient,
    halfmann_base_url,
    halfmann_norm_artid,
)
from app.services.haspl_http_client import (
    HasplHttpClient,
    haspl_base_url,
    haspl_net_price_eur,
    haspl_norm_code,
    haspl_pack_display_text,
    haspl_parse_open_order,
    haspl_pieces_to_pack_units,
    haspl_price_unit_key,
    haspl_variant_pack_quantity,
    supplier_shop_cart_url,
)
from app.services.argip_http_client import (
    ArgipHttpClient,
    argip_cart_url,
    argip_parse_cart_json,
)
from app.services.schachermayer_http_client import (
    SchachermayerHttpClient,
    schachermayer_parse_cart_json,
    schachermayer_web_cart_url,
)
from app.services.valenta_http_client import ValentaHttpClient, valenta_cart_url
from app.services.inoxmare_http_client import (
    InoxmareHttpClient,
    inoxmare_origin,
    inoxmare_parse_cookie_header,
    inoxmare_playwright_cookie_domain,
    inoxmare_store_path,
    parse_inoxmare_pdp,
)
from app.services.mekrs_http_client import (
    MekrsHttpClient,
    _mekrs_code_key,
    _mekrs_nominal_to_per_100ks_display,
    _mekrs_sanitize_variant_label,
    mekrs_parse_cart_json,
)
from app.services.supplier_logos import supplier_logo_public_url
from app.services.dev_run_log import (
    dev_run_log,
    dev_run_log_exception,
    dev_screens_dir,
    step_screenshots_enabled,
)

_SCRAPER_DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data")
)

try:
    _REMOTE_CART_CACHE_TTL_SEC = float(
        os.environ.get("REMOTE_CART_CACHE_TTL_SEC", "25") or "25"
    )
except (TypeError, ValueError):
    _REMOTE_CART_CACHE_TTL_SEC = 25.0
RemoteCartCacheStore = dict[str, tuple[float, dict[str, Any]]]
_remote_cart_overview_cache: RemoteCartCacheStore = {}
_remote_cart_detail_cache: RemoteCartCacheStore = {}
# Surové JSON z posledného prehľadu — detail môže preskočiť opätovné GET košíka.
_remote_haspl_order_snapshot: RemoteCartCacheStore = {}
_remote_mekrs_cart_snapshot: RemoteCartCacheStore = {}
_remote_argip_cart_snapshot: RemoteCartCacheStore = {}
_remote_schachermayer_cart_snapshot: RemoteCartCacheStore = {}


class ScraperProductNotFoundError(ValueError):
    """Vyhľadanie na e-shope nenašlo produkt (prázdny zoznam alebo čakanie na kartu výsledku vypršalo)."""


def _remote_cart_cache_enabled() -> bool:
    """0 = vypnuté (env REMOTE_CART_CACHE_TTL_SEC)."""
    return _REMOTE_CART_CACHE_TTL_SEC > 0


def _remote_cart_cache_key(user_id: int, supplier_id: int) -> str:
    return f"{int(user_id)}:{int(supplier_id)}"


def _remote_cart_cache_get(
    store: RemoteCartCacheStore,
    user_id: int,
    supplier_id: int,
) -> Optional[dict[str, Any]]:
    if not _remote_cart_cache_enabled():
        return None
    key = _remote_cart_cache_key(user_id, supplier_id)
    item = store.get(key)
    if not item:
        return None
    exp, payload = item
    if time.monotonic() > exp:
        del store[key]
        return None
    return copy.deepcopy(payload)


def _remote_cart_cache_set(
    store: RemoteCartCacheStore,
    user_id: int,
    supplier_id: int,
    payload: dict[str, Any],
) -> None:
    if not _remote_cart_cache_enabled():
        return
    key = _remote_cart_cache_key(user_id, supplier_id)
    store[key] = (
        time.monotonic() + _REMOTE_CART_CACHE_TTL_SEC,
        copy.deepcopy(payload),
    )


def _invalidate_remote_cart_cache(
    supplier_id: Optional[int],
    user_id: Optional[int] = None,
) -> None:
    """Po zmene košíka zahodiť cache. Ak je ``user_id`` None, zmaže záznamy pre všetkých používateľov daného dodávateľa."""
    if supplier_id is None:
        return
    sid = int(supplier_id)
    if user_id is not None:
        key = _remote_cart_cache_key(int(user_id), sid)
        _remote_cart_overview_cache.pop(key, None)
        _remote_cart_detail_cache.pop(key, None)
        _remote_haspl_order_snapshot.pop(key, None)
        _remote_mekrs_cart_snapshot.pop(key, None)
        _remote_argip_cart_snapshot.pop(key, None)
        _remote_schachermayer_cart_snapshot.pop(key, None)
        return
    suffix = f":{sid}"
    for store in (
        _remote_cart_overview_cache,
        _remote_cart_detail_cache,
        _remote_haspl_order_snapshot,
        _remote_mekrs_cart_snapshot,
        _remote_argip_cart_snapshot,
        _remote_schachermayer_cart_snapshot,
    ):
        for k in list(store.keys()):
            if k.endswith(suffix):
                store.pop(k, None)


def _clear_all_remote_cart_caches() -> None:
    """Úplné obnovenie záložky Košík (tlačidlo Obnoviť) — všetci dodávatelia."""
    _remote_cart_overview_cache.clear()
    _remote_cart_detail_cache.clear()
    _remote_haspl_order_snapshot.clear()
    _remote_mekrs_cart_snapshot.clear()
    _remote_argip_cart_snapshot.clear()
    _remote_schachermayer_cart_snapshot.clear()


def _session_reuse_enabled() -> bool:
    """Uložiť/načítať Playwright storage state — zapnúť explicitne: SCRAPER_REUSE_SESSION=1."""
    v = os.environ.get("SCRAPER_REUSE_SESSION", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _storage_state_path(supplier_id: int, automation_user_id: int = 0) -> str:
    uid = int(automation_user_id)
    if uid <= 0:
        uid = 0
    return os.path.join(
        _SCRAPER_DATA_DIR,
        "scraper_sessions",
        f"supplier_{int(supplier_id)}_user_{uid}.json",
    )


async def _persist_scraper_storage_state(
    context: BrowserContext,
    supplier: Supplier,
    *,
    run_label: str,
    run_id: str,
    logged_in: bool,
    automation_user_id: int = 0,
) -> None:
    if (
        not logged_in
        or not _session_reuse_enabled()
        or supplier.id is None
    ):
        return
    path = _storage_state_path(supplier.id, automation_user_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        await context.storage_state(path=path)
        _log(
            run_label,
            supplier,
            run_id,
            f"SCRAPER_REUSE_SESSION: uložený storage state ({path})",
        )
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"SCRAPER_REUSE_SESSION: nepodarilo sa uložiť storage: {exc!s}",
            "warn",
        )


def _supplier_is_mekrs(supplier: Supplier) -> bool:
    return "mekrs" in (supplier.name or "").lower()


def _supplier_is_hopefix(supplier: Supplier) -> bool:
    # „HOPE fix“, „Hope  Fix“ → po zrušení medzier musí sedieť s hopefix.cz
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    return "hopefix" in compact


def _supplier_has_remote_cart_credentials(supplier: Supplier) -> bool:
    return bool(
        (supplier.shop_url or "").strip()
        and (supplier.username or "").strip()
        and supplier.password is not None
        and str(supplier.password).strip() != ""
    )


def _supplier_is_haspl(supplier: Supplier) -> bool:
    return "haspl" in (supplier.name or "").lower()


def _supplier_is_bmkco(supplier: Supplier) -> bool:
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    if "bmkco" in compact:
        return True
    u = (supplier.shop_url or "").lower()
    return "bmkco.cz" in u


def _supplier_is_halfmann(supplier: Supplier) -> bool:
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    if "halfmann" in compact:
        return True
    u = (supplier.shop_url or "").lower()
    return "halfmann-schrauben.de" in u


def _supplier_is_argip(supplier: Supplier) -> bool:
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    if "argip" in compact:
        return True
    u = (supplier.shop_url or "").lower()
    return "argip.com.pl" in u


def _supplier_is_schachermayer(supplier: Supplier) -> bool:
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    if "schachermayer" in compact:
        return True
    u = (supplier.shop_url or "").lower()
    return "schachermayer.com" in u


def _supplier_is_valenta(supplier: Supplier) -> bool:
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    if "valenta" in compact:
        return True
    u = (supplier.shop_url or "").lower()
    return "valentazt.cz" in u


def _supplier_is_inoxmare(supplier: Supplier) -> bool:
    """
    Inox Mare / https://www.inoxmare.com/en/ — „Inox Mare“ → inoxmare; skrátený názov „Inox“
    pri URL na inoxmare (inak iný dodávateľ „Inox“).
    """
    raw = (supplier.shop_url or "").strip()
    ulow = raw.lower()
    # Primárne: spoľahlivá reťaz v URL (funguje aj bez schémy / s www).
    if "inoxmare.com" in ulow or "inoxmare.it" in ulow:
        return True
    host = ""
    if raw:
        u = raw if raw.startswith("http://") or raw.startswith("https://") else f"https://{raw}"
        try:
            host = (urlparse(u).hostname or "").lower()
        except ValueError:
            host = ""
    if host.endswith("inoxmare.com") or host.endswith("inoxmare.it"):
        return True
    if "inoxmare." in ulow:
        return True
    compact = re.sub(r"\s+", "", (supplier.name or "").lower())
    if "inoxmare" in compact:
        return True
    if compact == "inox" and (
        host.endswith("inoxmare.com")
        or host.endswith("inoxmare.it")
        or "inoxmare.com" in ulow
        or "inoxmare.it" in ulow
    ):
        return True
    return False


def _supplier_is_fabory(supplier: Supplier) -> bool:
    return "fabory" in (supplier.name or "").lower()


def supplier_allows_empty_cart_config(supplier: Supplier) -> bool:
    """Dodávatelia s HTTP klientom — Playwright JSON nie je povinný."""
    if (
        _supplier_is_haspl(supplier)
        or _supplier_is_inoxmare(supplier)
        or _supplier_is_bmkco(supplier)
        or _supplier_is_halfmann(supplier)
        or _supplier_is_argip(supplier)
        or _supplier_is_schachermayer(supplier)
        or _supplier_is_valenta(supplier)
    ):
        return True
    u = (supplier.shop_url or "").lower()
    # Záloha podľa URL, ak by názov nebol rozpoznaný (napr. vlastný názov záznamu).
    if "haspl.cz" in u or "haspl.sk" in u:
        return True
    if "inoxmare.com" in u or "inoxmare.it" in u:
        return True
    if "bmkco.cz" in u:
        return True
    if "halfmann-schrauben.de" in u:
        return True
    if "argip.com.pl" in u:
        return True
    if "schachermayer.com" in u:
        return True
    if "valentazt.cz" in u:
        return True
    return False


def _hopefix_http_enabled(config: ScraperConfig) -> bool:
    return bool((config.hopefix_catalog_url_template or "").strip())


def _hopefix_apply_oos_zero_pricing(target: dict[str, Any]) -> None:
    target["price_eur"] = 0.0
    target["raw_price"] = "0.00 €"
    target["stock"] = 0
    for pv in target.get("packaging_variants") or []:
        if isinstance(pv, dict):
            pv["price_eur"] = 0.0
            pv["stock"] = 0


def _hopefix_normalize_oos_display(data: dict[str, Any]) -> None:
    """HTTP aj Playwright: pri vypredaní (text alebo sklad 0) cena 0 € ako na hopefix.cz."""
    if hopefix_row_is_oos(
        {"stock": data.get("stock"), "raw_stock": data.get("raw_stock")}
    ):
        _hopefix_apply_oos_zero_pricing(data)
        return
    pvs = data.get("packaging_variants") or []
    if len(pvs) == 1 and isinstance(pvs[0], dict):
        pv0 = pvs[0]
        if hopefix_row_is_oos(
            {"stock": pv0.get("stock"), "raw_stock": pv0.get("raw_stock")}
        ):
            _hopefix_apply_oos_zero_pricing(data)


def _hopefix_ensure_packaging_variants_row(
    data: dict[str, Any],
    product_code: str,
    config: ScraperConfig,
) -> None:
    """Playwright doplní ``packaging_variants`` ako Hopefix HTTP — front-end inak nemá riadok variantu (živá cena / „demo“)."""
    if data.get("packaging_variants"):
        return
    have = (
        data.get("price_eur") is not None
        or data.get("stock") is not None
        or bool((data.get("raw_price") or "").strip())
        or bool((data.get("raw_stock") or "").strip())
    )
    if not have:
        return
    pkg_type = (config.hopefix_default_package_type or "box").strip() or "box"
    pq = data.get("pack_quantity")
    pack_q = int(pq) if isinstance(pq, int) and pq >= 1 else 1
    data["packaging_variants"] = [
        {
            "label": (product_code or "").strip(),
            "pack_quantity": pack_q,
            "price_eur": data.get("price_eur"),
            "raw_price": data.get("raw_price"),
            "stock": data.get("stock"),
            "raw_stock": data.get("raw_stock"),
            "hopefix_product_id": None,
            "hopefix_package_type": pkg_type,
        }
    ]


def _hopefix_login_prompt_in(text: str) -> bool:
    return "ihlásit" in (text or "").lower()


def _hopefix_parse_decimal(text: str) -> Optional[float]:
    """Číslo z bunky tabuľky (desatinná čiarka)."""
    t = (text or "").replace("\xa0", " ").strip()
    if not t or t.upper() == "N/A":
        return None
    t = re.sub(r"(?i)kč|czk|€|eur|\s*/\s*100\s*pcs?|\s*pcs?", " ", t)
    t = t.strip()
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _hopefix_cell_is_eur_price(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and not _hopefix_login_prompt_in(t) and (
        "€" in t or "eur" in t.lower()
    )


def _hopefix_cell_is_czk_price(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and not _hopefix_login_prompt_in(t) and (
        "kč" in t.lower() or "czk" in t.lower()
    )


def _hopefix_price_indices(texts: list[str]) -> tuple[list[int], list[int]]:
    """Indexy buniek s EUR resp. CZK cenou (B2B tabuľka má obe; pevný index už nie je spoľahlivý)."""
    eur_i: list[int] = []
    czk_i: list[int] = []
    for i, raw in enumerate(texts):
        if _hopefix_cell_is_eur_price(raw):
            if _parse_price_eur(raw) is not None:
                eur_i.append(i)
        elif _hopefix_cell_is_czk_price(raw):
            if _hopefix_parse_decimal(raw) is not None:
                czk_i.append(i)
    return eur_i, czk_i


def _hopefix_parse_pack_cell(text: str) -> Optional[float]:
    """Počet z bunky Box (podporuje „30,00“, „3 000“)."""
    t = (text or "").replace("\xa0", " ").strip()
    if not t or t.upper() == "N/A":
        return None
    while re.search(r"(?<=\d)\s+(?=\d)", t):
        t = re.sub(r"(?<=\d)\s+(?=\d)", "", t, count=1)
    return _hopefix_parse_decimal(t)


def _hopefix_box_cell_to_pack_pieces(val: float, *, header_is_100_pcs_box: bool) -> Optional[int]:
    """
    Hopefix hlavička „100 pcs Box“: číslo v bunke = počet **stoviek ks** v balení (30 → 3 000 ks).
    Ak je v bunke už veľké číslo (≥ 1000), berieme ho ako priamy počet kusov.
    """
    if val is None or val <= 0:
        return None
    if not header_is_100_pcs_box:
        return max(1, int(round(val)))
    if val >= 1000:
        return max(1, int(round(val)))
    return max(1, int(round(val * 100)))


async def _hopefix_box_pack_column_meta(row: Locator) -> tuple[Optional[int], bool]:
    """Index stĺpca Box a či hlavička hovorí o násobkoch 100 ks."""
    try:
        tbl = row.locator("xpath=ancestor::table[1]")
        if await tbl.count() < 1:
            return None, False
        heads = tbl.locator("thead tr").first.locator("th")
        hn = await heads.count()
    except Exception:
        return None, False
    for i in range(hn):
        try:
            h = (await heads.nth(i).inner_text()).lower()
        except Exception:
            continue
        hflat = re.sub(r"\s+", " ", h.replace("\n", " "))
        if "box" not in hflat:
            continue
        is_100 = bool(
            re.search(r"100\s*pcs", hflat)
            or "100pcs" in hflat.replace(" ", "")
        )
        return i, is_100
    return None, False


async def _hopefix_pick_search_autocomplete(
    page: Page,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    """
    Hopefix: po vyplnení #search_input sa zobrazí jQuery UI autocomplete.
    Treba kliknúť na riadok výsledku (animácia + presmerovanie) — ArrowDown+Enter
    a ešte jedno Enter z search_submit_key často nespustí správnu navigáciu.
    """
    code = (product_code or "").strip()
    key = hopefix_norm_code(code)
    menu_timeout = min(8_000, max(2_400, int(config.navigation_timeout_ms) // 2))
    await asyncio.sleep(
        max(0.08, min(0.72, float(config.search_suggestion_wait_ms) / 1000.0))
    )

    menu = page.locator("ul.ui-autocomplete:visible").first
    try:
        await menu.wait_for(state="visible", timeout=menu_timeout)
    except Exception:
        _log(
            run_label,
            supplier,
            run_id,
            "Hopefix autocomplete: menu nie je viditeľné — ArrowDown + Enter",
            "warn",
        )
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.06)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.15)
        return

    items = page.locator("ul.ui-autocomplete:visible li.ui-menu-item")
    try:
        n = await items.count()
    except Exception:
        n = 0
    if n < 1:
        items = page.locator("ul.ui-autocomplete li.ui-menu-item")

    target = items.first
    if key:
        try:
            hit = items.filter(has_text=re.compile(re.escape(key), re.I))
            if await hit.count() > 0:
                target = hit.first
        except Exception:
            pass

    try:
        await target.scroll_into_view_if_needed(timeout=4_000)
        await target.click(timeout=min(14_000, int(config.navigation_timeout_ms)))
        _log(run_label, supplier, run_id, "Hopefix: klik na návrh v autocomplete")
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix autocomplete: klik zlyhal ({exc!s}) — ArrowDown + Enter",
            "warn",
        )
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.06)
        await page.keyboard.press("Enter")
    await asyncio.sleep(0.17)


def _hopefix_url_with_row_anchor(raw_url: str, key: str) -> str:
    """
    Hopefix často používa query ?_ref=KÓD (search_via_url_template) a/alebo #KÓD.
    Bez oboch vie SPA nescrollovať na riadok — v DOM je <td>KÓD</td>, ale riadok je skrytý.
    """
    k = hopefix_norm_code(key)
    u = (raw_url or "").strip()
    if not k or not u:
        return u
    p = urlparse(u)
    q = [(a, b) for a, b in parse_qsl(p.query, keep_blank_values=True) if a != "_ref"]
    q.append(("_ref", k))
    new_query = urlencode(q)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, k))


async def _hopefix_apply_product_hash_if_needed(
    page: Page,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    """
    Doplní do URL **_ref** a **#kotvu** podľa kódu (Hopefix SPA).
    """
    key = hopefix_norm_code(product_code)
    if not key:
        return
    cur = (page.url or "").strip()
    if not cur:
        return
    p = urlparse(cur)
    qmap = dict(parse_qsl(p.query, keep_blank_values=True))
    ref_ok = hopefix_norm_code(str(qmap.get("_ref") or "")) == key
    frag = (p.fragment or "").split("?")[0].strip()
    frag_ok = hopefix_norm_code(frag) == key
    if ref_ok and frag_ok:
        return
    target = _hopefix_url_with_row_anchor(cur, key)
    if target == cur:
        return
    _log(
        run_label,
        supplier,
        run_id,
        f"Hopefix: dopĺňam _ref a kotvu v URL (riadok {key})",
    )
    nav_to = min(60_000, max(15_000, int(config.navigation_timeout_ms)))
    try:
        await page.goto(target, wait_until="domcontentloaded", timeout=nav_to)
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix kotva: goto {target!r} zlyhal ({exc!s}), skúšam hash + history",
            "warn",
        )
        try:
            await page.evaluate(
                """({ ref, hash }) => {
                  const u = new URL(window.location.href);
                  u.searchParams.set('_ref', ref);
                  u.hash = hash;
                  window.history.replaceState({}, '', u);
                  window.dispatchEvent(new PopStateEvent('popstate'));
                }""",
                {"ref": key, "hash": key},
            )
            await asyncio.sleep(0.32)
        except Exception as exc2:
            _log(
                run_label,
                supplier,
                run_id,
                f"Hopefix kotva: replaceState zlyhal ({exc2!s}), skúšam len hash",
                "warn",
            )
            try:
                await page.evaluate("h => { window.location.hash = h; }", key)
                await asyncio.sleep(0.28)
            except Exception as exc3:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Hopefix kotva: hash tiež zlyhal: {exc3!s}",
                    "warn",
                )
    await asyncio.sleep(0.35)


def _hopefix_site_origin(page_url: str) -> str:
    u = (page_url or "").strip()
    if not u:
        return "https://www.hopefix.cz"
    p = urlparse(u)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://www.hopefix.cz"


async def _hopefix_registration_row_index_in_tbody(page: Page, key: str) -> int:
    """
    Index riadku v tbody#rows, kde niektoré td obsahuje len registračné číslo
    (napr. <td>D9338810060B1</td>) — zarovnané s hopefix_norm_code.
    """
    k = hopefix_norm_code(key)
    if not k:
        return -1
    try:
        idx = await page.evaluate(
            """want => {
              const norm = (s) => String(s || '')
                .trim()
                .toUpperCase()
                .replace(/\\s+/g, '');
              const w = norm(want);
              const tb = document.getElementById('rows');
              if (!tb) return -1;
              let idx = 0;
              for (const tr of tb.getElementsByTagName('tr')) {
                const tds = tr.getElementsByTagName('td');
                for (let j = 0; j < tds.length; j++) {
                  const raw = tds[j].innerText || tds[j].textContent || '';
                  if (norm(raw) === w) return idx;
                }
                idx++;
              }
              return -1;
            }""",
            k,
        )
    except Exception:
        return -1
    if isinstance(idx, int) and idx >= 0:
        return idx
    return -1


async def _hopefix_find_row_by_exact_td_text(
    page: Page,
    key: str,
    eff_timeout: int,
) -> Optional[Locator]:
    """Riadok, ktorý má bunku s presne týmto kódom (typicky Registrační číslo)."""
    esc = re.escape(key)
    pat = re.compile(rf"^\s*{esc}\s*$", re.I)
    loc = page.locator("tbody#rows tr").filter(
        has=page.locator("td").filter(has_text=pat)
    )
    try:
        if await loc.count() < 1:
            return None
        row = loc.first
        try:
            await row.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        await row.wait_for(state="visible", timeout=min(eff_timeout, 25_000))
        return row
    except Exception:
        return None


async def _hopefix_scroll_catalog_until_line_present(
    page: Page,
    key: str,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    budget_ms: int,
) -> bool:
    """
    Na /sortiment/* je jedna veľká tabuľka (aj šróby); riadky sa často dopĺňajú
    až po scrollovaní. Netreba otvárať podkategórie — stačí dole nájsť kód.
    """
    line_sel = f'tr[id="line-{key}"]'
    t0 = time.monotonic()
    stable = 0
    prev_n = -1
    logged = False
    while (time.monotonic() - t0) * 1000.0 < float(budget_ms):
        try:
            if await page.locator(line_sel).count() > 0:
                return True
        except Exception:
            pass
        if await _hopefix_registration_row_index_in_tbody(page, key) >= 0:
            return True
        try:
            n_now = await page.evaluate(
                "() => document.querySelectorAll('tr[id^=\"line-\"]').length"
            )
        except Exception:
            n_now = prev_n
        if not isinstance(n_now, int):
            n_now = prev_n
        if not logged and isinstance(n_now, int) and n_now > 400:
            _log(
                run_label,
                supplier,
                run_id,
                f"Hopefix: veľká tabuľka (~{n_now} riadkov v DOM), scroll po riadok "
                f"(id line-{key} alebo stĺpec Registrační číslo)…",
            )
            logged = True
        try:
            await page.evaluate(
                """() => {
                  const scrollMax = (e) => {
                    if (!e) return false;
                    const sh = e.scrollHeight, ch = e.clientHeight;
                    if (sh > ch + 4) { e.scrollTop = sh; return true; }
                    return false;
                  };
                  const rows = document.getElementById('rows');
                  if (rows) {
                    let n = rows;
                    while (n && n !== document.documentElement) {
                      if (scrollMax(n)) return;
                      n = n.parentElement;
                    }
                  }
                  window.scrollTo(0, document.documentElement.scrollHeight);
                }"""
            )
        except Exception:
            pass
        await asyncio.sleep(0.085)
        try:
            n_after = await page.evaluate(
                "() => document.querySelectorAll('tr[id^=\"line-\"]').length"
            )
        except Exception:
            n_after = n_now
        if isinstance(n_after, int):
            if prev_n >= 0 and n_after == prev_n:
                stable += 1
                if stable >= 7:
                    break
            else:
                stable = 0
            prev_n = n_after
    try:
        if await page.locator(line_sel).count() > 0:
            return True
    except Exception:
        pass
    return await _hopefix_registration_row_index_in_tbody(page, key) >= 0


def _hopefix_fallback_category_segments(product_code: str) -> list[str]:
    """Poradie podcest /sortiment/<segment>#kód podľa typického zaradenia DIN kódu."""
    k = hopefix_norm_code(product_code)
    tail = [
        "trhaci-nyty",
        "kotevni-technika",
        "napinace",
    ]
    all_seg = [
        "srouby",
        "podlozky",
        "matice",
        "vruty",
        "zavitove-tyce",
        *tail,
    ]
    if not k:
        return all_seg
    # Skrutky / závit (DIN 933, 931, 912, …) — na /sortiment je len rozcestník, tabuľka je v /srouby
    if re.match(
        r"^D9(12|13|14|16|2[0-5]|31|33|34|35|60|61|62|63|64|91)",
        k,
    ) or k.startswith("D6912"):
        first = ["srouby", "vruty", "matice", "podlozky", "zavitove-tyce"]
        rest = [s for s in all_seg if s not in first]
        return first + rest
    if re.match(r"^D12[0-9]", k) or k.startswith("D9021"):
        first = ["podlozky", "srouby", "matice", "vruty", "zavitove-tyce"]
        rest = [s for s in all_seg if s not in first]
        return first + rest
    if re.match(r"^D98", k) or re.match(r"^D69", k):
        first = ["matice", "srouby", "podlozky", "vruty", "zavitove-tyce"]
        rest = [s for s in all_seg if s not in first]
        return first + rest
    return all_seg


async def _hopefix_try_catalog_categories_with_hash(
    page: Page,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> bool:
    """
    Ak sme na rozcestníku /sortiment alebo riadok v DOM nie je, skús správnu
    veľkú kategóriu s kotvou #KÓD (napr. /sortiment/srouby#D9338810060B1).
    """
    key = hopefix_norm_code(product_code)
    if not key:
        return False
    origin = _hopefix_site_origin(page.url or "")
    nav_to = min(60_000, max(15_000, int(config.navigation_timeout_ms)))
    tried: set[str] = set()
    for seg in _hopefix_fallback_category_segments(product_code):
        if seg in tried:
            continue
        tried.add(seg)
        target = f"{origin.rstrip('/')}/sortiment/{seg}#{key}"
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix: skúšam kategóriu+hash {target!r}",
        )
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=nav_to)
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"Hopefix kategória {seg!r}: goto zlyhal ({exc!s})",
                "warn",
            )
            continue
        await asyncio.sleep(0.5)
        scroll_budget = min(48_000, max(14_000, int(nav_to) * 2))
        await _hopefix_scroll_catalog_until_line_present(
            page,
            key,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            budget_ms=scroll_budget,
        )
        try:
            n = await page.locator(f'tr[id="line-{key}"]').count()
        except Exception:
            n = 0
        ridx = await _hopefix_registration_row_index_in_tbody(page, key)
        if n > 0 or ridx >= 0:
            _log(
                run_label,
                supplier,
                run_id,
                f"Hopefix: riadok pre {key} nájdený v /sortiment/{seg} "
                f"({'id line-' if n > 0 else 'stĺpec Registrační číslo'})",
            )
            return True
        await _hopefix_log_table_debug(
            page,
            key,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            context=f"po /sortiment/{seg} (riadok line-{key} v DOM nie)",
        )
    return False


async def _hopefix_log_table_debug(
    page: Page,
    key: str,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    context: str,
) -> None:
    """Do dev logu: URL, počet tr#line-*, vzorka id — na diagnostiku bez DevTools."""
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    tbody_n = -1
    try:
        tbody_n = await page.locator("tbody#rows tr").count()
    except Exception:
        pass
    snap: dict[str, Any] = {}
    try:
        snap = await page.evaluate(
            """() => {
              const rows = document.querySelectorAll('tr[id^="line-"]');
              return {
                lineTrCount: rows.length,
                sampleIds: Array.from(rows).slice(0, 30).map((r) => r.id),
              };
            }"""
        )
    except Exception as exc:
        snap = {"evaluateError": str(exc)}
    line_n = snap.get("lineTrCount", "?")
    sample = snap.get("sampleIds", [])
    needle = [x for x in sample if key in str(x).upper()] if isinstance(sample, list) else []
    _log(
        run_label,
        supplier,
        run_id,
        f"Hopefix DEBUG [{context}] url={url!r} tbody#rows_tr≈{tbody_n} "
        f"tr[line-*]={line_n} sample={sample!r} ids_containing_{key!r}={needle!r}",
    )


async def _hopefix_find_row_locator(
    page: Page,
    key: str,
    timeout: int,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> Optional[Locator]:
    try:
        await page.locator("#rows").first.wait_for(
            state="attached",
            timeout=min(timeout, 10_000),
        )
    except Exception:
        pass
    try:
        url_l = (page.url or "").lower()
    except Exception:
        url_l = ""
    if "/sortiment/" in url_l:
        try:
            has = await page.locator(f'tr[id="line-{key}"]').count() > 0
        except Exception:
            has = False
        if not has:
            has = await _hopefix_registration_row_index_in_tbody(page, key) >= 0
        if not has:
            scroll_budget = min(48_000, max(14_000, int(timeout) * 3))
            await _hopefix_scroll_catalog_until_line_present(
                page,
                key,
                run_label=run_label,
                supplier=supplier,
                run_id=run_id,
                budget_ms=scroll_budget,
            )
    eff_timeout = max(timeout, 12_000)
    loc = page.locator(f'tbody#rows tr[id="line-{key}"]')
    if await loc.count() < 1:
        loc = page.locator(f'tr[id="line-{key}"]')
    row = loc.first
    try:
        try:
            await row.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        await row.wait_for(state="visible", timeout=min(eff_timeout, 25_000))
        return row
    except Exception:
        by_td = await _hopefix_find_row_by_exact_td_text(page, key, eff_timeout)
        if by_td is not None:
            return by_td
        rows = page.locator('tbody#rows tr[id^="line-"], table tr[id^="line-"]')
        n = await rows.count()
        found: Optional[Locator] = None
        for i in range(min(n, 12_000)):
            cand = rows.nth(i)
            rid = await cand.get_attribute("id")
            if not rid:
                continue
            low = rid.lower()
            if not low.startswith("line-"):
                continue
            suf = rid[5:] if len(rid) > 5 else ""
            if hopefix_norm_code(suf) == key:
                found = cand
                break
        if found is not None:
            row = found
        else:
            ridx = await _hopefix_registration_row_index_in_tbody(page, key)
            if ridx < 0:
                return None
            row = page.locator("tbody#rows tr").nth(ridx)
        try:
            await row.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        await row.wait_for(state="visible", timeout=min(eff_timeout, 25_000))
        return row


async def _hopefix_scrape_offer_table_row(
    page: Page,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    timeout: int,
) -> dict[str, Any]:
    """
    Po vyhľadávaní je produkt často v tabuľke tbody#rows ako tr#line-<KÓD>.
    Prihlásený účet: často EUR/100 ks aj CZK/100 ks — berieme **EUR**, ak je v riadku prítomné.
    """
    out: dict[str, Any] = {}
    key = hopefix_norm_code(product_code)
    if not key:
        return out
    row = await _hopefix_find_row_locator(
        page,
        key,
        timeout,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
    )
    if row is None:
        await _hopefix_log_table_debug(
            page,
            key,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            context="pred fallback kategóriami (stav po vyhľadávaní)",
        )
        if await _hopefix_try_catalog_categories_with_hash(
            page,
            product_code,
            config,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
        ):
            row = await _hopefix_find_row_locator(
                page,
                key,
                timeout,
                run_label=run_label,
                supplier=supplier,
                run_id=run_id,
            )
    if row is None:
        await _hopefix_log_table_debug(
            page,
            key,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            context="finálne zlyhanie — po vyhľadávaní a fallback kategóriách",
        )
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix tabuľka: riadok line-{key} nie je k dispozícii (ani po skúške kategórií). "
            f"Pozri vyššie riadok „Hopefix DEBUG“ v logu alebo nižšie v návode pre používateľa.",
            "warn",
        )
        return out
    cells = row.locator("td")
    try:
        n = await cells.count()
    except Exception:
        n = 0
    if n < 5:
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix tabuľka: príliš málo buniek ({n})",
            "warn",
        )
        return out
    texts: list[str] = []
    for i in range(n):
        try:
            texts.append((await cells.nth(i).inner_text()).strip())
        except Exception:
            texts.append("")
    eur_is, czk_is = _hopefix_price_indices(texts)
    price_cols = sorted(set(eur_is + czk_is))
    left_price = price_cols[0] if price_cols else None
    raw_stock = ""
    if left_price is not None and left_price > 0:
        raw_stock = texts[left_price - 1]
    elif len(texts) > 5:
        raw_stock = texts[5]
    raw_price_eur = texts[eur_is[0]] if eur_is else ""
    raw_price_czk = texts[czk_is[0]] if czk_is else ""
    if raw_stock and _hopefix_login_prompt_in(raw_stock):
        out["hopefix_table_hint"] = (
            "V riadku sú ešte odkazy „Přihlásit se“ — bez platnej B2B relácie Hopefix neukáže sklad ani cenu v tabuľke."
        )
        return out
    if eur_is and _hopefix_login_prompt_in(raw_price_eur):
        out["hopefix_table_hint"] = (
            "V riadku sú ešte odkazy „Přihlásit se“ — bez platnej B2B relácie Hopefix neukáže sklad ani cenu v tabuľke."
        )
        return out
    if not eur_is and czk_is and _hopefix_login_prompt_in(raw_price_czk):
        out["hopefix_table_hint"] = (
            "V riadku sú ešte odkazy „Přihlásit se“ — bez platnej B2B relácie Hopefix neukáže sklad ani cenu v tabuľke."
        )
        return out
    stock = _parse_stock(raw_stock) if raw_stock else None
    if stock is None and raw_stock and raw_stock.upper() != "N/A":
        out["raw_stock"] = raw_stock
        if hopefix_raw_suggests_oos(raw_stock):
            out["stock"] = 0
    elif stock is not None:
        out["stock"] = stock
        out["raw_stock"] = raw_stock
    price_val: Optional[float] = None
    raw_disp: str = ""
    if eur_is:
        raw_disp = raw_price_eur
        price_val = _parse_price_eur(raw_disp)
        out["currency_code"] = "eur"
        out["currency_symbol"] = "€"
    elif czk_is:
        raw_disp = raw_price_czk
        price_val = _hopefix_parse_decimal(raw_disp)
        out["currency_code"] = "czk"
        out["currency_symbol"] = "Kč"
    if price_val is not None:
        out["price_eur"] = round(price_val, 4)
        out["raw_price"] = raw_disp
    box_idx, box_hdr_100 = await _hopefix_box_pack_column_meta(row)
    if box_idx is None and left_price is not None:
        has_both = bool(eur_is and czk_is)
        box_idx = left_price + (3 if has_both else 2)
        box_hdr_100 = True
    if box_idx is not None and box_idx < len(texts):
        pq = _hopefix_parse_pack_cell(texts[box_idx])
        if pq is not None and pq > 0:
            pi = _hopefix_box_cell_to_pack_pieces(pq, header_is_100_pcs_box=box_hdr_100)
            if pi is not None and pi >= 1:
                out["pack_quantity"] = pi
                out["raw_pack_quantity"] = str(pi)
    out["price_unit"] = "per_100_ks"
    _log(
        run_label,
        supplier,
        run_id,
        f"Hopefix tabuľka line-{key}: price={out.get('price_eur')} stock={out.get('stock')}",
    )
    return out


async def _hopefix_ensure_add_to_cart_visible(
    page: Page,
    product_code: str,
    add_selector: str,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    timeout: int,
) -> None:
    """Tlačidlo košíka je často v rozbalenom detaile riadku; bez kliku na riadok zostane skryté."""
    sel = (add_selector or "").strip()
    if not sel:
        return
    key = hopefix_norm_code(product_code)
    if not key:
        return
    btn = page.locator(sel).first
    try:
        await btn.wait_for(state="visible", timeout=4_000)
        return
    except Exception:
        pass
    line_loc = page.locator(f'tbody#rows tr[id="line-{key}"]')
    if await line_loc.count() < 1:
        line_loc = page.locator(f'tr[id="line-{key}"]')
    row: Locator
    if await line_loc.count() >= 1:
        row = line_loc.first
    else:
        ridx = await _hopefix_registration_row_index_in_tbody(page, key)
        if ridx < 0:
            _log(
                run_label,
                supplier,
                run_id,
                f"Hopefix košík: riadok pre {key} (id ani Registrační číslo) sa nenašiel",
                "warn",
            )
            return
        row = page.locator("tbody#rows tr").nth(ridx)
    try:
        _log(
            run_label,
            supplier,
            run_id,
            "Hopefix košík: klik na bunku/riadok (rozbalenie detailu s CTA)",
        )
        await row.scroll_into_view_if_needed(timeout=5_000)
        tds = row.locator("td")
        try:
            if await tds.count() > 0:
                await tds.first.click(timeout=5_000)
                await asyncio.sleep(0.14)
        except Exception:
            pass
        await row.click(timeout=8_000)
        await asyncio.sleep(0.28)
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix rozbalenie riadku pred košíkom: {exc!s}",
            "warn",
        )
    try:
        n_all = await page.locator(sel).count()
        for i in range(n_all):
            if await page.locator(sel).nth(i).is_visible():
                return
        await btn.wait_for(state="visible", timeout=min(12_000, max(5_000, timeout)))
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"Hopefix košík: tlačidlo stále neviditeľné po rozbalení: {exc!s}",
            "warn",
        )


async def _hopefix_click_add_to_cart_button(
    page: Page,
    sel: str,
    product_code: str,
    *,
    click_timeout: int,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    purpose: str,
) -> None:
    """
    Hopefix má často viac zhôd pre rovnaké CTA (skrytá kópia + viditeľná v rozbalenom riadku).
    Nepoužívame .first — berieme prvý viditeľný výskyt, prípadne force v scope riadku line-<kód>.
    """
    s = (sel or "").strip()
    if not s:
        raise ValueError("Chýba selektor pre Hopefix košík.")
    key = hopefix_norm_code(product_code)

    scoped: list[tuple[Locator, str]] = []
    if key:
        scoped.extend(
            [
                (
                    page.locator(f'tbody#rows tr[id="line-{key}"]').locator(s),
                    f"tbody#rows tr#line-{key}",
                ),
                (
                    page.locator(f'tbody#rows tr[id="line-{key}"] + tr').locator(s),
                    f"tbody#rows tr#line-{key} + tr",
                ),
                (page.locator(f'tr[id="line-{key}"]').locator(s), f"tr#line-{key}"),
                (
                    page.locator(f'tr[id="line-{key}"] + tr').locator(s),
                    f"tr#line-{key} + tr",
                ),
            ]
        )
        ridx = await _hopefix_registration_row_index_in_tbody(page, key)
        if ridx >= 0:
            rrow = page.locator("tbody#rows tr").nth(ridx)
            scoped.extend(
                [
                    (rrow.locator(s), f"tbody#rows tr[reg {key} idx={ridx}]"),
                    (
                        rrow.locator("xpath=./following-sibling::tr[1]").locator(s),
                        f"tbody#rows tr[reg {key}] + detail",
                    ),
                ]
            )
    scoped.append((page.locator(s), "globálne"))

    async def _click_first_visible(locator: Locator, desc: str) -> bool:
        try:
            n = await locator.count()
        except Exception:
            return False
        for i in range(n):
            btn = locator.nth(i)
            try:
                if not await btn.is_visible():
                    continue
            except Exception:
                continue
            try:
                await btn.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass
            await btn.click(timeout=click_timeout)
            _log(
                run_label,
                supplier,
                run_id,
                f"{purpose}: Hopefix klik ({desc}) index={i}",
            )
            return True
        return False

    for loc, desc in scoped:
        if await _click_first_visible(loc, desc):
            return

    _log(
        run_label,
        supplier,
        run_id,
        f"{purpose}: Hopefix žiadne viditeľné CTA — skúšam force v scope riadku",
        "warn",
    )
    for loc, desc in scoped[:-1]:
        try:
            if await loc.count() < 1:
                continue
        except Exception:
            continue
        btn = loc.first
        try:
            await btn.scroll_into_view_if_needed(timeout=4_000)
        except Exception:
            pass
        try:
            await btn.click(force=True, timeout=click_timeout)
            _log(
                run_label,
                supplier,
                run_id,
                f"{purpose}: Hopefix force klik ({desc})",
            )
            return
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"{purpose}: Hopefix force {desc}: {exc!s}",
                "warn",
            )

    gl = page.locator(s)
    try:
        n = await gl.count()
    except Exception:
        n = 0
    for i in range(n):
        btn = gl.nth(i)
        try:
            await btn.scroll_into_view_if_needed(timeout=4_000)
        except Exception:
            pass
        try:
            await btn.click(force=True, timeout=click_timeout)
            _log(
                run_label,
                supplier,
                run_id,
                f"{purpose}: Hopefix force globálne index={i}",
            )
            return
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"{purpose}: Hopefix force glob. {i}: {exc!s}",
                "warn",
            )

    raise RuntimeError(
        f"Hopefix: nepodarilo sa kliknúť na košík ({s!r}) — nie je viditeľné tlačidlo a force zlyhal."
    )


async def _mekrs_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    """
    Fulltext + varianty cez JSON API (httpx), bez Playwright.
    Každý riadok má mekrs_variant_id pre HTTP add_to_cart.

    Ceny zodpovedajú účtu dodávateľa (login + cookie EUR v MekrsHttpClient), nie anonymnému CZK katalógu.
    """
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")
    async with MekrsHttpClient() as client:
        await client.ensure_session(supplier.username, supplier.password)
        blob = await client.search_product(code)
    raw_vars: list[dict[str, Any]] = list(blob.get("variants") or [])
    code_key = _mekrs_code_key(code)
    if code_key and raw_vars:

        def _sku_matches(v: dict[str, Any]) -> bool:
            return _mekrs_code_key(str(v.get("sku2") or "")) == code_key

        narrowed = [v for v in raw_vars if _sku_matches(v)]
        if narrowed:
            raw_vars = narrowed
    if not raw_vars:
        raise RuntimeError(
            "Mekrs API nenašlo žiadny variant — skontroluj kód alebo skús Playwright."
        )

    packaging_variants: list[dict[str, Any]] = []
    price_display_is_with_vat = False

    def _to_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    for v in raw_vars:
        pq = v.get("pack_quantity")
        pack_q = int(pq) if isinstance(pq, int) and pq >= 1 else 1
        sym = str(v.get("currency_symbol") or "€").strip() or "€"
        ccode = str(v.get("currency_code") or "").strip().lower() or None

        price_f, row_vat, _scaled = _mekrs_nominal_to_per_100ks_display(
            price_net=_to_float(v.get("price")),
            price_gross=_to_float(v.get("price_with_vat")),
            currency_code=ccode,
            pack_quantity=pack_q,
        )
        price_display_is_with_vat = price_display_is_with_vat or row_vat
        raw_price: Optional[str] = None
        if price_f is not None:
            raw_price = f"{price_f:.2f} {sym} / 100 ks"
        st = v.get("stock_level")
        stock_i: Optional[int] = None
        if st is not None:
            try:
                stock_i = int(st)
            except (TypeError, ValueError):
                stock_i = None
        lbl = _mekrs_sanitize_variant_label(
            (v.get("label") or v.get("packaging_label") or "").strip()
        )
        if not lbl:
            lbl = f"Balenie ({pack_q} ks)"
        mpt_http = _mekrs_infer_package_stock_phrase(stock_i, pack_q)
        row_http: dict[str, Any] = {
            "label": lbl,
            "pack_quantity": pack_q,
            "price_eur": price_f,
            "raw_price": raw_price,
            "stock": stock_i,
            "raw_stock": f"{stock_i} ks" if stock_i is not None else None,
            # Ostáva po _mekrs_strip_variant_stock_fields — orezávanie qty pri HTTP košíku.
            "mekrs_variant_stock": stock_i,
            "mekrs_variant_id": v.get("variant_id"),
            "mekrs_product_slug": (v.get("product_slug") or None),
            "currency_code": ccode,
            "currency_symbol": sym if sym else None,
        }
        if mpt_http:
            row_http["mekrs_package_stock_text"] = mpt_http
        packaging_variants.append(row_http)

    fps = blob.get("product_stock_level")
    total_st: Optional[int] = None
    if isinstance(fps, int) and fps >= 0:
        total_st = fps
    if total_st is None:
        total_st = _mekrs_sum_variant_stocks(packaging_variants)
    _mekrs_finalize_mekrs_variant_package_labels(packaging_variants, total_st)
    _mekrs_strip_variant_stock_fields(packaging_variants)
    v0 = packaging_variants[0]
    data: dict[str, Any] = {
        "price_eur": v0.get("price_eur"),
        "stock": total_st if total_st is not None else v0.get("stock"),
        "pack_quantity": v0.get("pack_quantity"),
        "raw_price": v0.get("raw_price"),
        "raw_stock": (
            f"Skladem celkem {total_st} ks"
            if total_st is not None
            else v0.get("raw_stock")
        ),
        "raw_pack_quantity": str(v0.get("pack_quantity") or ""),
        "packaging_variants": packaging_variants,
        "logged_in": True,
        "mekrs_via_http": True,
        # UI vždy preferuje net; True len ak pri riadku chýbal price a použil sa priceWithVAT.
        "price_includes_vat": price_display_is_with_vat,
        "currency_code": v0.get("currency_code"),
        "currency_symbol": v0.get("currency_symbol"),
    }
    _mekrs_tag_price_unit_per_100(data)
    _mekrs_strip_prices_when_zero_stock(data)
    dbg = os.environ.get("MEKRS_PRICE_DEBUG", "").strip().lower()
    if dbg in ("1", "true", "yes", "on"):
        data["mekrs_price_debug"] = {
            "variants_raw": raw_vars,
            "hint": (
                "DevTools → Sieť (Network): vyfiltruj „api/product“ alebo konkrétne UUID; "
                "hľadaj GET končiaci na „/variants“ (nie slovo v tele). "
                "V odpovedi sú price.price a priceWithVAT."
            ),
        }
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Mekrs HTTP: {len(packaging_variants)} variant(ov)",
    )
    return data


async def _hopefix_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    """Katalógová stránka + parsovanie riadku line-<kód> po prihlásení (httpx)."""
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")
    enc = quote(code, safe=".-_~")
    try_urls: list[str] = []
    seen_u: set[str] = set()

    def _add(u: str) -> None:
        x = (u or "").strip()
        if not x:
            return
        if not x.startswith("http"):
            x = urljoin("https://www.hopefix.cz/", x.lstrip("/"))
        if x in seen_u:
            return
        seen_u.add(x)
        try_urls.append(x)

    _add(build_hopefix_catalog_url(config.hopefix_catalog_url_template or "", code))
    su = (config.search_via_url_template or "").strip()
    if su and "{code}" in su:
        _add(su.replace("{code}", enc))
    key = hopefix_norm_code(code)

    row: Optional[dict[str, Any]] = None
    last_html_len = 0
    async with HopefixHttpClient() as client:
        await client.ensure_login(supplier.username, supplier.password)

        async def _fetch_row(u: str) -> tuple[str, int, Optional[dict[str, Any]]]:
            html = await client.get_text(u)
            ln = len(html or "")
            r = find_hopefix_row(parse_hopefix_rows(html), code)
            return u, ln, r

        if len(try_urls) <= 1:
            for attempt in try_urls:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Hopefix HTTP: GET katalóg {attempt!r}",
                )
                _, last_html_len, row = await _fetch_row(attempt)
                if row:
                    break
        elif try_urls:
            _log(
                run_label,
                supplier,
                run_id,
                f"Hopefix HTTP: paralelné GET ({len(try_urls)} URL)",
            )
            packed = await asyncio.gather(*[_fetch_row(u) for u in try_urls])
            for _url, ln, r in packed:
                last_html_len = max(last_html_len, ln)
                if r:
                    row = r
                    break
        if not row and try_urls:
            anchored = _hopefix_url_with_row_anchor(try_urls[-1], key)
            if anchored not in seen_u:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Hopefix HTTP: GET s _ref+hash (fallback) {anchored!r}",
                )
                html = await client.get_text(anchored)
                last_html_len = len(html or "")
                rows = parse_hopefix_rows(html)
                row = find_hopefix_row(rows, code)
        if not row:
            # Úzka podkategória v šablóne často obsahuje len časť riadkov; veľká /sortiment/<seg>
            # (rovnaký fallback ako Playwright) má kompletnú tabuľku. Fragment #kód sa na server neposiela.
            for seg in _hopefix_fallback_category_segments(code):
                rel_ref = f"/sortiment/{seg}?_ref={enc}"
                if rel_ref not in seen_u:
                    seen_u.add(rel_ref)
                    try:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"Hopefix HTTP: GET fallback kategória {rel_ref!r}",
                        )
                        html_fb = await client.get_text(rel_ref)
                        last_html_len = max(last_html_len, len(html_fb or ""))
                        row = find_hopefix_row(parse_hopefix_rows(html_fb), code)
                        if row:
                            break
                    except Exception as exc:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"Hopefix HTTP: fallback {rel_ref!r}: {exc!s}",
                            "warn",
                        )
                rel_plain = f"/sortiment/{seg}"
                if not row and rel_plain not in seen_u:
                    seen_u.add(rel_plain)
                    try:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"Hopefix HTTP: GET fallback kategória {rel_plain!r}",
                        )
                        html_fb = await client.get_text(rel_plain)
                        last_html_len = max(last_html_len, len(html_fb or ""))
                        row = find_hopefix_row(parse_hopefix_rows(html_fb), code)
                        if row:
                            break
                    except Exception as exc:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"Hopefix HTTP: fallback {rel_plain!r}: {exc!s}",
                            "warn",
                        )
                if row:
                    break
    if not row:
        raise RuntimeError(
            f"Hopefix: v tabuľke sa nenašiel riadok pre kód {code!r} (skúšané {len(try_urls)} URL, "
            f"posledná odpoveď ≈{last_html_len} B). Skontroluj hopefix_catalog_url_template "
            f"a search_via_url_template — musia viesť na stránku, kde je tento kód v tabuľke."
        )
    pkg_type = (config.hopefix_default_package_type or "box").strip() or "box"
    label = (row.get("label") or "").strip() or code
    pq = row.get("pack_quantity")
    pack_q = int(pq) if isinstance(pq, int) and pq >= 1 else 1
    hopefix_id = row.get("hopefix_product_id")
    pv: dict[str, Any] = {
        "label": label,
        "pack_quantity": pack_q,
        "price_eur": row.get("price_eur"),
        "raw_price": row.get("raw_price"),
        "stock": row.get("stock"),
        "raw_stock": row.get("raw_stock"),
        "hopefix_product_id": hopefix_id,
        "hopefix_package_type": pkg_type,
    }
    packaging_variants = [pv]
    data: dict[str, Any] = {
        "price_eur": pv.get("price_eur"),
        "stock": pv.get("stock"),
        "pack_quantity": pack_q,
        "raw_price": pv.get("raw_price"),
        "raw_stock": pv.get("raw_stock"),
        "raw_pack_quantity": str(pack_q),
        "packaging_variants": packaging_variants,
        "logged_in": True,
        "hopefix_via_http": True,
    }
    _hopefix_normalize_oos_display(data)
    if not hopefix_id:
        data["hint"] = (
            "Hopefix: v HTML riadku sa nenašiel product_id (často až v rozbalenej časti). "
            "Otvor riadok v prehliadači alebo zachyť HAR pri „Vložit do košíku“ a doplň mapovanie."
        )
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Hopefix HTTP: row={code!r} product_id={hopefix_id!r}",
    )
    return data


async def _haspl_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    """Sylius Shop API: varianty podľa productCodeOrExternal, ceny po prihlásení (JWT)."""
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")
    base = haspl_base_url(supplier.shop_url or "")
    async with HasplHttpClient(base_url=base) as client:
        await client.ensure_session(supplier.username, supplier.password)
        members = await client.fetch_variants_by_supplier_code(code)
    if not members:
        raise RuntimeError(
            "Haspl API nenašlo variant pre tento kód — over mapovanie (supplier_code = kód v Haspl, "
            f"skúšaná normalizácia {haspl_norm_code(code)!r})."
        )
    packaging_variants: list[dict[str, Any]] = []
    for m in members:
        vcode = str(m.get("code") or "").strip()
        name = str(m.get("name") or "").strip() or vcode
        pack_q = haspl_variant_pack_quantity(m)
        raw_pack = haspl_pack_display_text(m, pack_quantity=pack_q)
        net = haspl_net_price_eur(m)
        raw_price = f"{net:.2f} € bez DPH" if net is not None else None
        pu = haspl_price_unit_key(m)
        instock = bool(m.get("inStock"))
        row: dict[str, Any] = {
            "label": name,
            "pack_quantity": pack_q,
            "raw_pack_quantity": raw_pack,
            "price_eur": net,
            "raw_price": raw_price,
            "stock": None,
            "raw_stock": "Skladem" if instock else "Není skladem",
            "haspl_variant_code": vcode or None,
            "currency_code": "eur",
            "currency_symbol": "€",
        }
        if pu:
            row["price_unit"] = pu
        packaging_variants.append(row)
    v0 = packaging_variants[0]
    data: dict[str, Any] = {
        "price_eur": v0.get("price_eur"),
        "stock": None,
        "pack_quantity": v0.get("pack_quantity"),
        "raw_price": v0.get("raw_price"),
        "raw_stock": v0.get("raw_stock"),
        "raw_pack_quantity": str(v0.get("raw_pack_quantity") or v0.get("pack_quantity") or ""),
        "packaging_variants": packaging_variants,
        "logged_in": True,
        "haspl_via_http": True,
        "price_includes_vat": False,
        "currency_code": "eur",
        "currency_symbol": "€",
    }
    pu0 = v0.get("price_unit")
    if isinstance(pu0, str) and pu0.strip():
        data["price_unit"] = pu0.strip()
    if len(packaging_variants) > 1:
        data["hint"] = (
            "Haspl: viac variantov/balení — vyber riadok v UI pred košíkom (API posiela množstvo v baleniach)."
        )
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Haspl HTTP: {len(packaging_variants)} variant(ov) base={base!r}",
    )
    return data


async def _bmkco_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    """BMCo B2C endpointy: login + /cs/Data/GetZboziDetail."""
    code = bmkco_norm_code(product_code)
    if not code:
        raise ValueError("BMCo: prázdny kód produktu.")
    base = bmkco_base_url(supplier.shop_url or "")
    async with BmkcoHttpClient(base) as client:
        await client.ensure_login(supplier.username, supplier.password)
        detail = await client.fetch_product_detail(code)
    data = BmkcoHttpClient.parse_supplier_data(detail)
    pvars = list(data.get("packaging_variants") or [])
    if pvars:
        pvars[0]["bmkco_karta"] = code
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data BMCo HTTP: karta={code!r} base={base!r}",
    )
    return data


async def _halfmann_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    """Halfmann HTTP endpointy: login + findpreis/verfbest."""
    artid = halfmann_norm_artid(product_code)
    if not artid:
        raise ValueError("Halfmann: prázdne artid produktu.")
    base = halfmann_base_url(supplier.shop_url or "")
    async with HalfmannHttpClient(base) as client:
        await client.ensure_login(supplier.username, supplier.password)
        price = await client.find_price_per_100(artid)
        stock = await client.find_stock(artid)
    data = HalfmannHttpClient.parse_supplier_data(
        artid=artid,
        price_row=price if isinstance(price, dict) else {},
        stock_row=stock if isinstance(stock, dict) else {},
    )
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Halfmann HTTP: artid={artid!r} base={base!r}",
    )
    return data


async def _inoxmare_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    """Magento: quicksearch resolve → PDP, cena/sklad po prihlásení (httpx)."""
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")
    ix_cookie = (config.inoxmare_session_cookie_header or "").strip()
    async with InoxmareHttpClient(
        supplier.shop_url or "",
        config.inoxmare_store_path,
        manual_cookie_header=ix_cookie or None,
    ) as client:
        await client.ensure_login(supplier.username, supplier.password)
        path = await client.resolve_product_path(code)
        html = await client.fetch_pdp_html_hydrated(path)
    meta = parse_inoxmare_pdp(html, product_code=code)
    pid = meta.get("inoxmare_product_id")
    if not pid:
        raise RuntimeError(
            "Inoxmare: na PDP sa nenašlo ID produktu (product) — skontroluj kód alebo prihlásenie."
        )
    pe = meta.get("price_eur")
    st = meta.get("stock")
    pdp_label = (meta.get("pdp_label") or "").strip()
    pq = meta.get("pack_quantity")
    mq = meta.get("master_pack_quantity")
    pal = meta.get("pallet_pack_quantity")
    pack_parts: list[str] = []
    if isinstance(pq, int) and pq > 0:
        pack_parts.append(f"Box {pq} ks")
    if isinstance(mq, int) and mq > 0:
        pack_parts.append(f"Master {mq} ks")
    if isinstance(pal, int) and pal > 0:
        pack_parts.append(f"Paleta {pal} ks")
    raw_pack = "; ".join(pack_parts)
    pv: dict[str, Any] = {
        "label": pdp_label or "Inoxmare",
        "inoxmare_product_id": str(pid),
        "inoxmare_referer_path": path,
        "price_eur": pe,
        "raw_price": meta.get("raw_price"),
        "stock": st,
        "raw_stock": meta.get("raw_stock"),
        "pack_quantity": pq if isinstance(pq, int) and pq > 0 else None,
        "raw_pack_quantity": raw_pack or (str(pq) if isinstance(pq, int) and pq > 0 else ""),
    }
    data: dict[str, Any] = {
        "price_eur": pe,
        "stock": st,
        "raw_price": meta.get("raw_price"),
        "raw_stock": meta.get("raw_stock"),
        "pack_quantity": pv.get("pack_quantity"),
        "raw_pack_quantity": pv.get("raw_pack_quantity") or "",
        "packaging_variants": [pv],
        "logged_in": True,
        "inoxmare_via_http": True,
        "price_includes_vat": False,
        "currency_code": "eur",
        "currency_symbol": "€",
    }
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Inoxmare HTTP: code={code!r} product_id={pid!r} path={path!r}",
    )
    return data


async def _argip_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")

    async with ArgipHttpClient(shop_url=supplier.shop_url or "") as client:
        await client.ensure_login(supplier.username, supplier.password)
        items = await client.search_products(code)

    if not items:
        raise ScraperProductNotFoundError(
            f"Argip: kód {code!r} sa nenašiel (GraphQL products search je prázdny)."
        )

    code_norm = re.sub(r"\s+", "", code).upper()
    def _index_base(it: dict[str, Any]) -> str:
        raw = str(it.get("index") or "").strip()
        if not raw:
            return ""
        compact = re.sub(r"\s+", " ", raw).strip()
        compact = re.sub(r"\s+[op]\d+$", "", compact, flags=re.IGNORECASE)
        return compact.upper()

    def _score(item: dict[str, Any]) -> int:
        sku = str(item.get("sku") or "").strip()
        sku_norm = re.sub(r"\s+", "", sku).upper()
        if sku_norm == code_norm:
            return 0
        if code_norm and code_norm in sku_norm:
            return 1
        return 2

    def _price_of(it: dict[str, Any]) -> Optional[float]:
        def _to_float(value: Any) -> Optional[float]:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return round(float(value), 4)
            txt = str(value).strip().replace("\xa0", " ").replace("€", "").strip()
            txt = txt.replace(" ", "")
            if "," in txt and "." not in txt:
                txt = txt.replace(",", ".")
            try:
                return round(float(txt), 4)
            except (TypeError, ValueError):
                return None

        # Priame polia (niektoré Argip endpointy vracajú cenu takto).
        for key in ("price", "price_without_tax", "final_price", "regular_price"):
            direct = _to_float(it.get(key))
            if direct is not None:
                return direct

        pr = (it.get("price_range") or {}) if isinstance(it, dict) else {}
        mp = (pr.get("minimum_price") or {}) if isinstance(pr, dict) else {}
        for key in (
            "final_price",
            "default_final_price",
            "final_price_excl_tax",
            "default_final_price_excl_tax",
            "regular_price",
            "default_price",
        ):
            fp = (mp.get(key) or {}) if isinstance(mp, dict) else {}
            nested = _to_float(fp.get("value") if isinstance(fp, dict) else None)
            if nested is not None:
                return nested

        # Fallback: ceny môžu byť v zozname attributes (Magento custom schema).
        attrs = it.get("attributes")
        if isinstance(attrs, list):
            preferred_codes = (
                "price",
                "price_without_tax",
                "catalog_price",
                "basic_price",
                "net_price",
            )
            for code in preferred_codes:
                for row in attrs:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("attribute_code") or "").strip().lower() != code:
                        continue
                    attr_price = _to_float(row.get("attribute_value"))
                    if attr_price is not None:
                        return attr_price

        return None

    def _stock_of(it: dict[str, Any]) -> int:
        nums: list[int] = []
        sq = it.get("salable_qty")
        if sq is not None and str(sq).strip() != "":
            try:
                nums.append(max(0, int(float(sq))))
            except (TypeError, ValueError):
                pass
        si = (it.get("stock_item") or {}) if isinstance(it, dict) else {}
        if isinstance(si, dict):
            for key in ("qty", "quantity"):
                raw = si.get(key)
                if raw is not None and str(raw).strip() != "":
                    try:
                        nums.append(max(0, int(float(raw))))
                    except (TypeError, ValueError):
                        pass
        if nums:
            return max(nums)
        st = str(it.get("stock_status") or "").strip().upper()
        return 1 if st == "IN_STOCK" else 0

    def _tiers_of(it: dict[str, Any]) -> list[tuple[int, Optional[float]]]:
        out: list[tuple[int, Optional[float]]] = []
        for key in ("price_tiers", "tier_prices"):
            raw = it.get(key)
            if not isinstance(raw, list):
                continue
            for row in raw:
                if not isinstance(row, dict):
                    continue
                try:
                    qty = int(float(row.get("quantity") or row.get("qty")))
                except (TypeError, ValueError):
                    continue
                if qty < 1:
                    continue
                fp = row.get("final_price") or row.get("price")
                if isinstance(fp, dict):
                    try:
                        val = round(float(fp.get("value")), 4)
                    except (TypeError, ValueError):
                        val = None
                else:
                    try:
                        val = round(float(fp), 4) if fp is not None else None
                    except (TypeError, ValueError):
                        val = None
                out.append((qty, val))
        out.sort(key=lambda x: x[0])
        return out

    def _merge_tier_rows(nodes: list[dict[str, Any]]) -> list[tuple[int, Optional[float]]]:
        by_qty: dict[int, Optional[float]] = {}
        for node in nodes:
            for qty, price in _tiers_of(node):
                if qty < 1:
                    continue
                if qty not in by_qty or by_qty[qty] is None:
                    by_qty[qty] = price
        return sorted(by_qty.items(), key=lambda x: x[0])

    def _expand_argip_configurable(parent: dict[str, Any]) -> list[dict[str, Any]]:
        out = [parent]
        variants = parent.get("variants")
        if not isinstance(variants, list):
            return out
        for row in variants:
            if not isinstance(row, dict):
                continue
            prod = row.get("product")
            if isinstance(prod, dict) and str(prod.get("sku") or "").strip():
                out.append(prod)
        return out

    def _catalog_price_of(it: dict[str, Any]) -> Optional[float]:
        pr = (it.get("price_range") or {}) if isinstance(it, dict) else {}
        mp = (pr.get("minimum_price") or {}) if isinstance(pr, dict) else {}
        for key in ("regular_price", "default_price"):
            fp = (mp.get(key) or {}) if isinstance(mp, dict) else {}
            try:
                return round(float(fp.get("value")), 4)
            except (TypeError, ValueError):
                continue
        return None

    def _min_sale_qty_of(it: dict[str, Any]) -> int:
        si = (it.get("stock_item") or {}) if isinstance(it, dict) else {}
        try:
            q = int(float(si.get("min_sale_qty")))
            if q >= 1:
                return q
        except (TypeError, ValueError):
            pass
        return 1

    def _pack_qty_of(it: dict[str, Any]) -> int:
        try:
            q = int(float(it.get("package")))
            if q >= 1:
                return q
        except (TypeError, ValueError):
            pass
        return 1

    ordered = sorted(items, key=_score)
    best_top = ordered[0]
    primary = _expand_argip_configurable(best_top)
    stock_sku = str(best_top.get("sku") or "").strip() or code
    stock_base = _index_base(best_top)
    same_family = [
        it
        for it in ordered
        if _index_base(it) and stock_base and _index_base(it) == stock_base
    ] or ordered
    price_item = next((it for it in same_family if _price_of(it) is not None), None)
    if price_item is None:
        price_item = next((it for it in ordered if _price_of(it) is not None), best_top)
    if _price_of(price_item) is None:
        price_item = next((it for it in primary if _price_of(it) is not None), best_top)

    best_price = _price_of(price_item)
    best_catalog_price = _catalog_price_of(price_item)
    best_stock = max(_stock_of(x) for x in primary)
    top_type = str(best_top.get("type_id") or "").strip().lower()
    list_sku = str(price_item.get("sku") or "").strip() or stock_sku
    cart_sku = stock_sku if "configurable" in top_type else list_sku
    min_qty = _min_sale_qty_of(price_item)
    pack_qty = _pack_qty_of(price_item)

    pvars: list[dict[str, Any]] = []
    tiers = _merge_tier_rows(primary)
    if tiers:
        cat = best_catalog_price
        base = best_price
        same_catalog_base = (
            cat is not None
            and base is not None
            and round(float(cat), 4) == round(float(base), 4)
        )
        if same_catalog_base:
            if base is not None:
                pvars.append(
                    {
                        "label": "Základná cena",
                        "pack": f"{pack_qty} ks",
                        "shop_pack_quantity": pack_qty,
                        "pack_quantity": min_qty,
                        "raw_pack_quantity": f"min. {min_qty} ks",
                        "price_eur": base,
                        "stock": best_stock,
                        "argip_sku": cart_sku,
                        "price_unit": "per_100_ks",
                    }
                )
        else:
            if cat is not None:
                pvars.append(
                    {
                        "label": "Katalógová cena",
                        "pack": f"{pack_qty} ks",
                        "shop_pack_quantity": pack_qty,
                        "pack_quantity": min_qty,
                        "raw_pack_quantity": f"min. {min_qty} ks",
                        "price_eur": cat,
                        "stock": best_stock,
                        "argip_sku": cart_sku,
                        "price_unit": "per_100_ks",
                    }
                )
            if base is not None:
                pvars.append(
                    {
                        "label": "Základná cena",
                        "pack": f"{pack_qty} ks",
                        "shop_pack_quantity": pack_qty,
                        "pack_quantity": min_qty,
                        "raw_pack_quantity": f"min. {min_qty} ks",
                        "price_eur": base,
                        "stock": best_stock,
                        "argip_sku": cart_sku,
                        "price_unit": "per_100_ks",
                    }
                )
        for qty, tier_price in tiers:
            if qty <= min_qty:
                continue
            pvars.append(
                {
                    "label": "Objemová cena",
                    "pack": f"{pack_qty} ks",
                    "shop_pack_quantity": pack_qty,
                    "pack_quantity": qty,
                    "raw_pack_quantity": f"min. {qty} ks",
                    "price_eur": tier_price,
                    "stock": best_stock,
                    "argip_sku": cart_sku,
                    "price_unit": "per_100_ks",
                }
            )
    else:
        pool: list[dict[str, Any]] = []
        seen: set[str] = set()
        for top in ordered[:8]:
            for node in _expand_argip_configurable(top):
                sku = str(node.get("sku") or "").strip()
                if not sku or sku in seen:
                    continue
                seen.add(sku)
                pool.append(node)
        for it in pool[:12]:
            sku = str(it.get("sku") or "").strip()
            if not sku:
                continue
            pe = _price_of(it)
            stq = _stock_of(it)
            pq_shop = _pack_qty_of(it)
            pvars.append(
                {
                    "label": str(it.get("name") or sku),
                    "pack": f"{pq_shop} ks",
                    "shop_pack_quantity": pq_shop,
                    "pack_quantity": _min_sale_qty_of(it),
                    "raw_pack_quantity": f"min. {_min_sale_qty_of(it)} ks",
                    "price_eur": pe,
                    "stock": stq,
                    "argip_sku": sku,
                }
            )

    data: dict[str, Any] = {
        "price_eur": best_price,
        "stock": best_stock,
        "raw_price": f"{best_price:.2f} €"
        if isinstance(best_price, (int, float))
        else None,
        "raw_stock": f"{best_stock} ks",
        "pack_quantity": min_qty,
        "raw_pack_quantity": f"min. {min_qty} ks",
        "price_unit": "per_100_ks",
        "shop_pack_quantity": pack_qty,
        "packaging_variants": pvars,
        "logged_in": True,
        "argip_via_http": True,
        "argip_sku": cart_sku,
        "cart_url": argip_cart_url(supplier.shop_url or ""),
    }
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Argip HTTP: code={code!r} stock_sku={stock_sku!r} "
        f"price_sku={str(price_item.get('sku') or '')!r} cart_sku={cart_sku!r} "
        f"stock={best_stock} price={best_price} tiers={len(tiers)}",
    )
    return data


async def _schachermayer_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    config: ScraperConfig,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")
    cat_override = (config.schachermayer_catalog_id or "").strip() or None
    async with SchachermayerHttpClient(supplier.shop_url or "") as client:
        await client.ensure_login(supplier.username, supplier.password)
        try:
            data = await client.fetch_product_pricing(
                code, catalog_override=cat_override
            )
        except RuntimeError as exc:
            msg = str(exc)
            if "nenašiel" in msg:
                raise ScraperProductNotFoundError(msg) from exc
            raise
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Schachermayer HTTP: code={code!r}",
    )
    return data


async def _valenta_get_supplier_data_via_http(
    supplier: Supplier,
    product_code: str,
    *,
    run_label: str,
    run_id: str,
) -> dict[str, Any]:
    code = (product_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód produktu.")

    async with ValentaHttpClient(supplier.shop_url or "") as client:
        await client.ensure_login(supplier.username, supplier.password)
        raw = await client.fetch_product_data(code)

    form_action_abs = str(raw.get("form_action_abs") or "").strip()
    product_id = str(raw.get("product_id") or "").strip()
    if not form_action_abs or not product_id:
        raise RuntimeError("Valenta: chýba form_action/product_id po vyhľadaní.")

    price = raw.get("price_eur")
    price_eur = float(price) if isinstance(price, (int, float)) else None
    stock_raw = raw.get("stock")
    stock = int(stock_raw) if isinstance(stock_raw, int) else None
    raw_price = str(raw.get("raw_price") or "").strip() or None
    raw_stock = str(raw.get("raw_stock") or "").strip() or None
    product_title = str(raw.get("product_title") or "").strip() or None
    row_label = product_title or f"Valenta {code}"

    pv: dict[str, Any] = {
        "label": row_label,
        "pack_quantity": 1,
        "raw_pack_quantity": "1 ks",
        "price_eur": price_eur,
        "raw_price": raw_price,
        "stock": stock,
        "raw_stock": raw_stock,
        "valenta_product_id": product_id,
        "valenta_form_action_abs": form_action_abs,
    }
    data: dict[str, Any] = {
        "price_eur": price_eur,
        "stock": stock,
        "raw_price": raw_price,
        "raw_stock": raw_stock,
        "product_title": product_title,
        "pack_quantity": 1,
        "raw_pack_quantity": "1 ks",
        "packaging_variants": [pv],
        "logged_in": True,
        "valenta_via_http": True,
        "valenta_product_id": product_id,
        "valenta_form_action_abs": form_action_abs,
        "cart_url": valenta_cart_url(supplier.shop_url or ""),
    }
    _log(
        run_label,
        supplier,
        run_id,
        f"get_supplier_data Valenta HTTP: code={code!r} product_id={product_id!r}",
    )
    return data


def _mekrs_sum_variant_stocks(
    variants: list[dict[str, Any]],
) -> Optional[int]:
    """
    Agregovaný sklad pre zobrazenie PDP. Mekrs API dáva na každom variante rovnaké
    ``stockLevel`` (spoločný počet kusov skladom), nie „balenie A + balenie B“.
    Ak sú všetky riadky rovnaké, vrátime jednu hodnotu; pri rozdielnych číslach súčet.
    """
    stocks: list[int] = []
    for v in variants:
        s = v.get("stock")
        if isinstance(s, int) and s >= 0:
            stocks.append(s)
    if not stocks:
        return None
    if len(set(stocks)) == 1:
        return stocks[0]
    return sum(stocks)


def _mekrs_apply_total_stock_for_display(
    data: dict[str, Any],
    *,
    modal_header_raw: Optional[str],
    modal_header_stock: Optional[int],
    packaging_variants: list[dict[str, Any]],
) -> None:
    """
    Hlavný `stock` v API = **skladom celkom** (nie prvý variant ani vybraný riadok).

    1) Ak je v hlavičke modala text „celkem“, použijeme jeho číslo.
    2) Inak agregácia ks z variantov (Mekrs: rovnaké čísla = jeden spoločný sklad).
    3) Inak hlavička bez celkem / prvý dostupný údaj.
    """
    hr = (modal_header_raw or "")
    hr_low = hr.lower()
    if modal_header_stock is not None and "celkem" in hr_low:
        data["stock"] = modal_header_stock
        data["raw_stock"] = hr.strip() or f"Skladem celkem {modal_header_stock} ks"
    else:
        var_sum = _mekrs_sum_variant_stocks(packaging_variants)
        if var_sum is not None:
            data["stock"] = var_sum
            data["raw_stock"] = f"Skladem celkem {var_sum} ks"
        elif modal_header_stock is not None:
            data["stock"] = modal_header_stock
            data["raw_stock"] = hr.strip() or f"{modal_header_stock} ks"
        elif packaging_variants:
            v0 = packaging_variants[0]
            if isinstance(v0.get("stock"), int):
                data["stock"] = v0["stock"]
                rs = v0.get("raw_stock")
                data["raw_stock"] = rs if isinstance(rs, str) and rs.strip() else None
    st_disp = data.get("stock")
    _mekrs_finalize_mekrs_variant_package_labels(
        packaging_variants,
        st_disp if isinstance(st_disp, int) else None,
    )
    _mekrs_strip_variant_stock_fields(packaging_variants)


def _mekrs_strip_variant_stock_fields(variants: list[dict[str, Any]]) -> None:
    """Mekrs: v zozname balení neposielame sklad po variantoch — len súhrn v `stock` / `raw_stock`; `mekrs_package_stock_text` ostáva."""
    for v in variants:
        if not isinstance(v, dict):
            continue
        v.pop("stock", None)
        v.pop("raw_stock", None)


def _mekrs_strip_prices_when_zero_stock(data: dict[str, Any]) -> None:
    """
    Na Mekrs webe pri „nie je skladem“ často nie je zobrazená cena; JSON API ju môže stále vrátiť.
    """
    st = data.get("stock")
    if not isinstance(st, int) or st > 0:
        return
    data["price_eur"] = None
    data["raw_price"] = None
    data["price_unit"] = None
    data["price_includes_vat"] = False
    for pv in data.get("packaging_variants") or []:
        if not isinstance(pv, dict):
            continue
        pv["price_eur"] = None
        pv["raw_price"] = None


def _mekrs_tag_price_unit_per_100(data: dict[str, Any]) -> None:
    """Ceny variantov sú už EUR / 100 ks (parsované z riadku „… € / 100 ks“)."""
    data["price_unit"] = "per_100_ks"


def _mekrs_price_per_100_from_row_text(text: str) -> tuple[Optional[float], Optional[str]]:
    """Hodnota pred „/ 100 ks“ v texte riadku variantu (Mekrs)."""
    if not text or not text.strip():
        return None, None
    t = text.replace("\xa0", " ").replace("\u202f", " ")
    t = re.sub(r"\s+", " ", t.strip())
    m = re.search(
        r"([\d]+(?:\s\d{3})*(?:[,\.]\d+)?)\s*€\s*/\s*100\s*ks",
        t,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"([,\.\d]+)\s*€\s*/\s*100\s*ks",
            t,
            re.IGNORECASE,
        )
    if not m:
        return None, None
    raw_num = m.group(1).replace(" ", "")
    pe = _parse_price_eur(raw_num)
    if pe is None:
        return None, None
    return round(pe, 2), m.group(0).strip()


async def _mekrs_extract_price_per_100_from_variant_row(
    row: Locator,
) -> tuple[Optional[float], Optional[str]]:
    """DOM: riadok s „/ 100 ks“ (čierna alebo červená cena), inak regex z inner_text."""
    grids = row.locator("div.grid.grid-flow-col").filter(
        has_text=re.compile(r"100\s*ks", re.IGNORECASE)
    )
    try:
        gn = await grids.count()
    except Exception:
        gn = 0
    for gi in range(gn):
        grid = grids.nth(gi)
        for sel in (
            "span.text-black.font-bold.text-sm",
            "span.text-primaryRed.font-bold.text-lg",
        ):
            try:
                cand = grid.locator(sel).first
                if await cand.count() < 1:
                    continue
                raw = (await cand.inner_text()).strip()
            except Exception:
                continue
            pe = _parse_price_eur(raw)
            if pe is not None:
                return round(pe, 2), f"{raw} / 100 ks"
    try:
        blob = (await row.inner_text()).strip()
    except Exception:
        blob = ""
    return _mekrs_price_per_100_from_row_text(blob)


class ScraperConfig(BaseModel):
    """Selektory pre konkrétny e-shop — doplň podľa HTML (login, vyhľadávanie, cena, sklad, košík)."""

    login_url: Optional[str] = None
    after_login_url: Optional[str] = None
    username_selector: str
    password_selector: str
    login_button_selector: str
    # Ak je vyplnené, polia a kontrola „či sme prihlásení“ sa viažu na tento formulár (Fabory: #faboryLoginForm).
    login_form_selector: Optional[str] = None
    # Ak e-shop najprv zobrazí odkaz/tlačidlo „Prihlásiť“ a až potom polia (modal), klikni na toto pred vyplnením.
    open_login_form_selector: Optional[str] = None
    open_login_form_wait_ms: int = 800
    optional_cookie_dismiss_selector: Optional[str] = None
    # Fabory / Spring: čakanie na POST j_spring_security_check. Pri SPA (Mekrs) nechaj false — inak timeout ~25 s a druhý klik.
    login_expect_spring_security_post: bool = True
    post_login_wait_ms: int = 2000
    # Bez vyhľadávania: nechaj null alebo vynechaj — scrape len overí prihlásenie, cenu/sklad nečíta.
    search_input_selector: Optional[str] = None
    # Namiesto poľa + submit: po prihlásení otvor URL, kde {code} = kód produktu (URL-encoded).
    # Príklad: "https://eshop.mekrs.cz/produkty?nazev={code}"
    search_via_url_template: Optional[str] = None
    # Hopefix: URL stránky s tabuľkou #rows (HTTP scrape bez Playwright). Môže obsahovať {code} alebo byť fixná kategória.
    hopefix_catalog_url_template: Optional[str] = None
    # Hopefix POST /api/add_to_cart — package_type (napr. box).
    hopefix_default_package_type: str = "box"
    # Inoxmare (Magento): cesta store view, napr. "/en" — inak z shop_url alebo "/en".
    inoxmare_store_path: Optional[str] = None
    # Schachermayer: ak automatika z vkorg/vtweg/sparte zlyhá, vlož presný catalog z URL (?catalog=…).
    schachermayer_catalog_id: Optional[str] = None
    # Celá hlavička Cookie z prehliadača po ručnom prihlásení (DevTools → Sieť → Cookie). Obíde CAPTCHA.
    inoxmare_session_cookie_header: Optional[str] = None
    search_submit_selector: Optional[str] = None
    search_submit_key: Optional[str] = "Enter"
    post_search_wait_ms: int = 2500
    # Headless UI / combobox: po zadaní kódu vyber prvý návrh (šípka dole + Enter) pred klikom na „Hledat“.
    search_pick_first_suggestion: bool = False
    search_suggestion_wait_ms: int = 600
    # Po výsledkoch vyhľadávania otvor detail produktu (cena/sklad sú často až tam).
    first_product_link_selector: Optional[str] = None
    # Čakanie na viditeľnosť ceny alebo skladu pred čítaním (ms).
    price_stock_timeout_ms: int = 18000
    # Čítanie ceny a skladu (voliteľné — bez nich get_supplier_data vráti null).
    price_selector: Optional[str] = None
    # Pred čítaním ceny: <select> + hodnota option (napr. Mekrs prepne CZK → EUR).
    price_currency_select_selector: Optional[str] = None
    price_currency_select_value: Optional[str] = None
    # Modal „vyberte variantu“ (viac balení): po kliku na add_to_cart sa načítajú riadky variantov.
    packaging_modal_visible_selector: Optional[str] = None
    # Ak je prázdne, klikne sa prvé add_to_cart_selector **mimo** [role=dialog] (nie tlačidlo v modale).
    packaging_modal_open_selector: Optional[str] = None
    packaging_modal_row_selector: Optional[str] = None
    packaging_modal_radio_selector: Optional[str] = None
    packaging_modal_price_selector: Optional[str] = None
    packaging_modal_header_stock_selector: Optional[str] = None
    # Počas čítania variantov z modalu zablokuj POST/PUT/PATCH/DELETE na URL zodpovedajúce regexu (ochrana pred pridaním do košíka).
    packaging_abort_cart_mutation_posts: bool = True
    packaging_cart_post_url_regex: Optional[str] = None
    stock_selector: Optional[str] = None
    # Kusy v balení (napr. predvolená hodnota inputu na ADP).
    pack_quantity_selector: Optional[str] = None
    # Názov produktu na PDP (voliteľné). Fabory: ak nevyplníš, použije sa prvý viditeľný h1 v main/article.
    product_title_selector: Optional[str] = None
    # Košík: bez selektora sa pridanie do košíka nespustí (môžeš doplniť neskôr).
    add_to_cart_selector: Optional[str] = None
    post_modal_open_wait_ms: int = 500
    add_to_cart_confirm_selector: Optional[str] = None
    quantity_input_selector: Optional[str] = None
    post_add_wait_ms: int = 3000
    navigation_timeout_ms: int = 60000
    # page.goto wait_until — pre SPA (Mekrs/Nuxt) je „domcontentloaded“ výrazne rýchlejšie ako „load“.
    navigation_goto_wait_until: str = "load"
    headless: bool = True
    # Napr. "chrome" — použije nainštalovaný Google Chrome namiesto bundled Chromium (niekedy lepšie s CMP / SSO).
    browser_channel: Optional[str] = None
    # Balík playwright-stealth: menej „automation“ odtlačok (navigator.webdriver, WebGL, jazyky, …).
    playwright_stealth: bool = False
    # Náhodné pauzy medzi krokmi prihlásenia (niekedy menej agresívne spustenie CAPTCHA — nie je zaručené).
    playwright_human_pause_between_actions: bool = False
    playwright_human_pause_min_sec: float = 1.0
    playwright_human_pause_max_sec: float = 3.0
    # Namiesto okamžitého .fill() simuluj písanie (press_sequentially) — niektoré e-shopy merajú timing vstupov.
    playwright_human_type_login_fields: bool = False
    playwright_human_type_min_ms: float = 25.0
    playwright_human_type_max_ms: float = 90.0
    # Zrýchli načítanie: neťahaj obrázky, fonty, video (text/layout ostáva).
    block_heavy_assets: bool = False


def _dry_run() -> bool:
    return os.environ.get("CART_AUTOMATION_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _goto_wait_until(config: ScraperConfig) -> str:
    w = (config.navigation_goto_wait_until or "load").strip().lower()
    if w in ("load", "domcontentloaded", "commit", "networkidle"):
        return w
    return "load"


def _chromium_launch_kwargs(
    config: ScraperConfig,
    supplier: Optional[Supplier] = None,
) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "headless": config.headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    ch = (config.browser_channel or "").strip()
    if ch:
        kw["channel"] = ch
    return kw


async def _install_heavy_asset_blocker(page: Page) -> None:
    """Neblokuje stylesheet/script — len médiá, ktoré zvyšujú čas načítania."""

    async def _route(route: Route) -> None:
        rt = route.request.resource_type
        if rt in ("image", "media", "font"):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _route)


def _should_block_heavy_assets(config: ScraperConfig) -> bool:
    """Len z JSON — automatické blokovanie podľa mena dodávateľa môže rozbiť hydráciu / ceny."""
    return bool(config.block_heavy_assets)


def _parse_price_eur(text: str) -> Optional[float]:
    if not text or not text.strip():
        return None
    t = (
        text.replace("\xa0", " ")
        .replace(" ", "")
        .replace("EUR", "")
        .replace("€", "")
        .strip()
    )
    t = t.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# Nad touto hranicou je často zle zlepený čiarový kód / interné číslo v jednom textovom bloku (nie sklad).
_MAX_REASONABLE_STOCK_PIECES = 99_999_999


def _parse_stock_numeric_chunk(chunk: str) -> Optional[int]:
    """Číslo ks z bloku typu „2 402 749“, „2.402.749“, „2402749\"."""
    if not chunk or not chunk.strip():
        return None
    norm = (
        chunk.replace(" ", "")
        .replace("\xa0", "")
        .replace("\u202f", "")
        .replace(".", "")
        .replace(",", "")
    )
    if not norm.isdigit():
        return None
    try:
        n = int(norm)
    except ValueError:
        return None
    if n > _MAX_REASONABLE_STOCK_PIECES:
        return None
    return n


def _parse_stock(text: str) -> Optional[int]:
    """
    Počet kusov na sklade z textu (Mekrs / CZ často: viac čísel v jednom bloku — napr. úryvok skladu vs celkový sklad).

    Absolútna priorita: **„Skladem celkem“ / „celkem … ks“** (súčet všetkých balení), aby UI zodpovedalo Mekrs modálu.
    Inak berieme rozumné kandidáty a maximum; čísla pred „ks“; hodnoty nad ~100M ignorujeme.
    """
    if not text or not text.strip():
        return None
    t = text.replace("\xa0", " ").replace("\u202f", " ")

    # 0) Mekrs modál / hlavička: „Skladem celkem 2 408 499 ks“
    m_tot = re.search(
        r"(?:skladem\s+)?celkem\s+([\d\s\u00a0\u202f.]{2,28})\s*ks\b",
        t,
        re.IGNORECASE,
    )
    if m_tot:
        parsed = _parse_stock_numeric_chunk(m_tot.group(1))
        if parsed is not None:
            return parsed

    candidates: list[int] = []

    # 1) Explicitné „… ks“ (Mekrs riadok skladu)
    for m in re.finditer(
        r"(?:^|\s)([\d]{1,3}(?:[\s\u00a0\u202f]\d{3})+|[\d]{1,3}(?:[.]\d{3})+|[\d]{4,12})\s*ks\b",
        t,
        re.IGNORECASE,
    ):
        parsed = _parse_stock_numeric_chunk(m.group(1))
        if parsed is not None:
            candidates.append(parsed)

    # 2) Fallback: všetky číselné skupiny v texte bez bodiek ako tisícdelenia v číslach (nie v kódoch s bodkami)
    normalized = t.replace(".", "").replace(",", "")
    for m in re.finditer(r"(\d{1,3}(?:\s\d{3})+|\d{4,12}|\d+)", normalized):
        try:
            n = int(m.group(1).replace(" ", ""))
        except ValueError:
            continue
        if n <= _MAX_REASONABLE_STOCK_PIECES:
            candidates.append(n)

    if not candidates:
        return None
    return max(candidates)


def _mekrs_format_int_cs(n: int) -> str:
    """Celé číslo s medzerami po trojiciach (ako na mekrs.cz)."""
    if n < 0:
        return str(n)
    s = str(int(n))
    parts: list[str] = []
    while s:
        parts.append(s[-3:])
        s = s[:-3]
    return " ".join(reversed(parts))


def _mekrs_package_stock_phrase_from_green_text(text: Optional[str]) -> Optional[str]:
    """
    Riadok typu „Skladem 15 balení“ z div.text-primaryGreen (môže byť aj „… / 7 500 ks“).
    """
    if not text or not str(text).strip():
        return None
    t = re.sub(r"\s+", " ", str(text).replace("\xa0", " ").replace("\u202f", " ").strip())
    m = re.search(r"(?i)\bskladem\s+([\d\s]{1,24})\s+balení\b", t)
    if not m:
        return None
    parsed = _parse_stock_numeric_chunk(m.group(1))
    if parsed is None or parsed < 1:
        return None
    return f"Skladem {_mekrs_format_int_cs(parsed)} balení"


def _mekrs_infer_package_stock_phrase(
    stock_i: Optional[int],
    pack_q: Optional[int],
) -> Optional[str]:
    """
    Ak API/DOM dáva sklad v ks a veľkosť balenia > 1, odvodí „Skladem N balení“ len keď ks % balenie == 0.
    (Pri zdieľanom celkovom sklade medzi variantmi modul zvyčajne nevyjde — nič sa nezobrazí.)
    """
    if stock_i is None or not isinstance(stock_i, int) or stock_i < 1:
        return None
    pq = pack_q if isinstance(pack_q, int) and pack_q >= 2 else 0
    if pq < 2:
        return None
    if stock_i % pq != 0:
        return None
    n = stock_i // pq
    if n < 1:
        return None
    return f"Skladem {_mekrs_format_int_cs(n)} balení"


def _mekrs_variant_package_stock_line(
    *,
    raw_green: Optional[str],
    stock_pieces: Optional[int],
    pack_quantity: int,
) -> Optional[str]:
    phrase = _mekrs_package_stock_phrase_from_green_text(raw_green)
    if phrase:
        return phrase
    return _mekrs_infer_package_stock_phrase(stock_pieces, pack_quantity)


def _mekrs_row_eligible_for_shared_total_balen_phrase(row: dict[str, Any]) -> bool:
    """
    Riadky „Doprodej (47 ks)“ majú v zátvorke ks ako pack_quantity ≥ 2, ale nejde o plné
    „balení“ v zmysle modálu — nesmú zablokovať heuristiku pre riadok „Balení (200 ks)“.
    """
    if not isinstance(row, dict):
        return False
    pq = row.get("pack_quantity")
    if not isinstance(pq, int) or pq < 2:
        return False
    lbl = str(row.get("label") or "").lower()
    skip = (
        "doprodej",
        "výprodej",
        "vyprodej",
        "jednotkov",
        "akční",
        "akcni",
    )
    if any(x in lbl for x in skip):
        return False
    return True


def _mekrs_hydrate_package_stock_text_from_row_raw_stocks(
    packaging_variants: list[dict[str, Any]],
) -> None:
    """Pred odstránením ``raw_stock`` skopíruj „Skladem N balení“ z riadkového zeleného textu."""
    for row in packaging_variants:
        if not isinstance(row, dict):
            continue
        if row.get("mekrs_package_stock_text"):
            continue
        rs = row.get("raw_stock")
        if not isinstance(rs, str) or not rs.strip():
            continue
        phrase = _mekrs_package_stock_phrase_from_green_text(rs)
        if phrase:
            row["mekrs_package_stock_text"] = phrase


def _mekrs_apply_shared_total_package_stock_phrase(
    packaging_variants: list[dict[str, Any]],
    total_stock: Optional[int],
) -> None:
    """
    Mekrs API často vráti rovnaký ``stockLevel`` (celkové ks) na každom riadku variantu.
    Delenie ks/balenie nemusí vyjsť — ale pri jedinom „veľkom“ balení stačí ``total // pack_q``
    (napr. 7 767 ks, balenie 500 → 15 balení ako v modáli).

    Kombinácia „Balení (200 ks)“ + „Doprodej (47 ks)“ (647 ks celkom): len jeden riadok je
    oprávnený → ``647 // 200`` = **3 balení**, ako zelený riadok na webe.

    Playwright často má na Jednotková/Doprodej riadku ``stock`` None — **nepožadujeme** rovnaký
    počet číselných ``stock`` ako počet riadkov (predtým sa heuristika vôbec nespustila).
    """
    if not packaging_variants:
        return
    # Pri kombinácii "Balení + Doprodej" nechceme dopočítavať "Skladem N balení"
    # pre hlavné balenie z celkového skladu; Mekrs v UI zvýrazňuje skôr doprodej riadok.
    for row in packaging_variants:
        if not isinstance(row, dict):
            continue
        lbl = str(row.get("label") or "").lower()
        if "doprodej" in lbl or "vyprodej" in lbl or "výprodej" in lbl:
            return
    eligible = [
        row
        for row in packaging_variants
        if isinstance(row, dict) and _mekrs_row_eligible_for_shared_total_balen_phrase(row)
    ]
    if len(eligible) != 1:
        return
    br = eligible[0]
    if br.get("mekrs_package_stock_text"):
        return
    pq = int(br["pack_quantity"])
    if pq < 2:
        return

    per_row: list[int] = []
    for row in packaging_variants:
        if not isinstance(row, dict):
            continue
        s = row.get("stock")
        if isinstance(s, int) and s >= 0:
            per_row.append(s)

    t_eff: Optional[int] = None
    if isinstance(total_stock, int) and total_stock >= 1:
        t_eff = total_stock
    elif per_row and len(set(per_row)) == 1:
        t_eff = per_row[0]

    if t_eff is None or t_eff < 1:
        return

    if len(per_row) > 1 and len(set(per_row)) > 1:
        if not (isinstance(total_stock, int) and total_stock >= 1):
            return

    n = t_eff // pq
    if n < 1:
        return
    br["mekrs_package_stock_text"] = f"Skladem {_mekrs_format_int_cs(n)} balení"


def _mekrs_finalize_mekrs_variant_package_labels(
    packaging_variants: list[dict[str, Any]],
    total_stock: Optional[int],
) -> None:
    """Najprv text z ``raw_stock`` riadku, potom doplnenie z celkového skladu / API."""
    _mekrs_hydrate_package_stock_text_from_row_raw_stocks(packaging_variants)
    _mekrs_apply_shared_total_package_stock_phrase(
        packaging_variants,
        total_stock if isinstance(total_stock, int) and total_stock >= 1 else None,
    )
    _mekrs_apply_doprodej_package_stock_phrase(
        packaging_variants,
        total_stock if isinstance(total_stock, int) and total_stock >= 1 else None,
    )


def _mekrs_apply_doprodej_package_stock_phrase(
    packaging_variants: list[dict[str, Any]],
    total_stock: Optional[int],
) -> None:
    """
    Mekrs často kombinuje "Balení (N ks)" + "Doprodej (R ks)".
    Ak je celkový sklad známy a zvyšok po delení hlavným balením sedí na doprodej riadok,
    vieme doplniť "Skladem 1 balení" aj keď API neposiela per-row sklad.
    """
    if not isinstance(total_stock, int) or total_stock < 1:
        return
    if not packaging_variants:
        return

    base_rows: list[dict[str, Any]] = []
    doprodej_rows: list[dict[str, Any]] = []
    for row in packaging_variants:
        if not isinstance(row, dict):
            continue
        pq = row.get("pack_quantity")
        if not isinstance(pq, int) or pq < 2:
            continue
        lbl = str(row.get("label") or "").lower()
        if "doprodej" in lbl or "vyprodej" in lbl or "výprodej" in lbl:
            doprodej_rows.append(row)
        else:
            base_rows.append(row)

    if len(base_rows) != 1 or len(doprodej_rows) != 1:
        return

    base = base_rows[0]
    dop = doprodej_rows[0]
    if dop.get("mekrs_package_stock_text"):
        return

    base_pq = int(base.get("pack_quantity") or 0)
    dop_pq = int(dop.get("pack_quantity") or 0)
    if base_pq < 2 or dop_pq < 2:
        return

    remainder = total_stock % base_pq
    if remainder <= 0 or remainder != dop_pq:
        return
    dop["mekrs_package_stock_text"] = "Skladem 1 balení"


def _parse_variant_pack_row(row_text: str) -> tuple[int, str]:
    """Veľkosť balenia z textu riadku variantu (Mekrs: „Balení (100 ks)“ / „Jednotková …“)."""
    lines = [ln.strip() for ln in row_text.split("\n") if ln.strip()]
    head = (lines[0] if lines else row_text)[:160]
    low = row_text.lower()
    if "jednotkov" in low:
        return 1, head
    m = re.search(r"\(\s*(\d+)\s*ks\s*\)", row_text, re.IGNORECASE)
    if m:
        return int(m.group(1)), head
    return 1, head


_PACKAGING_CART_POST_DEFAULT_RE = re.compile(
    r"(basket|cart|ko[sš]ik|shopping-?cart|line-?item|add-?to-?cart|addToCart|"
    r"/api/[^?\s]*/(basket|cart|line|order|checkout))",
    re.IGNORECASE,
)


def _variants_from_packaging_modal_plain_text(text: str) -> list[dict[str, Any]]:
    """Mekrs modal: bez klikania na rádia — z celého textu dialógu (CZK/EUR, Balení / Jednotková)."""
    t = (
        text.replace("\xa0", " ")
        .replace("\u202f", " ")
        .replace("\r", "")
    )
    out: list[dict[str, Any]] = []
    m_pack = re.search(r"Balení\s*\(\s*(\d+)\s*ks\s*\)", t, re.IGNORECASE)
    if m_pack:
        pack = int(m_pack.group(1))
        label = m_pack.group(0).strip()
        rest = t[m_pack.end() :]
        mj = re.search(r"jednotkov", rest, re.IGNORECASE)
        chunk_b = rest[: mj.start()] if mj else rest
        if "Vložit" in chunk_b:
            chunk_b = chunk_b.split("Vložit")[0]
        pe, raw_disp = _mekrs_price_per_100_from_row_text(chunk_b)
        if pe is None:
            pm = re.search(r"(\d+[,\.]\d+)\s*€", chunk_b)
            raw_p = pm.group(0).strip() if pm else None
            pe_pkg = _parse_price_eur(raw_p) if raw_p else None
            if pe_pkg is not None:
                pe = round(pe_pkg * 100.0 / float(pack), 2)
                raw_disp = f"{pe:.2f} € / 100 ks"
            else:
                raw_disp = raw_p
        out.append(
            {
                "label": _mekrs_sanitize_variant_label(label),
                "pack_quantity": pack,
                "price_eur": pe,
                "raw_price": raw_disp,
                "stock": None,
                "raw_stock": None,
            }
        )
    if re.search(r"jednotkov", t, re.IGNORECASE):
        mj = re.search(r"(jednotkov[^\n]*)", t, re.IGNORECASE)
        label_j = _mekrs_sanitize_variant_label(
            mj.group(1).strip() if mj else "Jednotková položka"
        )
        rest_j = t[mj.end() :] if mj else t
        if "Vložit" in rest_j:
            rest_j = rest_j.split("Vložit")[0]
        pe, raw_disp = _mekrs_price_per_100_from_row_text(rest_j)
        if pe is None:
            pm = re.search(r"(\d+[,\.]\d+)\s*€", rest_j)
            raw_p = pm.group(0).strip() if pm else None
            pe = _parse_price_eur(raw_p) if raw_p else None
            raw_disp = raw_p
        out.append(
            {
                "label": label_j,
                "pack_quantity": 1,
                "price_eur": pe,
                "raw_price": raw_disp,
                "stock": None,
                "raw_stock": None,
            }
        )
    return out


def _mekrs_packaging_section(page: Page) -> Locator:
    """Mekrs: panel s H2 „Vyberte variantu“ a role=radiogroup (nemusí byť v [role=dialog])."""
    return page.locator("section").filter(
        has=page.get_by_role("heading", level=2, name="Vyberte variantu")
    ).first


async def _extract_variants_from_packaging_section(section: Locator) -> list[dict[str, Any]]:
    """Číta riadky variantov z div.relative.isolate + input[name=variant] (bez klikania)."""
    out: list[dict[str, Any]] = []
    try:
        if await section.count() < 1:
            return out
    except Exception:
        return out
    rows = section.locator('div.relative.isolate:has(input[name="variant"])')
    n = await rows.count()
    for i in range(n):
        row = rows.nth(i)
        label = ""
        try:
            label = (await row.locator("h3").first.inner_text()).strip()
        except Exception:
            pass
        m = re.search(
            r"\(\s*(\d+)[\s\u00a0\u202f]*ks\s*\)",
            label,
            re.IGNORECASE,
        )
        pack_qty = int(m.group(1)) if m else 1
        raw_price: Optional[str] = None
        price_eur: Optional[float] = None
        price_eur, raw_price = await _mekrs_extract_price_per_100_from_variant_row(row)
        if price_eur is None:
            try:
                raw_pkg = (
                    await row.locator("span.text-primaryRed.font-bold.text-lg")
                    .first.inner_text()
                ).strip()
                pe_pkg = _parse_price_eur(raw_pkg)
            except Exception:
                pe_pkg = None
            if pe_pkg is not None and pack_qty >= 1:
                price_eur = round(pe_pkg * 100.0 / float(pack_qty), 2)
                raw_price = f"{price_eur:.2f} € / 100 ks"
        raw_row_stock: Optional[str] = None
        row_stock: Optional[int] = None
        try:
            raw_row_stock = (
                await _mekrs_aggregate_primary_green_in_scope(row)
            ).strip()
            row_stock = _parse_stock(raw_row_stock) if raw_row_stock else None
        except Exception:
            pass
        mpt = _mekrs_variant_package_stock_line(
            raw_green=raw_row_stock,
            stock_pieces=row_stock,
            pack_quantity=pack_qty,
        )
        out.append(
            {
                "label": (
                    _mekrs_sanitize_variant_label(label)[:220]
                    if label
                    else f"Variant {i + 1}"
                ),
                "pack_quantity": pack_qty,
                "price_eur": price_eur,
                "raw_price": raw_price,
                "stock": row_stock,
                "raw_stock": raw_row_stock,
                **(
                    {"mekrs_package_stock_text": mpt}
                    if mpt
                    else {}
                ),
            }
        )
    return out


async def _install_packaging_cart_post_blocker(
    page: Page,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
):
    if not config.packaging_abort_cart_mutation_posts:
        async def _noop() -> None:
            return None

        return _noop

    custom = (config.packaging_cart_post_url_regex or "").strip()
    pat = re.compile(custom, re.IGNORECASE) if custom else _PACKAGING_CART_POST_DEFAULT_RE

    async def _handler(route: Route) -> None:
        req = route.request
        if req.method in ("POST", "PUT", "PATCH", "DELETE") and pat.search(req.url):
            _log(
                run_label,
                supplier,
                run_id,
                f"packaging scrape: blok {req.method} {req.url[:200]}",
            )
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _handler)

    async def _unroute() -> None:
        await page.unroute("**/*", _handler)

    return _unroute


async def _maybe_select_price_currency(
    page: Page,
    config: ScraperConfig,
    *,
    timeout: int,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    cur_sel = (config.price_currency_select_selector or "").strip()
    cur_val = (config.price_currency_select_value or "").strip()
    if not cur_sel or not cur_val or not config.price_selector:
        return
    try:
        sel_loc = page.locator(cur_sel).first
        cur_cap = min(timeout, 15_000)
        await sel_loc.wait_for(state="visible", timeout=cur_cap)
        await sel_loc.select_option(value=cur_val)
        _log(
            run_label,
            supplier,
            run_id,
            f"price currency select {cur_sel!r} -> {cur_val!r}",
        )
        # Mekrs (Nuxt): krátka pauza nestačí — ceny v DOM ešte môžu byť v pôvodnej mene.
        await asyncio.sleep(0.52 if _supplier_is_mekrs(supplier) else 0.2)
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"price currency select FAILED {cur_sel!r}: {exc}",
            "warn",
        )


async def _click_add_to_cart_outside_dialog(
    page: Page,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    purpose: str,
    allow_unscoped_fallback: bool = True,
    product_code: str = "",
) -> None:
    """Klik na CTA na stránke produktu, nie vo vnútri modalu (aria-modal / dialog)."""
    sel = (
        (config.packaging_modal_open_selector or "").strip()
        or (config.add_to_cart_selector or "").strip()
    )
    if not sel:
        raise ValueError("Chýba add_to_cart_selector / packaging_modal_open_selector.")

    # Mekrs: hydrácia PDP + CTA môže presiahnuť 12 s; strop držíme podľa navigation_timeout_ms.
    nav_cap = int(config.navigation_timeout_ms)

    # Hopefix: CTA je v rozbalenom riadku alebo v paneli s role=dialog — pôvodná logika také tlačidlá
    # preskakuje a fallback môže visieť na dlhom timeoute; klikneme priamo na prvý match.
    if _supplier_is_hopefix(supplier):
        click_to = min(nav_cap, 22_000)
        await _hopefix_click_add_to_cart_button(
            page,
            sel,
            product_code,
            click_timeout=click_to,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            purpose=purpose,
        )
        return

    pdp_click_timeout_ms = (
        min(nav_cap, 20_000) if _supplier_is_mekrs(supplier) else nav_cap
    )

    in_modal_js = (
        "el => !!(el.closest && el.closest("
        "'[role=dialog],[role=alertdialog],[aria-modal=true],[aria-modal=\"true\"]'))"
    )

    async def _try_click(locator: Locator, desc: str) -> bool:
        cnt = await locator.count()
        for i in range(cnt):
            btn = locator.nth(i)
            try:
                in_dialog = await btn.evaluate(in_modal_js)
            except Exception:
                in_dialog = True
            if in_dialog:
                continue
            try:
                await btn.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass
            try:
                await btn.click(timeout=pdp_click_timeout_ms)
            except Exception as exc:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"{purpose}: klik {desc} index={i} zlyhal ({exc!s}) — ďalší výskyt",
                    "warn",
                )
                continue
            _log(run_label, supplier, run_id, f"{purpose}: klik {desc} {sel!r} index={i}")
            return True
        return False

    loc = page.locator(sel)
    if await _try_click(loc, "PDP (globálne mimo modal)"):
        return

    for scope in ("main", "article", "#__nuxt", "[id=__nuxt]"):
        scoped = page.locator(scope).locator(sel)
        if await _try_click(scoped, f"PDP ({scope})"):
            return

    if allow_unscoped_fallback:
        _log(
            run_label,
            supplier,
            run_id,
            f"{purpose}: fallback prvý výskyt {sel!r} (môže byť v modale — riziko)",
            "warn",
        )
        await loc.first.click(timeout=pdp_click_timeout_ms)
        return

    _log(
        run_label,
        supplier,
        run_id,
        f"{purpose}: žiadne bezpečné PDP tlačidlo pre {sel!r} — modal sa neotvorí",
        "warn",
    )


async def _fill_cart_quantity_input(
    page: Page,
    config: ScraperConfig,
    quantity: int,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    sel = (config.quantity_input_selector or "").strip()
    if not sel:
        return
    _log(
        run_label,
        supplier,
        run_id,
        f"fill quantity selector={sel!r} value={quantity}",
    )
    loc = page.locator(sel).first
    await loc.wait_for(state="visible", timeout=15_000)
    try:
        await loc.click(timeout=5000)
        await loc.fill("", timeout=5000)
        await loc.fill(str(quantity), timeout=5000)
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"quantity fill (fallback DOM udalosti): {exc}",
            "warn",
        )
        qn = int(quantity)
        await loc.evaluate(
            """(el) => {
                el.value = %s;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
            % repr(str(qn))
        )
    await asyncio.sleep(0.15)


async def _click_confirm_add_to_cart(
    page: Page,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    sel = (config.add_to_cart_confirm_selector or "").strip()
    if not sel:
        return
    timeout = config.navigation_timeout_ms
    sec = _mekrs_packaging_section(page)
    try:
        if await sec.count() > 0:
            btn = sec.locator(sel).first
            if await btn.count() > 0:
                await btn.wait_for(state="visible", timeout=15_000)
                try:
                    await btn.scroll_into_view_if_needed(timeout=4_000)
                except Exception:
                    pass
                await btn.click(timeout=timeout)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"click confirm v sekcii variantov: {sel!r}",
                )
                return
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"confirm v sekcii variantov: {exc} — globálny klik",
            "warn",
        )
    loc = page.locator(sel).first
    await loc.wait_for(state="visible", timeout=15_000)
    try:
        await loc.scroll_into_view_if_needed(timeout=4_000)
    except Exception:
        pass
    await loc.click(timeout=timeout)
    _log(run_label, supplier, run_id, f"click confirm (globálne): {sel!r}")


async def _dismiss_cookies(page: Page, selector: Optional[str]) -> None:
    if not selector:
        return
    try:
        await page.click(selector, timeout=5000)
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def _dismiss_cookiescript_if_present(
    page: Page,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    config_selector: Optional[str] = None,
) -> None:
    """CookieScript (#cookiescript_injected_wrapper) často zakrýva tlačidlo prihlásenia — Playwright
    click potom zlyhá na „intercepts pointer events“. Skúsime DOM click; ak nič, overlay skryjeme."""
    try:
        cfg = (config_selector or "").strip() or None
        result = await page.evaluate(
            """(configSel) => {
              const wrap =
                document.getElementById("cookiescript_injected_wrapper") ||
                document.getElementById("cookiescript_injected");
              const tryClickSel = (sel) => {
                if (!sel || typeof sel !== "string") return false;
                const el = document.querySelector(sel);
                if (!el || !(el instanceof HTMLElement)) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) return false;
                const st = window.getComputedStyle(el);
                if (st.visibility === "hidden" || st.display === "none") return false;
                el.click();
                return true;
              };
              if (tryClickSel(configSel)) {
                return { ok: true, via: "config", sel: configSel };
              }
              const byId = [
                "#cookiescript_accept",
                "#cookiescript_reject",
                "#CookieScriptAccept",
                "#CookieScriptReject",
              ];
              for (const sel of byId) {
                if (tryClickSel(sel)) return { ok: true, via: "id", sel };
              }
              if (wrap) {
                const btnRe =
                  /přijmout|prijmout|přijmout vše|accept|souhlas|súhlas|odmítnout|odmitnout|odmítnout|nezbytné|nezbytne|reject|decline|necessary|only essential|essential only|deny|odmietnuť/i;
                const buttons = wrap.querySelectorAll(
                  'button, a[href="#"], a[role="button"], [role="button"], input[type="button"]'
                );
                for (const b of buttons) {
                  const t = (b.innerText || b.textContent || "")
                    .replace(/\\s+/g, " ")
                    .trim();
                    if (t && t.length < 120 && btnRe.test(t)) {
                    b.click();
                    return { ok: true, via: "text", text: t.slice(0, 80) };
                  }
                }
                if (wrap instanceof HTMLElement) {
                  wrap.style.setProperty("pointer-events", "none", "important");
                  wrap.style.setProperty("visibility", "hidden", "important");
                  wrap.style.setProperty("display", "none", "important");
                  return { ok: true, via: "hidden" };
                }
              }
              return { ok: false };
            }""",
            cfg,
        )
        if isinstance(result, dict) and result.get("ok"):
            _log(run_label, supplier, run_id, f"CookieScript overlay: {result!r}")
            await asyncio.sleep(0.45)
    except Exception as exc:
        _log(run_label, supplier, run_id, f"CookieScript dismiss: {exc!s}", "warn")


async def _handle_usercentrics_cmp(
    page: Page,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    """Usercentrics (Fabory): najprv skús „Prijať/Súhlas…“ (nastavia sa cookies), až potom skrytie overlay."""
    try:
        result = await page.evaluate(
            """
            () => {
              function walk(node, visit) {
                if (!node) return;
                visit(node);
                if (node.shadowRoot) walk(node.shadowRoot, visit);
                const ch = node.children;
                if (ch) for (let i = 0; i < ch.length; i++) walk(ch[i], visit);
              }
              const root = document.querySelector('#usercentrics-cmp-ui');
              if (!root) return { action: 'no_ui' };
              const re = /súhlas|suhlas|prijať|prijat|accept\\s+all|allow\\s+all|alle\\s+akzeptieren/i;
              let clicked = null;
              walk(root, (node) => {
                if (clicked) return;
                if (node.nodeType !== 1) return;
                const tag = node.tagName;
                const role = node.getAttribute && node.getAttribute('role');
                if (tag !== 'BUTTON' && role !== 'button') return;
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                if (text.length > 0 && re.test(text)) {
                  node.click();
                  clicked = text.slice(0, 120);
                }
              });
              if (clicked) return { action: 'accepted', label: clicked };
              root.style.setProperty('display', 'none', 'important');
              root.style.setProperty('pointer-events', 'none', 'important');
              root.setAttribute('data-ai-hidden', '1');
              return { action: 'hidden' };
            }
            """
        )
        _log(run_label, supplier, run_id, f"usercentrics CMP: {result!r}")
        if isinstance(result, dict) and result.get("action") == "accepted":
            await asyncio.sleep(0.45 if _supplier_is_fabory(supplier) else 0.85)
    except Exception as exc:
        _log(run_label, supplier, run_id, f"usercentrics CMP failed: {exc}", "warn")


async def _dismiss_eu_cookie_banner_overlay(
    page: Page,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    """
    Magento (Amasty Cookie / GDPR) a podobné lišty sa často vykreslia až cez JS — v statickom HTML
    nemusia byť vôbec. Overlay potom blokuje „visible“ na prihlasovacích poliach.
    """
    try:
        result = await page.evaluate(
            r"""
            () => {
              function visible(el) {
                if (!el || el.nodeType !== 1) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) return false;
                const st = window.getComputedStyle(el);
                if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0)
                  return false;
                return true;
              }
              const contRe =
                /cookie|gdpr|consent|cmp|privacy-notice|tracking-banner|amcookie|amgdpr|amasty|eu-cookie/i;
              const btnRe =
                /^(accept\s+all|accept|allow\s+all|allow\s+cookies|agree(\s+all)?|ok|got\s+it|prijať|přijmout|súhlas|suhlas|súhlasím|accetta(\s+tutto)?|accepter|akzeptieren|alle\s+akzeptieren|i\s+agree)/i;
              const nodes = document.querySelectorAll(
                'button, a[role="button"], [role="button"], input[type="button"]'
              );
              for (const el of nodes) {
                if (!visible(el)) continue;
                let p = el;
                let inBanner = false;
                for (let d = 0; d < 12 && p; d++) {
                  const id = String(p.id || "");
                  const cl = String((p.className && p.className.toString()) || "");
                  if (contRe.test(id) || contRe.test(cl)) {
                    inBanner = true;
                    break;
                  }
                  p = p.parentElement;
                }
                if (!inBanner) continue;
                const text = String(
                  el.innerText || el.value || el.textContent || ""
                )
                  .replace(/\s+/g, " ")
                  .trim();
                if (!text || text.length > 96) continue;
                if (btnRe.test(text)) {
                  el.click();
                  return { ok: true, text: text.slice(0, 72) };
                }
              }
              return { ok: false };
            }
            """
        )
        _log(run_label, supplier, run_id, f"EU cookie banner: {result!r}")
        if isinstance(result, dict) and result.get("ok"):
            await asyncio.sleep(0.55)
    except Exception as exc:
        _log(run_label, supplier, run_id, f"EU cookie banner: {exc}", "warn")


async def _neutralize_blocking_cmp_overlays(
    page: Page,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    """Fallback: skryje CMP overlay, ktorý zachytáva pointer events nad inputmi."""
    try:
        result = await page.evaluate(
            r"""
            () => {
              const hidden = [];
              const selectors = [
                '#usercentrics-cmp-ui',
                '#usercentrics-root',
                'aside#usercentrics-cmp-ui',
                '[id*="usercentrics"]',
                '[class*="usercentrics"]',
                '[id*="cookie"]',
                '[class*="cookie-banner"]',
                '[class*="consent"]'
              ];
              for (const sel of selectors) {
                const nodes = document.querySelectorAll(sel);
                for (const el of nodes) {
                  if (!(el instanceof HTMLElement)) continue;
                  const st = window.getComputedStyle(el);
                  if (st.display === 'none') continue;
                  el.style.setProperty('pointer-events', 'none', 'important');
                  el.style.setProperty('display', 'none', 'important');
                  el.setAttribute('data-ai-cmp-hidden', '1');
                  hidden.push(sel);
                }
              }
              return { hidden_count: hidden.length };
            }
            """
        )
        _log(run_label, supplier, run_id, f"cmp overlay fallback: {result!r}")
    except Exception as exc:
        _log(run_label, supplier, run_id, f"cmp overlay fallback failed: {exc}", "warn")


async def _probe_post_login(
    page: Page,
    supplier: Supplier,
    config: ScraperConfig,
    run_label: str,
    run_id: str,
    *,
    spring_status: Optional[int] = None,
    spring_location: Optional[str] = None,
    max_probe_attempts: int = 32,
) -> bool:
    """True ak prihlasovací formulár už nie je „aktívny“ (úspešný redirect alebo zmiznutie formulára)."""
    if spring_status is not None and 300 <= spring_status < 400:
        raw = (spring_location or "").strip()
        low = raw.lower()
        if raw.startswith("http"):
            redir_path = urlparse(raw).path.lower().rstrip("/")
        else:
            redir_path = raw.split("?")[0].lower().rstrip("/")
        if raw and (
            "error" in low
            or "badcredentials" in low
            or redir_path.endswith("/login")
        ):
            _log(
                run_label,
                supplier,
                run_id,
                f"Spring redirect {spring_status} pravdepodobne neúspech Location={spring_location!r}",
                "warn",
            )
        else:
            _log(
                run_label,
                supplier,
                run_id,
                f"Spring redirect HTTP {spring_status} Location={spring_location!r} — čakám na DOM",
            )
            await asyncio.sleep(
                0.65
                if _supplier_is_fabory(supplier)
                else (0.85 if _supplier_is_hopefix(supplier) else 1.2)
            )

    form_sel = (config.login_form_selector or "").strip()
    attempts = max(1, min(64, int(max_probe_attempts)))
    for attempt in range(attempts):
        try:
            url = page.url
            url_l = url.lower()

            if form_sel:
                form = page.locator(form_sel).first
                try:
                    form_visible = await form.is_visible(timeout=450)
                except Exception:
                    form_visible = False
                if not form_visible:
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"login probe OK (krok {attempt}): formulár {form_sel!r} nie je viditeľný, url={url!r}",
                    )
                    return True
                try:
                    pwd_vis = await form.locator(config.password_selector).first.is_visible(
                        timeout=450
                    )
                except Exception:
                    pwd_vis = False
                if not pwd_vis:
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"login probe OK (krok {attempt}): pole hesla vo formulári nie je viditeľné, url={url!r}",
                    )
                    return True
            else:
                path = urlparse(url).path.lower().rstrip("/")
                if path and not path.endswith("/login"):
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"login probe OK (krok {attempt}): path mimo /login ({path!r})",
                    )
                    return True
                pwd_visible = False
                try:
                    pwd_visible = await page.locator(config.password_selector).first.is_visible(
                        timeout=450
                    )
                except Exception:
                    pwd_visible = False
                if not pwd_visible:
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"login probe OK (krok {attempt}): heslo neviditeľné globálne, url={url!r}",
                    )
                    return True

            if "/login" not in url_l and "j_spring_security_check" not in url_l:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"login probe OK (krok {attempt}): URL bez /login, url={url!r}",
                )
                return True
        except Exception as exc:
            _log(run_label, supplier, run_id, f"login probe krok {attempt}: {exc}", "warn")
        await asyncio.sleep(
            0.3
            if _supplier_is_fabory(supplier)
            else (0.35 if _supplier_is_hopefix(supplier) else 0.5)
        )
    _log(
        run_label,
        supplier,
        run_id,
        f"login probe FAIL: stále viditeľný login formulár / polia, url={page.url!r}",
        "warn",
    )
    return False


async def _new_browser_context(
    browser: Any,
    supplier: Optional[Supplier] = None,
    automation_user_id: int = 0,
) -> BrowserContext:
    """Menej „headless“ odtlačok — niektoré B2B weby blokujú prázdny UA."""
    kwargs: dict[str, Any] = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "locale": "sk-SK",
        "timezone_id": "Europe/Bratislava",
        "viewport": {"width": 1400, "height": 900},
    }
    if (
        supplier is not None
        and supplier.id is not None
        and _session_reuse_enabled()
    ):
        path = _storage_state_path(supplier.id, automation_user_id)
        if os.path.isfile(path):
            kwargs["storage_state"] = path
    return await browser.new_context(**kwargs)


async def _apply_playwright_stealth_if_enabled(
    context: BrowserContext,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> None:
    if not config.playwright_stealth:
        return
    try:
        from playwright_stealth import Stealth
    except ImportError:
        _log(
            run_label,
            supplier,
            run_id,
            "playwright_stealth je true, ale balík playwright-stealth nie je nainštalovaný.",
            "warn",
        )
        return
    stealth = Stealth(
        navigator_languages_override=("sk-SK", "sk", "en-US", "en"),
        navigator_platform_override="Win32",
        navigator_user_agent_override=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    try:
        await stealth.apply_stealth_async(context)
        _log(run_label, supplier, run_id, "playwright-stealth: aplikované na kontext")
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"playwright-stealth zlyhalo: {exc!s}",
            "warn",
        )


async def _playwright_human_action_pause(config: ScraperConfig) -> None:
    """async náhrada za time.sleep(random.uniform) — neblokuje event loop."""
    if not config.playwright_human_pause_between_actions:
        return
    lo = float(config.playwright_human_pause_min_sec)
    hi = float(config.playwright_human_pause_max_sec)
    if hi < lo:
        lo, hi = hi, lo
    hi = min(hi, 30.0)
    lo = max(0.05, min(lo, hi))
    await asyncio.sleep(random.uniform(lo, hi))


async def _playwright_fill_login_field(
    loc: Locator,
    value: str,
    config: ScraperConfig,
) -> None:
    """Normálne okamžité fill; pri playwright_human_type_login_fields postupné stlačenia ako človek."""
    if not config.playwright_human_type_login_fields:
        await loc.fill(value)
        return
    lo = float(config.playwright_human_type_min_ms)
    hi = float(config.playwright_human_type_max_ms)
    if hi < lo:
        lo, hi = hi, lo
    lo = max(1.0, min(lo, 500.0))
    hi = max(lo, min(hi, 500.0))
    delay_ms = random.uniform(lo, hi)
    try:
        await loc.click(timeout=12_000)
    except Exception:
        pass
    try:
        await loc.fill("")
    except Exception:
        pass
    if not value:
        return
    await loc.press_sequentially(value, delay=delay_ms)


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return out or "supplier"


def _log_ctx(run_label: str, supplier: Supplier, run_id: str) -> dict[str, Any]:
    return {
        "supplier": supplier.name,
        "supplier_id": supplier.id,
        "run_id": run_id,
    }


def _log(
    run_label: str,
    supplier: Supplier,
    run_id: str,
    message: str,
    level: str = "info",
    **extra: Any,
) -> None:
    dev_run_log(run_label, message, level, **_log_ctx(run_label, supplier, run_id), **extra)


async def _save_step_screenshot(
    page: Page,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    step: str,
) -> Optional[str]:
    if not step_screenshots_enabled():
        return None
    name = f"{run_id}_{_slug(supplier.name)}_{_slug(run_label)}_{_slug(step)}.png"
    abs_path = os.path.join(dev_screens_dir(), name)
    try:
        # full_page=True je na dlhých PDP veľmi pomalé; viewport stačí na diagnostiku.
        await page.screenshot(path=abs_path, full_page=False)
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"screenshot failed step={step!r}: {exc}",
            "warn",
        )
        return None
    rel_url = f"/dev-assets/{name}"
    _log(
        run_label,
        supplier,
        run_id,
        f"screenshot step={step!r}",
        screenshot_url=rel_url,
    )
    return rel_url


def _login_failure_hint(extra: Optional[str] = None) -> str:
    extra_txt = (extra or "").strip()
    low = extra_txt.lower()
    if (
        extra_txt
        and (
            "používateľské meno alebo heslo bolo nesprávne" in low
            or "nesprávne prihlasovacie meno alebo heslo" in low
            or "badcredentials" in low
            or "error=true" in low
        )
    ):
        return (
            f"{extra_txt}\n\n"
            "Fabory odmietlo prihlasovacie údaje. Skontroluj meno/heslo uložené pre aktuálneho používateľa "
            "v sekcii Dodávatelia → Fabory. Ak sa vieš ručne prihlásiť, prepíš heslo aj sem (často po zmene "
            "hesla alebo pri inom účte na live)."
        )
    base = (
        "Prihlásenie neprešlo (formulár ostáva alebo redirect s error=true). Skús v cart_config_json "
        "„headless“: false a „browser_channel“: „chrome“, prípadne vyšší post_login_wait_ms. "
        "Ak ručne funguje rovnaké heslo, často ide o cookies (Usercentrics) alebo rozdiel headless vs. normálny prehliadač."
    )
    if extra_txt:
        return f"{extra_txt}\n\n{base}"
    return base


def _looks_like_invalid_credentials(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    needles = (
        "vaše používateľské meno alebo heslo bolo nesprávne",
        "vaše používateľské meno alebo heslo bolo nespravne",
        "nesprávne prihlasovacie meno alebo heslo",
        "nespravne prihlasovacie meno alebo heslo",
        "badcredentials",
        "error=true",
        "incorrect username or password",
        "wrong username or password",
    )
    return any(n in low for n in needles)


async def _extract_login_error_message(page: Page) -> Optional[str]:
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    on_login = "customer/account/login" in url
    if (
        "error=true" not in url
        and "badcredentials" not in url
        and not on_login
    ):
        return None
    selectors = (
        ".alert-danger",
        ".alert.alert-danger",
        ".global-alerts .alert",
        ".has-error .help-block",
        ".form-group.has-error .help-block",
        "#faboryLoginForm .has-error",
        ".message-error",
        ".message.message-error",
        ".messages .message-error",
        "div.page.messages .error",
        "[role='alert']",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible(timeout=450):
                t = (await loc.inner_text()).strip()
                if t and len(t) > 2:
                    return t[:800]
        except Exception:
            continue
    return None


async def _inoxmare_seed_playwright_cookies(
    page: Page, shop_url: str, header: str
) -> None:
    pairs = inoxmare_parse_cookie_header(header)
    if not pairs:
        return
    dom = inoxmare_playwright_cookie_domain(shop_url)
    await page.context.add_cookies(
        [
            {
                "name": k,
                "value": v,
                "domain": dom,
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
            for k, v in pairs.items()
        ]
    )


async def _inoxmare_raise_if_login_captcha_required(
    page: Page, config: ScraperConfig
) -> None:
    """
    Magento Customer Login + modul Captcha: pole sa často doplní cez Knockout až po načítaní.
    Bez OCR / služby na riešenie CAPTCHA automat prihlásenie nedokončí (hláska „incorrect captcha“).
    """
    if (config.inoxmare_session_cookie_header or "").strip():
        return
    await asyncio.sleep(1.0)
    loc = page.locator(
        'form#login-form input[name^="captcha"], '
        "#login-form .field.captcha input.input-text"
    ).first
    try:
        await loc.wait_for(state="visible", timeout=4_000)
    except Exception:
        return
    raise RuntimeError(
        "Inoxmare má na prihlásení zapnutú CAPTCHA (Magento). Bez ručného prepisu kódu z obrázka "
        "dostaneš „incorrect captcha“ — Playwright ju nevie vyplniť. "
        "Najrýchlejšie: vlož do cart_config_json pole inoxmare_session_cookie_header (celá hlavička Cookie "
        "z DevTools → Sieť po ručnom prihlásení). Alebo vypni CAPTCHA pre Customer Login v Magento / výnimku "
        "od obchodu; prípadne uložený storage state po ručnom prihlásení."
    )


async def _inoxmare_raise_if_login_temporarily_locked(page: Page) -> None:
    """Magento lockout hláška po sérii neúspešných loginov."""
    markers = (
        "account sign-in was incorrect",
        "account is disabled temporarily",
        "please wait and try again later",
        "too many failed login attempts",
    )
    selectors = (
        ".message-error",
        ".messages .message-error",
        ".message.message-error",
        ".messages .message",
    )
    for sel in selectors:
        try:
            txt = (await page.locator(sel).first.inner_text(timeout=900)).strip()
        except Exception:
            continue
        low = txt.lower()
        if any(m in low for m in markers):
            raise RuntimeError(
                "Inoxmare: účet je dočasne zablokovaný po neúspešných pokusoch "
                "(\"Please wait and try again later\"). Počkaj na odblokovanie a potom "
                "použi inoxmare_session_cookie_header (cookies z ručného prihlásenia), "
                "aby sa lockout neopakoval."
            )


def _timeout_waiting_for_first_product_result(exc: BaseException) -> bool:
    """Playwright timeout pri wait_for na prvý produkt v zozname výsledkov (často stiahnutý / neexistujúci kód)."""
    if type(exc).__name__ == "TimeoutError":
        return True
    msg = str(exc).lower()
    if "timeout" not in msg:
        return False
    return "wait_for" in msg or "waiting for locator" in msg


def _is_navigation_interrupted_by_redirect(exc: BaseException) -> bool:
    """Playwright pri SPA redirecte vie hodiť goto chybu typu:
    'Navigation to ... is interrupted by another navigation to ...'."""
    msg = str(exc).lower()
    return "interrupted by another navigation" in msg


async def _login_and_search(
    page: Page,
    supplier: Supplier,
    config: ScraperConfig,
    product_code: str,
    *,
    run_label: str = "playwright",
    run_id: str = "",
    login_diagnostic: Optional[dict[str, Any]] = None,
    storage_user_id: int = 0,
) -> bool:
    code = product_code.strip()
    if not code:
        raise ValueError("product_code je prázdny.")

    inox_cookie_hdr = ""
    if _supplier_is_inoxmare(supplier):
        inox_cookie_hdr = (config.inoxmare_session_cookie_header or "").strip()
        if inox_cookie_hdr:
            await _inoxmare_seed_playwright_cookies(
                page, supplier.shop_url or "", inox_cookie_hdr
            )

    start_url = (config.login_url or supplier.shop_url or "").strip()
    if not start_url:
        raise ValueError("Chýba login_url alebo shop_url.")
    if inox_cookie_hdr:
        start_url = (config.after_login_url or supplier.shop_url or start_url).strip()
        _log(
            run_label,
            supplier,
            run_id,
            "Inoxmare: inoxmare_session_cookie_header — prvý goto je prihlásená zóna (bez login stránky)",
        )

    _log(
        run_label,
        supplier,
        run_id,
        f"goto start_url={start_url!r} headless={config.headless}",
    )
    wu = _goto_wait_until(config)
    try:
        await page.goto(
            start_url,
            wait_until=wu,
            timeout=min(90_000, config.navigation_timeout_ms),
        )
        _log(run_label, supplier, run_id, f"page loaded ({wu})")
    except Exception as exc:
        if _supplier_is_hopefix(supplier) and _is_navigation_interrupted_by_redirect(exc):
            _log(
                run_label,
                supplier,
                run_id,
                f"goto {wu}: Hopefix redirect race ({exc!s}) — pokračujem po domcontentloaded",
                "warn",
            )
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=12_000)
            except Exception:
                pass
        else:
            _log(
                run_label,
                supplier,
                run_id,
                f"goto {wu}: {exc!s}, fallback domcontentloaded",
                "warn",
            )
            await page.goto(start_url, wait_until="domcontentloaded")
            _log(run_label, supplier, run_id, "page loaded (domcontentloaded)")
    await _save_step_screenshot(
        page,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
        step="01_loaded_login_page",
    )
    # Cookie banner (CookieScript, Cookiebot, …) musí ísť preč skôr než wait_for_selector na login —
    # inak overlay zablokuje viditeľnosť polí.
    await _dismiss_cookies(page, config.optional_cookie_dismiss_selector)
    await _handle_usercentrics_cmp(
        page,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
    )
    await _dismiss_eu_cookie_banner_overlay(
        page,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
    )
    await _dismiss_cookiescript_if_present(
        page,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
        config_selector=config.optional_cookie_dismiss_selector,
    )
    await asyncio.sleep(0.35)

    try:
        login_field_timeout = min(25_000, int(config.navigation_timeout_ms))
        if _supplier_is_fabory(supplier):
            login_field_timeout = min(login_field_timeout, 12_000)
        await page.wait_for_selector(
            config.username_selector,
            state="visible",
            timeout=login_field_timeout,
        )
    except Exception as exc:
        _log(
            run_label,
            supplier,
            run_id,
            f"login polia ešte nie sú viditeľné po goto: {exc}",
            "warn",
        )

    # Skorý probe len ak už máme uložený storage state — inak na čistej prihlasovacej
    # stránke (Fabory a i.) 5× probe + sleep zbytočne pridá sekundy pred loginom.
    reuse_skip_login = False
    has_saved_session = (
        supplier.id is not None
        and os.path.isfile(_storage_state_path(supplier.id, storage_user_id))
    )
    if _session_reuse_enabled() and has_saved_session:
        await asyncio.sleep(0.2)
        reuse_skip_login = await _probe_post_login(
            page,
            supplier,
            config,
            run_label,
            run_id,
            spring_status=None,
            spring_location=None,
            max_probe_attempts=5,
        )
        if reuse_skip_login:
            _log(
                run_label,
                supplier,
                run_id,
                "SCRAPER_REUSE_SESSION: platná relácia — preskakujem prihlásenie",
            )

    spring_status: Optional[int] = None
    spring_location: Optional[str] = None

    if not reuse_skip_login:
        await _playwright_human_action_pause(config)
        if config.open_login_form_selector:
            _log(
                run_label,
                supplier,
                run_id,
                f"open login form click: {config.open_login_form_selector!r}",
            )
            await page.click(config.open_login_form_selector)
            await _playwright_human_action_pause(config)
            # Odkaz na /sk/login spôsobí navigáciu — počkáme na polia formulára, nie len fixný sleep.
            try:
                await page.wait_for_selector(
                    config.username_selector,
                    state="visible",
                    timeout=max(5_000, min(config.navigation_timeout_ms, 30_000)),
                )
            except Exception:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    "wait_for_selector(username) after open_login timed out, using open_login_form_wait_ms",
                    "warn",
                )
                await asyncio.sleep(config.open_login_form_wait_ms / 1000.0)
            else:
                await asyncio.sleep(min(0.35, config.open_login_form_wait_ms / 1000.0))

        user = (supplier.username or "").strip()
        pwd = (supplier.password or "").strip()
        if not user or not pwd:
            if not (
                _supplier_is_inoxmare(supplier)
                and (config.inoxmare_session_cookie_header or "").strip()
            ):
                raise ValueError(
                    "Prihlasovacie meno alebo heslo dodávateľa je prázdne (po orezaní medzier)."
                )

        if _supplier_is_inoxmare(supplier):
            await _inoxmare_raise_if_login_captcha_required(page, config)

        await _playwright_human_action_pause(config)

        form_sel = (config.login_form_selector or "").strip()
        _log(
            run_label,
            supplier,
            run_id,
            f"fill username selector={config.username_selector!r} (form={form_sel!r})",
        )
        if form_sel:
            frm = page.locator(form_sel).first
            await _playwright_fill_login_field(
                frm.locator(config.username_selector), user, config
            )
            await _playwright_human_action_pause(config)
            _log(
                run_label,
                supplier,
                run_id,
                f"fill password selector={config.password_selector!r}",
            )
            await _playwright_fill_login_field(
                frm.locator(config.password_selector), pwd, config
            )
        else:
            await _playwright_fill_login_field(
                page.locator(config.username_selector), user, config
            )
            await _playwright_human_action_pause(config)
            _log(
                run_label,
                supplier,
                run_id,
                f"fill password selector={config.password_selector!r}",
            )
            await _playwright_fill_login_field(
                page.locator(config.password_selector), pwd, config
            )

        await _playwright_human_action_pause(config)

        login_btn = page.locator(config.login_button_selector).first
        try:
            await login_btn.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            # Nuxt/SPA: uzol môže medzi krokmi zmiznúť — klik funguje aj bez scrollu.
            pass
        try:
            await login_btn.wait_for(
                state="visible",
                timeout=8_000 if _supplier_is_fabory(supplier) else 12_000,
            )
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"tlačidlo Prihlásenie nie je viditeľné: {exc}",
                "warn",
            )
            await _handle_usercentrics_cmp(
                page,
                run_label=run_label,
                supplier=supplier,
                run_id=run_id,
            )

        await _dismiss_cookiescript_if_present(
            page,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            config_selector=config.optional_cookie_dismiss_selector,
        )

        _log(
            run_label,
            supplier,
            run_id,
            f"login submit (requestSubmit alebo klik): {config.login_button_selector!r}",
        )
        _login_click_timeout = min(30_000, int(config.navigation_timeout_ms))

        async def _login_button_click() -> None:
            """Hopefix: po odoslaní často okamžitá navigácia na /sortiment (SPA). Štandardný
            locator.click() čaká na „dokončenie“ navigácie a môže visieť 20+ s."""
            if _supplier_is_hopefix(supplier):
                await login_btn.click(
                    no_wait_after=True,
                    timeout=_login_click_timeout,
                )
                await asyncio.sleep(0.22)
                try:
                    await page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=15_000,
                    )
                except Exception:
                    pass
                return
            await login_btn.click(timeout=_login_click_timeout)

        try:
            if config.login_expect_spring_security_post:
                spring_wait = (
                    15_000 if _supplier_is_fabory(supplier) else 25_000
                )
                async with page.expect_response(
                    lambda r: "j_spring_security_check" in r.url
                    and r.request.method.upper() == "POST",
                    timeout=spring_wait,
                ) as resp_info:
                    if form_sel:
                        form_el = page.locator(form_sel).first
                        try:
                            await form_el.evaluate("form => form.requestSubmit()")
                        except Exception as exc:
                            _log(
                                run_label,
                                supplier,
                                run_id,
                                f"requestSubmit zlyhal ({exc!s}), klik na tlačidlo",
                                "warn",
                            )
                            await _login_button_click()
                    else:
                        await _login_button_click()
                sec_resp = await resp_info.value
                spring_status = sec_resp.status
                spring_location = sec_resp.headers.get("location")
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"j_spring_security_check status={spring_status} "
                    f"Location={spring_location!r} url={sec_resp.url!r}",
                )
            else:
                if form_sel:
                    form_el = page.locator(form_sel).first
                    try:
                        await form_el.evaluate("form => form.requestSubmit()")
                    except Exception as exc:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"requestSubmit zlyhal ({exc!s}), klik na tlačidlo",
                            "warn",
                        )
                        await _login_button_click()
                else:
                    await _login_button_click()
                _log(
                    run_label,
                    supplier,
                    run_id,
                    "login: bez čakania na j_spring_security_check (SPA / API login)",
                )
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"POST j_spring_security_check nebol zachytený ({exc!s}), klik na tlačidlo",
                "warn",
            )
            await _login_button_click()
        await _save_step_screenshot(
            page,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
            step="02_after_submit",
        )

        await asyncio.sleep(config.post_login_wait_ms / 1000.0)
        _log(run_label, supplier, run_id, "post-login wait done")
        if _supplier_is_inoxmare(supplier):
            await _inoxmare_raise_if_login_temporarily_locked(page)
    else:
        await asyncio.sleep(0.12)

    if config.after_login_url:
        _log(run_label, supplier, run_id, f"goto after_login_url={config.after_login_url!r}")
        try:
            await page.goto(config.after_login_url, wait_until="domcontentloaded")
        except Exception as exc:
            if _supplier_is_hopefix(supplier) and _is_navigation_interrupted_by_redirect(exc):
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"after_login goto: Hopefix redirect race ({exc!s}) — pokračujem",
                    "warn",
                )
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8_000)
                except Exception:
                    pass
            else:
                raise
        await asyncio.sleep(
            0.12
            if _supplier_is_hopefix(supplier)
            else (0.28 if _supplier_is_fabory(supplier) else 0.5)
        )
        # Cookie lišta sa často znova vykreslí na cieľovej stránke (Hopefix: úvod → hlavičkové hľadanie).
        await _dismiss_cookies(page, config.optional_cookie_dismiss_selector)

    if reuse_skip_login:
        probe_max = 10
    elif _supplier_is_mekrs(supplier):
        probe_max = 14
    elif _supplier_is_hopefix(supplier):
        # Hopefix občas oneskorene prepne reláciu na B2B tabuľku.
        probe_max = 8
    elif _supplier_is_fabory(supplier):
        probe_max = 12
    else:
        probe_max = 32
    logged_in = await _probe_post_login(
        page,
        supplier,
        config,
        run_label,
        run_id,
        spring_status=spring_status,
        spring_location=spring_location,
        max_probe_attempts=probe_max,
    )
    _log(run_label, supplier, run_id, f"login probe logged_in={logged_in}")
    if login_diagnostic is not None:
        if spring_status is not None:
            login_diagnostic["spring_status"] = spring_status
        if spring_location:
            login_diagnostic["spring_redirect"] = spring_location
        if not logged_in:
            if _supplier_is_inoxmare(supplier):
                await _inoxmare_raise_if_login_temporarily_locked(page)
            pe = await _extract_login_error_message(page)
            if pe:
                login_diagnostic["page_error"] = pe
            if _supplier_is_fabory(supplier):
                err_parts: list[str] = []
                if pe:
                    err_parts.append(pe)
                if spring_location:
                    err_parts.append(str(spring_location))
                if _looks_like_invalid_credentials(" | ".join(err_parts)):
                    detail = pe or "Fabory: neplatné prihlasovacie údaje."
                    raise RuntimeError(
                        f"Fabory credentials invalid: {detail} "
                        "Skontroluj meno/heslo pre aktuálneho používateľa v sekcii Dodávatelia."
                    )
    await _save_step_screenshot(
        page,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
        step="03_login_probe",
    )

    search_sel = (config.search_input_selector or "").strip()
    search_url_tmpl = (config.search_via_url_template or "").strip()
    if (
        search_url_tmpl
        and search_sel
        and _supplier_is_hopefix(supplier)
    ):
        _log(
            run_label,
            supplier,
            run_id,
            "Hopefix: máš search_via_url_template aj search_input_selector — "
            "používam vyhľadávacie pole (autocomplete), URL šablónu preskakujem.",
        )
        search_url_tmpl = ""
    if not search_sel and not search_url_tmpl:
        _log(
            run_label,
            supplier,
            run_id,
            "search_input_selector aj search_via_url_template chýbajú — končím po prihlásení (bez vyhľadania produktu).",
        )
        _log(run_label, supplier, run_id, f"current URL: {page.url}")
        return logged_in

    if search_url_tmpl:
        if "{code}" not in search_url_tmpl:
            raise ValueError(
                'search_via_url_template musí obsahovať zástupný text "{code}" (kód produktu).'
            )
        # Zachová bodky a iné „nechránené“ znaky ako v bežnom query (napr. MEKR'S kódy).
        encoded = quote(code, safe=".-_~")
        search_url = search_url_tmpl.replace("{code}", encoded)
        _log(
            run_label,
            supplier,
            run_id,
            f"search via URL template -> {search_url!r}",
        )
        wu = _goto_wait_until(config)
        try:
            await page.goto(
                search_url,
                wait_until=wu,
                timeout=min(90_000, config.navigation_timeout_ms),
            )
        except Exception as exc:
            err_txt = str(exc)
            _log(
                run_label,
                supplier,
                run_id,
                f"goto search URL {wu}: {err_txt}, fallback domcontentloaded",
                "warn",
            )
            # Inoxmare quicksearch občas vráti net::ERR_ABORTED počas interného redirectu.
            # V tom prípade druhý goto často zlyhá na TargetClosedError, preto počkáme
            # krátko na stabilizáciu URL namiesto okamžitého opakovania.
            if "ERR_ABORTED" in err_txt.upper():
                try:
                    await page.wait_for_url("**/quicksearch/**", timeout=6_000)
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=6_000)
                except Exception:
                    pass
            else:
                if page.is_closed():
                    raise RuntimeError(
                        "Playwright stránka sa zatvorila počas vyhľadávania (quicksearch)."
                    ) from exc
                await page.goto(search_url, wait_until="domcontentloaded")
    else:
        _log(
            run_label,
            supplier,
            run_id,
            f"search fill selector={search_sel!r} code={code!r}",
        )
        loc_search = page.locator(search_sel).first

        async def _submit_search_click(selector: str) -> None:
            submit_sel = (selector or "").strip()
            if not submit_sel:
                return
            click_timeout = min(30_000, int(config.navigation_timeout_ms))
            loc_submit = page.locator(submit_sel).first
            if _supplier_is_fabory(supplier) or _supplier_is_hopefix(supplier):
                # Fabory/Hopefix často vykonajú submit bez klasickej navigácie.
                # Bez no_wait_after vie click čakať na "scheduled navigations" až do timeoutu.
                await loc_submit.click(
                    no_wait_after=True,
                    timeout=click_timeout,
                )
                await asyncio.sleep(0.2)
                return
            await loc_submit.click(timeout=click_timeout)

        if _supplier_is_hopefix(supplier):
            await loc_search.fill(code)
            if config.search_pick_first_suggestion:
                await _hopefix_pick_search_autocomplete(
                    page,
                    code,
                    config,
                    run_label=run_label,
                    supplier=supplier,
                    run_id=run_id,
                )
            elif (config.search_submit_selector or "").strip():
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"search submit click: {config.search_submit_selector!r}",
                )
                await _submit_search_click(config.search_submit_selector)
            elif (config.search_submit_key or "").strip():
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"search submit key: {config.search_submit_key!r}",
                )
                await page.keyboard.press(config.search_submit_key)
        else:
            await _handle_usercentrics_cmp(
                page, run_label=run_label, supplier=supplier, run_id=run_id
            )
            await _dismiss_eu_cookie_banner_overlay(
                page, run_label=run_label, supplier=supplier, run_id=run_id
            )
            await _neutralize_blocking_cmp_overlays(
                page, run_label=run_label, supplier=supplier, run_id=run_id
            )
            try:
                await loc_search.click(timeout=6_000)
            except Exception as exc:
                # Fabory: search input môže byť síce „visible“, ale overlay/animácia
                # občas zablokuje pointer events. Fallback bez kliku, nech scrape pokračuje.
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"search input click fallback (focus bez kliku): {exc!s}",
                    "warn",
                )
                try:
                    await loc_search.focus()
                except Exception:
                    pass
            await loc_search.fill(code)

            if config.search_pick_first_suggestion:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    "search_pick_first_suggestion: ArrowDown + Enter",
                )
                await asyncio.sleep(config.search_suggestion_wait_ms / 1000.0)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.15)
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.25)

            if (config.search_submit_selector or "").strip():
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"search submit click: {config.search_submit_selector!r}",
                )
                await _submit_search_click(config.search_submit_selector)
            elif (config.search_submit_key or "").strip():
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"search submit key: {config.search_submit_key!r}",
                )
                await page.keyboard.press(config.search_submit_key)

    # SPA: namiesto dlhého pevného sleepu čakáme na prvý produkt alebo aspoň domcontentloaded.
    fp_sel = (config.first_product_link_selector or "").strip()
    # MEKRS (Nuxt): výsledky po hydrácii — príliš krátky wait → click čaká celý navigation_timeout; príliš dlhý → >10 s scrape.
    t_nav_cap = (
        11_000
        if _supplier_is_mekrs(supplier)
        else (4_400 if _supplier_is_hopefix(supplier) else 12_000)
    )
    t_nav = min(t_nav_cap, config.navigation_timeout_ms)
    if fp_sel:
        try:
            await page.locator(fp_sel).first.wait_for(state="visible", timeout=t_nav)
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"čakanie na výsledky (first_product): {exc}",
                "warn",
            )
        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=(
                    5_000
                    if _supplier_is_mekrs(supplier)
                    else (2_350 if _supplier_is_hopefix(supplier) else 8_000)
                ),
            )
        except Exception:
            pass
        tail = min(
            config.post_search_wait_ms,
            280
            if _supplier_is_mekrs(supplier)
            else (900 if _supplier_is_hopefix(supplier) else 350),
        )
        if tail > 0:
            await asyncio.sleep(tail / 1000.0)
        _log(
            run_label,
            supplier,
            run_id,
            f"post_search krátke dobehnutie {tail}ms (bol first_product selector)",
        )
    else:
        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=(
                    2_600
                    if _supplier_is_hopefix(supplier)
                    else (6_500 if _supplier_is_fabory(supplier) else 12_000)
                ),
            )
        except Exception:
            _log(
                run_label,
                supplier,
                run_id,
                "wait_for_load_state(domcontentloaded) timed out (ignored)",
                "warn",
            )
        tail_ps = config.post_search_wait_ms
        if _supplier_is_hopefix(supplier):
            # Hopefix tabuľka sa po login redirecte dopĺňa asynchrónne.
            tail_ps = min(tail_ps, 1_800)
        await asyncio.sleep(tail_ps / 1000.0)
        _log(
            run_label,
            supplier,
            run_id,
            f"post_search wait {tail_ps}ms done",
        )

    if config.first_product_link_selector:
        fp_trim = (config.first_product_link_selector or "").strip()
        _log(
            run_label,
            supplier,
            run_id,
            f"first product click: {fp_trim!r}",
        )
        nav_cap = int(config.navigation_timeout_ms)
        if _supplier_is_mekrs(supplier):
            click_timeout = min(nav_cap, 16_000)
        else:
            click_timeout = min(nav_cap, 45_000)
        loc = page.locator(fp_trim).first
        try:
            await loc.wait_for(state="visible", timeout=click_timeout)
            await loc.click(timeout=click_timeout)
        except Exception as exc:
            if _timeout_waiting_for_first_product_result(exc):
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"first_product: žiadna karta produktu v zozname (pravdepodobne kód v e-shope nie je): {exc}",
                    "warn",
                )
                raise ScraperProductNotFoundError(
                    "Tento kód v e-shope nie je k dispozícii (produkt sa v zozname výsledkov nenašiel alebo bol stiahnutý)."
                ) from exc
            raise
        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=5_000 if _supplier_is_mekrs(supplier) else 12_000,
            )
        except Exception:
            _log(run_label, supplier, run_id, "after first_product: load wait timeout (ignored)", "warn")
        cur_sel = (config.price_currency_select_selector or "").strip()
        pr_sel = (config.price_selector or "").strip()
        add_open = (
            (config.packaging_modal_open_selector or "").strip()
            or (config.add_to_cart_selector or "").strip()
        )
        pdp_cap = 6_500 if _supplier_is_mekrs(supplier) else 14_000
        pdp_timeout = min(pdp_cap, config.navigation_timeout_ms)
        pdp_ready = False
        for sel in (cur_sel, pr_sel, add_open):
            if not sel:
                continue
            try:
                await page.locator(sel).first.wait_for(
                    state="visible", timeout=pdp_timeout
                )
                pdp_ready = True
                _log(run_label, supplier, run_id, f"PDP pripravené (viditeľné {sel!r})")
                break
            except Exception:
                continue
        if not pdp_ready:
            await asyncio.sleep(0.35)
            _log(
                run_label,
                supplier,
                run_id,
                "PDP: žiadny známy selektor v čase — krátky sleep",
                "warn",
            )

    if _supplier_is_hopefix(supplier) and not (
        (config.first_product_link_selector or "").strip()
    ):
        await _hopefix_apply_product_hash_if_needed(
            page,
            code,
            config,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
        )

    await _save_step_screenshot(
        page,
        run_label=run_label,
        supplier=supplier,
        run_id=run_id,
        step="04_after_search",
    )
    _log(run_label, supplier, run_id, f"current URL: {page.url}")
    return logged_in


async def _input_dom_pack_value(loc: Locator) -> str:
    """Fabory: veľkosť balenia v HTML value / defaultValue alebo v .value po hydrácii."""
    return (
        await loc.evaluate(
            """el => {
              if (!el || el.tagName !== 'INPUT') return '';
              const t = (s) => (s == null ? '' : String(s)).trim();
              return t(el.getAttribute('value'))
                || t(el.defaultValue)
                || t(el.value)
                || '';
            }"""
        )
    ).strip()


async def _poll_input_pack_value(
    loc: Locator, *, rounds: int, delay_s: float
) -> str:
    for _ in range(rounds):
        raw = await _input_dom_pack_value(loc)
        if raw:
            return raw
        await asyncio.sleep(delay_s)
    return ""


async def _try_read_pack_quantity(
    page: Page,
    config: ScraperConfig,
    *,
    timeout: int,
    run_label: str,
    supplier: Supplier,
    run_id: str,
) -> tuple[Optional[int], Optional[str]]:
    per_sel_timeout = min(14_000, max(4000, timeout))
    candidates: list[str] = []
    for sel in (
        (config.pack_quantity_selector or "").strip(),
        (config.quantity_input_selector or "").strip(),
        "input.js-alp-add-to-cart.alp-add-to-cart",
        "input.alp-add-to-cart.js-alp-add-to-cart[data-add-to-cart-quantity]",
        "input.alp-add-to-cart[type='number']",
        "input.alp-add-to-cart",
    ):
        if sel and sel not in candidates:
            candidates.append(sel)

    last_err: Optional[str] = None
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="attached", timeout=per_sel_timeout)
        except Exception as exc:
            last_err = str(exc)
            continue
        try:
            await loc.scroll_into_view_if_needed()
        except Exception:
            pass
        raw = await _poll_input_pack_value(loc, rounds=28, delay_s=0.12)
        if not raw:
            continue
        pack_quantity = _parse_stock(raw)
        _log(
            run_label,
            supplier,
            run_id,
            f"pack_quantity ok selector={sel!r} raw={raw!r} parsed={pack_quantity}",
        )
        return pack_quantity, raw

    msg = last_err or "žiadny zhodný selektor"
    _log(
        run_label,
        supplier,
        run_id,
        f"pack_quantity: nepodarilo sa načítať ({msg})",
        "warn",
    )
    return None, None


async def _variant_row_text_from_radio(radio_loc: Locator) -> str:
    """Text bloku variantu okolo rádia (bez kliku na celý široký riadok = bez omylného košíka)."""
    return (
        await radio_loc.evaluate(
            r"""el => {
              let n = el.closest("label");
              if (n && (n.innerText || "").trim().length > 3) return (n.innerText || "").trim();
              n = el.parentElement;
              for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                const t = (n.innerText || "").trim();
                if (t.length > 15 && t.length < 1200 && (t.includes("ks") || t.includes("€") || t.includes("EUR")))
                  return t;
              }
              return (el.parentElement && el.parentElement.innerText) ? el.parentElement.innerText.trim() : "";
            }"""
        )
    ).strip()


async def _scrape_packaging_modal_variants(
    page: Page,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    product_code: str = "",
) -> tuple[list[dict[str, Any]], Optional[int], Optional[str]]:
    """Modal varianty: blok POSTov do košíka, PDP klik bez rizikového fallbacku; najprv DOM sekcia (Mekrs), potom text, potom rádia."""
    vis_sel = (config.packaging_modal_visible_selector or "").strip()
    if not vis_sel:
        return [], None, None

    row_sel = (config.packaging_modal_row_selector or "").strip()
    radio_within_row = (config.packaging_modal_radio_selector or 'input[type="radio"]').strip()
    price_within_row = (config.packaging_modal_price_selector or "").strip()
    header_sel = (config.packaging_modal_header_stock_selector or "").strip()

    unroute = await _install_packaging_cart_post_blocker(
        page, config, run_label=run_label, supplier=supplier, run_id=run_id
    )
    variants: list[dict[str, Any]] = []
    header_stock: Optional[int] = None
    header_raw: Optional[str] = None

    try:
        try:
            await _click_add_to_cart_outside_dialog(
                page,
                config,
                run_label=run_label,
                supplier=supplier,
                run_id=run_id,
                purpose="packaging modal otvorenie",
                allow_unscoped_fallback=False,
                product_code=product_code,
            )
            await asyncio.sleep(min(120, config.post_modal_open_wait_ms) / 1000.0)
            modal_vis_ms = 8_500 if _supplier_is_mekrs(supplier) else 12_000
            await page.locator(vis_sel).first.wait_for(
                state="visible", timeout=modal_vis_ms
            )
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"packaging modal: otvorenie zlyhalo: {exc}",
                "warn",
            )
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return [], None, None

        section = _mekrs_packaging_section(page)

        if header_sel:
            try:
                hdr_loc = page.locator(header_sel)
                await hdr_loc.first.wait_for(state="visible", timeout=7_000)
                hn = await hdr_loc.count()
                hdr_parts: list[str] = []
                for hi in range(hn):
                    try:
                        ht = (await hdr_loc.nth(hi).inner_text()).strip()
                        if ht:
                            hdr_parts.append(ht)
                    except Exception:
                        continue
                header_raw = "\n".join(hdr_parts).strip()
                header_stock = _parse_stock(header_raw) if header_raw else None
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"packaging modal header stock raw={header_raw!r} parsed={header_stock}",
                )
            except Exception as exc:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"packaging modal header stock: {exc}",
                    "warn",
                )

        if header_stock is None:
            try:
                if await section.count() > 0:
                    hdr_scope = section.locator("header").first
                    hr = (
                        await _mekrs_aggregate_primary_green_in_scope(hdr_scope)
                    ).strip()
                    if hr:
                        if not header_raw:
                            header_raw = hr
                        header_stock = _parse_stock(hr)
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            "packaging modal header stock (section fallback) "
                            f"raw={hr!r} parsed={header_stock}",
                        )
            except Exception as exc:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"packaging modal header stock section fallback: {exc}",
                    "warn",
                )

        dlg = page.locator('[aria-modal="true"], [role="dialog"]').first

        dom_variants = await _extract_variants_from_packaging_section(section)
        if len(dom_variants) >= 2:
            variants = dom_variants
            _log(
                run_label,
                supplier,
                run_id,
                f"packaging modal: {len(dom_variants)} variantov z DOM (sekcia variantov)",
            )
        else:
            modal_text = ""
            try:
                if await section.count() > 0:
                    modal_text = (await section.inner_text()).strip()
            except Exception as exc:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"packaging modal: inner_text sekcie: {exc}",
                    "warn",
                )
            if len(modal_text) < 40:
                try:
                    modal_text = (await dlg.inner_text()).strip()
                except Exception as exc:
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"packaging modal: inner_text dialógu: {exc}",
                        "warn",
                    )
                    modal_text = ""

            parsed = _variants_from_packaging_modal_plain_text(modal_text)
            if len(parsed) >= 2:
                variants = parsed
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"packaging modal: {len(parsed)} variantov z textu (bez klikov na rádia)",
                )
            else:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    "packaging modal: z textu menej ako 2 varianty — skúšam rádia",
                    "warn",
                )
                dialog: Locator = dlg
                try:
                    if await section.count() > 0:
                        dialog = section
                except Exception:
                    pass
                radios = dialog.locator('input[name="variant"][type="radio"]')
                n_radio = await radios.count()
                if n_radio < 1:
                    radios = dialog.locator("input[type=radio]")
                    n_radio = await radios.count()
                if n_radio < 1:
                    radios = dialog.get_by_role("radio")
                    n_radio = await radios.count()

                async def _append_variant(
                    *,
                    radio: Locator,
                    row: Optional[Locator],
                    idx: int,
                ) -> None:
                    try:
                        await radio.click(timeout=8000)
                    except Exception as exc:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"packaging modal variant {idx}: radio klik: {exc}",
                            "warn",
                        )
                        return
                    await asyncio.sleep(0.10 if _supplier_is_mekrs(supplier) else 0.18)

                    row_text = ""
                    if row is not None:
                        row_text = (await row.inner_text()).strip()
                    if len(row_text) < 5:
                        row_text = await _variant_row_text_from_radio(radio)
                    pack_qty, short_label = _parse_variant_pack_row(row_text)
                    short_label = _mekrs_sanitize_variant_label(short_label)

                    raw_price: Optional[str] = None
                    price_eur: Optional[float] = None
                    mekrs_per100_ok = False
                    if _supplier_is_mekrs(supplier) and row_text:
                        price_eur, raw_price = _mekrs_price_per_100_from_row_text(
                            row_text
                        )
                        mekrs_per100_ok = price_eur is not None
                    if price_eur is None:
                        if row is not None and price_within_row:
                            try:
                                raw_price = (
                                    await row.locator(price_within_row)
                                    .first.inner_text()
                                ).strip()
                            except Exception:
                                raw_price = None
                        if not raw_price and row is not None:
                            try:
                                raw_price = (
                                    await row.locator("span.text-primaryRed.font-bold")
                                    .first.inner_text()
                                ).strip()
                            except Exception:
                                raw_price = None
                        if not raw_price:
                            try:
                                reds = dialog.locator("span.text-primaryRed.font-bold")
                                if await reds.count() > idx:
                                    raw_price = (
                                        await reds.nth(idx).inner_text()
                                    ).strip()
                            except Exception:
                                pass
                        if raw_price:
                            price_eur = _parse_price_eur(raw_price)
                    if (
                        _supplier_is_mekrs(supplier)
                        and not mekrs_per100_ok
                        and price_eur is not None
                        and pack_qty >= 1
                    ):
                        price_eur = round(
                            float(price_eur) * 100.0 / float(pack_qty), 2
                        )
                        raw_price = f"{price_eur:.2f} € / 100 ks"
    
                    row_stock: Optional[int] = None
                    raw_row_stock: Optional[str] = None
                    if row is not None:
                        try:
                            raw_row_stock = (
                                await _mekrs_aggregate_primary_green_in_scope(row)
                            ).strip()
                            row_stock = (
                                _parse_stock(raw_row_stock)
                                if raw_row_stock
                                else None
                            )
                        except Exception:
                            pass
    
                    mpt = _mekrs_variant_package_stock_line(
                        raw_green=raw_row_stock,
                        stock_pieces=row_stock,
                        pack_quantity=pack_qty,
                    )
                    row_dict: dict[str, Any] = {
                        "label": short_label,
                        "pack_quantity": pack_qty,
                        "price_eur": price_eur,
                        "raw_price": raw_price,
                        "stock": row_stock,
                        "raw_stock": raw_row_stock,
                    }
                    if mpt:
                        row_dict["mekrs_package_stock_text"] = mpt
                    variants.append(row_dict)
    
                if n_radio >= 1:
                    for i in range(n_radio):
                        radio = radios.nth(i)
                        await _append_variant(radio=radio, row=None, idx=i)
                elif row_sel:
                    rows = page.locator(row_sel)
                    n = await rows.count()
                    for i in range(n):
                        row = rows.nth(i)
                        radio = row.locator(radio_within_row).first
                        if await radio.count() < 1:
                            _log(
                                run_label,
                                supplier,
                                run_id,
                                f"packaging modal riadok {i}: bez rádia, preskakujem",
                                "warn",
                            )
                            continue
                        await _append_variant(radio=radio, row=row, idx=i)
                else:
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        "packaging modal: žiadne rádio ani packaging_modal_row_selector",
                        "warn",
                    )

    finally:
        await unroute()

    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.06)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.06)
    except Exception:
        pass

    _log(
        run_label,
        supplier,
        run_id,
        f"packaging modal: načítaných {len(variants)} variantov",
    )
    return variants, header_stock, header_raw


async def _mekrs_stock_text_all_matches(
    page: Page,
    selector: str,
    *,
    timeout: int,
) -> str:
    """
    Mekrs má často viac zelených blokov skladu (úryvok vs celkový počet) — .first bralo len prvé číslo.
    Zbierame text zo všetkých viditeľných zhôd selektora a spojíme (ďalej _parse_stock vyberie správnu hodnotu).
    """
    wait_cap = max(2_000, min(int(timeout), 10_000))
    loc = page.locator(selector)
    await loc.first.wait_for(state="visible", timeout=wait_cap)
    n = await loc.count()
    parts: list[str] = []
    for i in range(n):
        try:
            el = loc.nth(i)
            if not await el.is_visible():
                continue
            t = (await el.inner_text()).strip()
            if t:
                parts.append(t)
        except Exception:
            continue
    if not parts:
        raise RuntimeError("mekrs stock: žiadny viditeľný text pre selektor")
    return "\n".join(parts)


async def _mekrs_aggregate_primary_green_in_scope(scope: Locator) -> str:
    """Všetky div.text-primaryGreen v scope (riadok variantu / hlavička) — nie len .first."""
    greens = scope.locator("div.text-primaryGreen")
    try:
        n = await greens.count()
    except Exception:
        return ""
    parts: list[str] = []
    for i in range(n):
        try:
            t = (await greens.nth(i).inner_text()).strip()
            if t:
                parts.append(t)
        except Exception:
            continue
    return "\n".join(parts)


async def _visible_inner_text_resilient(
    page: Page,
    selector: str,
    *,
    timeout: int,
) -> str:
    """
    Počká na viditeľný uzol, skúsi scroll (môže zlyhať pri re-renderi) a prečíta text.
    Opakuje s novým locatorom — Nuxt/MEKRS často odtrhne starý uzol medzi krokmi.
    """
    wait_cap = max(2_000, min(int(timeout), 10_000))
    last_exc: Optional[BaseException] = None
    for attempt in range(4):
        loc = page.locator(selector).first
        try:
            w = wait_cap if attempt == 0 else min(4_000, wait_cap)
            await loc.wait_for(state="visible", timeout=w)
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(0.22)
            continue
        try:
            await loc.scroll_into_view_if_needed(timeout=3_500)
        except Exception:
            pass
        try:
            text = (await loc.inner_text()).strip()
            if text:
                return text
            last_exc = ValueError("prázdny text po inner_text")
        except Exception as exc:
            last_exc = exc
        await asyncio.sleep(0.22)
    if last_exc:
        raise last_exc
    raise RuntimeError("inner_text: neznáma chyba")


async def _read_pdp_product_title(
    page: Page,
    config: ScraperConfig,
    *,
    supplier: Supplier,
    run_label: str,
    run_id: str,
) -> Optional[str]:
    """Text názvu z PDP (Fabory: <h1>…</h1>) — pre zobrazenie vo fronte bez modalu."""
    custom = (config.product_title_selector or "").strip()
    candidates: list[str] = []
    if custom:
        candidates.append(custom)
    elif _supplier_is_fabory(supplier):
        candidates.extend(
            (
                "main h1",
                "article h1",
                ".product-detail h1",
                "[itemprop=name]",
                "h1",
            )
        )
    else:
        return None
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await loc.count() < 1:
                continue
            try:
                await loc.wait_for(state="visible", timeout=4_000)
            except Exception:
                pass
            t = (await loc.inner_text()).strip()
            t = re.sub(r"\s+", " ", t)
            if len(t) >= 3:
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"PDP product_title selector={sel!r} len={len(t)}",
                )
                return t[:500]
        except Exception as exc:
            _log(
                run_label,
                supplier,
                run_id,
                f"PDP product_title {sel!r}: {exc!s}",
                "warn",
            )
            continue
    return None


async def _read_price_stock(
    page: Page,
    config: ScraperConfig,
    *,
    run_label: str,
    supplier: Supplier,
    run_id: str,
    skip_currency: bool = False,
    skip_price: bool = False,
    skip_stock: bool = False,
    skip_pack: bool = False,
) -> dict[str, Any]:
    raw_price: Optional[str] = None
    raw_stock: Optional[str] = None
    raw_pack_quantity: Optional[str] = None
    price_eur: Optional[float] = None
    stock: Optional[int] = None
    pack_quantity: Optional[int] = None
    timeout = max(1000, config.price_stock_timeout_ms)
    if _supplier_is_hopefix(supplier):
        timeout = min(timeout, 4_500)

    if not skip_currency:
        await _maybe_select_price_currency(
            page,
            config,
            timeout=timeout,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
        )

    if not skip_price and config.price_selector:
        try:
            raw_price = await _visible_inner_text_resilient(
                page, config.price_selector, timeout=timeout
            )
            price_eur = _parse_price_eur(raw_price)
            _log(
                run_label,
                supplier,
                run_id,
                f"price ok selector={config.price_selector!r} raw={raw_price!r} parsed={price_eur}",
            )
        except Exception as exc:
            raw_price = None
            _log(
                run_label,
                supplier,
                run_id,
                f"price FAILED selector={config.price_selector!r}: {exc}",
                "warn",
            )

    if not skip_stock and config.stock_selector:
        try:
            if _supplier_is_mekrs(supplier):
                raw_stock = await _mekrs_stock_text_all_matches(
                    page,
                    config.stock_selector,
                    timeout=timeout,
                )
            else:
                raw_stock = await _visible_inner_text_resilient(
                    page, config.stock_selector, timeout=timeout
                )
            stock = _parse_stock(raw_stock)
            _log(
                run_label,
                supplier,
                run_id,
                f"stock ok selector={config.stock_selector!r} raw={raw_stock!r} parsed={stock}",
            )
        except Exception as exc:
            raw_stock = None
            _log(
                run_label,
                supplier,
                run_id,
                f"stock FAILED selector={config.stock_selector!r}: {exc}",
                "warn",
            )

    if not skip_pack and (
        (config.pack_quantity_selector or "").strip()
        or (config.quantity_input_selector or "").strip()
    ):
        pq, rw = await _try_read_pack_quantity(
            page,
            config,
            timeout=timeout,
            run_label=run_label,
            supplier=supplier,
            run_id=run_id,
        )
        if pq is not None or (rw and rw.strip()):
            pack_quantity = pq
            raw_pack_quantity = rw

    return {
        "price_eur": price_eur,
        "stock": stock,
        "pack_quantity": pack_quantity,
        "raw_price": raw_price,
        "raw_stock": raw_stock,
        "raw_pack_quantity": raw_pack_quantity,
    }


def _run_async_on_windows_proactor(coro: Any) -> Any:
    """Playwright na Windows potrebuje ProactorEventLoop; Uvicorn často spustí iný loop → NotImplementedError."""
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)


class ScraperService:
    @staticmethod
    def invalidate_remote_cart_cache(
        supplier_id: Optional[int],
        *,
        user_id: Optional[int] = None,
    ) -> None:
        """Zahodiť cache vzdialeného košíka pre dodávateľa (napr. po pridaní položky)."""
        _invalidate_remote_cart_cache(supplier_id, user_id)

    @staticmethod
    def clear_all_remote_cart_caches() -> None:
        """Zahodiť celú cache vzdialených košíkov (napr. ?refresh=1 na prehľade)."""
        _clear_all_remote_cart_caches()

    @staticmethod
    async def get_supplier_data(
        supplier: Supplier,
        product_code: str,
        config: ScraperConfig,
        *,
        automation_user_id: int = 0,
    ) -> dict[str, Any]:
        """Prihlási sa, vyhľadá produkt, vráti cenu a sklad (ak sú v JSON selektory)."""
        if _dry_run():
            run_id = _run_id()
            run_label = f"scrape:{supplier.id}:{_slug(supplier.name)}"
            _log(run_label, supplier, run_id, "CART_AUTOMATION_DRY_RUN: mock supplier data")
            await asyncio.sleep(0.05)
            return {
                "price_eur": 0.41,
                "stock": 450,
                "pack_quantity": 100,
                "raw_price": "0,41 EUR (dry-run)",
                "raw_stock": "450 ks (dry-run)",
                "raw_pack_quantity": "100",
                "logged_in": True,
            }

        run_id = _run_id()
        run_label = f"scrape:{supplier.id}:{_slug(supplier.name)}"
        _log(
            run_label,
            supplier,
            run_id,
            f"get_supplier_data start supplier={supplier.name!r} id={supplier.id} code={product_code!r}",
        )

        if _supplier_is_hopefix(supplier) and _hopefix_http_enabled(config):
            try:
                hf = await _hopefix_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    config,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Hopefix HTTP): {hf}",
                )
                return hf
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Hopefix HTTP zlyhalo, pokračujem Playwright: {exc}",
                    "warn",
                )

        if _supplier_is_haspl(supplier):
            try:
                hp = await _haspl_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Haspl HTTP): {hp}",
                )
                return hp
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Haspl HTTP zlyhalo, pokračujem Playwright: {exc}",
                    "warn",
                )

        if _supplier_is_bmkco(supplier):
            try:
                bm = await _bmkco_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (BMCo HTTP): {bm}",
                )
                return bm
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"BMCo HTTP zlyhalo, pokračujem Playwright: {exc}",
                    "warn",
                )

        if _supplier_is_halfmann(supplier):
            try:
                hfm = await _halfmann_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Halfmann HTTP): {hfm}",
                )
                return hfm
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Halfmann HTTP zlyhalo, pokračujem Playwright: {exc}",
                    "warn",
                )

        if _supplier_is_inoxmare(supplier):
            try:
                ix = await _inoxmare_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    config,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Inoxmare HTTP): {ix}",
                )
                return ix
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Inoxmare HTTP zlyhalo, pokračujem Playwright: {exc}",
                    "warn",
                )

        if _supplier_is_argip(supplier):
            try:
                ag = await _argip_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Argip HTTP): {ag}",
                )
                return ag
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Argip HTTP zlyhalo: {exc}",
                    "error",
                )
                raise RuntimeError(f"Argip HTTP zlyhalo: {exc}") from exc

        if _supplier_is_schachermayer(supplier):
            try:
                sch = await _schachermayer_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    config,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Schachermayer HTTP): {sch}",
                )
                return sch
            except ScraperProductNotFoundError:
                raise
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Schachermayer HTTP zlyhalo: {exc}",
                    "error",
                )
                raise RuntimeError(f"Schachermayer HTTP zlyhalo: {exc}") from exc

        if _supplier_is_valenta(supplier):
            try:
                val = await _valenta_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (Valenta HTTP): {val}",
                )
                return val
            except ScraperProductNotFoundError:
                raise
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Valenta HTTP zlyhalo: {exc}",
                    "error",
                )
                raise RuntimeError(f"Valenta HTTP zlyhalo: {exc}") from exc

        if _supplier_is_mekrs(supplier):
            try:
                data_http = await _mekrs_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"get_supplier_data done (HTTP): {data_http}",
                )
                return data_http
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                _log(
                    run_label,
                    supplier,
                    run_id,
                    f"Mekrs HTTP zlyhalo, pokračujem Playwright: {exc}",
                    "warn",
                )

        async def _playwright_flow() -> dict[str, Any]:
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        **_chromium_launch_kwargs(config, supplier)
                    )
                    try:
                        context = await _new_browser_context(
                            browser, supplier, automation_user_id
                        )
                        await _apply_playwright_stealth_if_enabled(
                            context,
                            config,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                        )
                        page = await context.new_page()
                        page.set_default_timeout(config.navigation_timeout_ms)
                        if _should_block_heavy_assets(config):
                            await _install_heavy_asset_blocker(page)
                        login_diagnostic: dict[str, Any] = {}
                        logged_in = await _login_and_search(
                            page,
                            supplier,
                            config,
                            product_code,
                            run_label=run_label,
                            run_id=run_id,
                            login_diagnostic=login_diagnostic,
                            storage_user_id=automation_user_id,
                        )
                        timeout = max(1000, config.price_stock_timeout_ms)
                        if _supplier_is_hopefix(supplier):
                            timeout = min(timeout, 4_500)
                        await _maybe_select_price_currency(
                            page,
                            config,
                            timeout=timeout,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                        )
                        if _supplier_is_mekrs(supplier):
                            await asyncio.sleep(0.28)

                        packaging_variants: list[dict[str, Any]] = []
                        modal_header_stock: Optional[int] = None
                        modal_header_raw: Optional[str] = None
                        if (config.packaging_modal_visible_selector or "").strip():
                            (
                                packaging_variants,
                                modal_header_stock,
                                modal_header_raw,
                            ) = await _scrape_packaging_modal_variants(
                                page,
                                config,
                                run_label=run_label,
                                supplier=supplier,
                                run_id=run_id,
                                product_code=product_code,
                            )
                            if _supplier_is_mekrs(supplier) and packaging_variants:
                                await _maybe_select_price_currency(
                                    page,
                                    config,
                                    timeout=timeout,
                                    run_label=run_label,
                                    supplier=supplier,
                                    run_id=run_id,
                                )

                        multi_v = len(packaging_variants) > 1
                        skip_pdp_stock = (multi_v and modal_header_stock is not None) or (
                            _supplier_is_mekrs(supplier) and len(packaging_variants) > 0
                        )
                        data = await _read_price_stock(
                            page,
                            config,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                            skip_currency=True,
                            skip_price=multi_v,
                            skip_stock=skip_pdp_stock,
                            skip_pack=multi_v,
                        )
                        if packaging_variants:
                            v0 = packaging_variants[0]
                            if v0.get("price_eur") is not None:
                                data["price_eur"] = v0["price_eur"]
                            if v0.get("raw_price"):
                                data["raw_price"] = v0["raw_price"]
                            pq0 = v0.get("pack_quantity")
                            if isinstance(pq0, int) and pq0 >= 1:
                                data["pack_quantity"] = pq0
                                data["raw_pack_quantity"] = str(pq0)
                            if len(packaging_variants) > 1:
                                data["packaging_variants"] = packaging_variants
                            elif len(packaging_variants) == 1 and _supplier_is_fabory(
                                supplier
                            ):
                                # Fabory: jeden riadok z modalu — front-end zobrazí názov bez tabuľky (Mekrs má tabuľku).
                                data["packaging_variants"] = packaging_variants
                            if _supplier_is_mekrs(supplier):
                                _mekrs_apply_total_stock_for_display(
                                    data,
                                    modal_header_raw=modal_header_raw,
                                    modal_header_stock=modal_header_stock,
                                    packaging_variants=packaging_variants,
                                )
                                _mekrs_tag_price_unit_per_100(data)
                            else:
                                if modal_header_stock is not None:
                                    data["stock"] = modal_header_stock
                                    data["raw_stock"] = modal_header_raw
                        if _supplier_is_hopefix(supplier):
                            hf_tbl = await _hopefix_scrape_offer_table_row(
                                page,
                                product_code,
                                config,
                                run_label=run_label,
                                supplier=supplier,
                                run_id=run_id,
                                timeout=max(1000, config.price_stock_timeout_ms),
                            )
                            th = hf_tbl.pop("hopefix_table_hint", None)
                            if th:
                                if not data.get("hint"):
                                    data["hint"] = th
                            oos_hf = hopefix_row_is_oos(hf_tbl)
                            for fld in (
                                "price_eur",
                                "raw_price",
                                "stock",
                                "raw_stock",
                                "pack_quantity",
                                "raw_pack_quantity",
                                "currency_code",
                                "currency_symbol",
                                "price_unit",
                            ):
                                if fld not in hf_tbl:
                                    continue
                                v = hf_tbl[fld]
                                if v is None:
                                    continue
                                if (
                                    fld in ("price_eur", "stock")
                                    and data.get(fld) is not None
                                    and not oos_hf
                                ):
                                    continue
                                data[fld] = v
                            _hopefix_normalize_oos_display(data)
                            _hopefix_ensure_packaging_variants_row(
                                data, product_code, config
                            )
                        data["logged_in"] = logged_in
                        if not logged_in:
                            extra_parts: list[str] = []
                            if login_diagnostic.get("page_error"):
                                extra_parts.append(
                                    f"Text na prihlasovacej stránke: {login_diagnostic['page_error']}"
                                )
                            if login_diagnostic.get("spring_redirect"):
                                extra_parts.append(
                                    f"Location po POST: {login_diagnostic['spring_redirect']}"
                                )
                            elif login_diagnostic.get("spring_status") is not None:
                                extra_parts.append(
                                    f"HTTP stav odpovede: {login_diagnostic['spring_status']}"
                                )
                            data["login_hint"] = _login_failure_hint(
                                "\n".join(extra_parts) if extra_parts else None
                            )
                        if (
                            logged_in
                            and
                            data.get("price_eur") is None
                            and data.get("stock") is None
                            and (config.price_selector or config.stock_selector)
                            and not config.first_product_link_selector
                        ):
                            data["hint"] = (
                                "Cena/sklad sa nenašli – po vyhľadávaní si často treba otvoriť detail produktu. "
                                "Doplň do JSON pole „first_product_link_selector“ (napr. prvý odkaz v zozname výsledkov). "
                                "Prípadne skús „search_pick_first_suggestion“: true pri comboboxe."
                            )
                        elif (
                            logged_in
                            and
                            data.get("pack_quantity") is None
                            and not (data.get("raw_pack_quantity") or "").strip()
                            and (
                                (config.pack_quantity_selector or "").strip()
                                or (config.quantity_input_selector or "").strip()
                            )
                            and not config.first_product_link_selector
                        ):
                            data["hint"] = (
                                "Veľkosť balenia (value v poli množstva) sa nenašla – si pravdepodobne na zozname "
                                "výsledkov, nie na stránke produktu. Doplň „first_product_link_selector“ "
                                "(klik na prvý výsledok), prípadne zvýš „post_search_wait_ms“."
                            )
                        if _supplier_is_mekrs(supplier):
                            _mekrs_strip_prices_when_zero_stock(data)
                        if _supplier_is_fabory(supplier) and not (
                            data.get("product_title") or ""
                        ).strip():
                            ptitle = await _read_pdp_product_title(
                                page,
                                config,
                                supplier=supplier,
                                run_label=run_label,
                                run_id=run_id,
                            )
                            if ptitle:
                                data["product_title"] = ptitle
                        _log(run_label, supplier, run_id, f"get_supplier_data done: {data}")
                        await _persist_scraper_storage_state(
                            context,
                            supplier,
                            run_label=run_label,
                            run_id=run_id,
                            logged_in=bool(logged_in),
                            automation_user_id=automation_user_id,
                        )
                        return data
                    finally:
                        await browser.close()
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                raise

        if sys.platform == "win32":

            def _thread_entry() -> dict[str, Any]:
                return _run_async_on_windows_proactor(_playwright_flow())

            return await asyncio.to_thread(_thread_entry)
        return await _playwright_flow()

    @staticmethod
    async def add_to_cart(
        supplier: Supplier,
        product_code: str,
        quantity: int,
        config: ScraperConfig,
        packaging_variant_index: Optional[int] = None,
        mekrs_product_variant_id: Optional[str] = None,
        hopefix_product_id: Optional[str] = None,
        hopefix_package_type: Optional[str] = None,
        haspl_variant_code: Optional[str] = None,
        inoxmare_product_id: Optional[str] = None,
        inoxmare_referer_path: Optional[str] = None,
        *,
        automation_user_id: int = 0,
    ) -> None:
        """Prihlási sa, vyhľadá produkt, nastaví množstvo (ak je selector) a klikne pridať do košíka."""
        if quantity < 1:
            raise ValueError("Množstvo musí byť aspoň 1.")
        search_sel = (config.search_input_selector or "").strip()
        search_url_tmpl = (config.search_via_url_template or "").strip()
        add_sel = (config.add_to_cart_selector or "").strip()
        is_mekrs = _supplier_is_mekrs(supplier)
        is_haspl = _supplier_is_haspl(supplier)
        is_bmkco = _supplier_is_bmkco(supplier)
        is_halfmann = _supplier_is_halfmann(supplier)
        is_argip = _supplier_is_argip(supplier)
        is_schachermayer = _supplier_is_schachermayer(supplier)
        is_valenta = _supplier_is_valenta(supplier)
        hopefix_http = _supplier_is_hopefix(supplier) and _hopefix_http_enabled(config)
        inoxmare_http = _supplier_is_inoxmare(supplier)
        if (
            not is_mekrs
            and not hopefix_http
            and not is_haspl
            and not is_bmkco
            and not is_halfmann
            and not is_argip
            and not is_schachermayer
            and not is_valenta
            and not inoxmare_http
        ):
            if not search_sel and not search_url_tmpl:
                raise ValueError(
                    "V cart_config_json chýba search_input_selector alebo search_via_url_template — "
                    "bez vyhľadania produktu košík nejde."
                )
            if not add_sel:
                raise ValueError(
                    "V cart_config_json chýba add_to_cart_selector — zatiaľ máš len prihlásenie; "
                    "košík doplníme neskôr."
                )

        if _dry_run():
            run_id = _run_id()
            run_label = f"cart:{supplier.id}:{_slug(supplier.name)}"
            _log(run_label, supplier, run_id, "CART_AUTOMATION_DRY_RUN: skip real browser")
            await asyncio.sleep(0.05)
            return

        run_id = _run_id()
        run_label = f"cart:{supplier.id}:{_slug(supplier.name)}"
        _log(
            run_label,
            supplier,
            run_id,
            f"add_to_cart start supplier={supplier.name!r} id={supplier.id} code={product_code!r} qty={quantity}",
        )

        if hopefix_http:
            code_hf = (product_code or "").strip()
            if not code_hf:
                raise ValueError("Prázdny kód produktu.")
            cat_url = build_hopefix_catalog_url(
                config.hopefix_catalog_url_template or "", code_hf
            )
            ref_path = urlparse(cat_url).path or "/"
            pid_hf = (hopefix_product_id or "").strip()
            pkg_hf = (hopefix_package_type or "").strip() or (
                (config.hopefix_default_package_type or "box").strip() or "box"
            )
            if not pid_hf:
                data_hf = await _hopefix_get_supplier_data_via_http(
                    supplier,
                    code_hf,
                    config,
                    run_label=run_label,
                    run_id=run_id,
                )
                pvars_hf = list(data_hf.get("packaging_variants") or [])
                if not pvars_hf:
                    raise RuntimeError(
                        "Hopefix HTTP: zo scrapu neprišli žiadne varianty — skontroluj kód a kategóriu."
                    )
                vidx = (
                    int(packaging_variant_index)
                    if packaging_variant_index is not None
                    else 0
                )
                vidx = max(0, min(vidx, len(pvars_hf) - 1))
                row_hf = pvars_hf[vidx]
                pid_hf = str(row_hf.get("hopefix_product_id") or "").strip()
                p2 = str(row_hf.get("hopefix_package_type") or "").strip()
                if p2:
                    pkg_hf = p2
            if not pid_hf:
                raise RuntimeError(
                    "Hopefix: chýba product_id (v HTML riadku alebo po rozbalení) — bez neho API košík nepridá."
                )
            nr_api = hopefix_norm_code(code_hf)
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart Hopefix HTTP product_id={pid_hf!r} qty={quantity} "
                f"package_type={pkg_hf!r} referer={ref_path!r}",
            )

            async def _hopefix_http_cart() -> None:
                async with HopefixHttpClient() as client:
                    await client.ensure_login(supplier.username, supplier.password)
                    await client.add_to_cart(
                        product_nr=nr_api,
                        product_id=pid_hf,
                        quantity=quantity,
                        package_type=pkg_hf,
                        referer_path=ref_path,
                    )

            await _hopefix_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Hopefix HTTP)")
            return

        if is_mekrs:
            vid = (mekrs_product_variant_id or "").strip()
            referer_path = "/"
            row: Optional[dict[str, Any]] = None
            if not vid:
                data = await _mekrs_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                pvars = list(data.get("packaging_variants") or [])
                if not pvars:
                    raise RuntimeError(
                        "Mekrs: z API neprišli žiadne varianty balenia — nedá sa vybrať riadok do košíka."
                    )
                idx = (
                    int(packaging_variant_index)
                    if packaging_variant_index is not None
                    else 0
                )
                idx = max(0, min(idx, len(pvars) - 1))
                row = pvars[idx]
                vid = str(row.get("mekrs_variant_id") or "").strip()
                slug = str(row.get("mekrs_product_slug") or "").strip()
                if slug:
                    referer_path = f"/produkty/{slug}"
                if not vid:
                    raise RuntimeError(
                        f"Mekrs: variant na indexe {idx} nemá mekrs_variant_id — "
                        "obnov ceny (scrape) alebo vyber iné balenie."
                    )
            else:
                referer_path = "/produkty"

            qty_requested = int(quantity)

            async def _mekrs_http_cart() -> None:
                async with MekrsHttpClient() as client:
                    await client.ensure_session(supplier.username, supplier.password)
                    vs: Optional[int] = None
                    if row is not None:
                        rvs = row.get("mekrs_variant_stock")
                        if isinstance(rvs, int):
                            vs = rvs
                    if vs is None and (product_code or "").strip():
                        vs = await client.stock_level_for_variant(
                            product_code=product_code.strip(),
                            variant_id=vid,
                        )
                    q = qty_requested
                    if isinstance(vs, int) and vs >= 1 and q > vs:
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"Mekrs: požadované množstvo {q} znížené na sklad variantu {vs} ks",
                            "warn",
                        )
                        q = vs
                    if isinstance(vs, int) and vs < 1 and q >= 1:
                        raise RuntimeError(
                            "Mekrs: vybraný variant nemá sklad (0 ks) — nedá sa pridať do košíka."
                        )
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"add_to_cart Mekrs HTTP variant_id={vid!r} qty={q} referer={referer_path!r}",
                    )
                    await client.add_to_cart(vid, q, referer_path=referer_path)

            await _mekrs_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Mekrs HTTP)")
            return

        if is_haspl:
            hc = (haspl_variant_code or "").strip()
            row_hp: Optional[dict[str, Any]] = None
            if not hc:
                data_hp = await _haspl_get_supplier_data_via_http(
                    supplier,
                    product_code,
                    run_label=run_label,
                    run_id=run_id,
                )
                pvars_hp = list(data_hp.get("packaging_variants") or [])
                if not pvars_hp:
                    raise RuntimeError(
                        "Haspl HTTP: z API neprišli žiadne varianty — skontroluj kód dodávateľa."
                    )
                vidx = (
                    int(packaging_variant_index)
                    if packaging_variant_index is not None
                    else 0
                )
                vidx = max(0, min(vidx, len(pvars_hp) - 1))
                row_hp = pvars_hp[vidx]
                hc = str(row_hp.get("haspl_variant_code") or "").strip()
            if not hc:
                raise RuntimeError(
                    "Haspl: chýba kód variantu — obnov ceny alebo vyber konkrétne balenie."
                )
            pack_q = 1
            if row_hp is not None:
                pq0 = row_hp.get("pack_quantity")
                if isinstance(pq0, int) and pq0 >= 1:
                    pack_q = pq0
            need_pack_lookup = row_hp is None

            async def _haspl_http_cart() -> None:
                base_h = haspl_base_url(supplier.shop_url or "")
                async with HasplHttpClient(base_url=base_h) as hclient:
                    await hclient.ensure_session(supplier.username, supplier.password)
                    pq_eff = pack_q
                    if need_pack_lookup:
                        mem = await hclient.fetch_variants_by_supplier_code(product_code)
                        for m in mem:
                            if str(m.get("code") or "").strip() == hc:
                                pq_eff = haspl_variant_pack_quantity(m)
                                break
                    packs = haspl_pieces_to_pack_units(int(quantity), pq_eff)
                    _log(
                        run_label,
                        supplier,
                        run_id,
                        f"add_to_cart Haspl HTTP variant={hc!r} packs={packs} "
                        f"(ks={quantity}, ks/bal={pq_eff})",
                    )
                    await hclient.add_to_cart(
                        variant_code=hc, quantity_packs=packs
                    )

            await _haspl_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Haspl HTTP)")
            return

        if is_bmkco:
            code_bm = bmkco_norm_code(product_code)
            if not code_bm:
                raise ValueError("BMCo: prázdny kód produktu (karta).")
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart BMCo HTTP karta={code_bm!r} qty={quantity}",
            )

            async def _bmkco_http_cart() -> None:
                base_bm = bmkco_base_url(supplier.shop_url or "")
                async with BmkcoHttpClient(base_bm) as bclient:
                    await bclient.ensure_login(supplier.username, supplier.password)
                    await bclient.add_to_cart(code_bm, int(quantity))

            await _bmkco_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (BMCo HTTP)")
            return

        if is_halfmann:
            artid_hf = halfmann_norm_artid(product_code)
            if not artid_hf:
                raise ValueError("Halfmann: prázdne artid produktu.")
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart Halfmann HTTP artid={artid_hf!r} qty={quantity}",
            )

            async def _halfmann_http_cart() -> None:
                base_hf = halfmann_base_url(supplier.shop_url or "")
                async with HalfmannHttpClient(base_hf) as hclient:
                    await hclient.ensure_login(supplier.username, supplier.password)
                    await hclient.add_to_cart(artid_hf, int(quantity))

            await _halfmann_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Halfmann HTTP)")
            return

        if is_argip:
            code_ag = (product_code or "").strip()
            if not code_ag:
                raise ValueError("Argip: prázdny kód produktu.")
            sku_ag = code_ag
            if packaging_variant_index is not None:
                data_ag = await _argip_get_supplier_data_via_http(
                    supplier,
                    code_ag,
                    run_label=run_label,
                    run_id=run_id,
                )
                pvars_ag = list(data_ag.get("packaging_variants") or [])
                if pvars_ag:
                    idx_ag = max(0, min(int(packaging_variant_index), len(pvars_ag) - 1))
                    row_ag = pvars_ag[idx_ag]
                    sku_ag = str(row_ag.get("argip_sku") or "").strip() or sku_ag
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart Argip HTTP sku={sku_ag!r} qty={quantity}",
            )

            async def _argip_http_cart() -> None:
                async with ArgipHttpClient(shop_url=supplier.shop_url or "") as aclient:
                    await aclient.ensure_login(supplier.username, supplier.password)
                    await aclient.add_to_cart(sku_ag, int(quantity))

            await _argip_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Argip HTTP)")
            return

        if is_schachermayer:
            code_sch = (product_code or "").strip()
            if not code_sch:
                raise ValueError("Schachermayer: prázdny kód produktu.")
            cat_ov = (config.schachermayer_catalog_id or "").strip() or None
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart Schachermayer HTTP code={code_sch!r} qty={quantity}",
            )

            async def _schachermayer_http_cart() -> None:
                async with SchachermayerHttpClient(
                    supplier.shop_url or ""
                ) as sclient:
                    await sclient.ensure_login(supplier.username, supplier.password)
                    await sclient.add_to_cart_for_product_code(
                        code_sch,
                        int(quantity),
                        catalog_override=cat_ov,
                    )

            await _schachermayer_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Schachermayer HTTP)")
            return

        if is_valenta:
            code_va = (product_code or "").strip()
            if not code_va:
                raise ValueError("Valenta: prázdny kód produktu.")
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart Valenta HTTP code={code_va!r} qty={quantity}",
            )

            async def _valenta_http_cart() -> None:
                async with ValentaHttpClient(supplier.shop_url or "") as vclient:
                    await vclient.ensure_login(supplier.username, supplier.password)
                    row = await vclient.fetch_product_data(code_va)
                    pid = str(row.get("product_id") or "").strip()
                    action = str(row.get("form_action_abs") or "").strip()
                    if not pid or not action:
                        raise RuntimeError("Valenta: po vyhľadaní chýba formulár add-to-cart.")
                    await vclient.add_to_cart(
                        product_id=pid,
                        form_action_abs=action,
                        quantity=int(quantity),
                    )

            await _valenta_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Valenta HTTP)")
            return

        if inoxmare_http:
            code_ix = (product_code or "").strip()
            if not code_ix:
                raise ValueError("Prázdny kód produktu.")
            pid_ix = (inoxmare_product_id or "").strip()
            path_ix = (inoxmare_referer_path or "").strip()
            if not pid_ix or not path_ix:
                data_ix = await _inoxmare_get_supplier_data_via_http(
                    supplier,
                    code_ix,
                    config,
                    run_label=run_label,
                    run_id=run_id,
                )
                pvars_ix = list(data_ix.get("packaging_variants") or [])
                if not pvars_ix:
                    raise RuntimeError(
                        "Inoxmare HTTP: zo scrapu neprišli údaje produktu."
                    )
                vix = (
                    int(packaging_variant_index)
                    if packaging_variant_index is not None
                    else 0
                )
                vix = max(0, min(vix, len(pvars_ix) - 1))
                row_ix = pvars_ix[vix]
                pid_ix = str(row_ix.get("inoxmare_product_id") or "").strip()
                path_ix = str(row_ix.get("inoxmare_referer_path") or "").strip()
            elif packaging_variant_index is not None and int(packaging_variant_index) > 0:
                data_ix2 = await _inoxmare_get_supplier_data_via_http(
                    supplier,
                    code_ix,
                    config,
                    run_label=run_label,
                    run_id=run_id,
                )
                p2 = list(data_ix2.get("packaging_variants") or [])
                vix = int(packaging_variant_index)
                vix = max(0, min(vix, len(p2) - 1)) if p2 else 0
                if p2:
                    row2 = p2[vix]
                    pid_ix = str(row2.get("inoxmare_product_id") or "").strip()
                    path_ix = str(row2.get("inoxmare_referer_path") or "").strip()
            if not pid_ix or not path_ix:
                raise RuntimeError(
                    "Inoxmare: chýba product_id alebo cesta PDP — obnov ceny v UI."
                )
            _log(
                run_label,
                supplier,
                run_id,
                f"add_to_cart Inoxmare HTTP product_id={pid_ix!r} qty={quantity} path={path_ix!r}",
            )

            async def _inoxmare_http_cart() -> None:
                ix_ck = (config.inoxmare_session_cookie_header or "").strip()
                async with InoxmareHttpClient(
                    supplier.shop_url or "",
                    config.inoxmare_store_path,
                    manual_cookie_header=ix_ck or None,
                ) as iclient:
                    await iclient.ensure_login(supplier.username, supplier.password)
                    await iclient.add_to_cart(
                        product_id=pid_ix,
                        quantity=quantity,
                        product_path_for_context=path_ix,
                        custom_price=None,
                    )

            await _inoxmare_http_cart()
            _log(run_label, supplier, run_id, "add_to_cart done (Inoxmare HTTP)")
            return

        async def _playwright_flow() -> None:
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        **_chromium_launch_kwargs(config, supplier)
                    )
                    try:
                        context = await _new_browser_context(
                            browser, supplier, automation_user_id
                        )
                        await _apply_playwright_stealth_if_enabled(
                            context,
                            config,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                        )
                        page = await context.new_page()
                        page.set_default_timeout(config.navigation_timeout_ms)
                        if _should_block_heavy_assets(config):
                            await _install_heavy_asset_blocker(page)
                        logged_in = await _login_and_search(
                            page,
                            supplier,
                            config,
                            product_code,
                            run_label=run_label,
                            run_id=run_id,
                            storage_user_id=automation_user_id,
                        )
                        if not logged_in:
                            raise RuntimeError(
                                "Prihlásenie na e-shop nebolo úspešné (relácia nie je prihlásená). "
                                "Mekrs často nezobrazí tlačidlo „Vložit do košíku“ na PDP bez platného účtu."
                            )

                        await _maybe_select_price_currency(
                            page,
                            config,
                            timeout=max(1000, config.price_stock_timeout_ms),
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                        )

                        if _supplier_is_mekrs(supplier):
                            cta_wait_ms = min(
                                30_000, max(20_000, int(config.navigation_timeout_ms))
                            )
                            try:
                                loc_cta = page.locator(add_sel)
                                if await loc_cta.count() > 0:
                                    try:
                                        await loc_cta.first.scroll_into_view_if_needed(
                                            timeout=5_000
                                        )
                                    except Exception:
                                        pass
                                await loc_cta.first.wait_for(
                                    state="visible", timeout=cta_wait_ms
                                )
                            except Exception as exc:
                                raise RuntimeError(
                                    f"Mekrs PDP: tlačidlo do košíka ({add_sel!r}) nie je viditeľné "
                                    f"do {cta_wait_ms} ms (url={page.url!r}). Skontroluj kód produktu "
                                    f"a či vyhľadanie otvorilo detail produktu, nie len zoznam."
                                ) from exc

                        if _supplier_is_hopefix(supplier):
                            await _hopefix_ensure_add_to_cart_visible(
                                page,
                                product_code,
                                add_sel,
                                run_label=run_label,
                                supplier=supplier,
                                run_id=run_id,
                                timeout=min(
                                    20_000, int(config.navigation_timeout_ms)
                                ),
                            )

                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"click add_to_cart (open modal?): {add_sel!r}",
                        )
                        await _click_add_to_cart_outside_dialog(
                            page,
                            config,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                            purpose="add_to_cart otvorenie modalu",
                            product_code=product_code,
                        )
                        await asyncio.sleep(config.post_modal_open_wait_ms / 1000.0)

                        if packaging_variant_index is not None:
                            vis = (config.packaging_modal_visible_selector or "").strip()
                            row_sel = (config.packaging_modal_row_selector or "").strip()
                            radio_wr = (
                                config.packaging_modal_radio_selector or 'input[type="radio"]'
                            ).strip()
                            if not vis:
                                _log(
                                    run_label,
                                    supplier,
                                    run_id,
                                    "packaging_variant_index vyžaduje packaging_modal_visible_selector v JSON",
                                    "warn",
                                )
                            else:
                                try:
                                    await page.locator(vis).first.wait_for(
                                        state="visible", timeout=15_000
                                    )
                                    if row_sel:
                                        await page.locator(row_sel).nth(
                                            packaging_variant_index
                                        ).locator(radio_wr).first.click(timeout=10_000)
                                    else:
                                        sec = _mekrs_packaging_section(page)
                                        if await sec.count() < 1:
                                            raise RuntimeError(
                                                "sekciu variantov (H2 Vyberte variantu) sa nepodarilo nájsť"
                                            )
                                        radios = sec.locator(
                                            'input[name="variant"][type="radio"]'
                                        )
                                        nrd = await radios.count()
                                        if packaging_variant_index >= nrd:
                                            raise RuntimeError(
                                                f"index variantu {packaging_variant_index} mimo rozsahu ({nrd} rádii)"
                                            )
                                        await radios.nth(packaging_variant_index).click(
                                            timeout=10_000
                                        )
                                    await asyncio.sleep(
                                        0.10 if _supplier_is_mekrs(supplier) else 0.18
                                    )
                                    _log(
                                        run_label,
                                        supplier,
                                        run_id,
                                        f"packaging variant vybraný index={packaging_variant_index}",
                                    )
                                except Exception as exc:
                                    _log(
                                        run_label,
                                        supplier,
                                        run_id,
                                        f"výber variantu {packaging_variant_index}: {exc}",
                                        "warn",
                                    )

                        await _fill_cart_quantity_input(
                            page,
                            config,
                            quantity,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                        )

                        if config.add_to_cart_confirm_selector:
                            await _click_confirm_add_to_cart(
                                page,
                                config,
                                run_label=run_label,
                                supplier=supplier,
                                run_id=run_id,
                            )

                        await asyncio.sleep(config.post_add_wait_ms / 1000.0)
                        await _save_step_screenshot(
                            page,
                            run_label=run_label,
                            supplier=supplier,
                            run_id=run_id,
                            step="10_after_add_to_cart",
                        )
                        _log(
                            run_label,
                            supplier,
                            run_id,
                            f"add_to_cart finished OK url={page.url!r}",
                        )
                        await _persist_scraper_storage_state(
                            context,
                            supplier,
                            run_label=run_label,
                            run_id=run_id,
                            logged_in=True,
                            automation_user_id=automation_user_id,
                        )
                    finally:
                        await browser.close()
            except Exception as exc:
                dev_run_log_exception(run_label, exc)
                raise

        if sys.platform == "win32":

            def _thread_entry() -> None:
                _run_async_on_windows_proactor(_playwright_flow())

            await asyncio.to_thread(_thread_entry)
            return
        await _playwright_flow()

    @staticmethod
    async def fetch_remote_cart_overview_row(
        supplier: Supplier,
        *,
        automation_user_id: int = 0,
    ) -> dict[str, Any]:
        """Jeden riadok pre zoznam dodávateľov v košíku (bez detailu položiek)."""
        uid = int(automation_user_id)
        logo = supplier_logo_public_url(supplier.logo_path)
        base: dict[str, Any] = {
            "supplier_id": supplier.id,
            "name": supplier.name,
            "logo_url": logo,
            "remote_supported": False,
            "logged_in": None,
            "total_eur": None,
            "line_count": 0,
            "message": None,
            "web_cart_url": supplier_shop_cart_url(supplier.shop_url or ""),
            "free_shipping_threshold_eur": supplier.free_shipping_threshold_eur,
        }
        if supplier.id is None:
            base["message"] = "Neplatný dodávateľ."
            return base
        if not _supplier_has_remote_cart_credentials(supplier):
            base["message"] = "Chýba URL alebo prihlasovacie údaje."
            return base
        sid = supplier.id
        if (
            sid is not None
            and _remote_cart_cache_enabled()
            and (
                _supplier_is_haspl(supplier)
                or _supplier_is_mekrs(supplier)
                or _supplier_is_argip(supplier)
                or _supplier_is_schachermayer(supplier)
            )
        ):
            hit = _remote_cart_cache_get(_remote_cart_overview_cache, uid, sid)
            if hit is not None:
                hit["free_shipping_threshold_eur"] = (
                    supplier.free_shipping_threshold_eur
                )
                hit["logo_url"] = supplier_logo_public_url(supplier.logo_path)
                hit["name"] = supplier.name
                hit["web_cart_url"] = supplier_shop_cart_url(
                    supplier.shop_url or ""
                )
                return hit
        try:
            if _supplier_is_haspl(supplier):
                base_url = haspl_base_url(supplier.shop_url or "")
                async with HasplHttpClient(base_url=base_url) as client:
                    await client.ensure_session(supplier.username, supplier.password)
                    order = await client.fetch_open_cart_order()
                parsed = haspl_parse_open_order(order)
                result = {
                    **base,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "line_count": parsed["line_count"],
                    "message": None,
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_cart_overview_cache, uid, sid, result
                    )
                    _remote_cart_cache_set(
                        _remote_haspl_order_snapshot, uid, sid, order
                    )
                    # Rovnaké dáta ako detail — prvé rozbalenie bez ďalšieho GET.
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache,
                        uid,
                        sid,
                        {
                            "supplier_id": supplier.id,
                            "name": supplier.name,
                            "logo_url": supplier_logo_public_url(
                                supplier.logo_path
                            ),
                            "remote_supported": True,
                            "logged_in": True,
                            "total_eur": parsed["total_eur"],
                            "lines": parsed["lines"],
                            "message": None,
                        },
                    )
                return result
            if _supplier_is_mekrs(supplier):
                async with MekrsHttpClient() as client:
                    await client.ensure_session(supplier.username, supplier.password)
                    # Prehľad: bez calculate-price ak je košík prázdny (ušetrí 1 POST).
                    cart = await client.get_cart(recalculate_prices=False)
                    parsed = mekrs_parse_cart_json(cart)
                    if parsed["line_count"] > 0 and parsed["total_eur"] is None:
                        cart = await client.get_cart(recalculate_prices=True)
                        parsed = mekrs_parse_cart_json(cart)
                result = {
                    **base,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "line_count": parsed["line_count"],
                    "message": None,
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_cart_overview_cache, uid, sid, result
                    )
                    _remote_cart_cache_set(
                        _remote_mekrs_cart_snapshot, uid, sid, cart
                    )
                return result
            if _supplier_is_argip(supplier):
                async with ArgipHttpClient(shop_url=supplier.shop_url or "") as client:
                    await client.ensure_login(supplier.username, supplier.password)
                    cart = await client.fetch_customer_cart()
                    parsed = argip_parse_cart_json(cart)
                result = {
                    **base,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "line_count": parsed["line_count"],
                    "message": None,
                    "web_cart_url": argip_cart_url(supplier.shop_url or ""),
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_cart_overview_cache, uid, sid, result
                    )
                    _remote_cart_cache_set(
                        _remote_argip_cart_snapshot, uid, sid, cart
                    )
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache,
                        uid,
                        sid,
                        {
                            "supplier_id": supplier.id,
                            "name": supplier.name,
                            "logo_url": supplier_logo_public_url(
                                supplier.logo_path
                            ),
                            "remote_supported": True,
                            "logged_in": True,
                            "total_eur": parsed["total_eur"],
                            "lines": parsed["lines"],
                            "message": None,
                        },
                    )
                return result
            if _supplier_is_schachermayer(supplier):
                async with SchachermayerHttpClient(
                    supplier.shop_url or ""
                ) as client:
                    await client.ensure_login(supplier.username, supplier.password)
                    basket = await client.fetch_basket()
                    summary_html = await client.fetch_basket_summary_html()
                    parsed = schachermayer_parse_cart_json(
                        basket, summary_html=summary_html
                    )
                result = {
                    **base,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "line_count": parsed["line_count"],
                    "message": None,
                    "web_cart_url": schachermayer_web_cart_url(
                        supplier.shop_url or ""
                    ),
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_cart_overview_cache, uid, sid, result
                    )
                    _remote_cart_cache_set(
                        _remote_schachermayer_cart_snapshot,
                        uid,
                        sid,
                        {"basket": basket, "summary_html": summary_html},
                    )
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache,
                        uid,
                        sid,
                        {
                            "supplier_id": supplier.id,
                            "name": supplier.name,
                            "logo_url": supplier_logo_public_url(
                                supplier.logo_path
                            ),
                            "remote_supported": True,
                            "logged_in": True,
                            "total_eur": parsed["total_eur"],
                            "lines": parsed["lines"],
                            "message": None,
                        },
                    )
                return result
            if _supplier_is_halfmann(supplier):
                base_hf = halfmann_base_url(supplier.shop_url or "")
                async with HalfmannHttpClient(base_hf) as client:
                    await client.ensure_login(supplier.username, supplier.password)
                return {
                    **base,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": None,
                    "line_count": 0,
                    "message": None,
                }
            if _supplier_is_valenta(supplier):
                async with ValentaHttpClient(supplier.shop_url or "") as client:
                    await client.ensure_login(supplier.username, supplier.password)
                return {
                    **base,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": None,
                    "line_count": 0,
                    "message": None,
                }
        except Exception as exc:
            return {
                **base,
                "remote_supported": True,
                "logged_in": False,
                "message": str(exc),
            }
        base["message"] = (
            "Košík cez API je zatiaľ pre Haspl, Mekrs, Argip a Schachermayer. "
            "U ostatných sa položky pridávajú v prehliadači — obsah tu nevieme načítať."
        )
        return base

    @staticmethod
    async def fetch_remote_cart_lines(
        supplier: Supplier,
        *,
        automation_user_id: int = 0,
    ) -> dict[str, Any]:
        """Detail košíka: zoznam položiek (Haspl / Mekrs HTTP)."""
        uid = int(automation_user_id)
        logo = supplier_logo_public_url(supplier.logo_path)
        out: dict[str, Any] = {
            "supplier_id": supplier.id,
            "name": supplier.name,
            "logo_url": logo,
            "remote_supported": False,
            "logged_in": None,
            "total_eur": None,
            "lines": [],
            "message": None,
        }
        if supplier.id is None:
            out["message"] = "Neplatný dodávateľ."
            return out
        if not _supplier_has_remote_cart_credentials(supplier):
            out["message"] = "Chýba URL alebo prihlasovacie údaje."
            return out
        sid = supplier.id
        if (
            sid is not None
            and _remote_cart_cache_enabled()
            and (
                _supplier_is_haspl(supplier)
                or _supplier_is_mekrs(supplier)
                or _supplier_is_argip(supplier)
                or _supplier_is_schachermayer(supplier)
            )
        ):
            hit = _remote_cart_cache_get(_remote_cart_detail_cache, uid, sid)
            if hit is not None:
                hit["logo_url"] = supplier_logo_public_url(supplier.logo_path)
                hit["name"] = supplier.name
                return hit
        try:
            if _supplier_is_haspl(supplier):
                base_url = haspl_base_url(supplier.shop_url or "")
                order_snap = (
                    _remote_cart_cache_get(_remote_haspl_order_snapshot, uid, sid)
                    if sid is not None
                    else None
                )
                async with HasplHttpClient(base_url=base_url) as client:
                    await client.ensure_session(supplier.username, supplier.password)
                    if order_snap is not None:
                        order = order_snap
                    else:
                        order = await client.fetch_open_cart_order()
                parsed = haspl_parse_open_order(order)
                result = {
                    **out,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "lines": parsed["lines"],
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_haspl_order_snapshot, uid, sid, order
                    )
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache, uid, sid, result
                    )
                return result
            if _supplier_is_mekrs(supplier):
                cart_snap = (
                    _remote_cart_cache_get(_remote_mekrs_cart_snapshot, uid, sid)
                    if sid is not None
                    else None
                )
                async with MekrsHttpClient() as client:
                    await client.ensure_session(supplier.username, supplier.password)
                    if cart_snap is not None:
                        cart = cart_snap
                    else:
                        cart = await client.get_cart()
                    parsed = mekrs_parse_cart_json(cart)
                    await client.enrich_cart_lines_variant_codes(
                        cart, parsed["lines"]
                    )
                result = {
                    **out,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "lines": parsed["lines"],
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_mekrs_cart_snapshot, uid, sid, cart
                    )
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache, uid, sid, result
                    )
                return result
            if _supplier_is_argip(supplier):
                cart_snap = (
                    _remote_cart_cache_get(_remote_argip_cart_snapshot, uid, sid)
                    if sid is not None
                    else None
                )
                async with ArgipHttpClient(shop_url=supplier.shop_url or "") as client:
                    await client.ensure_login(supplier.username, supplier.password)
                    if cart_snap is not None:
                        cart = cart_snap
                    else:
                        cart = await client.fetch_customer_cart()
                    parsed = argip_parse_cart_json(cart)
                result = {
                    **out,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "lines": parsed["lines"],
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_argip_cart_snapshot, uid, sid, cart
                    )
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache, uid, sid, result
                    )
                return result
            if _supplier_is_schachermayer(supplier):
                snap = (
                    _remote_cart_cache_get(
                        _remote_schachermayer_cart_snapshot, uid, sid
                    )
                    if sid is not None
                    else None
                )
                async with SchachermayerHttpClient(
                    supplier.shop_url or ""
                ) as client:
                    await client.ensure_login(supplier.username, supplier.password)
                    if isinstance(snap, dict) and isinstance(
                        snap.get("basket"), dict
                    ):
                        basket = snap["basket"]
                        summary_html = str(snap.get("summary_html") or "")
                    else:
                        basket = await client.fetch_basket()
                        summary_html = await client.fetch_basket_summary_html()
                    parsed = schachermayer_parse_cart_json(
                        basket, summary_html=summary_html
                    )
                result = {
                    **out,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": parsed["total_eur"],
                    "lines": parsed["lines"],
                }
                if sid is not None:
                    _remote_cart_cache_set(
                        _remote_schachermayer_cart_snapshot,
                        uid,
                        sid,
                        {"basket": basket, "summary_html": summary_html},
                    )
                    _remote_cart_cache_set(
                        _remote_cart_detail_cache, uid, sid, result
                    )
                return result
            if _supplier_is_halfmann(supplier):
                base_hf = halfmann_base_url(supplier.shop_url or "")
                async with HalfmannHttpClient(base_hf) as client:
                    await client.ensure_login(supplier.username, supplier.password)
                return {
                    **out,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": None,
                    "lines": [],
                    "message": (
                        "Prihlásenie funguje; zoznam položiek košíka cez API zatiaľ nečítame."
                    ),
                }
            if _supplier_is_valenta(supplier):
                async with ValentaHttpClient(supplier.shop_url or "") as client:
                    await client.ensure_login(supplier.username, supplier.password)
                return {
                    **out,
                    "remote_supported": True,
                    "logged_in": True,
                    "total_eur": None,
                    "lines": [],
                    "message": (
                        "Prihlásenie funguje; zoznam položiek košíka cez API zatiaľ nečítame."
                    ),
                }
        except Exception as exc:
            return {
                **out,
                "remote_supported": True,
                "logged_in": False,
                "message": str(exc),
            }
        out["message"] = (
            "Detail košíka cez API je zatiaľ len pre Haspl, Mekrs, Argip a Schachermayer."
        )
        return out


def load_scraper_config(supplier: Supplier) -> ScraperConfig:
    raw = (supplier.cart_config_json or "").strip()
    if raw:
        return ScraperConfig.model_validate_json(supplier.cart_config_json)
    if supplier_allows_empty_cart_config(supplier):
        # Zástupca pre Pydantic — pri zlyhaní HTTP musí Playwright mať reálne selektory (nie „body“).
        if _supplier_is_inoxmare(supplier):
            sp = inoxmare_store_path(supplier.shop_url or "", None)
            login_page = (
                f"{inoxmare_origin(supplier.shop_url or '')}{sp}/customer/account/login/"
            )
            return ScraperConfig(
                login_url=login_page,
                login_form_selector=None,
                username_selector='#login-form input[name="login[username]"]',
                password_selector='#login-form input[name="login[password]"]',
                login_button_selector="#login-form button.action.login",
                login_expect_spring_security_post=False,
            )
        if _supplier_is_bmkco(supplier):
            base = bmkco_base_url(supplier.shop_url or "")
            return ScraperConfig(
                login_url=f"{base}/cs/Account/LoginB2C",
                after_login_url=f"{base}/cs/Home/IndexB2C",
                login_form_selector="form",
                username_selector='input[name="Email"]',
                password_selector='input[name="Password"]',
                login_button_selector='button[type="submit"], input[type="submit"]',
                login_expect_spring_security_post=False,
            )
        if _supplier_is_halfmann(supplier):
            base = halfmann_base_url(supplier.shop_url or "")
            return ScraperConfig(
                login_url=f"{base}/login",
                after_login_url=f"{base}/",
                login_form_selector="form",
                username_selector='input[name="uname"], input[name="username"]',
                password_selector='input[name="psw"], input[type="password"]',
                login_button_selector='button[type="submit"], input[type="submit"]',
                login_expect_spring_security_post=False,
            )
        if _supplier_is_schachermayer(supplier):
            from app.services.schachermayer_http_client import schachermayer_base_url

            base_sm = schachermayer_base_url(supplier.shop_url or "")
            return ScraperConfig(
                login_url=f"{base_sm}/sso/oauth2/authorization/keycloak?ui_locales=sk",
                after_login_url=f"{base_sm}/cat/sk-SK",
                login_form_selector="#kc-form-login",
                username_selector='input[name="username"]',
                password_selector='input[name="password"]',
                login_button_selector=(
                    '#kc-form-login input[type="submit"], #kc-form-login button[type="submit"]'
                ),
                login_expect_spring_security_post=False,
            )
        if _supplier_is_valenta(supplier):
            base_va = valenta_cart_url(supplier.shop_url or "").rsplit("/", 1)[0]
            return ScraperConfig(
                login_url=f"{base_va}/login.php",
                after_login_url=valenta_cart_url(supplier.shop_url or ""),
                login_form_selector="form",
                username_selector='input[name="areboua"]',
                password_selector='input[name="arebopwd"]',
                login_button_selector='input[name="arebosb"], button[type="submit"]',
                login_expect_spring_security_post=False,
            )
        return ScraperConfig(
            username_selector="body",
            password_selector="body",
            login_button_selector="body",
            login_expect_spring_security_post=False,
        )
    raise ValueError(
        "Chýba cart_config_json u dodávateľa (aspoň selektory prihlásenia)."
    )
