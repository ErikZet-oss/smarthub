"""
Schachermayer webshop (webshop.schachermayer.com): Keycloak OIDC + REST API z HAR.

- GET  /sso/oauth2/authorization/keycloak — prihlasovací formulár Keycloak
- POST login-actions/authenticate — meno/heslo
- GET  /cat/api/extranet/cas/storeUserInfo — kunnr, branch, vkorg, …
- POST /cat/api/extranet/catalogCore/search?catalog=… — vyhľadanie artiklu
- GET  /cat/api/private/extranet/webshopCore/price-and-availability — cena/sklad
- POST /cat/api/private/extranet/webshopCore/add-article-to-basket — košík
"""

from __future__ import annotations

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
    """Verejná vstupná stránka katalógu (košík je v hornom paneli)."""
    return f"{schachermayer_base_url(shop_url)}/cat/sk-SK"


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
                "Origin": self._shop,
                "Referer": f"{self._shop}/cat/sk-SK",
                "Accept": "application/json",
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
            "supplier_product_url": search_href,
        }
