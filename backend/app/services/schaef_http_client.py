"""
Schäfer-Peters B2B (shop.schaefer-peters.com): DCShop PHP, kombinovaný HTML + JSON tok.

HAR / skúsenosť:
  - GET  ``/sp/en/home/`` — zoznamy cookies + form bootstrap (anonymný)
  - POST ``/b2b/en/?action=shop_login`` — form-encoded ``action=shop_login``,
    ``catalog_selected_item=``, ``input_login``, ``input_password``. Set-Cookie sa
    nastaví na 200 (response body je prázdny → kontrola login úspechu = re-fetch PDP).
  - GET  ``/b2b/en/search/?SP_B2B_LIVE_ENU[query]=<code>&searchAlgolia=<code>``
    Server presmeruje (302) priamo na PDP, ak má exact match na článkové číslo.
  - GET  ``/b2b/en/.../p<id>/`` — PDP s cenou, skladom, packagingom a hidden ``item_id``.
  - POST ``/module/dcshop/GeneralAjaxData.php?function=cart&site=b2b&language=en``
    multipart/form-data: ``item_id``, ``item_var_code``, ``item_qty``,
    ``action=shop_add_item_to_basket_card`` (+ ``optional_performances_item_id``
    opakovaná dvojica pre certifikáty, ktoré nezapíname). Server vracia 302 →
    ``function=readCart`` JSON s ``headerBasketContent`` (basketCount).

Pre náš FE: každý PDP má **jediný** „packaging variant" (1 cena za pack_quantity ks).
Schäfer interne odkazuje ceny po 100 ks (``priceLabel = "Price 100 Pcs."``), ale
order step je menší (typicky 50 ks). Do FE posielame ``pack_quantity`` zhodné s
referenciou ceny, a ``order_step`` ako voliteľný hint (UI ho má len pre validáciu).
"""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

DEFAULT_BASE = "https://shop.schaefer-peters.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# Algolia kľúče zo Schäf front-endu (public read-only, dostupné v HAR-e i v JS
# bundlovi v ``/layout/frontend/.../algolia-config.js``). Použité na rýchle
# rozlíšenie itemId + PDP slugu bez nutnosti chodiť cez server-side search.
SCHAEF_ALGOLIA_APP_ID = "KLANID5F8G"
SCHAEF_ALGOLIA_API_KEY = "49cac20b0727fe2f5d4e3cd79be617d1"
SCHAEF_ALGOLIA_INDEX = "SP_B2B_LIVE_ENU"
SCHAEF_ALGOLIA_HOST = f"https://{SCHAEF_ALGOLIA_APP_ID.lower()}-dsn.algolia.net"

# --- Regulárky na parsing PDP -------------------------------------------------
_ITEM_ID_RE = re.compile(
    r'<input\s+name=["\']item_id["\']\s+value=["\'](\d+)["\']',
    re.IGNORECASE,
)
_ITEM_VAR_CODE_RE = re.compile(
    r'<input\s+name=["\']item_var_code["\']\s+[^>]*value=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_PRICE_CONTENT_RE = re.compile(
    r'itemprop=["\']price["\']\s+content=["\']([0-9]+(?:\.[0-9]+)?)["\']',
    re.IGNORECASE,
)
_PRICE_LABEL_RE = re.compile(
    r'class=["\']priceLabel["\'][^>]*>\s*([^<]+?)\s*<',
    re.IGNORECASE,
)
_BASE_PRICE_TEXT_RE = re.compile(
    r'class=["\']basePrice["\'][^>]*>([\s\S]{0,400}?)</div>',
    re.IGNORECASE,
)
_AVAILABILITY_RE = re.compile(
    r'itemprop=["\']availability["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# „<p>384.500 Pcs.</p>" v sekcii onlineInventory__label (po itemprop="availability").
_INVENTORY_AMOUNT_RE = re.compile(
    r'class=["\']onlineInventory__amount["\'][\s\S]{0,1500}?'
    r"<p>\s*([0-9][0-9\.,]*)\s*([A-Za-z\u00B5\u20AC\u00A0\.]*)\s*</p>",
    re.IGNORECASE,
)
_INVENTORY_LABEL_FALLBACK_RE = re.compile(
    r'class=["\']onlineInventory__label["\'][\s\S]{0,200}?'
    r"<p>\s*([0-9][0-9\.,]*)\s*([A-Za-z\u00B5\u20AC\u00A0\.]*)\s*</p>",
    re.IGNORECASE,
)
_PACKAGING_BOX_RE = re.compile(
    r'id=["\']js-packagingBox["\'][^>]*>\s*<i[^>]*></i>\s*([0-9]+)',
    re.IGNORECASE,
)
_QTY_INPUT_RE = re.compile(
    r'<input[^>]*name=["\']item_qty["\']'
    r'(?:[^>]*?value=["\'](\d+)["\']|[^>]*?min=["\'](\d+)["\']|[^>]*?step=["\'](\d+)["\']){1,3}',
    re.IGNORECASE,
)
_QTY_MIN_RE = re.compile(
    r'<input[^>]*name=["\']item_qty["\'][^>]*?\bmin=["\'](\d+)["\']',
    re.IGNORECASE,
)
_QTY_STEP_RE = re.compile(
    r'<input[^>]*name=["\']item_qty["\'][^>]*?\bstep=["\'](\d+)["\']',
    re.IGNORECASE,
)
_QTY_VALUE_RE = re.compile(
    r'<input[^>]*name=["\']item_qty["\'][^>]*?\bvalue=["\'](\d+)["\']',
    re.IGNORECASE,
)
_AMOUNT_ITEM_NUMBER_RE = re.compile(
    r"Item number[\s\S]{0,160}?"
    r'class=["\']amountLine__value["\'][^>]*>\s*([^<]+)\s*<',
    re.IGNORECASE,
)
_CANONICAL_URL_RE = re.compile(
    r'rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_PRODUCT_NAME_CONTENT_RE = re.compile(
    r'itemprop=["\']name["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_PRODUCT_HEADLINE_RE = re.compile(
    r'class=["\']itemcardHeadline["\'][^>]*>\s*([^<]+)',
    re.IGNORECASE,
)
_PRODUCT_TITLE_CLASS_RE = re.compile(
    r'class=["\'][^"\']*(?:productTitle|itemcard__title|itemcardTitle)[^"\']*["\'][^>]*>\s*([^<]+)',
    re.IGNORECASE,
)
_PAGE_H1_RE = re.compile(
    r"<h1[^>]*>\s*([^<]{3,240})",
    re.IGNORECASE,
)
_INSPECTION_PERF_RE = re.compile(
    r"input_ihk_certificate_\d+",
    re.IGNORECASE,
)


def schaef_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return DEFAULT_BASE
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if not p.netloc:
        return DEFAULT_BASE
    return f"{p.scheme or 'https'}://{p.netloc}".rstrip("/")


# --- Parser pomocné funkcie ---------------------------------------------------


def _parse_european_int(text: str) -> Optional[int]:
    """Schäfer používa „384.500 Pcs." kde „." je oddeľovač tisícov.

    Akceptuje aj „384,500" (zriedkavé), „384500" a vráti integer. Pre desatinné
    čísla typu „384.500,5" odhodí desatinnú časť (zvýši to len mierne nepresnosť
    pri stock-ks, ale tých nikdy nie sú zlomky).
    """
    s = (text or "").strip()
    if not s:
        return None
    s = s.replace("\u00a0", "").replace(" ", "")
    # „384.500,5" → integer = 384500
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").split(",", 1)[0]
        else:
            s = s.replace(",", "").split(".", 1)[0]
    elif "," in s:
        # Predpokladaj európsky desatinný formát, ber celú časť pred „,".
        s = s.split(",", 1)[0]
    else:
        # Iba „." → tisíce. „384.500" → 384500
        s = s.replace(".", "")
    if not s.isdigit():
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_european_decimal(text: str) -> Optional[float]:
    s = (text or "").strip().replace("\u00a0", "").replace(" ", "")
    if not s:
        return None
    # Ak je tam aj „." aj „,", posledný oddeľovač je desatinný.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    s = re.sub(r"<[^>]+>", " ", text or "")
    s = html_module.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def schaef_title_from_pdp_path(path: str) -> Optional[str]:
    """Z URL slugu typu ``din-933-a2-70-m-12x40-p133791`` — záložný názov."""
    m = re.search(r"/([^/?#]+)-p\d+/?", path or "", re.I)
    if not m:
        return None
    slug = m.group(1).strip()
    if not slug or slug.isdigit():
        return None
    title = re.sub(r"\s+", " ", slug.replace("-", " ")).strip().upper()
    return title if len(title) >= 3 else None


def schaef_parse_product_title(
    html: str,
    *,
    product_title_hint: Optional[str] = None,
    pdp_path: Optional[str] = None,
) -> Optional[str]:
    hint = (product_title_hint or "").strip()
    if hint:
        return hint
    for pat in (
        _PRODUCT_NAME_CONTENT_RE,
        _PRODUCT_HEADLINE_RE,
        _PRODUCT_TITLE_CLASS_RE,
        _PAGE_H1_RE,
    ):
        m = pat.search(html or "")
        if not m:
            continue
        title = _strip_html(m.group(1))
        low = title.lower()
        if title and low not in (
            "schäfer-peters",
            "schaefer-peters",
            "schafer-peters",
            "schäfer + peters",
        ):
            return title
    return schaef_title_from_pdp_path(pdp_path or "")


def schaef_parse_pdp_html(
    html: str,
    *,
    product_title_hint: Optional[str] = None,
    pdp_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Extrahuje cenu, sklad, pack_quantity a ``item_id`` (potrebné pre košík).

    Vracia normalizovaný dict v rovnakom tvare, aký konzumuje scraper pipeline
    (price_eur per pack_quantity, raw_price na zobrazenie, packaging_variants).
    """
    html = html or ""
    item_id: Optional[str] = None
    m = _ITEM_ID_RE.search(html)
    if m:
        item_id = m.group(1).strip() or None

    item_var_code = ""
    mv = _ITEM_VAR_CODE_RE.search(html)
    if mv:
        item_var_code = (mv.group(1) or "").strip()

    # Cena: itemprop="price" content="12.2598" — presnejšie než zobrazená „12,26 €".
    price_eur: Optional[float] = None
    mp = _PRICE_CONTENT_RE.search(html)
    if mp:
        try:
            price_eur = round(float(mp.group(1)), 4)
        except ValueError:
            price_eur = None

    # Pack quantity: z labelu „Price 100 Pcs." (alebo „Price 1000 Pcs.")
    pack_quantity = 1
    ml = _PRICE_LABEL_RE.search(html)
    if ml:
        digits = re.search(r"(\d{1,6})", ml.group(1) or "")
        if digits:
            try:
                pq = int(digits.group(1))
                if pq >= 1:
                    pack_quantity = pq
            except ValueError:
                pass

    # Raw price na zobrazenie: text vo vnútri .basePrice
    raw_price: Optional[str] = None
    mb = _BASE_PRICE_TEXT_RE.search(html)
    if mb:
        text = _strip_html(mb.group(1))
        # „EUR 12,26 €" alebo „12,26 €" — vyber posledný cenový token
        m2 = re.search(r"-?\d[\d \u00a0\.,]*\s*€", text)
        if m2:
            raw_price = m2.group(0).strip()
    if not raw_price and price_eur is not None:
        # Fallback: zostav „X,YY € / N Pcs."
        eu = f"{price_eur:.2f}".replace(".", ",")
        raw_price = f"{eu} € / {pack_quantity} Pcs."

    # Sklad: hľadáme blok onlineInventory__amount → onlineInventory__label → <p>NNN Pcs.</p>
    stock: Optional[int] = None
    raw_stock: Optional[str] = None
    mi = _INVENTORY_AMOUNT_RE.search(html)
    if not mi:
        mi = _INVENTORY_LABEL_FALLBACK_RE.search(html)
    if mi:
        raw_qty = (mi.group(1) or "").strip()
        unit = (mi.group(2) or "").strip()
        stock = _parse_european_int(raw_qty)
        if raw_qty:
            raw_stock = f"{raw_qty} {unit}".strip() if unit else raw_qty
    # Availability schema (InStock / OutOfStock / OnRequest)
    avail = ""
    ma = _AVAILABILITY_RE.search(html)
    if ma:
        avail = (ma.group(1) or "").strip()
    if stock is None and "InStock" in avail:
        stock = 1
    if stock is None and avail and "OutOfStock" in avail:
        stock = 0
    if raw_stock is None and avail:
        raw_stock = avail.rsplit("/", 1)[-1]

    # Order step / min — informačné, FE ho použije na validáciu
    order_step = 1
    om = _QTY_STEP_RE.search(html)
    if om:
        try:
            order_step = max(1, int(om.group(1)))
        except ValueError:
            order_step = 1
    order_min = order_step
    om2 = _QTY_MIN_RE.search(html)
    if om2:
        try:
            order_min = max(1, int(om2.group(1)))
        except ValueError:
            order_min = order_step

    # Parcel size (z PDP „packagingBox") — len pre raw_pack_quantity zobrazenie.
    parcel_size: Optional[int] = None
    mp2 = _PACKAGING_BOX_RE.search(html)
    if mp2:
        try:
            parcel_size = max(1, int(mp2.group(1)))
        except ValueError:
            parcel_size = None

    # Item number (supplier code v ľudskom tvare, len pre logy / label).
    supplier_label: Optional[str] = None
    mn = _AMOUNT_ITEM_NUMBER_RE.search(html)
    if mn:
        supplier_label = _strip_html(mn.group(1)) or None

    canonical_path: Optional[str] = None
    mc = _CANONICAL_URL_RE.search(html)
    if mc:
        url = (mc.group(1) or "").strip()
        path = urlparse(url).path if url else ""
        if path:
            canonical_path = path

    product_title = schaef_parse_product_title(
        html,
        product_title_hint=product_title_hint,
        pdp_path=pdp_path or canonical_path,
    )
    display_label = product_title or supplier_label
    if not display_label or display_label.lower() in (
        "schäfer-peters",
        "schaefer-peters",
        "schafer-peters",
    ):
        display_label = product_title or supplier_label or None

    if pack_quantity > 1:
        packaging_label = f"{pack_quantity} ks"
    else:
        packaging_label = "1 ks"

    raw_pack_quantity = None
    if parcel_size and parcel_size != pack_quantity:
        raw_pack_quantity = f"Min. {order_min} ks · krok {order_step} ks · krabica {parcel_size} ks"
    elif order_step > 1 and order_step != pack_quantity:
        raw_pack_quantity = f"Min. {order_min} ks · krok {order_step} ks"

    variant: dict[str, Any] = {
        "label": display_label,
        "pack_quantity": pack_quantity,
        "price_eur": price_eur,
        "raw_price": raw_price,
        "stock": stock,
        "raw_stock": raw_stock,
        "currency_code": "eur",
        "currency_symbol": "€",
        "packaging_label": packaging_label,
        "schaef_item_id": item_id,
        "schaef_item_var_code": item_var_code,
        "schaef_pdp_path": canonical_path or pdp_path,
        "schaef_referer_path": canonical_path or pdp_path,
        "schaef_order_step": order_step,
        "schaef_order_min": order_min,
        "schaef_parcel_size": parcel_size,
    }
    if raw_pack_quantity:
        variant["raw_pack_quantity"] = raw_pack_quantity

    return {
        "price_eur": price_eur,
        "raw_price": raw_price,
        "pack_quantity": pack_quantity,
        "stock": stock,
        "raw_stock": raw_stock,
        "product_title": product_title or display_label,
        "currency_code": "eur",
        "currency_symbol": "€",
        "packaging_variants": [variant],
        "schaef_item_id": item_id,
        "schaef_item_var_code": item_var_code,
        "schaef_pdp_path": canonical_path or pdp_path,
        "schaef_referer_path": canonical_path or pdp_path,
        "schaef_order_step": order_step,
        "schaef_order_min": order_min,
        "schaef_parcel_size": parcel_size,
        "schaef_via_http": True,
        "logged_in": True,
    }


def schaef_round_qty_to_step(pieces: int, step: int, minimum: int) -> int:
    """Zaokrúhli ks nahor na najbližší násobok kroku, minimálne ``min`` ks.

    UI Schäfera neumožní odoslať formulár s qty mimo ``step``. My to robíme aj
    server-side, aby cart nikdy nedostal nevalidnú hodnotu.
    """
    pc = int(pieces) if pieces else 0
    st = max(1, int(step) if step else 1)
    mn = max(st, int(minimum) if minimum else st)
    if pc < mn:
        pc = mn
    rem = pc % st
    if rem:
        pc += st - rem
    return pc


class SchaefHttpClient:
    """Per-process HTTP client pre Schäfer-Peters B2B.

    Po ``ensure_login`` drží session cookies — všetky ďalšie requesty (PDP /
    cart-add) chodia ako prihlásený zákazník. ``fetch_product_price_and_stock``
    vráti ``schaef_parse_pdp_html`` z PDP nájdeného search redirectom.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE,
        user_agent: str = DEFAULT_UA,
        timeout: float = 45.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sk,sk-SK;q=0.9,cs;q=0.8,en;q=0.7",
        }
        try:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                follow_redirects=True,
                timeout=httpx.Timeout(timeout, connect=8.0),
                http2=True,
            )
        except (ImportError, RuntimeError):
            self._client = httpx.AsyncClient(
                base_url=self._base,
                headers=self._headers,
                follow_redirects=True,
                timeout=httpx.Timeout(timeout, connect=8.0),
            )
        self._login_ok = False

    @property
    def login_ok(self) -> bool:
        return self._login_ok

    @property
    def base(self) -> str:
        return self._base

    async def __aenter__(self) -> "SchaefHttpClient":
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
            raise ValueError("Schäfer-Peters: chýba meno alebo heslo.")

        # 1) bootstrap session (cookie) — bez tohto občas login mlčky neprejde.
        await self._client.get("/sp/en/home/")

        # 2) login: action=shop_login&catalog_selected_item=&input_login=...&input_password=...
        r = await self._client.post(
            "/b2b/en/?action=shop_login",
            data={
                "action": "shop_login",
                "catalog_selected_item": "",
                "input_login": user,
                "input_password": pwd,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self._base,
                "Referer": f"{self._base}/sp/en/home/",
            },
        )
        # Server vracia 200 (alebo 302→200 cez follow_redirects). HTML formulár ostáva
        # s 'input_login' iba ak prihlásenie zlyhalo. Po úspechu sa stránka pretočí
        # na profil/home a tieto polia tam nie sú.
        body_login = r.text or ""
        login_form_still_visible = (
            'name="input_login"' in body_login and 'name="input_password"' in body_login
        )
        # Probe: skús účet (uvidíme „Logout"/„My account"); pre B2B menu hľadáme „logout".
        probe = await self._client.get("/b2b/en/")
        probe_body = (probe.text or "").lower()
        logged = (
            "logout" in probe_body
            or "?action=shop_logout" in probe_body
            or 'class="iconbaricon iconbaricon--account' in probe_body
        )
        if not logged or (login_form_still_visible and "shop_login" in body_login):
            raise RuntimeError(
                "Schäfer-Peters: prihlásenie zlyhalo (skontroluj meno/heslo)."
            )
        self._login_ok = True

    @staticmethod
    def _normalize_supplier_code(code: str) -> str:
        """Odstráni nbsp / tab / multi-space — Excel kódy obsahujú často ``\\xa0``
        (non-breaking space), ktorý Schäfer-server na search-i nezhoduje s
        bežnou medzerou v internej databáze (HAR ukazuje ``%20``).
        """
        if not code:
            return ""
        # Všetky unicode whitespacy (\xa0, \u2007, \u202f, tab, …) zjednotíme na " ".
        s = re.sub(r"\s+", " ", code, flags=re.UNICODE).strip()
        return s

    @staticmethod
    def _code_match_key(code: str) -> str:
        """Kanonická forma na porovnanie itemNo z Algolia-y s naším supplier_code:
        bez whitespace, ASCII lower-case. Schäf zobrazuje „0933212 16" v Algolii
        a aj my po normalizácii ten istý string vidíme, ale chceme robustnosť
        proti dashom / nonprinting characters.
        """
        if not code:
            return ""
        return re.sub(r"[\s\-_\u00a0]", "", str(code)).casefold()

    async def _algolia_find_product(
        self, supplier_code: str
    ) -> Optional[dict[str, Any]]:
        """Algolia search → vráti exact-match hit alebo ``None``.

        Schäf používa Algolia ako primárny katalógový index (HAR jasne ukazuje,
        že server-side ``/b2b/en/search/`` interne tiež volá Algolia a podľa
        výsledku 302-uje na PDP, ak je exact match). My idealne preskakujeme
        celé HTML rendrovanie a pýtame sa Algolia priamo — vráti
        ``itemId`` (= numerický product id pre cart) a ``itemLink``
        (= PDP slug bez ``/b2b/en`` prefixu).
        """
        query = self._normalize_supplier_code(supplier_code)
        if not query:
            return None
        body = json.dumps(
            {
                "requests": [
                    {
                        "indexName": SCHAEF_ALGOLIA_INDEX,
                        "query": query,
                        "params": "hitsPerPage=10",
                    }
                ]
            },
            ensure_ascii=False,
        )
        url = (
            f"{SCHAEF_ALGOLIA_HOST}/1/indexes/*/queries"
            f"?x-algolia-application-id={SCHAEF_ALGOLIA_APP_ID}"
            f"&x-algolia-api-key={SCHAEF_ALGOLIA_API_KEY}"
        )
        try:
            # Cross-origin call mimo nášho ``self._client`` (iný host, žiadna
            # closed-session cookie nepotrebujeme). Krátky timeout — Algolia je CDN.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=4.0),
                headers={
                    "User-Agent": self._headers.get("User-Agent", DEFAULT_UA),
                    "Origin": self._base,
                    "Referer": f"{self._base}/",
                    "X-Algolia-Application-Id": SCHAEF_ALGOLIA_APP_ID,
                    "X-Algolia-API-Key": SCHAEF_ALGOLIA_API_KEY,
                    "Accept": "application/json",
                },
            ) as ag:
                r = await ag.post(url, content=body)
        except (httpx.HTTPError, httpx.NetworkError):
            # Algolia môže byť dočasne nedostupná — fallback na HTML search.
            return None
        if r.status_code >= 400:
            return None
        try:
            payload = r.json()
        except (json.JSONDecodeError, ValueError):
            return None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        hits = first.get("hits") or []
        if not isinstance(hits, list) or not hits:
            return None

        # Exact match na itemNo (canonicalized). Algolia môže vrátiť aj „0933212 160"
        # pri hľadaní „0933212 16" — to si filtrujeme my, nie server.
        wanted = self._code_match_key(query)
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            itemno = self._code_match_key(str(hit.get("itemNo") or ""))
            old_itemno = self._code_match_key(str(hit.get("oldItemNo") or ""))
            itemno2 = self._code_match_key(str(hit.get("itemNo2") or ""))
            parent = self._code_match_key(str(hit.get("parentItemNo") or ""))
            if wanted and wanted in {itemno, old_itemno, itemno2, parent}:
                return hit
        # Žiadny exact — vrátime prvý hit ako best-effort (UI ho potom môže
        # explicitne potvrdiť po načítaní mena produktu).
        return hits[0] if isinstance(hits[0], dict) else None

    async def _try_search(self, query: str) -> Tuple[Optional[str], str]:
        """Jeden HTML-search pokus. Vráti ``(pdp_path | None, body)``."""
        enc = quote(query, safe="")
        path = (
            f"/b2b/en/search/?SP_B2B_LIVE_ENU%5Bquery%5D={enc}&searchAlgolia={enc}"
        )
        r = await self._client.get(path)
        if r.status_code >= 400:
            raise RuntimeError(
                f"Schäfer-Peters: search status {r.status_code} pre kód {query!r}"
            )
        final_path = urlparse(str(r.url)).path or path
        body = r.text or ""
        is_pdp = bool(re.search(r"-p\d+/?$", final_path))
        if is_pdp and 'name="item_id"' in body:
            return final_path, body
        return None, body

    @staticmethod
    def _extract_pdp_path_from_results(body: str) -> Optional[str]:
        """Z HTML zoznamu výsledkov vyber prvý relevantný PDP odkaz.

        Schäf má dva tvary linkov:
          - relatívny ``/b2b/en/<slug>-pNNNN/``
          - absolútny ``https://shop.schaefer-peters.com/b2b/en/<slug>-pNNNN/``
        a niektoré PDP odkazy končia query stringom (?utm=…). Regex preto
        akceptuje aj voliteľné ``?`` resp. zachytí len cestu.
        """
        if not body:
            return None
        for pat in (
            r'href=["\'](\/b2b\/en\/[^"\'?#]+-p\d+\/)',
            r'href=["\']https?:\/\/[^"\']*\/b2b\/en\/([^"\'?#]+-p\d+\/)',
            r'data-href=["\'](\/b2b\/en\/[^"\']+-p\d+\/)',
            r'itemid=["\'](\/b2b\/en\/[^"\']+-p\d+\/)',
        ):
            m = re.search(pat, body, re.IGNORECASE)
            if m:
                path = m.group(1)
                if not path.startswith("/"):
                    path = f"/b2b/en/{path}"
                return path
        return None

    async def _search_to_pdp(
        self,
        supplier_code: str,
        *,
        algolia_hit: Optional[dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """Vráti ``(pdp_path, html)``.

        Stratégia (najrýchlejšie → najpomalšie):
          1. **Algolia API** — JSON s ``itemLink`` (PDP slug) a ``itemId``.
             Public read-only key, 300-500 ms, žiadna autentifikácia.
          2. **HTML search ``/b2b/en/search/?...``** — server-side fuzzy
             match; pri exact-i 302-uje priamo na PDP. Vyžaduje login session.
          3. **Extract z HTML zoznamu** — ak ani 2) nepresmeroval, vyberieme
             prvý PDP link z výsledkov a otvoríme ho.
        """
        raw_code = (supplier_code or "").strip()
        if not raw_code:
            raise ValueError("Schäfer-Peters: prázdny kód produktu.")

        # 1) Algolia: najspoľahlivejšia cesta. Stačí jeden POST a vieme
        # presný PDP path, ku ktorému prejdeme len pre live cenu/sklad.
        hit = algolia_hit if algolia_hit is not None else await self._algolia_find_product(raw_code)
        if hit and isinstance(hit, dict):
            item_link = str(hit.get("itemLink") or "").strip()
            if item_link:
                pdp_path = item_link
                if not pdp_path.startswith("/"):
                    pdp_path = "/" + pdp_path
                if not pdp_path.startswith("/b2b/en"):
                    pdp_path = "/b2b/en" + pdp_path
                r = await self._client.get(pdp_path)
                if r.status_code >= 400:
                    raise RuntimeError(
                        f"Schäfer-Peters: PDP {pdp_path!r} status {r.status_code} "
                        f"(Algolia hit itemId={hit.get('itemId')!r})"
                    )
                body = r.text or ""
                if 'name="item_id"' in body:
                    return pdp_path, body
                # Schäf v zriedkavých prípadoch vráti login stránku, ak session
                # expirovala — vyhodíme špecifickú chybu, aby ju vonkajší
                # handler invalidoval ako 401/403 ekvivalent.
                if 'name="input_login"' in body and 'name="input_password"' in body:
                    raise RuntimeError(
                        "Schäfer-Peters: PDP vrátilo login stránku — "
                        "session expirovala (skúsim relogin)."
                    )
                # Algolia hit existuje, ale PDP HTML nemá očakávaný form-tag.
                # Padáme do HTML cesty s tým, čo Algolia stihla získať.

        # 2) HTML search: skúsime postupne normalizovanú formu, bez medzier
        # a surový raw.
        normalized = self._normalize_supplier_code(raw_code)
        candidates: list[str] = []
        for c in (normalized, normalized.replace(" ", ""), raw_code):
            if c and c not in candidates:
                candidates.append(c)

        last_body = ""
        last_body_path = ""
        for candidate in candidates:
            pdp_path, body = await self._try_search(candidate)
            if pdp_path:
                return pdp_path, body
            last_body = body
            last_body_path = candidate
            # 3) HTML fallback: prvý PDP link zo zoznamu výsledkov.
            extracted = self._extract_pdp_path_from_results(body)
            if extracted:
                r2 = await self._client.get(extracted)
                if r2.status_code >= 400:
                    raise RuntimeError(
                        f"Schäfer-Peters: PDP {extracted!r} status {r2.status_code}"
                    )
                body2 = r2.text or ""
                if 'name="item_id"' in body2:
                    return extracted, body2

        tried = ", ".join(repr(c) for c in candidates)
        body_len = len(last_body)
        raise RuntimeError(
            f"Schäfer-Peters: pre kód {raw_code!r} sa nenašiel produkt "
            f"(Algolia ani HTML search nepresmerovali na PDP; "
            f"skúšané varianty: {tried}; "
            f"posledný body {body_len} znakov pri kóde {last_body_path!r})."
        )

    async def fetch_product_price_and_stock(
        self, supplier_code: str
    ) -> dict[str, Any]:
        """Spojí search → PDP → parse, vráti slovník v štandardnom tvare."""
        hit = await self._algolia_find_product(supplier_code)
        pdp_path, body = await self._search_to_pdp(supplier_code, algolia_hit=hit)
        title_hint = None
        if isinstance(hit, dict):
            title_hint = str(hit.get("description") or hit.get("name") or "").strip() or None
        data = schaef_parse_pdp_html(
            body,
            product_title_hint=title_hint,
            pdp_path=pdp_path,
        )
        if not data.get("schaef_pdp_path"):
            data["schaef_pdp_path"] = pdp_path
        if data.get("schaef_item_id") is None:
            raise RuntimeError(
                f"Schäfer-Peters: PDP {pdp_path!r} nemá hidden item_id — "
                "očakávaný formulár chýba (možno guest stránka, ne-prihlásený)."
            )
        return data

    async def add_to_cart(
        self,
        *,
        item_id: str,
        quantity_pieces: int,
        item_var_code: str = "",
        referer_path: str = "/",
    ) -> dict[str, Any]:
        """Pridá riadok do košíka.

        Schäfer endpoint berie multipart/form-data — preto použijeme ``files``
        (httpx vytvorí korektný boundary). Cert addony zámerne neposielame.
        ``quantity_pieces`` je v *kusoch* (Schäfer interne ráta v ks, ``item_qty``
        validovaný cez min/step v PDP). Volajúca strana ho zaokrúhli cez
        ``schaef_round_qty_to_step``.
        """
        iid = (item_id or "").strip()
        if not iid:
            raise ValueError("Schäfer-Peters: chýba item_id.")
        if quantity_pieces < 1:
            raise ValueError("Schäfer-Peters: quantity musí byť aspoň 1.")
        ref = referer_path if referer_path.startswith("http") else f"{self._base}{referer_path}"
        files = {
            "item_id": (None, str(int(iid))),
            "item_var_code": (None, item_var_code or ""),
            "item_qty": (None, str(int(quantity_pieces))),
            "action": (None, "shop_add_item_to_basket_card"),
        }
        # Multipart 302 → readCart. follow_redirects vráti finálny JSON.
        r = await self._client.post(
            "/module/dcshop/GeneralAjaxData.php?function=cart&site=b2b&language=en",
            files=files,
            headers={
                "Accept": "*/*",
                "Origin": self._base,
                "Referer": ref,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Schäfer-Peters cart-add zlyhal (HTTP {r.status_code})."
            )
        body = (r.text or "").strip()
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw_body": body[:2000]}

    async def fetch_cart_snapshot(self) -> dict[str, Any]:
        """Pre porovnanie / kontrolu — JSON odpoveď z readCart (basketCount)."""
        r = await self._client.get(
            "/module/dcshop/GeneralAjaxData.php?function=readCart&site=b2b&language=en",
            headers={"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"},
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Schäfer-Peters readCart zlyhal (HTTP {r.status_code})."
            )
        try:
            return r.json()
        except (json.JSONDecodeError, ValueError):
            return {"raw_body": (r.text or "")[:2000]}
