"""
Fabory B2B (fabory.com): Hybris + Spring Security, košík ako HTML.

HAR / skúsenosť:
  - POST {locale}/j_spring_security_check (j_username, j_password, _csrf)
  - GET {locale}/cart — riadky (formuláre updateCartFormN)
  - GET {locale}/cart/simulation — ceny (item-price / item__total bez DPH)
  - POST {locale}/product/price — JSON ["<code>", ...] → ceny per zákazník
  - POST {locale}/product/stock — JSON {"pageType":"ADPG","materialCodes":[...]}
    Tieto dva endpointy fronend volá z PDP cez XMLHttpRequest. Vrátia presne to,
    čo užívateľ vidí na produktovej stránke — bez nutnosti otvárať Chromium.
"""

from __future__ import annotations

import asyncio
import html as html_module
import json
import re
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlparse

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


def fabory_pdp_search_url(shop_url: str, code: str) -> str:
    """Search URL, ktorá 301-uje na PDP. Slúži pre kanonickú PDP-URL (Referer header)."""
    enc = quote((code or "").strip(), safe=".-_~")
    return f"{fabory_shop_prefix(shop_url)}/search/?text={enc}"


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


def _fabory_product_title_from_pdp_html(html: str) -> Optional[str]:
    """Názov z PDP (h1 / itemprop / data-ga-product-name) — price API nemusí poslať productName."""
    h = html or ""
    for pat in (
        r'itemprop=["\']name["\'][^>]*content=["\']([^"\']+)["\']',
        r'itemprop=["\']name["\'][^>]*>([^<]+)',
        r'data-ga-product-name=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, h, re.I)
        if m:
            t = html_module.unescape(re.sub(r"\s+", " ", (m.group(1) or "").strip()))
            if len(t) >= 3 and not re.fullmatch(r"[\d.\-\s]+", t):
                return t[:500]
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.I | re.S)
    if m:
        t = re.sub(r"<[^>]+>", " ", m.group(1))
        t = html_module.unescape(re.sub(r"\s+", " ", t).strip())
        if len(t) >= 3 and not re.fullmatch(r"[\d.\-\s]+", t):
            return t[:500]
    return None


_ALP_ADD_TO_CART_VALUE_RE = re.compile(
    r'class="[^"]*\balp-add-to-cart\b[^"]*"[^>]*\bvalue=["\'](\d+)["\']',
    re.IGNORECASE,
)
_ALP_ADD_TO_CART_VALUE_REV_RE = re.compile(
    r'\bvalue=["\'](\d+)["\'][^>]*class="[^"]*\balp-add-to-cart\b',
    re.IGNORECASE,
)
_FABORY_PACK_LABEL_RE = re.compile(
    r"(?:Ambalat|Balen[íi]|Balenie|Pack(?:age)?|Verpackung)[^0-9]{0,40}(\d+)",
    re.IGNORECASE,
)


def _fabory_pack_quantity_from_pdp_html(html: str) -> Optional[int]:
    """Ks v jednom balení — z inputu ``alp-add-to-cart`` na PDP (nie ``unitQuantity`` z price API).

    Price API vracia ``unitQuantity`` ako základ ceny (typicky 100 ks). Objednávacie
    balenie môže byť menšie (napr. M30×90 → 10 ks pri cene za 100).
    """
    h = html or ""
    for pat in (_ALP_ADD_TO_CART_VALUE_RE, _ALP_ADD_TO_CART_VALUE_REV_RE):
        m = pat.search(h)
        if m:
            try:
                pq = int(m.group(1))
                if pq >= 1:
                    return pq
            except (TypeError, ValueError):
                pass
    m = _FABORY_PACK_LABEL_RE.search(h)
    if m:
        try:
            pq = int(m.group(1))
            if pq >= 1:
                return pq
        except (TypeError, ValueError):
            pass
    return None


def _fabory_price_for_pack(
    unit_net: Optional[float],
    *,
    unit_quantity: int,
    pack_quantity: int,
) -> Optional[float]:
    if unit_net is None:
        return None
    uq = max(1, int(unit_quantity))
    pq = max(1, int(pack_quantity))
    if pq == uq:
        return float(unit_net)
    return round(float(unit_net) * pq / uq, 4)


def _fabory_catalog_price_per_100(
    unit_net: Optional[float], unit_quantity: int
) -> Optional[float]:
    """Cena ako na PDP Fabory — vždy prepočítaná na 100 ks (``cena za 100``)."""
    if unit_net is None:
        return None
    uq = max(1, int(unit_quantity))
    if uq == 100:
        return round(float(unit_net), 4)
    return round(float(unit_net) * 100.0 / uq, 4)


def _fabory_price_unit_key(unit_quantity: int) -> str:
    """Fabory v UI vždy zobrazuje cenu za 100 ks (label na PDP)."""
    _ = unit_quantity
    return "per_100_ks"


def _fabory_format_net_price_eur(amount: float) -> str:
    """Slovenský formát ako Fabory API (``11,80\u00a0€``)."""
    s = f"{amount:.2f}".replace(".", ",")
    return f"{s}\u00a0€"


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
    if not lines and line_count <= 0:
        line_count = 0
        if total_eur is None:
            total_eur = 0.0

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": line_count,
        "empty_cart": line_count <= 0 and not lines,
    }


class FaboryHttpClient:
    def __init__(self, shop_url: str) -> None:
        self._prefix = fabory_shop_prefix(shop_url)
        self._shop_url = (shop_url or "").strip()
        # HTTP/1.1: pri HTTP/2 + keep-alive pooli (Render, paralelný dopyt) Fabory/CDN
        # občas zavrie spojenie a ďalší request skončí
        # ``ConnectionState.CLOSED / SEND_HEADERS`` — produkt pritom na webe existuje.
        self._client = self._build_httpx_client()
        self._login_ok = False
        self._last_pdp_url: Optional[str] = None
        self._lock = asyncio.Lock()

    def _build_httpx_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._prefix,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "sk,sk-SK;q=0.9,cs;q=0.8,en;q=0.7",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(45.0, connect=10.0),
            http2=False,
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )

    async def _reset_transport(self) -> None:
        """Zahoď rozbité TCP/H2 spojenie — ďalší request vytvorí nové."""
        self._login_ok = False
        try:
            await self._client.aclose()
        except Exception:
            pass
        self._client = self._build_httpx_client()

    @property
    def login_ok(self) -> bool:
        return self._login_ok

    @property
    def prefix(self) -> str:
        return self._prefix

    async def __aenter__(self) -> FaboryHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

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

    async def _fetch_pdp_html(self, pdp_url: str) -> str:
        p = urlparse(pdp_url)
        path = p.path or "/"
        if p.query:
            path = f"{path}?{p.query}"
        r = await self._client.get(path)
        r.raise_for_status()
        return r.text or ""

    async def _resolve_pdp_url(self, code: str) -> Optional[str]:
        """`/search/?text=<code>` 301-uje na PDP. Stačí spraviť HEAD a vrátiť finálnu URL.
        Používa sa len ako Referer pre POST /product/price (server tým validuje kontext)."""
        c = (code or "").strip()
        if not c:
            return None
        try:
            r = await self._client.get(
                f"/search/?text={quote(c, safe='.-_~')}",
                follow_redirects=True,
            )
            final = str(r.url)
            if "/p/" in final:
                self._last_pdp_url = final
                return final
        except Exception:
            return None
        return None

    async def _post_json(
        self,
        path: str,
        payload: Any,
        *,
        referer: Optional[str] = None,
    ) -> Any:
        """POST cez XHR — Fabory front-end nepoužíva CSRF na tieto endpointy (HAR ukazuje
        prázdny `x-csrf-token`), ale `x-requested-with` a `accept` musia byť JSON-friendly."""
        body = json.dumps(payload, ensure_ascii=False)
        ref = referer or self._last_pdp_url or self._prefix
        r = await self._client.post(
            path,
            content=body,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/json",
                "Origin": f"https://{urlparse(self._prefix).netloc}",
                "Referer": ref,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if r.status_code >= 400:
            # Bez raw body sa nedá zistiť, prečo Fabory backend vrátil 5xx
            # (často signalizuje vypadnutú session, zmenený payload formát
            # alebo bot-detekciu). Detailný excerpt sa propaguje do logu aj do UI.
            body_excerpt = (r.text or "").strip().replace("\n", " ")[:400]
            ref_short = (ref or "")[:160]
            raise RuntimeError(
                f"Fabory {path} → HTTP {r.status_code} "
                f"(referer={ref_short!r}, payload={body[:120]!r}, "
                f"odpoveď: {body_excerpt!r})"
            )
        txt = (r.text or "").strip()
        if not txt:
            return {}
        try:
            return r.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Fabory {path}: neočakávaná odpoveď ({len(txt)} B).") from exc

    @staticmethod
    def _fabory_api_code(code: str) -> str:
        """API endpointy `/product/price` a `/product/stock` chcú materialCode bez bodiek.

        Excel/UI nesie kód v ľudskom formáte ``51010.120.016``, Fabory v URL aj
        v Hybris API pracuje s ``51010120016``. Bez normalizácie API hádže 500
        (server-side NumberFormatException).
        """
        return re.sub(r"[.\-\s]", "", (code or "").strip())

    async def fetch_prices(
        self,
        codes: Iterable[str],
        *,
        referer: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch: `POST /product/price` s poľom kódov. Vráti `{code: {...}}`."""
        raw_items = [c.strip() for c in codes if c and c.strip()]
        if not raw_items:
            return {}
        items = [self._fabory_api_code(c) for c in raw_items]
        data = await self._post_json("/product/price", items, referer=referer)
        if not isinstance(data, dict):
            return {}
        # Server vracia normalizovaný kľúč (bez bodiek). Vrátime kľúč aj
        # v pôvodnom tvare, aby downstream kód mohol pýtať obe varianty.
        out: dict[str, Any] = dict(data)
        for raw, norm in zip(raw_items, items):
            if raw != norm and norm in data and raw not in out:
                out[raw] = data[norm]
        return out

    async def fetch_stock(
        self,
        codes: Iterable[str],
        *,
        page_type: str = "ADPG",
        referer: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch: `POST /product/stock` s {materialCodes:[...]}. Vráti `{code: {...}}`."""
        raw_items = [c.strip() for c in codes if c and c.strip()]
        if not raw_items:
            return {}
        items = [self._fabory_api_code(c) for c in raw_items]
        data = await self._post_json(
            "/product/stock",
            {"pageType": page_type, "materialCodes": items},
            referer=referer,
        )
        if not isinstance(data, dict):
            return {}
        out: dict[str, Any] = dict(data)
        for raw, norm in zip(raw_items, items):
            if raw != norm and norm in data and raw not in out:
                out[raw] = data[norm]
        return out

    async def fetch_product_price_and_stock(self, code: str) -> dict[str, Any]:
        """Pre jeden kód získa cenu aj sklad v ~2 paralelných XHR-och.

        Návratový formát zodpovedá ostatným supplier HTTP cestám (Hopefix, Halfmann):
        ``price_eur``, ``stock``, ``pack_quantity`` + ``raw_*`` + ``packaging_variants``.
        Cena je za jedno objednávateľné balenie (``pack_quantity`` ks z PDP). Price API
        uvádza ``unitNetPrice`` za ``unitQuantity`` ks (často 100) — pri menšom balení
        cenu proporcionálne prepočítame.
        """
        async with self._lock:
            return await self._fetch_product_price_and_stock_unlocked(code)

    async def _fetch_product_price_and_stock_unlocked(self, code: str) -> dict[str, Any]:
        c = (code or "").strip()
        if not c:
            raise ValueError("Fabory price/stock: prázdny kód.")

        try:
            await self._resolve_pdp_url(c)
        except Exception:
            self._last_pdp_url = None
        ref = self._last_pdp_url

        # Paralelné POST — Fabory front-end ich tiež strieľa naraz; lock vyššie bráni
        # viacerým dopytom naraz na tom istom pooled klientovi (inquiry batch).
        price_task = self.fetch_prices([c], referer=ref)
        stock_task = self.fetch_stock([c], referer=ref)
        price_map, stock_map = await asyncio.gather(price_task, stock_task)

        if c not in price_map:
            raise RuntimeError(
                f"Fabory: cena pre kód {c!r} nedostupná (B2B účet nemusí mať tento materiál). "
                f"PDP={self._last_pdp_url or 'neznáme'}"
            )
        p = price_map.get(c) or {}
        s = stock_map.get(c) or {}

        unit_quantity = int(p.get("unitQuantity") or 1)
        if unit_quantity < 1:
            unit_quantity = 1
        unit_net = p.get("unitNetPrice")
        try:
            unit_net_f = float(unit_net) if unit_net is not None else None
        except (TypeError, ValueError):
            unit_net_f = None

        pdp_html = ""
        if self._last_pdp_url:
            try:
                pdp_html = await self._fetch_pdp_html(self._last_pdp_url)
            except Exception:
                pdp_html = ""

        pack_quantity = _fabory_pack_quantity_from_pdp_html(pdp_html) or unit_quantity
        pack_price_eur = _fabory_price_for_pack(
            unit_net_f,
            unit_quantity=unit_quantity,
            pack_quantity=pack_quantity,
        )
        # PDP Fabory: „cena za 100“ — unitNetPrice platí pre unitQuantity z API (50/100/200…).
        price_eur = _fabory_catalog_price_per_100(unit_net_f, unit_quantity)
        price_unit = _fabory_price_unit_key(unit_quantity)
        raw_price = p.get("formattedUnitNetPrice") or None
        if price_eur is not None and unit_quantity != 100:
            raw_price = _fabory_format_net_price_eur(price_eur)

        stock_status = (s.get("stockLevelStatus") or "").upper()
        stock_qty_raw = s.get("stockQuantity")
        try:
            stock_qty = int(stock_qty_raw) if stock_qty_raw is not None else 0
        except (TypeError, ValueError):
            stock_qty = 0
        # Fabory pre B2B niekedy vracia stockQuantity=0 aj keď je INSTOCK (vyrába sa na zákazku).
        # Zachováme "1" ako "dostupný", "0" len ak je explicitne OUTOFSTOCK alebo NOT_AVAILABLE.
        if stock_status in ("OUTOFSTOCK", "NOT_AVAILABLE", "OUT_OF_STOCK"):
            stock_final: Optional[int] = 0
        elif stock_qty > 0:
            stock_final = stock_qty
        elif stock_status == "INSTOCK":
            stock_final = 1
        else:
            stock_final = None
        raw_stock = (s.get("stockLevelMessage") or "").strip() or None

        title = (p.get("productName") or "").strip() or None
        if not title and pdp_html:
            title = _fabory_product_title_from_pdp_html(pdp_html)
        label = title

        packaging_variants = [
            {
                "label": label or c,
                "pack_quantity": pack_quantity,
                "price_eur": price_eur,
                "pack_price_eur": pack_price_eur,
                "price_unit": price_unit,
                "raw_price": raw_price,
                "stock": stock_final,
                "raw_stock": raw_stock,
                "currency_symbol": "€",
            }
        ]
        return {
            "price_eur": price_eur,
            "pack_price_eur": pack_price_eur,
            "price_unit": price_unit,
            "stock": stock_final,
            "pack_quantity": pack_quantity,
            "raw_price": raw_price,
            "raw_stock": raw_stock,
            "raw_pack_quantity": str(pack_quantity),
            "packaging_variants": packaging_variants,
            "logged_in": True,
            "fabory_via_http": True,
            "pdp_url": self._last_pdp_url,
            "product_title": title,
            "label": label,
            "currency": (p.get("currencyIso") or "EUR"),
            "currency_symbol": "€",
        }
