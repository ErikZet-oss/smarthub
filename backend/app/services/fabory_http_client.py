"""
Fabory B2B (fabory.com): Hybris + Spring Security, košík ako HTML.

HAR / skúsenosť:
  - POST {locale}/j_spring_security_check (j_username, j_password, _csrf)
  - GET {locale}/cart — riadky (formuláre updateCartFormN)
  - GET {locale}/cart/simulation — ceny (item-price / item__total bez DPH)
"""

from __future__ import annotations

import html as html_module
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_BASE = "https://www.fabory.com/sk"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_UPDATE_FORM_RE = re.compile(
    r'<form id="updateCartForm(\d+)"[^>]*data-cart="[^"]*"[^>]*>'
    r"[\s\S]*?name=\"productCode\"\s+value=\"([^\"]+)\""
    r"[\s\S]*?id=\"quantity_\d+\"[^>]*value=\"(\d+)\"",
    re.IGNORECASE,
)
_LINE_NET_RE = re.compile(
    r'item__total js-item-total[\s\S]*?excluding-price"><span>([^<]+)</span>',
    re.IGNORECASE,
)
_ITEM_PRICE_RE = re.compile(
    r'class="item-price"[^>]*>\s*<b>([^<]+)</b>',
    re.IGNORECASE,
)
_SUBTOTAL_RE = re.compile(
    r'class="subtotal-price"[\s\S]*?pull-right">\s*([^<]+)',
    re.IGNORECASE,
)
_GA_TOTAL_RE = re.compile(r'data-ga-total-price="([^"]+)"', re.IGNORECASE)
_LINE_COUNT_RE = re.compile(
    r'id="total-items-in-cart"[^>]*data-total-items="(\d+)"',
    re.IGNORECASE,
)
_GA_VARIANT_NAME_RE = re.compile(
    r'data-ga-product-name="([^"]+)"[\s\S]{0,400}?data-ga-variant="([^"]+)"',
    re.IGNORECASE,
)
_CSRF_RE = re.compile(
    r'name="_csrf"[^>]*value="([^"]+)"',
    re.IGNORECASE,
)
_DESC_RE = re.compile(
    r'class="item__description ">\s*([^<]{8,300})',
    re.IGNORECASE,
)


def fabory_shop_prefix(shop_url: str) -> str:
    """Origin + locale prefix, napr. ``https://www.fabory.com/sk``."""
    raw = (shop_url or "").strip()
    if not raw:
        return DEFAULT_BASE
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    host = p.netloc or "www.fabory.com"
    scheme = p.scheme or "https"
    path = (p.path or "/sk").strip("/")
    locale = path.split("/")[0] if path else "sk"
    return f"{scheme}://{host}/{locale}"


def fabory_cart_url(shop_url: str) -> str:
    return f"{fabory_shop_prefix(shop_url)}/cart/simulation"


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


def _fabory_ga_labels(page_html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _GA_VARIANT_NAME_RE.finditer(page_html or ""):
        label = html_module.unescape((m.group(1) or "").strip())
        code = (m.group(2) or "").strip()
        if code and label and code not in out:
            out[code] = label
    rev = re.compile(
        r'data-ga-variant="([^"]+)"[\s\S]{0,400}?data-ga-product-name="([^"]+)"',
        re.IGNORECASE,
    )
    for m in rev.finditer(page_html or ""):
        code = (m.group(1) or "").strip()
        label = html_module.unescape((m.group(2) or "").strip())
        if code and label and code not in out:
            out[code] = label
    return out


def fabory_parse_cart_html(
    simulation_html: str,
    *,
    cart_html: str = "",
) -> dict[str, Any]:
    """
    Z ``/cart/simulation`` (ceny) a voliteľne ``/cart`` — riadky, súčet bez DPH, počet.
    """
    sim = simulation_html or ""
    cart = cart_html or ""
    merged = sim if sim else cart
    if not merged.strip():
        return {"lines": [], "total_eur": None, "line_count": 0}

    form_hits = list(_UPDATE_FORM_RE.finditer(merged))
    form_hits.sort(key=lambda m: int(m.group(1)))
    ga_labels = _fabory_ga_labels(merged)
    item_prices = [_parse_eur_amount(x) for x in _ITEM_PRICE_RE.findall(sim)]

    lines: list[dict[str, Any]] = []
    for i, m in enumerate(form_hits):
        try:
            _entry = int(m.group(1))
        except (TypeError, ValueError):
            _entry = i
        code = (m.group(2) or "").strip()
        try:
            qty = int(m.group(3))
        except (TypeError, ValueError):
            qty = 1
        if not code or qty < 1:
            continue
        line_total = None
        if i < len(item_prices) and item_prices[i] is not None:
            line_total = item_prices[i]
        if line_total is None:
            end = m.end()
            after_end = (
                form_hits[i + 1].start()
                if i + 1 < len(form_hits)
                else min(len(merged), end + 12_000)
            )
            m_net = _LINE_NET_RE.search(merged[end:after_end])
            if m_net:
                line_total = _parse_eur_amount(m_net.group(1))
        unit_eur = None
        if line_total is not None and qty > 0:
            unit_eur = round(line_total / qty, 6)
        label = ga_labels.get(code) or ""
        if not label:
            dm = _DESC_RE.search(merged)
            if dm:
                label = html_module.unescape(dm.group(1).strip())
        if not label:
            label = code
        lines.append(
            {
                "label": label,
                "quantity": qty,
                "unit_price_eur": unit_eur,
                "line_total_eur": line_total,
                "variant_code": code,
            }
        )

    line_count = 0
    m_lc = _LINE_COUNT_RE.search(merged)
    if m_lc:
        try:
            line_count = int(m_lc.group(1))
        except (TypeError, ValueError):
            line_count = 0
    if line_count <= 0:
        line_count = len(lines)

    total_eur: Optional[float] = None
    m_sub = _SUBTOTAL_RE.search(sim)
    if m_sub:
        total_eur = _parse_eur_amount(m_sub.group(1))
    if total_eur is None:
        m_ga = _GA_TOTAL_RE.search(sim)
        if m_ga:
            total_eur = _parse_eur_amount(m_ga.group(1))
    line_nets_all = [_parse_eur_amount(x) for x in _LINE_NET_RE.findall(sim)]
    if total_eur is None and line_nets_all:
        total_eur = round(
            sum(x for x in line_nets_all if x is not None),
            4,
        )
    if total_eur is None and lines:
        total_eur = round(
            sum((ln.get("line_total_eur") or 0.0) for ln in lines),
            4,
        )

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": line_count,
    }


class FaboryHttpClient:
    def __init__(self, shop_url: str) -> None:
        self._prefix = fabory_shop_prefix(shop_url)
        self._client = httpx.AsyncClient(
            base_url=self._prefix,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "sk,sk-SK;q=0.9,cs;q=0.8,en;q=0.7",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(45.0),
        )
        self._login_ok = False

    async def __aenter__(self) -> FaboryHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def ensure_login(self, username: str, password: str) -> None:
        if self._login_ok:
            return
        user = (username or "").strip()
        pwd = password or ""
        if not user or not pwd:
            raise ValueError("Fabory: chýba meno alebo heslo.")
        login_html = (await self._client.get("/login")).text
        if "faboryLoginForm" not in login_html and "j_username" not in login_html:
            raise RuntimeError("Fabory: stránka prihlásenia nemá očakávaný formulár.")
        csrf_m = _CSRF_RE.search(login_html)
        data: dict[str, str] = {"j_username": user, "j_password": pwd}
        if csrf_m:
            data["_csrf"] = csrf_m.group(1).strip()
        r = await self._client.post(
            "/j_spring_security_check",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{self._prefix}/login",
            },
        )
        r.raise_for_status()
        probe = await self._client.get("/cart")
        body = probe.text or ""
        if probe.url.path.endswith("/login") or "faboryLoginForm" in body:
            raise RuntimeError(
                "Fabory: prihlásenie zlyhalo (stále login stránka). Skontroluj údaje."
            )
        self._login_ok = True

    async def fetch_cart_snapshot(self) -> dict[str, Any]:
        cart_r = await self._client.get("/cart")
        cart_r.raise_for_status()
        sim_r = await self._client.get("/cart/simulation")
        sim_r.raise_for_status()
        cart_html = cart_r.text or ""
        sim_html = sim_r.text or ""
        if "faboryLoginForm" in sim_html or sim_r.url.path.endswith("/login"):
            raise RuntimeError("Fabory: košík vyžaduje prihlásenie.")
        return {
            "cart_html": cart_html,
            "simulation_html": sim_html,
        }
