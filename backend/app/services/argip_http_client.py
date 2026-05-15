from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def argip_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return "https://b2b.argip.com.pl"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def argip_graphql_url(shop_url: str) -> str:
    return f"{argip_base_url(shop_url)}/eu_en/graphql"


def argip_cart_url(shop_url: str) -> str:
    return f"{argip_base_url(shop_url)}/eu_en/checkout/cart/"


class ArgipHttpClient:
    def __init__(self, *, shop_url: str):
        self.shop_url = shop_url
        self.base_url = argip_base_url(shop_url)
        self.graphql_url = argip_graphql_url(shop_url)
        self._token: Optional[str] = None
        self._cart_id: Optional[str] = None
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Content-Currency": "EUR",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/eu_en/",
            },
        )

    async def __aenter__(self) -> "ArgipHttpClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.aclose()

    async def _gql(
        self, query: str, variables: dict[str, Any], *, auth_required: bool = False
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if auth_required:
            if not self._token:
                raise RuntimeError("Argip: chýba customer token (najprv login).")
            headers["Authorization"] = f"Bearer {self._token}"
        r = await self._client.post(
            self.graphql_url, json={"query": query, "variables": variables}, headers=headers
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Argip: neplatná GraphQL odpoveď.")
        errs = payload.get("errors")
        if isinstance(errs, list) and errs:
            msg = str(errs[0].get("message") or "GraphQL chyba")
            raise RuntimeError(f"Argip GraphQL: {msg}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Argip: odpoveď neobsahuje data.")
        return data

    async def ensure_login(self, username: str, password: str) -> None:
        if self._token:
            return
        q = (
            "mutation($email_1:String!,$password_1:String!){"
            "generateCustomerTokenV2(email:$email_1,password:$password_1){token}}"
        )
        data = await self._gql(
            q, {"email_1": (username or "").strip(), "password_1": password or ""}
        )
        token = (
            ((data.get("generateCustomerTokenV2") or {}) if isinstance(data, dict) else {})
            .get("token")
        )
        tok = str(token or "").strip()
        if not tok:
            raise RuntimeError("Argip: login zlyhal (token je prázdny).")
        self._token = tok

    async def search_products(self, product_code: str) -> list[dict[str, Any]]:
        code = (product_code or "").strip()
        if not code:
            return []
        q_search = (
            "query($search:String!,$pageSize:Int!,$currentPage:Int!){"
            "products(search:$search,pageSize:$pageSize,currentPage:$currentPage){"
            "items{sku name type_id index stock_status salable_qty package "
            "stock_item{min_sale_qty qty_increments qty} "
            "price_range{minimum_price{final_price{value currency} "
            "regular_price{value currency} default_price{value currency} default_final_price{value currency}}} "
            "price_tiers{quantity final_price{value currency}} "
            "... on ConfigurableProduct{"
            "variants{product{sku name type_id index stock_status salable_qty package "
            "stock_item{min_sale_qty qty_increments qty} "
            "price_range{minimum_price{final_price{value currency} "
            "regular_price{value currency} default_price{value currency} default_final_price{value currency}}} "
            "price_tiers{quantity final_price{value currency}}}}}}}}"
        )
        data = await self._gql(
            q_search, {"search": code, "pageSize": 24, "currentPage": 1}, auth_required=True
        )
        items = self._extract_products_items(data)
        if items:
            return items

        # HAR fallback: Argip frontend používa quoted search (napr. "\"2284\"").
        data2 = await self._gql(
            q_search,
            {"search": f'"{code}"', "pageSize": 24, "currentPage": 1},
            auth_required=True,
        )
        items2 = self._extract_products_items(data2)
        if items2:
            return items2

        # Priamy SKU filter (Magento GraphQL štýl) ako ďalší fallback.
        q_sku = (
            "query($sku:String!){"
            'products(filter:{sku:{eq:$sku}},pageSize:24,currentPage:1){'
            "items{sku name type_id index stock_status salable_qty package "
            "stock_item{min_sale_qty qty_increments qty} "
            "price_range{minimum_price{final_price{value currency} "
            "regular_price{value currency} default_price{value currency} default_final_price{value currency}}} "
            "price_tiers{quantity final_price{value currency}} "
            "... on ConfigurableProduct{"
            "variants{product{sku name type_id index stock_status salable_qty package "
            "stock_item{min_sale_qty qty_increments qty} "
            "price_range{minimum_price{final_price{value currency} "
            "regular_price{value currency} default_price{value currency} default_final_price{value currency}}} "
            "price_tiers{quantity final_price{value currency}}}}}}}}"
        )
        data3 = await self._gql(q_sku, {"sku": code}, auth_required=True)
        items3 = self._extract_products_items(data3)
        if items3:
            return items3

        # Posledný fallback podľa HAR: GET /graphql?hash=...&search_1=... (persisted query).
        items4 = await self._search_products_via_har_endpoint(code)
        return items4

    @staticmethod
    def _extract_products_items(data: dict[str, Any]) -> list[dict[str, Any]]:
        products = data.get("products")
        if not isinstance(products, dict):
            return []
        items = products.get("items")
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict)]

    async def _search_products_via_har_endpoint(self, code: str) -> list[dict[str, Any]]:
        if not self._token:
            return []
        params = {
            "hash": "1121303192",
            "search_1": f'"{code}"',
            "pageSize_1": "24",
            "currentPage_1": "1",
            "filter_1": '{"type_id":{"eq":"simple"},"customer_group_id":{"eq":134}}',
            "isAutocomplete_1": "true",
        }
        r = await self._client.get(
            self.graphql_url,
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            return []
        return self._find_items_deep(payload)

    def _find_items_deep(self, node: Any) -> list[dict[str, Any]]:
        if isinstance(node, dict):
            items = node.get("items")
            if isinstance(items, list):
                valid = [x for x in items if isinstance(x, dict) and str(x.get("sku") or "").strip()]
                if valid:
                    return valid
            for value in node.values():
                found = self._find_items_deep(value)
                if found:
                    return found
            return []
        if isinstance(node, list):
            for value in node:
                found = self._find_items_deep(value)
                if found:
                    return found
        return []

    async def customer_cart_id(self) -> str:
        if self._cart_id:
            return self._cart_id
        cart = await self.fetch_customer_cart()
        cid = str(cart.get("id") or "").strip()
        if not cid:
            raise RuntimeError("Argip: customerCart id sa nepodarilo načítať.")
        self._cart_id = cid
        return cid

    async def fetch_customer_cart(self) -> dict[str, Any]:
        """Načíta aktívny košík prihláseného zákazníka (Magento GraphQL)."""
        queries = (
            (
                "query{customerCart{id total_quantity items{quantity product{sku name} "
                "prices{price{value currency} row_total{value currency}} "
                "... on ConfigurableCartItem{configured_variant{sku name}}} "
                "prices{subtotal_excluding_tax{value currency} grand_total{value currency}}}}"
            ),
            (
                "query{customerCart{id items{quantity product{sku name} "
                "prices{price{value} row_total{value}} prices{grand_total{value}}}}"
            ),
            ("query{customerCart{id items{quantity product{sku name}}}}"),
        )
        last_err: Optional[Exception] = None
        for q in queries:
            try:
                data = await self._gql(q, {}, auth_required=True)
                cart = data.get("customerCart")
                if isinstance(cart, dict):
                    cid = str(cart.get("id") or "").strip()
                    if cid:
                        self._cart_id = cid
                    return cart
            except Exception as exc:
                last_err = exc
        if last_err:
            raise last_err
        raise RuntimeError("Argip: customerCart sa nepodarilo načítať.")

    async def add_to_cart(self, sku: str, quantity: int) -> None:
        cart_id = await self.customer_cart_id()
        q = (
            "mutation($cartId_1:String!,$cartItems_1:[CartItemInput!]!){"
            "addProductsToCart(cartId:$cartId_1,cartItems:$cartItems_1){"
            "user_errors{message code}}}"
        )
        vars_payload = {
            "cartId_1": cart_id,
            "cartItems_1": [
                {"sku": sku, "quantity": int(quantity), "selected_options": [], "entered_options": []}
            ],
        }
        data = await self._gql(q, vars_payload, auth_required=True)
        user_errors = ((data.get("addProductsToCart") or {}) if isinstance(data, dict) else {}).get(
            "user_errors"
        )
        if isinstance(user_errors, list) and user_errors:
            msg = str(user_errors[0].get("message") or "Argip cart chyba")
            raise RuntimeError(f"Argip: {msg}")


def _argip_money_value(node: Any) -> Optional[float]:
    if not isinstance(node, dict):
        return None
    raw = node.get("value")
    if raw is None:
        return None
    try:
        return round(float(raw), 4)
    except (TypeError, ValueError):
        return None


def argip_parse_cart_json(cart: dict[str, Any]) -> dict[str, Any]:
    """
    Z GraphQL ``customerCart`` — riadky a súčet (EUR, prednostne bez DPH ak je v odpovedi).
    """
    lines: list[dict[str, Any]] = []
    raw_items = cart.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    for it in raw_items:
        if not isinstance(it, dict):
            continue
        try:
            qn = int(it.get("quantity") or 0)
        except (TypeError, ValueError):
            qn = 0
        if qn <= 0:
            continue
        product = it.get("product") if isinstance(it.get("product"), dict) else {}
        variant = (
            it.get("configured_variant")
            if isinstance(it.get("configured_variant"), dict)
            else {}
        )
        sku = str(variant.get("sku") or product.get("sku") or "").strip()
        label = str(variant.get("name") or product.get("name") or sku or "—").strip()
        prices = it.get("prices") if isinstance(it.get("prices"), dict) else {}
        unit_eur = _argip_money_value(
            prices.get("price") if isinstance(prices.get("price"), dict) else None
        )
        line_total = _argip_money_value(
            prices.get("row_total") if isinstance(prices.get("row_total"), dict) else None
        )
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
                "variant_code": sku or None,
            }
        )

    cart_prices = cart.get("prices") if isinstance(cart.get("prices"), dict) else {}
    total_eur: Optional[float] = None
    for key in (
        "subtotal_excluding_tax",
        "subtotal_with_discount_excluding_tax",
        "grand_total",
    ):
        node = cart_prices.get(key)
        if isinstance(node, dict):
            total_eur = _argip_money_value(node)
            if total_eur is not None:
                break
    if total_eur is None:
        total_eur = round(
            sum((ln.get("line_total_eur") or 0.0) for ln in lines),
            4,
        )
        if not lines:
            total_eur = None

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": len(lines),
    }

