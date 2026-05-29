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

# Oramat (Shoptet): vyhľadávanie /vyhladavanie/?string=, cena bez DPH v .price-additional
_ORAMAT_FOLLOW_LINK_RE = (
    r'href="(/[^"]+)"[\s\S]{0,4000}?data-micro="sku">{code}'
)
_ORAMAT_SCRAPE_CONFIG = CompetitorScrapeConfig(
    search_via_url_template="{shop_url}/vyhladavanie/?string={code}",
    follow_product_link_regex=_ORAMAT_FOLLOW_LINK_RE,
    price_selector_regex=r'class="price-additional[^"]*"[^>]*>\s*([0-9,.]+)\s*€',
)

_ORAMAT_SCRAPE_CONFIG_JSON = json.dumps(
    {
        "search_via_url_template": _ORAMAT_SCRAPE_CONFIG.search_via_url_template,
        "follow_product_link_regex": _ORAMAT_SCRAPE_CONFIG.follow_product_link_regex,
        "price_selector_regex": _ORAMAT_SCRAPE_CONFIG.price_selector_regex,
    },
    ensure_ascii=False,
    indent=2,
)


def _is_svx_shop(shop_url: str) -> bool:
    return "svx.sk" in (shop_url or "").lower()


def _is_oramat_shop(shop_url: str) -> bool:
    return "oramat.sk" in (shop_url or "").lower()


def _apply_code_to_regex(pattern: str, code: str) -> str:
    return pattern.replace("{code}", re.escape(code))


def _config_needs_shop_preset(cfg: CompetitorScrapeConfig) -> bool:
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
        if "/search" in low or "search?q=" in low or "/katalog/?search" in low:
            return True
    if cfg.product_url_template:
        low = cfg.product_url_template.lower()
        if "/search" in low or "search?q=" in low:
            return True
    if not cfg.search_via_url_template and not cfg.product_url_template:
        return True
    return False


def resolve_competitor_scrape_config(shop_url: str, raw: str | None) -> CompetitorScrapeConfig:
    """Efektívna konfigurácia — známe e-shopy majú preset (SVX, Oramat)."""
    cfg = load_competitor_scrape_config(raw)
    if _is_svx_shop(shop_url):
        if _config_needs_shop_preset(cfg):
            return _SVX_SCRAPE_CONFIG
        return _merge_shop_preset(cfg, _SVX_SCRAPE_CONFIG)
    if _is_oramat_shop(shop_url):
        if _config_needs_shop_preset(cfg):
            return _ORAMAT_SCRAPE_CONFIG
        return _merge_shop_preset(cfg, _ORAMAT_SCRAPE_CONFIG)
    return cfg


def normalize_scrape_config_json_for_shop(shop_url: str, raw: str | None) -> str | None:
    """Pri uložení konkurenta vráti opravený JSON pre známe e-shopy."""
    text = (raw or "").strip()
    if _is_svx_shop(shop_url) and _config_needs_shop_preset(load_competitor_scrape_config(text)):
        return _SVX_SCRAPE_CONFIG_JSON
    if _is_oramat_shop(shop_url) and _config_needs_shop_preset(load_competitor_scrape_config(text)):
        return _ORAMAT_SCRAPE_CONFIG_JSON
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
    if _is_oramat_shop(base):
        return f"{base}/vyhladavanie/?string={enc}"
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
