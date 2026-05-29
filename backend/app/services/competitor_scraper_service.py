"""Verejné ceny konkurencie — HTTP bez prihlásenia (selektory v scrape_config_json)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_DEFAULT_PRICE_RE = re.compile(
    r'(?:itemprop=["\']price["\'][^>]*content=["\']([^"\']+)["\']'
    r'|class="[^"]*\bprice\b[^"]*"[^>]*>\s*([^<]+))',
    re.IGNORECASE,
)


@dataclass
class CompetitorScrapeConfig:
    product_url_template: Optional[str] = None
    search_via_url_template: Optional[str] = None
    follow_product_link_regex: Optional[str] = None
    price_selector_regex: Optional[str] = None
    pack_quantity_selector_regex: Optional[str] = None
    user_agent: Optional[str] = None


_price_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 900.0

# SVX: autocomplete ide cez /vyhladavanie/?search_query= (nie /search/?q=).
_SVX_SCRAPE_CONFIG = CompetitorScrapeConfig(
    search_via_url_template="https://www.svx.sk/vyhladavanie/?search_query={code}",
    follow_product_link_regex=r'href="(/[^"]+_\d+/+)"',
    price_selector_regex=r"data-config-product-price-secondary[^>]*>\s*([0-9,.]+)\s*€",
)

_SVX_SCRAPE_CONFIG_JSON = json.dumps(
    {
        "search_via_url_template": _SVX_SCRAPE_CONFIG.search_via_url_template,
        "follow_product_link_regex": _SVX_SCRAPE_CONFIG.follow_product_link_regex,
        "price_selector_regex": _SVX_SCRAPE_CONFIG.price_selector_regex,
    },
    ensure_ascii=False,
    indent=2,
)

# Shoptet (Oramat, Bbtechnik, …): /vyhladavanie/?string=, cena bez DPH v .price-additional
_SHOPTET_SHOP_HOSTS = ("oramat.sk", "bbtechnik.sk")
_SHOPTET_FOLLOW_LINK_RE = (
    r'href="(/[^"]+)"[\s\S]{0,4000}?data-micro="sku">{code}'
)
_SHOPTET_SCRAPE_CONFIG = CompetitorScrapeConfig(
    search_via_url_template="{shop_url}/vyhladavanie/?string={code}",
    follow_product_link_regex=_SHOPTET_FOLLOW_LINK_RE,
    price_selector_regex=r'class="price-additional[^"]*"[^>]*>\s*([0-9,.]+)\s*€',
)

_SHOPTET_SCRAPE_CONFIG_JSON = json.dumps(
    {
        "search_via_url_template": _SHOPTET_SCRAPE_CONFIG.search_via_url_template,
        "follow_product_link_regex": _SHOPTET_SCRAPE_CONFIG.follow_product_link_regex,
        "price_selector_regex": _SHOPTET_SCRAPE_CONFIG.price_selector_regex,
    },
    ensure_ascii=False,
    indent=2,
)

# VKP Steel (PrestaShop): vyhľadávanie + cena bez DPH v GTM dataLayer na search stránke
_PRESTASHOP_VKP_HOSTS = ("vkpsteel.com",)
_PRESTASHOP_VKP_SCRAPE_CONFIG = CompetitorScrapeConfig(
    search_via_url_template="{shop_url}/vyhladavanie?controller=search&s={code}",
    price_selector_regex=r'"reference":"{code}"[\s\S]{0,250}?"price_tax_exc":"([0-9.]+)"',
)

_PRESTASHOP_VKP_SCRAPE_CONFIG_JSON = json.dumps(
    {
        "search_via_url_template": _PRESTASHOP_VKP_SCRAPE_CONFIG.search_via_url_template,
        "price_selector_regex": _PRESTASHOP_VKP_SCRAPE_CONFIG.price_selector_regex,
    },
    ensure_ascii=False,
    indent=2,
)

# FEVA (WooCommerce): WP search ?s= + Store API pre cenu (search bar v UI často nefunguje)
_WOOCOMMERCE_FEVA_HOSTS = ("feva.sk",)
_FEVA_SCRAPE_CONFIG = CompetitorScrapeConfig(
    search_via_url_template="{shop_url}/?s={code}",
    price_selector_regex=r'"price":\s*"([0-9.]+)"',
)

_FEVA_SCRAPE_CONFIG_JSON = json.dumps(
    {
        "search_via_url_template": _FEVA_SCRAPE_CONFIG.search_via_url_template,
        "price_selector_regex": _FEVA_SCRAPE_CONFIG.price_selector_regex,
    },
    ensure_ascii=False,
    indent=2,
)

_FEVA_PRODUCT_PATH_RE = re.compile(
    r"/(?:metricke-skrutky|matice|podlozky|skrutky|hmozdinky|zavitove-tyce-trapezove-tyce|"
    r"skrutky-do-[^/]+|nity-trhacie|lanove-prislusenstvo)/[^/]+/?$",
    re.IGNORECASE,
)


def _is_svx_shop(shop_url: str) -> bool:
    return "svx.sk" in (shop_url or "").lower()


def _is_shoptet_shop(shop_url: str) -> bool:
    low = (shop_url or "").lower()
    return any(host in low for host in _SHOPTET_SHOP_HOSTS)


def _is_prestashop_vkp_shop(shop_url: str) -> bool:
    low = (shop_url or "").lower()
    return any(host in low for host in _PRESTASHOP_VKP_HOSTS)


def _is_feva_shop(shop_url: str) -> bool:
    low = (shop_url or "").lower()
    return any(host in low for host in _WOOCOMMERCE_FEVA_HOSTS)


def _wc_store_price_eur(prices: dict[str, Any] | None) -> Optional[float]:
    if not prices:
        return None
    minor = int(prices.get("currency_minor_unit") or 2)
    raw = prices.get("price") or prices.get("regular_price")
    if raw is None:
        return None
    try:
        return round(int(str(raw)) / (10**minor), 5)
    except (TypeError, ValueError):
        return None


def _is_direct_http_url(value: str) -> bool:
    return (value or "").strip().lower().startswith(("http://", "https://"))


def _normalize_direct_product_url(value: str, shop_url: str = "") -> Optional[str]:
    c = (value or "").strip()
    if _is_direct_http_url(c):
        return c
    if c.startswith("/"):
        base = _normalize_base_url(shop_url)
        if base:
            return urljoin(f"{base}/", c.lstrip("/"))
    return None


def _feva_slug_from_input(code: str) -> Optional[str]:
    c = (code or "").strip()
    if not c:
        return None
    if c.startswith(("http://", "https://")):
        path = (urlparse(c).path or "").strip("/")
        return path.split("/")[-1] if path else None
    if "/" in c:
        return c.strip("/").split("/")[-1]
    return None


def _feva_query_tokens(query: str) -> list[str]:
    out: list[str] = []
    q = (query or "").lower()
    for m in re.finditer(r"m0*(\d+)", q):
        out.append(f"m{m.group(1)}")
    for m in re.finditer(r"x\s*0*(\d+)", q):
        out.append(f"x-{m.group(1)}")
    for tok in re.split(r"[\s\-]+", q):
        tok = tok.strip()
        if tok and tok not in {"x"}:
            out.append(tok)
    return out


def _feva_score_slug(slug: str, query: str) -> int:
    slug_l = (slug or "").lower()
    score = 0
    for tok in _feva_query_tokens(query):
        if tok in slug_l:
            score += 3
    dim = re.search(r"m0*(\d+)\s*x\s*0*(\d+)", query, re.IGNORECASE)
    if dim:
        needle = f"m{dim.group(1)}-x-{dim.group(2)}"
        if needle in slug_l:
            score += 20
        if re.search(rf"-x-{dim.group(2)}(?:/|$)", slug_l):
            score += 5
    if dim and re.search(rf"m{dim.group(1)}-x-{dim.group(2)}$", slug_l):
        score += 8
    return score


def _feva_pick_best_product_url(urls: list[str], query: str) -> Optional[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    for url in urls:
        u = (url or "").strip().rstrip("/")
        if not u or u in seen:
            continue
        path = urlparse(u).path or ""
        if not _FEVA_PRODUCT_PATH_RE.search(path):
            continue
        seen.add(u)
        candidates.append(u)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda u: (
            _feva_score_slug(u.rstrip("/").split("/")[-1], query),
            -len(u),
        ),
    )


def _feva_result_from_store_product(
    product: dict[str, Any],
    *,
    preferred_url: Optional[str] = None,
) -> dict[str, Any]:
    prices = product.get("prices") if isinstance(product.get("prices"), dict) else {}
    price_eur = _wc_store_price_eur(prices)
    if price_eur is None:
        raise RuntimeError("FEVA Store API nevrátilo cenu produktu.")
    name = str(product.get("name") or "").strip()
    name = re.sub(r"&#8211;", "–", name)
    permalink = str(product.get("permalink") or "").strip()
    raw_price = str(prices.get("price") or price_eur)
    return {
        "price_eur": price_eur,
        "raw_price": raw_price,
        "stock": None,
        "raw_stock": None,
        "pack_quantity": None,
        "raw_pack_quantity": None,
        "product_title": name or None,
        "competitor_product_url": (preferred_url or permalink) or None,
        "logged_in": True,
        "competitor_via_http": True,
    }


async def _feva_store_product_by_slug(
    client: httpx.AsyncClient,
    base: str,
    slug: str,
) -> Optional[dict[str, Any]]:
    slug = (slug or "").strip().strip("/")
    if not slug:
        return None
    r = await client.get(f"{base}/wp-json/wc/store/v1/products?slug={quote(slug, safe='')}")
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and data:
        first = data[0]
        return first if isinstance(first, dict) else None
    return None


async def _feva_store_search_products(
    client: httpx.AsyncClient,
    base: str,
    query: str,
) -> list[dict[str, Any]]:
    r = await client.get(
        f"{base}/wp-json/wc/store/v1/products?search={quote(query, safe='')}&per_page=20"
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)]


async def _feva_html_search_product_urls(
    client: httpx.AsyncClient,
    base: str,
    query: str,
) -> list[str]:
    r = await client.get(f"{base}/?s={quote(query, safe='')}")
    r.raise_for_status()
    html = r.text or ""
    origin = base.rstrip("/")
    urls: list[str] = []
    for href in re.findall(r'href="([^"]+)"', html, re.IGNORECASE):
        href = href.strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(f"{origin}/", href.lstrip("/"))
        if origin in full:
            urls.append(full.rstrip("/"))
    return urls


def _feva_normalize_query(query: str) -> str:
    q = (query or "").strip()
    for ch in ("\u2013", "\u2014", "\u2212"):
        q = q.replace(ch, " ")
    return re.sub(r"\s+", " ", q).strip()


def _feva_search_query_variants(query: str) -> list[str]:
    raw = (query or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for candidate in (raw, _feva_normalize_query(raw)):
        if candidate and candidate not in out:
            out.append(candidate)
    norm = _feva_normalize_query(raw)
    m = re.search(
        r"(din\s*\d+[a-z]?).*?(m0*\d+\s*x\s*0*\d+)",
        norm,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        dim = re.sub(r"^m0+", "M", m.group(2).strip(), flags=re.IGNORECASE)
        dim = re.sub(r"\s+X\s+", " x ", dim, flags=re.IGNORECASE)
        short = f"{m.group(1).upper()} {dim}"
        short = re.sub(r"\s+", " ", short).strip()
        if short not in out:
            out.append(short)
    return out


async def _feva_resolve_search_query(
    client: httpx.AsyncClient,
    base: str,
    query: str,
) -> Optional[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return None

    store_items = await _feva_store_search_products(client, base, q)
    if store_items:
        best = max(
            store_items,
            key=lambda p: _feva_score_slug(str(p.get("slug") or ""), q),
        )
        if _feva_score_slug(str(best.get("slug") or ""), q) > 0 or len(store_items) == 1:
            return _feva_result_from_store_product(best)

    html_urls = await _feva_html_search_product_urls(client, base, q)
    best_url = _feva_pick_best_product_url(html_urls, q)
    if not best_url:
        return None

    slug = best_url.rstrip("/").split("/")[-1]
    product = await _feva_store_product_by_slug(client, base, slug)
    if product:
        out = _feva_result_from_store_product(product)
        if not out.get("competitor_product_url"):
            out["competitor_product_url"] = best_url
        return out

    r = await client.get(best_url)
    r.raise_for_status()
    price_eur, raw_price = _extract_price_from_html(r.text or "", _FEVA_SCRAPE_CONFIG, q)
    if price_eur is None:
        return None
    return {
        "price_eur": price_eur,
        "raw_price": raw_price,
        "stock": None,
        "raw_stock": None,
        "pack_quantity": None,
        "raw_pack_quantity": None,
        "product_title": None,
        "competitor_product_url": str(r.url),
        "logged_in": True,
        "competitor_via_http": True,
    }


async def _fetch_feva_public_price(
    client: httpx.AsyncClient,
    *,
    shop_url: str,
    query: str,
) -> dict[str, Any]:
    base = _normalize_base_url(shop_url)
    q = (query or "").strip()
    if not base or not q:
        raise ValueError("Prázdny dotaz alebo URL e-shopu pre FEVA.")

    direct_url = _normalize_direct_product_url(q, base)
    if direct_url:
        slug = _feva_slug_from_input(direct_url)
        if slug:
            product = await _feva_store_product_by_slug(client, base, slug)
            if product:
                return _feva_result_from_store_product(product, preferred_url=direct_url)
        r = await client.get(direct_url)
        r.raise_for_status()
        price_eur, raw_price = _extract_price_from_html(r.text or "", _FEVA_SCRAPE_CONFIG, q)
        if price_eur is None:
            raise RuntimeError(
                f"FEVA: cena sa nenašla na stránke produktu. URL={direct_url[:120]!r}"
            )
        return {
            "price_eur": price_eur,
            "raw_price": raw_price,
            "stock": None,
            "raw_stock": None,
            "pack_quantity": None,
            "raw_pack_quantity": None,
            "product_title": None,
            "competitor_product_url": direct_url,
            "logged_in": True,
            "competitor_via_http": True,
        }

    slug = _feva_slug_from_input(q)
    if slug:
        direct = await _feva_store_product_by_slug(client, base, slug)
        if direct:
            return _feva_result_from_store_product(direct)

    for search_q in _feva_search_query_variants(q):
        resolved = await _feva_resolve_search_query(client, base, search_q)
        if resolved:
            return resolved

    raise RuntimeError(
        f"FEVA: pre dotaz {q!r} sa nenašiel produkt (skúste presnejší názov alebo URL produktu)."
    )


def _apply_code_to_regex(pattern: str, code: str) -> str:
    return pattern.replace("{code}", re.escape(code))


def _template_url_host(tmpl: str | None) -> str:
    if not tmpl or not tmpl.strip().startswith(("http://", "https://")):
        return ""
    return (urlparse(tmpl.strip()).netloc or "").lower()


def _is_placeholder_or_foreign_config(shop_url: str, cfg: CompetitorScrapeConfig) -> bool:
    shop_host = (urlparse(_normalize_base_url(shop_url)).netloc or "").lower()
    for tmpl in (cfg.search_via_url_template, cfg.product_url_template):
        if not tmpl:
            continue
        low = tmpl.lower()
        if "example.sk" in low or "example.com" in low:
            return True
        if "{shop_url}" in tmpl:
            continue
        tmpl_host = _template_url_host(tmpl)
        if tmpl_host and shop_host and tmpl_host != shop_host:
            return True
    return False


def _shoptet_config_is_wrong(cfg: CompetitorScrapeConfig) -> bool:
    tmpl = (cfg.search_via_url_template or "").lower()
    if not tmpl:
        return False
    if "search_query" in tmpl:
        return True
    if "vyhladavanie" in tmpl and "string=" not in tmpl:
        return True
    return False


def _svx_config_is_wrong(cfg: CompetitorScrapeConfig) -> bool:
    tmpl = (cfg.search_via_url_template or "").lower()
    if not tmpl:
        return False
    if "vyhladavanie" in tmpl and "string=" in tmpl:
        return True
    return False


def _prestashop_vkp_config_is_wrong(cfg: CompetitorScrapeConfig) -> bool:
    tmpl = (cfg.search_via_url_template or "").lower()
    if not tmpl:
        return False
    if "controller=search" in tmpl:
        return False
    if "search_query" in tmpl or "string=" in tmpl:
        return True
    return False


def _feva_config_is_wrong(cfg: CompetitorScrapeConfig) -> bool:
    tmpl = (cfg.search_via_url_template or "").lower()
    if not tmpl:
        return False
    if "{shop_url}/?s={code}" in tmpl or tmpl.endswith("/?s={code}"):
        return False
    if "string=" in tmpl or "search_query" in tmpl or "controller=search" in tmpl:
        return True
    if "/search/" in tmpl or "search?q=" in tmpl:
        return True
    return False


def _config_needs_shop_preset(shop_url: str, cfg: CompetitorScrapeConfig) -> bool:
    if _is_placeholder_or_foreign_config(shop_url, cfg):
        return True
    if _is_shoptet_shop(shop_url) and _shoptet_config_is_wrong(cfg):
        return True
    if _is_prestashop_vkp_shop(shop_url) and _prestashop_vkp_config_is_wrong(cfg):
        return True
    if _is_feva_shop(shop_url) and _feva_config_is_wrong(cfg):
        return True
    if _is_svx_shop(shop_url) and _svx_config_is_wrong(cfg):
        return True
    if _uses_broken_generic_search(cfg):
        return True
    if not cfg.search_via_url_template and not cfg.product_url_template:
        return True
    return False


def _merge_shop_preset(
    cfg: CompetitorScrapeConfig,
    preset: CompetitorScrapeConfig,
) -> CompetitorScrapeConfig:
    return CompetitorScrapeConfig(
        product_url_template=cfg.product_url_template,
        search_via_url_template=cfg.search_via_url_template or preset.search_via_url_template,
        follow_product_link_regex=cfg.follow_product_link_regex or preset.follow_product_link_regex,
        price_selector_regex=cfg.price_selector_regex or preset.price_selector_regex,
        pack_quantity_selector_regex=cfg.pack_quantity_selector_regex,
        user_agent=cfg.user_agent,
    )


def _uses_broken_generic_search(cfg: CompetitorScrapeConfig) -> bool:
    if cfg.search_via_url_template:
        low = cfg.search_via_url_template.lower()
        if "vyhladavanie" in low and "search_query" in low:
            return False
        if "controller=search" in low:
            return False
        if "vyhladavanie" in low and "string=" in low:
            return False
        if "/search/" in low or "search?q=" in low or "/katalog/?search" in low:
            return True
    if cfg.product_url_template:
        low = cfg.product_url_template.lower()
        if "/search/" in low or "search?q=" in low:
            return True
    if not cfg.search_via_url_template and not cfg.product_url_template:
        return True
    return False


def resolve_competitor_scrape_config(shop_url: str, raw: str | None) -> CompetitorScrapeConfig:
    """Efektívna konfigurácia — známe e-shopy majú preset (SVX, Shoptet, PrestaShop)."""
    cfg = load_competitor_scrape_config(raw)
    if _is_svx_shop(shop_url):
        if _config_needs_shop_preset(shop_url, cfg):
            return _SVX_SCRAPE_CONFIG
        return _merge_shop_preset(cfg, _SVX_SCRAPE_CONFIG)
    if _is_shoptet_shop(shop_url):
        if _config_needs_shop_preset(shop_url, cfg):
            return _SHOPTET_SCRAPE_CONFIG
        return _merge_shop_preset(cfg, _SHOPTET_SCRAPE_CONFIG)
    if _is_prestashop_vkp_shop(shop_url):
        if _config_needs_shop_preset(shop_url, cfg):
            return _PRESTASHOP_VKP_SCRAPE_CONFIG
        return _merge_shop_preset(cfg, _PRESTASHOP_VKP_SCRAPE_CONFIG)
    if _is_feva_shop(shop_url):
        if _config_needs_shop_preset(shop_url, cfg):
            return _FEVA_SCRAPE_CONFIG
        return _merge_shop_preset(cfg, _FEVA_SCRAPE_CONFIG)
    return cfg


def normalize_scrape_config_json_for_shop(shop_url: str, raw: str | None) -> str | None:
    """Pri uložení konkurenta vráti opravený JSON pre známe e-shopy."""
    text = (raw or "").strip()
    if _is_svx_shop(shop_url) and _config_needs_shop_preset(
        shop_url, load_competitor_scrape_config(text)
    ):
        return _SVX_SCRAPE_CONFIG_JSON
    if _is_shoptet_shop(shop_url) and _config_needs_shop_preset(
        shop_url, load_competitor_scrape_config(text)
    ):
        return _SHOPTET_SCRAPE_CONFIG_JSON
    if _is_prestashop_vkp_shop(shop_url) and _config_needs_shop_preset(
        shop_url, load_competitor_scrape_config(text)
    ):
        return _PRESTASHOP_VKP_SCRAPE_CONFIG_JSON
    if _is_feva_shop(shop_url) and _config_needs_shop_preset(
        shop_url, load_competitor_scrape_config(text)
    ):
        return _FEVA_SCRAPE_CONFIG_JSON
    return text or None


def load_competitor_scrape_config(raw: str | None) -> CompetitorScrapeConfig:
    if not (raw or "").strip():
        return CompetitorScrapeConfig()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return CompetitorScrapeConfig()
    if not isinstance(parsed, dict):
        return CompetitorScrapeConfig()
    return CompetitorScrapeConfig(
        product_url_template=_opt_str(parsed.get("product_url_template")),
        search_via_url_template=_opt_str(parsed.get("search_via_url_template")),
        follow_product_link_regex=_opt_str(parsed.get("follow_product_link_regex")),
        price_selector_regex=_opt_str(parsed.get("price_selector_regex")),
        pack_quantity_selector_regex=_opt_str(parsed.get("pack_quantity_selector_regex")),
        user_agent=_opt_str(parsed.get("user_agent")),
    )


def _opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _normalize_base_url(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    return raw


def _shop_origin(shop_url: str, fallback_request_url: str = "") -> str:
    base = _normalize_base_url(shop_url)
    if base:
        return base
    parsed = urlparse(fallback_request_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _apply_url_template(tmpl: str, shop_url: str, code: str) -> str:
    enc = quote(code, safe="")
    base = _normalize_base_url(shop_url)
    out = tmpl.replace("{code}", enc)
    if "{shop_url}" in out:
        out = out.replace("{shop_url}", base)
    return out


def competitor_product_url(
    shop_url: str,
    competitor_code: str,
    config: CompetitorScrapeConfig,
) -> Optional[str]:
    code = (competitor_code or "").strip()
    if not code:
        return None
    direct = _normalize_direct_product_url(code, shop_url)
    if direct:
        return direct
    enc = quote(code, safe="")
    for tmpl in (config.product_url_template, config.search_via_url_template):
        if tmpl and "{code}" in tmpl:
            return _apply_url_template(tmpl, shop_url, code)
    base = _normalize_base_url(shop_url)
    if not base:
        return None
    if config.search_via_url_template:
        t = config.search_via_url_template.strip()
        if "{code}" in t:
            return _apply_url_template(t, shop_url, code)
        sep = "&" if "?" in t else "?"
        return f"{t}{sep}q={enc}"
    if _is_svx_shop(base):
        return f"{base}/vyhladavanie/?search_query={enc}"
    if _is_shoptet_shop(base):
        return f"{base}/vyhladavanie/?string={enc}"
    if _is_prestashop_vkp_shop(base):
        return f"{base}/vyhladavanie?controller=search&s={enc}"
    if _is_feva_shop(base):
        return f"{base}/?s={enc}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}q={enc}"


def _parse_eur_amount(text: Any) -> Optional[float]:
    if text is None:
        return None
    t = str(text).replace("\xa0", " ").replace("€", "").strip()
    if not t or "XX" in t.upper():
        return None
    t = t.replace(" ", "").replace(",", ".")
    m = re.search(r"(-?\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return round(float(m.group(1)), 4)
    except ValueError:
        return None


def _extract_price_from_html(
    html: str,
    config: CompetitorScrapeConfig,
    competitor_code: str = "",
) -> tuple[Optional[float], Optional[str]]:
    h = html or ""
    if config.price_selector_regex:
        try:
            pat = re.compile(
                _apply_code_to_regex(config.price_selector_regex, competitor_code),
                re.IGNORECASE | re.DOTALL,
            )
            m = pat.search(h)
            if m:
                raw = (m.group(1) if m.lastindex else m.group(0)) or ""
                raw = str(raw).strip()
                val = _parse_eur_amount(raw)
                if val is not None:
                    return val, raw
        except re.error:
            pass
    m = _DEFAULT_PRICE_RE.search(h)
    if m:
        raw = (m.group(1) or m.group(2) or "").strip()
        val = _parse_eur_amount(raw)
        if val is not None:
            return val, raw
    return None, None


def _extract_pack_quantity(
    html: str,
    config: CompetitorScrapeConfig,
    competitor_code: str = "",
) -> Optional[int]:
    if not config.pack_quantity_selector_regex:
        return None
    try:
        pat = re.compile(
            _apply_code_to_regex(config.pack_quantity_selector_regex, competitor_code),
            re.IGNORECASE,
        )
        m = pat.search(html or "")
        if m:
            return max(1, int(m.group(1)))
    except (re.error, TypeError, ValueError):
        pass
    return None


async def fetch_competitor_public_price(
    *,
    competitor_id: int,
    shop_url: str,
    competitor_code: str,
    scrape_config_json: str | None,
) -> dict[str, Any]:
    code = (competitor_code or "").strip()
    if not code:
        raise ValueError("Prázdny kód konkurencie.")
    cache_key = (int(competitor_id), code.casefold())
    now = time.monotonic()
    cached = _price_cache.get(cache_key)
    if cached is not None and cached[0] > now:
        out = dict(cached[1])
        out["from_cache"] = True
        return out

    cfg = resolve_competitor_scrape_config(shop_url, scrape_config_json)
    direct = _normalize_direct_product_url(code, shop_url)
    if direct and not _is_feva_shop(shop_url):
        target_url = direct
    else:
        target_url = competitor_product_url(shop_url, code, cfg)
    if not target_url:
        raise RuntimeError("Chýba URL e-shopu alebo šablóna produktu v scrape_config_json.")
    if not target_url.startswith(("http://", "https://")):
        raise RuntimeError(
            f"Neplatná URL pre scraper (chýba http/https): {target_url[:120]!r}. "
            "Skontroluj URL e-shopu a scrape_config_json (placeholdery {{shop_url}}, {{code}})."
        )

    ua = cfg.user_agent or DEFAULT_UA
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(35.0, connect=8.0),
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sk,sk-SK;q=0.9,cs;q=0.8,en;q=0.7",
        },
    ) as client:
        if _is_feva_shop(shop_url):
            result = await _fetch_feva_public_price(
                client,
                shop_url=shop_url,
                query=code,
            )
            _price_cache[cache_key] = (now + _CACHE_TTL_SEC, result)
            return result

        r = await client.get(target_url)
        r.raise_for_status()
        html = r.text or ""
        final_url = str(r.url)
        if cfg.follow_product_link_regex:
            try:
                link_pat = re.compile(
                    _apply_code_to_regex(cfg.follow_product_link_regex, code),
                    re.IGNORECASE | re.DOTALL,
                )
                link_m = link_pat.search(html)
                if link_m:
                    link = (link_m.group(1) if link_m.lastindex else link_m.group(0)) or ""
                    link = str(link).strip()
                    if link:
                        origin = _shop_origin(shop_url, target_url)
                        product_url = urljoin(f"{origin}/", link.lstrip("/"))
                        r2 = await client.get(product_url)
                        r2.raise_for_status()
                        html = r2.text or ""
                        final_url = str(r2.url)
            except re.error:
                pass

    price_eur, raw_price = _extract_price_from_html(html, cfg, code)
    if price_eur is None:
        raise RuntimeError(
            f"Na stránke sa nepodarilo nájsť cenu (skontroluj scrape_config_json "
            f"price_selector_regex). URL={final_url[:120]!r}"
        )
    pack_q = _extract_pack_quantity(html, cfg, code)
    result: dict[str, Any] = {
        "price_eur": price_eur,
        "raw_price": raw_price,
        "stock": None,
        "raw_stock": None,
        "pack_quantity": pack_q,
        "raw_pack_quantity": str(pack_q) if pack_q else None,
        "product_title": None,
        "competitor_product_url": final_url,
        "logged_in": True,
        "competitor_via_http": True,
    }
    _price_cache[cache_key] = (now + _CACHE_TTL_SEC, result)
    return result


def invalidate_competitor_price_cache(competitor_id: int | None = None) -> None:
    if competitor_id is None:
        _price_cache.clear()
        return
    cid = int(competitor_id)
    for k in list(_price_cache.keys()):
        if k[0] == cid:
            _price_cache.pop(k, None)
