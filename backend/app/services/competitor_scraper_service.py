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
    price_selector_regex: Optional[str] = None
    pack_quantity_selector_regex: Optional[str] = None
    user_agent: Optional[str] = None


_price_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 900.0


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
        price_selector_regex=_opt_str(parsed.get("price_selector_regex")),
        pack_quantity_selector_regex=_opt_str(parsed.get("pack_quantity_selector_regex")),
        user_agent=_opt_str(parsed.get("user_agent")),
    )


def _opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


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
            return tmpl.replace("{code}", enc)
    base = (shop_url or "").strip().rstrip("/")
    if not base:
        return None
    if config.search_via_url_template:
        t = config.search_via_url_template.strip()
        if "{code}" in t:
            return t.replace("{code}", enc)
        sep = "&" if "?" in t else "?"
        return f"{t}{sep}q={enc}"
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


def _extract_price_from_html(html: str, config: CompetitorScrapeConfig) -> tuple[Optional[float], Optional[str]]:
    h = html or ""
    if config.price_selector_regex:
        try:
            pat = re.compile(config.price_selector_regex, re.IGNORECASE | re.DOTALL)
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


def _extract_pack_quantity(html: str, config: CompetitorScrapeConfig) -> Optional[int]:
    if not config.pack_quantity_selector_regex:
        return None
    try:
        pat = re.compile(config.pack_quantity_selector_regex, re.IGNORECASE)
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

    cfg = load_competitor_scrape_config(scrape_config_json)
    target_url = competitor_product_url(shop_url, code, cfg)
    if not target_url:
        raise RuntimeError("Chýba URL e-shopu alebo šablóna produktu v scrape_config_json.")

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

    price_eur, raw_price = _extract_price_from_html(html, cfg)
    if price_eur is None:
        raise RuntimeError(
            f"Na stránke sa nepodarilo nájsť cenu (skontroluj scrape_config_json "
            f"price_selector_regex). URL={final_url[:120]!r}"
        )
    pack_q = _extract_pack_quantity(html, cfg)
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
