"""
Schachermayer webshop (webshop.schachermayer.com): Keycloak OIDC + REST API z HAR.

- GET  /sso/oauth2/authorization/keycloak — prihlasovací formulár Keycloak
- POST login-actions/authenticate — meno/heslo
- GET  /cat/api/extranet/cas/storeUserInfo — kunnr, branch, vkorg, …
- GET  /cat/api/_auth/session — extranetSessionId (horný panel / košík)
- POST /cat/api/extranet/catalogCore/search?catalog=… — vyhľadanie artiklu
- GET  /cat/api/private/extranet/webshopCore/price-and-availability — cena/sklad
- GET  /cat/api/private/extranet/webshopCore/basket — obsah košíka (JSON)
- POST /cat/api/private/extranet/webshopCore/add-article-to-basket — pridanie do košíka
- GET  /app-bar/get-basket-summary-content — súhrn košíka (HTML fallback)
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

DEFAULT_SHOP_BASE = "https://webshop.schachermayer.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_KC_FORM_ACTION_RE = re.compile(
    r'<form[^>]+id=["\']kc-form-login["\'][^>]*action=["\']([^"\']+)["\']',
    re.I | re.DOTALL,
)
_KC_FORM_ACTION_RE_ALT = re.compile(
    r'<form[^>]+action=["\']([^"\']*login-actions/authenticate[^"\']*)["\'][^>]*id=["\']kc-form-login["\']',
    re.I | re.DOTALL,
)


def _kc_login_form_action(html: str) -> str | None:
    for rx in (_KC_FORM_ACTION_RE, _KC_FORM_ACTION_RE_ALT):
        m = rx.search(html or "")
        if m:
            return m.group(1).replace("&amp;", "&")
    return None


def schachermayer_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return DEFAULT_SHOP_BASE
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def schachermayer_web_cart_url(shop_url: str) -> str:
    """Stránka nákupného košíka na eshope."""
    return f"{schachermayer_base_url(shop_url)}/webshop/basket"


def schachermayer_norm_code(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _parse_decimal(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    t = str(val).strip().replace("\xa0", " ").replace(" ", "").replace(",", ".")
    m = re.search(r"(-?\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_int(val: Any) -> Optional[int]:
    x = _parse_decimal(val)
    if x is None:
        return None
    try:
        return int(round(x))
    except (TypeError, ValueError):
        return None


def _http_json_dict(response: httpx.Response) -> dict[str, Any] | None:
    """Bezpečné parsovanie JSON objektu (prázdna/HTML odpoveď → None)."""
    body = (response.text or "").strip()
    if not body:
        return None
    try:
        parsed = response.json()
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _catalog_id_from_user(user: dict[str, Any], override: str | None) -> str:
    if override and str(override).strip():
        return str(override).strip()
    vkorg = str(user.get("vkorg") or "").strip()
    vtweg = str(user.get("vtweg") or "").strip() or "00"
    sparte = str(user.get("sparte") or "").strip() or "00"
    cur = str(user.get("currency") or "EUR").strip().upper()
    if vkorg:
        return f"{vkorg}-{vtweg}-{sparte}-SK-{cur}"
    return "8850-00-00-SK-EUR"


class SchachermayerHttpClient:
    def __init__(self, shop_url: str) -> None:
        self._shop = schachermayer_base_url(shop_url)
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0),
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept-Language": "sk,sk-SK;q=0.9",
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            },
        )
        self._login_ok = False
        self._user: dict[str, Any] = {}
        self._catalog_id: str = ""
        self._extranet_session_id: str = ""

    async def __aenter__(self) -> SchachermayerHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def _get_store_user(self) -> dict[str, Any]:
        r = await self._client.get(f"{self._shop}/cat/api/extranet/cas/storeUserInfo")
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("Schachermayer: storeUserInfo nie je JSON objekt.")
        return data

    async def ensure_login(self, username: str, password: str) -> None:
        if self._login_ok and self._user:
            return
        user = (username or "").strip()
        pwd = password or ""
        if not user or not pwd:
            raise ValueError("Schachermayer: chýba meno alebo heslo.")

        try:
            probe = await self._get_store_user()
            if probe.get("kunnr") or probe.get("username"):
                self._user = probe
                self._login_ok = True
                await self._refresh_auth_session()
                return
        except httpx.HTTPStatusError:
            pass
        except Exception:
            pass

        start = f"{self._shop}/sso/oauth2/authorization/keycloak?ui_locales=sk"
        r0 = await self._client.get(start)
        r0.raise_for_status()
        html = r0.text or ""
        action = _kc_login_form_action(html)
        if not action:
            if "kc-form-login" not in html.lower():
                try:
                    probe2 = await self._get_store_user()
                    if probe2.get("kunnr") or probe2.get("username"):
                        self._user = probe2
                        self._login_ok = True
                        await self._refresh_auth_session()
                        return
                except Exception:
                    pass
            raise RuntimeError(
                "Schachermayer: na prihlasovacej stránke sa nenašiel formulár Keycloak (kc-form-login)."
            )
        post_url = urljoin(str(r0.url), action)
        r1 = await self._client.post(
            post_url,
            data={
                "username": user,
                "password": pwd,
                "credentialId": "",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r1.raise_for_status()

        self._user = await self._get_store_user()
        if not (self._user.get("kunnr") or self._user.get("username")):
            raise RuntimeError("Schachermayer: prihlásenie zlyhalo (prázdny profil po OAuth).")
        self._login_ok = True
        await self._refresh_auth_session()

    async def _refresh_auth_session(self) -> dict[str, Any]:
        r = await self._client.get(
            f"{self._shop}/cat/api/_auth/session",
            headers={
                "Accept": "application/json",
                "Referer": f"{self._shop}/cat/sk-SK",
            },
        )
        r.raise_for_status()
        data = _http_json_dict(r) or {}
        sid = str(data.get("extranetSessionId") or "").strip()
        if not sid and isinstance(data.get("user"), dict):
            sid = str(data["user"].get("extranetSessionId") or "").strip()
        if sid:
            self._extranet_session_id = sid
        return data

    def _api_headers(self) -> dict[str, str]:
        return {
            "Origin": self._shop,
            "Referer": f"{self._shop}/cat/sk-SK",
            "Accept": "application/json",
        }

    async def fetch_basket(self) -> dict[str, Any]:
        """Načíta JSON košíka (štandardný košík webshopu)."""
        if not self._login_ok:
            raise RuntimeError("Schachermayer: nie ste prihlásení.")
        url = f"{self._shop}/cat/api/private/extranet/webshopCore/basket"
        r = await self._client.get(url, headers=self._api_headers())
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        data = _http_json_dict(r)
        return data if data is not None else {}

    async def fetch_basket_summary_html(self) -> str:
        """HTML súhrn z app-bar (fallback ak JSON neobsahuje riadky)."""
        if not self._extranet_session_id:
            await self._refresh_auth_session()
        sid = self._extranet_session_id
        if not sid:
            return ""
        locale = str(self._user.get("locale") or "sk_SK").strip() or "sk_SK"
        url = (
            f"{self._shop}/app-bar/get-basket-summary-content"
            f"?locale={locale}&publicKey=cat&extranetSessionId={sid}"
        )
        r = await self._client.get(
            url,
            headers={
                "Accept": "text/html,*/*",
                "Referer": f"{self._shop}/cat/sk-SK",
            },
        )
        r.raise_for_status()
        return r.text or ""

    def _resolve_catalog(self, catalog_override: str | None) -> str:
        cid = _catalog_id_from_user(self._user, catalog_override)
        self._catalog_id = cid
        return cid

    async def search_article(
        self,
        product_code: str,
        *,
        catalog_override: str | None = None,
    ) -> dict[str, Any]:
        code = schachermayer_norm_code(product_code)
        if not code:
            raise ValueError("Schachermayer: prázdny kód produktu.")
        catalog = self._resolve_catalog(catalog_override)
        kunnr = str(self._user.get("kunnr") or "").strip()
        branch = str(self._user.get("branch") or "").strip()
        body = {
            "text": code,
            "forcedQuery": False,
            "category": "1",
            "maxResults": 50,
            "offset": 0,
            "categoryFacetDepth": 1,
            "facetLimit": 0,
            "customer": kunnr,
            "branch": branch,
            "masterUser": False,
            "sortOrder": "Best",
            "featureConstraints": {},
            "specialSearchFeatures": [],
            "maxResultsSpecialSearch": 100,
            "sortByWarehouse": False,
            "sortByBlaetterkatalog": False,
            "additionalIdsToSearchFor": [],
        }
        url = f"{self._shop}/cat/api/extranet/catalogCore/search"
        r = await self._client.post(
            url,
            params={"catalog": catalog},
            json=body,
            headers={
                "Origin": self._shop,
                "Referer": f"{self._shop}/cat/sk-SK",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Schachermayer: search vrátil neplatný JSON.")
        sr = payload.get("searchResult") or {}
        articles = sr.get("articles") if isinstance(sr, dict) else None
        if not isinstance(articles, list):
            return {}
        code_u = code.upper()
        best: dict[str, Any] | None = None
        best_rank = 99
        for a in articles:
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id") or a.get("idTrim") or "").strip()
            if not aid:
                continue
            rank = 2
            if aid.upper() == code_u:
                rank = 0
            elif code_u and code_u in aid.upper():
                rank = 1
            if rank < best_rank:
                best_rank = rank
                best = a
            if rank == 0:
                break
        return best or {}

    async def fetch_product_pricing(
        self,
        product_code: str,
        *,
        catalog_override: str | None = None,
    ) -> dict[str, Any]:
        code = (product_code or "").strip()
        if not code:
            raise ValueError("Schachermayer: prázdny kód produktu.")
        article = await self.search_article(code, catalog_override=catalog_override)
        if not article:
            raise RuntimeError(
                f"Schachermayer: kód {code!r} sa v katalógu nenašiel (search prázdny)."
            )
        article_nr = str(article.get("id") or article.get("idTrim") or "").strip()
        prices = article.get("prices")
        default_p = 0.0
        if isinstance(prices, list) and prices and isinstance(prices[0], dict):
            default_p = float(_parse_decimal(prices[0].get("price")) or 0.0)
        price_qty = _parse_int(article.get("priceQuantity"))
        if not price_qty or price_qty < 1:
            price_qty = _parse_int(article.get("contentUnitsPerOrderUnit")) or 100
        price_row = await self.fetch_price_stock(
            article_nr,
            default_price=default_p,
            amount=price_qty,
        )
        return SchachermayerHttpClient.parse_supplier_data(
            article, price_row, shop_base=self._shop
        )

    async def fetch_price_stock(
        self,
        article_nr: str,
        *,
        default_price: float,
        amount: int,
    ) -> dict[str, Any]:
        r = await self._client.get(
            f"{self._shop}/cat/api/private/extranet/webshopCore/price-and-availability",
            params={
                "articleNumber": article_nr,
                "defaultPrice": f"{default_price:.2f}",
                "amount": str(int(amount)),
            },
            headers={
                "Accept": "application/json",
                "Referer": f"{self._shop}/cat/sk-SK",
            },
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("Schachermayer: price-and-availability nie je objekt.")
        return data

    async def add_to_cart_for_product_code(
        self,
        product_code: str,
        quantity_pieces: int,
        *,
        catalog_override: str | None = None,
    ) -> None:
        article = await self.search_article(product_code, catalog_override=catalog_override)
        if not article:
            raise RuntimeError(
                f"Schachermayer: kód {(product_code or '').strip()!r} sa nenašiel — nedá sa pridať do košíka."
            )
        nr = str(article.get("id") or article.get("idTrim") or "").strip()
        if not nr:
            raise RuntimeError("Schachermayer: artikel bez čísla — košík nie je možný.")
        cu = _parse_int(article.get("contentUnitsPerOrderUnit"))
        if not cu or cu < 1:
            cu = _parse_int(article.get("priceQuantity")) or 1
        q = max(1, int(quantity_pieces))
        packs = max(1, (q + cu - 1) // cu)
        await self.add_to_cart(nr, packs)

    async def add_to_cart(self, article_nr: str, amount_order_units: int) -> None:
        nr = (article_nr or "").strip()
        if not nr:
            raise ValueError("Schachermayer: prázdny číslo artiklu.")
        q = int(amount_order_units)
        if q < 1:
            raise ValueError("Schachermayer: množstvo musí byť aspoň 1.")
        r = await self._client.post(
            f"{self._shop}/cat/api/private/extranet/webshopCore/add-article-to-basket",
            json=[{"articleNr": nr, "amount": q}],
            headers={
                **self._api_headers(),
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()

    @staticmethod
    def parse_supplier_data(
        article: dict[str, Any],
        price_row: dict[str, Any],
        *,
        shop_base: str,
    ) -> dict[str, Any]:
        title = str(article.get("title") or "").strip()
        article_nr = str(article.get("id") or article.get("idTrim") or "").strip()
        prices = article.get("prices")
        list_price = None
        if isinstance(prices, list) and prices and isinstance(prices[0], dict):
            list_price = _parse_decimal(prices[0].get("price"))
        price_qty = _parse_int(article.get("priceQuantity"))
        if not price_qty or price_qty < 1:
            price_qty = _parse_int(article.get("contentUnitsPerOrderUnit")) or 1
        pack_q = _parse_int(article.get("contentUnitsPerOrderUnit"))
        if not pack_q or pack_q < 1:
            pack_q = price_qty if price_qty >= 1 else 1

        net_price = _parse_decimal(price_row.get("price"))
        avail = _parse_int(price_row.get("availability"))
        qty_block = _parse_int(price_row.get("quantity")) or price_qty
        if not qty_block or qty_block < 1:
            qty_block = 1
        stock_text = str(price_row.get("stockAvailabilityText") or "").strip()

        price_for_display: float | None = None
        if net_price is not None:
            price_for_display = round(float(net_price) * (100.0 / float(qty_block)), 4)
        elif list_price is not None:
            pq0 = price_qty if price_qty >= 1 else 1
            price_for_display = round(float(list_price) * (100.0 / float(pq0)), 4)

        raw_price = None
        if price_for_display is not None:
            raw_price = f"{price_for_display:.2f} € / 100 ks"

        base = schachermayer_base_url(shop_base)
        search_href = (
            f"{base}/cat/sk-SK/products/v-etky-kateg-rie/1?sSearch={article_nr}"
            if article_nr
            else None
        )

        pv: dict[str, Any] = {
            "label": title or article_nr or "Schachermayer",
            "pack_quantity": pack_q,
            "price_eur": price_for_display,
            "raw_price": raw_price,
            "stock": avail,
            "raw_stock": stock_text or (str(avail) if avail is not None else None),
            "schachermayer_article_nr": article_nr or None,
            "price_unit": "per_100_ks",
            "currency_symbol": "€",
        }
        return {
            "price_eur": price_for_display,
            "stock": avail,
            "pack_quantity": pack_q,
            "raw_price": raw_price,
            "raw_stock": pv.get("raw_stock"),
            "raw_pack_quantity": str(pack_q),
            "product_title": title or None,
            "packaging_variants": [pv],
            "logged_in": True,
            "schachermayer_via_http": True,
            "price_unit": "per_100_ks",
            "currency_symbol": "€",
            "supplier_product_url": search_href,
        }


def _schach_item_quantity(item: dict[str, Any]) -> int:
    for key in ("amount", "quantity", "orderAmount", "orderUnits", "qty"):
        q = _parse_int(item.get(key))
        if q is not None and q > 0:
            return q
    return 0


def _schach_item_article_nr(item: dict[str, Any]) -> str:
    for key in ("articleNr", "articleNumber", "articleId", "id", "idTrim"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    article = item.get("article")
    if isinstance(article, dict):
        for key in ("id", "idTrim", "articleNr", "articleNumber"):
            val = str(article.get(key) or "").strip()
            if val:
                return val
    return ""


def _schach_item_label(item: dict[str, Any], article_nr: str) -> str:
    for key in ("title", "name", "description", "articleTitle"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    article = item.get("article")
    if isinstance(article, dict):
        val = str(article.get("title") or article.get("name") or "").strip()
        if val:
            return val
    return article_nr or "—"


def _schach_collect_item_lists(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for el in node:
            if isinstance(el, dict) and _schach_item_article_nr(el):
                out.append(el)
            elif isinstance(el, dict):
                _schach_collect_item_lists(el, out)
        return
    if not isinstance(node, dict):
        return
    for key in (
        "articles",
        "items",
        "lineItems",
        "basketItems",
        "basketLines",
        "positions",
        "orderPositions",
    ):
        raw = node.get(key)
        if isinstance(raw, list):
            for el in raw:
                if isinstance(el, dict) and _schach_item_article_nr(el):
                    out.append(el)
    for key in ("baskets", "standardBaskets", "shoppingBaskets"):
        raw = node.get(key)
        if isinstance(raw, list):
            for el in raw:
                if isinstance(el, dict):
                    _schach_collect_item_lists(el, out)
        elif isinstance(raw, dict):
            _schach_collect_item_lists(raw, out)
    basket = node.get("basket")
    if isinstance(basket, dict):
        _schach_collect_item_lists(basket, out)


def _schach_total_from_node(node: dict[str, Any]) -> Optional[float]:
    for key in (
        "total",
        "totalPrice",
        "totalNet",
        "netTotal",
        "grandTotal",
        "sum",
        "basketTotal",
        "totalAmount",
    ):
        val = _parse_decimal(node.get(key))
        if val is not None:
            return round(float(val), 4)
    prices = node.get("prices")
    if isinstance(prices, dict):
        for key in ("grand_total", "subtotal_excluding_tax", "total"):
            sub = prices.get(key)
            if isinstance(sub, dict):
                val = _parse_decimal(sub.get("value"))
                if val is not None:
                    return round(float(val), 4)
            else:
                val = _parse_decimal(sub)
                if val is not None:
                    return round(float(val), 4)
    return None


def schachermayer_parse_basket_summary_html(html: str) -> dict[str, Any]:
    """Z HTML app-bar súhrnu — počet položiek a súčet EUR."""
    text = html or ""
    total_eur: Optional[float] = None
    for m in re.finditer(
        r"([\d]{1,6}[,\.][\d]{1,4})\s*(?:&nbsp;|\s)*EUR",
        text,
        re.I,
    ):
        total_eur = _parse_decimal(m.group(1))
    line_count = 0
    for m in re.finditer(
        r'data-cy="appbar-shoppingcart-count-span"[^>]*>\s*(\d+)\s*<',
        text,
        re.I,
    ):
        line_count = max(line_count, int(m.group(1)))
    if line_count <= 0:
        for m in re.finditer(r'class="appbar-badge[^"]*"[^>]*>\s*(\d+)\s*<', text):
            line_count = max(line_count, int(m.group(1)))
    return {
        "total_eur": total_eur,
        "line_count": line_count,
    }


def schachermayer_parse_cart_json(
    basket: dict[str, Any],
    *,
    summary_html: str = "",
) -> dict[str, Any]:
    """
    Z JSON ``webshopCore/basket`` (a prípadne HTML súhrnu) — riadky a súčet v EUR.
    """
    raw_items: list[dict[str, Any]] = []
    _schach_collect_item_lists(basket, raw_items)
    seen: set[str] = set()
    lines: list[dict[str, Any]] = []
    for it in raw_items:
        article_nr = _schach_item_article_nr(it)
        if not article_nr:
            continue
        dedupe_key = article_nr
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        qn = _schach_item_quantity(it)
        if qn <= 0:
            qn = 1
        label = _schach_item_label(it, article_nr)
        unit_eur = None
        line_total = None
        for uk in ("unitPrice", "netPrice", "price", "salesPrice"):
            unit_eur = _parse_decimal(it.get(uk))
            if unit_eur is not None:
                break
        for tk in (
            "lineTotal",
            "totalPrice",
            "rowTotal",
            "netLineTotal",
            "positionTotal",
        ):
            line_total = _parse_decimal(it.get(tk))
            if line_total is not None:
                break
        if unit_eur is None and line_total is not None and qn > 0:
            unit_eur = round(line_total / qn, 4)
        if line_total is None and unit_eur is not None:
            line_total = round(unit_eur * qn, 4)
        lines.append(
            {
                "label": label,
                "quantity": qn,
                "unit_price_eur": unit_eur,
                "line_total_eur": line_total,
                "variant_code": article_nr,
            }
        )

    total_eur = _schach_total_from_node(basket)
    if total_eur is None:
        for sub in basket.values():
            if isinstance(sub, dict):
                total_eur = _schach_total_from_node(sub)
                if total_eur is not None:
                    break
    if total_eur is None and lines:
        total_eur = round(
            sum((ln.get("line_total_eur") or 0.0) for ln in lines),
            4,
        )
    summary = schachermayer_parse_basket_summary_html(summary_html)
    line_count = len(lines)
    if summary.get("line_count"):
        line_count = max(line_count, int(summary["line_count"]))
    if total_eur is None and summary.get("total_eur") is not None:
        total_eur = summary["total_eur"]
    if not lines and line_count <= 0 and not summary.get("line_count"):
        line_count = 0
    elif not lines and summary.get("line_count"):
        line_count = int(summary["line_count"])

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": line_count,
    }
