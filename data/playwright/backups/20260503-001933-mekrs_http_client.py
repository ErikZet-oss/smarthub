"""
Mekrs e-shop: priame HTTP volania (httpx) podľa verejného JSON API z eshopu.

Z HAR / prehliadača:
  - POST /api/user/login  →  { "token": { "accessToken": "<JWT>" } }  (bez Set-Cookie v odpovedi)
  - GET /api/user/user    →  profil vrátane activeCartId (vyžaduje Bearer token)
  - POST /api/cart/{cartId}/add  →  { "productVariant": "<uuid>", "quantity": <int> }

Cookies: Pre ceny v EUR treba cookie „currency“ = „eur“ (ako prepínač meny v Nuxt fronte).
  Voliteľne: MEKRS_DISPLAY_CURRENCY=eur|czk (predvolene eur).

Bez JWT (neprihlásený) /api/product/…/variants často vracia **CZK** a **inú katalógovú hladinu**
(než po prihlásení, napr. ~0,80 Kč namiesto B2B **1,49 € / 100 ks**). Preto vždy najprv
`ensure_session` (login) a potom rovnaká session + cookie meny pri fulltexte a variantoch.

Heslo nikdy neukladaj do kódu — len premenné prostredia alebo parametre funkcie.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_BASE = "https://eshop.mekrs.cz"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _mekrs_display_currency_code() -> str:
    """Cookie hodnota pre menu: „eur“ alebo „czk“ (nie UUID z /api/currency)."""
    c = (os.environ.get("MEKRS_DISPLAY_CURRENCY") or "eur").strip().lower()
    return c if c in ("eur", "czk") else "eur"


def _norm_sku(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("\xa0", " ").replace(" ", "")
    return t


def _mekrs_code_key(text: str) -> str:
    """
    Kľúč pre porovnanie dodávateľského čísla (Mekrs sku2) s kódom z DB.
    Odstráni medzery a interpunkciu — „00200.14.00.030.016“ a variácie sa zrovnajú.
    Žiadne čiastočné zhody (tie vracali M20 namiesto M3).
    """
    t = (text or "").strip().lower().replace("\xa0", "")
    t = re.sub(r"\s+", "", t)
    return re.sub(r"[.\-_/]+", "", t)


def _fulltext_items_exact_for_code(
    items: list[dict[str, Any]], code_key: str
) -> list[dict[str, Any]]:
    if not code_key:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        sku2 = str(it.get("sku2") or "")
        if _mekrs_code_key(sku2) != code_key:
            continue
        slug = it.get("slug")
        if not slug or not isinstance(slug, str):
            continue
        if slug in seen:
            continue
        seen.add(slug)
        out.append(it)
    return out


def _mekrs_nominal_to_per_100ks_display(
    *,
    price_net: Optional[float],
    price_gross: Optional[float],
    currency_code: Optional[str],
) -> tuple[Optional[float], bool, bool]:
    """
    Z API /variants vypočíta číslo pre „… / 100 ks“ v UI.

    **Prednostne bez DPH** (`price`); ak chýba, použije sa `priceWithVAT` (zriedkavé).
    JSON občas vracia cenu za 1 ks (malé číslo) — pod prahom v danej mene ×100.

    Vracia: (hodnota pre UI, price_includes_vat — True len pri núdzovom fallbacku na gross, scaled).
    """
    c = (currency_code or "").strip().lower() or "eur"
    includes_vat = False
    ref: Optional[float] = None
    if price_net is not None:
        try:
            ref = float(price_net)
        except (TypeError, ValueError):
            ref = None
    if ref is None and price_gross is not None:
        try:
            ref = float(price_gross)
            includes_vat = True
        except (TypeError, ValueError):
            ref = None
    if ref is None:
        return None, False, False
    x = ref
    # Pod prahom berieme hodnotu ako cenu za 1 ks → ×100 pre „/ 100 ks“.
    # CZK: prah < 0,75 aby ~0,80 Kč/100 ks (identická pri viacerých baleniach) ostalo bez ×100.
    # EUR: prah 0,10 — B2B často vracia napr. ~0,0525 €/ks (5,25 €/100 ks); pri 0,04 by sa
    # nenásobilo a UI ukázalo cenu za ks pri popise „/ 100 ks“.
    if c == "czk":
        threshold = 0.75
    elif c == "eur":
        threshold = 0.10
    else:
        threshold = 0.10
    scaled = x < threshold
    out = (x * 100.0) if scaled else x
    return out, includes_vat, scaled


_MEKRS_RE_ZA_POPLETEK_TAIL = re.compile(
    r"\s*[·•]\s*Za\s+poplatek\s*$",
    re.IGNORECASE,
)


def _mekrs_sanitize_variant_label(label: str) -> str:
    """Odstráni z konca štítku variantu Mekrs text „· Za poplatek“ (z API / PDP)."""
    return _MEKRS_RE_ZA_POPLETEK_TAIL.sub("", (label or "").strip()).strip()


def _pack_qty_from_variant(pv: dict[str, Any]) -> int:
    raw = pv.get("amountInVariant")
    if raw is None:
        return 1
    s = str(raw).strip()
    if s.isdigit():
        n = int(s)
        return n if n >= 1 else 1
    m = re.search(r"(\d+)", s)
    if m:
        n = int(m.group(1))
        return n if n >= 1 else 1
    return 1


class MekrsHttpClient:
    """Jedna async session: login raz, potom add_to_cart pod rovnakým účtom."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE,
        timeout: float = 60.0,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._access_token: Optional[str] = None
        self._active_cart_id: Optional[str] = None
        self._profile: Optional[dict[str, Any]] = None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json, */*",
                "Origin": self.base_url,
            },
            follow_redirects=True,
        )

    @property
    def access_token(self) -> Optional[str]:
        return self._access_token

    @property
    def active_cart_id(self) -> Optional[str]:
        return self._active_cart_id

    @property
    def profile(self) -> Optional[dict[str, Any]]:
        return self._profile

    def _set_bearer(self, token: Optional[str]) -> None:
        self._access_token = token
        if token:
            self._client.headers["Authorization"] = f"Bearer {token}"
        else:
            self._client.headers.pop("Authorization", None)

    def _ensure_display_currency_cookie(self) -> None:
        """
        Bez cookie „currency“ API často vracia CZK; prehliadač po zvolení € posiela currency=eur.
        UUID meny v cookie spôsobuje 404 — musí byť reťazec „eur“ / „czk“.
        """
        host = urlparse(self.base_url).hostname
        if not host:
            return
        self._client.cookies.set(
            "currency",
            _mekrs_display_currency_code(),
            domain=host,
            path="/",
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> MekrsHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def login(
        self,
        username: str,
        password: str,
        *,
        redirect: bool = False,
    ) -> dict[str, Any]:
        """
        POST /api/user/login — uloží JWT a nastaví Authorization pre ďalšie requesty.
        """
        r = await self._client.post(
            "/api/user/login",
            json={
                "username": username.strip(),
                "password": password,
                "redirect": redirect,
            },
            headers={"Referer": f"{self.base_url}/"},
        )
        r.raise_for_status()
        data = r.json()
        token = (data.get("token") or {}).get("accessToken")
        if not token or not isinstance(token, str):
            raise RuntimeError(f"Login: neočakávaná odpoveď (chýba token.accessToken): {data!r}")
        self._set_bearer(token)
        return data

    async def fetch_user(self) -> dict[str, Any]:
        """
        GET /api/user/user — aktívny košík (activeCartId) a údaje účtu.
        """
        r = await self._client.get(
            "/api/user/user",
            headers={"Referer": f"{self.base_url}/"},
        )
        r.raise_for_status()
        self._profile = r.json()
        cart = self._profile.get("activeCartId")
        self._active_cart_id = cart if isinstance(cart, str) else None
        return self._profile

    async def ensure_session(self, username: str, password: str) -> dict[str, Any]:
        """Login + načítanie profilu (vrátane activeCartId)."""
        await self.login(username, password)
        self._ensure_display_currency_cookie()
        return await self.fetch_user()

    async def search_product(
        self,
        product_code: str,
        *,
        fulltext_limit: int = 100,
        max_products: int = 30,
    ) -> dict[str, Any]:
        """
        GET /api/product/fulltext + pre každý výsledok GET /api/product/{slug}
        a GET /api/product/{id}/variants.

        Vráti zoznam variantov (UUID, sklad, cena, balenie, názov).
        Verejná časť API nevyžaduje JWT; s Bearer môžu platiť B2B ceny.
        """
        code = (product_code or "").strip()
        if not code:
            raise ValueError("Prázdny kód produktu.")
        self._ensure_display_currency_cookie()
        code_key = _mekrs_code_key(code)

        async def _fetch_fulltext(search_q: str) -> dict[str, Any]:
            resp = await self._client.get(
                "/api/product/fulltext",
                params={"fulltext": search_q, "limit": int(fulltext_limit)},
                headers={"Referer": f"{self.base_url}/"},
            )
            resp.raise_for_status()
            return resp.json()

        blob = await _fetch_fulltext(code)
        items: list[dict[str, Any]] = list(blob.get("items") or [])
        exact_rows = _fulltext_items_exact_for_code(items, code_key)
        if not exact_rows and code_key and code_key != code:
            blob = await _fetch_fulltext(code_key)
            items = list(blob.get("items") or [])
            exact_rows = _fulltext_items_exact_for_code(items, code_key)

        slug_batch: list[str] = []
        for it in exact_rows:
            slug = it.get("slug")
            if slug and isinstance(slug, str) and slug not in slug_batch:
                slug_batch.append(slug)
            if len(slug_batch) >= max_products:
                break

        variants_out: list[dict[str, Any]] = []

        async def expand_slug(slug: str) -> None:
            try:
                pr = await self._client.get(
                    f"/api/product/{slug}",
                    headers={"Referer": f"{self.base_url}/produkty"},
                )
                if pr.status_code != 200:
                    return
                pj = pr.json()
                pid = pj.get("id")
                if not pid:
                    return
                sku2_p = str(pj.get("sku2") or "").strip()
                if code_key and _mekrs_code_key(sku2_p) != code_key:
                    return
                vr = await self._client.get(
                    f"/api/product/{pid}/variants",
                    headers={"Referer": f"{self.base_url}/produkty/{slug}"},
                )
                if vr.status_code != 200:
                    return
                vj = vr.json()
                ptitle = str(pj.get("title") or pj.get("productName") or "").strip()
                unpack = (pj.get("unpack") or {}) if isinstance(pj.get("unpack"), dict) else {}
                unpack_title = str(unpack.get("title") or "").strip()
                din = pj.get("din")
                for pv in vj.get("productVariants") or []:
                    if not isinstance(pv, dict):
                        continue
                    vid = pv.get("id")
                    if not vid:
                        continue
                    pq = _pack_qty_from_variant(pv)
                    price_obj = pv.get("price") if isinstance(pv.get("price"), dict) else {}
                    curr = price_obj.get("currency") if isinstance(price_obj.get("currency"), dict) else {}
                    price = price_obj.get("price")
                    price_vat = price_obj.get("priceWithVAT")
                    sym = str(curr.get("symbol") or "€")
                    ccode = str(curr.get("code") or "eur").lower()
                    stock = pv.get("stockLevel")
                    try:
                        stock_i = int(stock) if stock is not None else None
                    except (TypeError, ValueError):
                        stock_i = None
                    balenie = f"{pq} ks"
                    if unpack_title:
                        balenie = f"{balenie} · {unpack_title}"
                    balenie = _mekrs_sanitize_variant_label(balenie)
                    label = _mekrs_sanitize_variant_label(
                        f"{ptitle} — {balenie}" if ptitle else balenie
                    )
                    variants_out.append(
                        {
                            "variant_id": str(vid),
                            "product_id": str(pid),
                            "product_slug": slug,
                            "product_title": ptitle or slug,
                            "sku2": sku2_p,
                            "din": din,
                            "unpack_title": (
                                None
                                if unpack_title.casefold() == "za poplatek"
                                else (unpack_title or None)
                            ),
                            "pack_quantity": pq,
                            "packaging_label": balenie,
                            "label": label,
                            "stock_level": stock_i,
                            "price": float(price) if price is not None else None,
                            "price_with_vat": float(price_vat) if price_vat is not None else None,
                            "currency_code": ccode,
                            "currency_symbol": sym,
                            "packaged": bool(pv.get("packaged")),
                        }
                    )
            except Exception:
                return

        await asyncio.gather(*(expand_slug(s) for s in slug_batch))
        return {
            "query": code,
            "fulltext_count": blob.get("count"),
            "fulltext_page_limit": fulltext_limit,
            "products_expanded": len(slug_batch),
            "variants": variants_out,
        }

    async def get_cart(self, cart_id: Optional[str] = None) -> dict[str, Any]:
        cid = cart_id or self._active_cart_id
        if not cid:
            await self.fetch_user()
            cid = self._active_cart_id
        if not cid:
            raise RuntimeError("Nie je známe ID košíka (activeCartId). Najprv fetch_user().")
        r = await self._client.get(
            f"/api/cart/{cid}",
            headers={"Referer": f"{self.base_url}/"},
        )
        r.raise_for_status()
        return r.json()

    async def add_to_cart(
        self,
        product_variant_id: str,
        quantity: int,
        *,
        cart_id: Optional[str] = None,
        referer_path: str = "/",
    ) -> dict[str, Any]:
        """
        POST /api/cart/{cartId}/add
        product_variant_id: UUID variantu (z PDP / API produktu), nie kód výrobku.
        """
        if quantity < 1:
            raise ValueError("quantity musí byť aspoň 1")
        cid = cart_id or self._active_cart_id
        if not cid:
            await self.fetch_user()
            cid = self._active_cart_id
        if not cid:
            raise RuntimeError("Chýba cart_id — skontroluj prihlásenie a fetch_user().")

        ref = f"{self.base_url}{referer_path}" if referer_path.startswith("/") else referer_path
        r = await self._client.post(
            f"/api/cart/{cid}/add",
            json={"productVariant": product_variant_id, "quantity": int(quantity)},
            headers={
                "Referer": ref,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if r.status_code >= 400:
            snippet = (r.text or "").strip()
            if len(snippet) > 1200:
                snippet = snippet[:1200] + "…"
            raise RuntimeError(
                f"Mekrs košík HTTP {r.status_code} pre variant {product_variant_id!r}: {snippet!r}"
            )
        return r.json()

    async def calculate_cart_price(
        self,
        *,
        cart_id: Optional[str] = None,
        referer_path: str = "/",
    ) -> dict[str, Any]:
        """POST /api/cart/{cartId}/calculate-price (prázdne telo {}) — ako v HAR."""
        cid = cart_id or self._active_cart_id
        if not cid:
            await self.fetch_user()
            cid = self._active_cart_id
        if not cid:
            raise RuntimeError("Chýba cart_id.")
        ref = f"{self.base_url}{referer_path}" if referer_path.startswith("/") else referer_path
        r = await self._client.post(
            f"/api/cart/{cid}/calculate-price",
            json={},
            headers={
                "Referer": ref,
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        return r.json()


async def _demo() -> None:
    """Spustenie: nastav MEKRS_USER / MEKRS_PASSWORD, voliteľne MEKRS_VARIANT_UUID."""
    user = os.environ.get("MEKRS_USER", "").strip()
    pwd = os.environ.get("MEKRS_PASSWORD", "")
    variant = os.environ.get(
        "MEKRS_VARIANT_UUID",
        "018a17f0-2da9-7136-bc69-0c5e7813b5f5",
    ).strip()
    if not user or not pwd:
        print("Nastav MEKRS_USER a MEKRS_PASSWORD (napr. v .env alebo shell).")
        return

    async with MekrsHttpClient() as api:
        prof = await api.ensure_session(user, pwd)
        print("Profil:", prof.get("email"), "cart:", api.active_cart_id)
        out = await api.add_to_cart(
            variant,
            1,
            referer_path="/produkty/sr-6hr-8-8-m03x010-49892",
        )
        mod = out.get("modifiedProductVariant") or {}
        print("Pridané:", mod.get("title"), "sku2:", mod.get("sku2"))


if __name__ == "__main__":
    import asyncio

    asyncio.run(_demo())
