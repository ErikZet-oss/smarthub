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
        q = (
            "query($search:String!,$pageSize:Int!,$currentPage:Int!){"
            "products(search:$search,pageSize:$pageSize,currentPage:$currentPage,"
            'filter:{type_id:{eq:"simple"}}){'
            "items{sku name stock_status salable_qty "
            "price_range{minimum_price{final_price{value currency}}}}}}"
        )
        data = await self._gql(
            q, {"search": code, "pageSize": 24, "currentPage": 1}, auth_required=True
        )
        products = data.get("products")
        if not isinstance(products, dict):
            return []
        items = products.get("items")
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict)]

    async def customer_cart_id(self) -> str:
        if self._cart_id:
            return self._cart_id
        q = "query{customerCart{id}}"
        data = await self._gql(q, {}, auth_required=True)
        cart = data.get("customerCart")
        cid = str((cart or {}).get("id") if isinstance(cart, dict) else "").strip()
        if not cid:
            raise RuntimeError("Argip: customerCart id sa nepodarilo načítať.")
        self._cart_id = cid
        return cid

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

