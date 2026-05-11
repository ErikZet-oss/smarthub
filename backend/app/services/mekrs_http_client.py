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
import json
import os
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

DEFAULT_BASE = "https://eshop.mekrs.cz"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_MEKRS_VARIANT_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _mekrs_display_currency_code() -> str:
    """Cookie hodnota pre menu: „eur“ alebo „czk“ (nie UUID z /api/currency)."""
    c = (os.environ.get("MEKRS_DISPLAY_CURRENCY") or "eur").strip().lower()
    return c if c in ("eur", "czk") else "eur"


def _mekrs_variant_uuid_equal(a: str, b: str) -> bool:
    xa = str(a or "").strip().lower()
    xb = str(b or "").strip().lower()
    return bool(xa and xb and xa == xb)


def _mekrs_stock_cap_from_add_cart_error(body: str) -> Optional[int]:
    """
    Z odpovede POST /api/cart/.../add (Symfony violations) vytiahne povolené množstvo
    (napr. šablóna „only has {{ stock }} items“ → parameters „{{ stock }}“: „1“).
    """
    try:
        blob = json.loads(body)
    except Exception:
        return None
    if not isinstance(blob, dict):
        return None
    inner = blob.get("data")
    if not isinstance(inner, dict):
        return None
    viol = inner.get("violations")
    if not isinstance(viol, list):
        return None
    for v in viol:
        if not isinstance(v, dict):
            continue
        tmpl = str(v.get("template") or "").lower()
        title = str(v.get("title") or "").lower()
        stockish = (
            "stock" in tmpl
            or "sklad" in title
            or "položek" in title
            or "položky" in title
        )
        if not stockish:
            continue
        params = v.get("parameters")
        if not isinstance(params, dict):
            continue
        for k, val in params.items():
            if "stock" in str(k).lower():
                try:
                    return max(0, int(str(val).strip()))
                except (TypeError, ValueError):
                    pass
        nums: list[int] = []
        for val in params.values():
            try:
                nums.append(int(str(val).strip()))
            except (TypeError, ValueError):
                continue
        if len(nums) == 1:
            return max(0, nums[0])
    return None


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
    pack_quantity: int = 1,
) -> tuple[Optional[float], bool, bool]:
    """
    Z API /variants vypočíta číslo pre „… / 100 ks“ v UI.

    **Prednostne bez DPH** (`price`); ak chýba, použije sa `priceWithVAT` (zriedkavé).

    Pri ``pack_quantity`` > 1 API často vracia **cenu za 1 ks** (napr. 0,1059 € pri „Balení (100 ks)“,
    na webe 10,59 €/bal. = 10,59 €/100 ks). Vtedy platí „/ 100 ks“ = ``price * 100`` (nedeľ ``pq``).
    Ak je ``price`` už **za celé balenie** (napr. 9,95 € / 94 ks), použijeme ``price * 100 / pq``.

    Rozlišovanie: pri ``pq >= 5`` a „malej“ cene v danej mene berieme hodnotu ako za ks; inak ako za balenie.

    Pri ``pack_quantity`` == 1: pod prahom (EUR < 0,65; CZK < 0,75) ×100 (malé číslo = cena za ks).

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
    try:
        pq = int(float(pack_quantity))
    except (TypeError, ValueError):
        pq = 1
    pq = max(1, pq)
    if pq > 1:
        # „Malá“ suma pri väčšom balení = Mekrs často posiela unit price, nie line total za balenie.
        if c == "czk":
            unit_like_cutoff = 25.0
        else:
            unit_like_cutoff = 1.0
        if pq >= 5 and x < unit_like_cutoff:
            out = round(x * 100.0, 4)
        else:
            out = round(x * (100.0 / float(pq)), 4)
        return out, includes_vat, False
    # Jednotkové balenie: pod prahom berieme hodnotu ako cenu za 1 ks (resp. za 1 predajnú jednotku) → ×100 pre „/ 100 ks“.
    # CZK: prah < 0,75 aby ~0,80 Kč/100 ks (identická pri viacerých baleniach) ostalo bez ×100.
    # EUR: 0,15 bolo príliš nízke — napr. 0,51 €/bal (1 ks) ostalo v UI ako 0,51 namiesto ~51 €/100 ks (viď Mekrs PDP).
    # Prah 0,65: pod ním typicky „malá“ jednotková cena (0,51; 0,1059); nad tým častejšie už suma blízka „/ 100 ks“.
    if c == "czk":
        threshold = 0.75
    elif c == "eur":
        threshold = 0.65
    else:
        threshold = 0.65
    scaled = x < threshold
    out = round((x * 100.0) if scaled else x, 4)
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
        slug_to_fulltext_item: dict[str, dict[str, Any]] = {}
        for it in exact_rows:
            slug = it.get("slug")
            if slug and isinstance(slug, str) and slug.strip():
                s = slug.strip()
                slug_to_fulltext_item[s] = it if isinstance(it, dict) else {}
            if slug and isinstance(slug, str) and slug not in slug_batch:
                slug_batch.append(slug)
            if len(slug_batch) >= max_products:
                break

        product_stock_level: Optional[int] = None
        for it in exact_rows:
            sl = it.get("stockLevel")
            if sl is None:
                continue
            try:
                product_stock_level = int(sl)
                break
            except (TypeError, ValueError):
                continue

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
                rows_added = 0
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
                    rows_added += 1

                # Jednoverziové alebo vypredané: GET …/variants vracia prázdne
                # ``productVariants`` (napr. ``multipleProductVariants: false``, sklad 0).
                # Údaje sú na koreni PDP a v zázname z fulltextu (cena).
                if rows_added == 0:
                    ft_hit = slug_to_fulltext_item.get(slug) or {}
                    price_src: dict[str, Any] = {}
                    p_ft = ft_hit.get("price")
                    if isinstance(p_ft, dict):
                        price_src = p_ft
                    p_pj = pj.get("price")
                    if not price_src and isinstance(p_pj, dict):
                        price_src = p_pj
                    curr = (
                        price_src.get("currency")
                        if isinstance(price_src.get("currency"), dict)
                        else {}
                    )
                    price = price_src.get("price")
                    price_vat = price_src.get("priceWithVAT")
                    sym = str(curr.get("symbol") or "€")
                    ccode = str(curr.get("code") or "eur").lower()
                    st_src = pj.get("stockLevel")
                    if st_src is None and ft_hit.get("stockLevel") is not None:
                        st_src = ft_hit.get("stockLevel")
                    try:
                        stock_i = int(st_src) if st_src is not None else None
                    except (TypeError, ValueError):
                        stock_i = None
                    root_id = pj.get("id")
                    if not root_id:
                        return
                    pq = 1
                    balenie = f"{pq} ks"
                    if unpack_title:
                        balenie = f"{balenie} · {unpack_title}"
                    balenie = _mekrs_sanitize_variant_label(balenie)
                    label = _mekrs_sanitize_variant_label(
                        f"{ptitle} — {balenie}" if ptitle else balenie
                    )
                    variants_out.append(
                        {
                            "variant_id": str(root_id),
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
                            "packaged": bool(ft_hit.get("multipleProductVariants") is True),
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
            # Rovnaké ako „skladom celkom“ na PDP — nie súčet riadkov variantov.
            "product_stock_level": product_stock_level,
        }

    async def stock_level_for_variant(
        self,
        *,
        product_code: str,
        variant_id: str,
    ) -> Optional[int]:
        """Sklad konkrétnej varianty (UUID) cez fulltext + /variants — pre orezanie qty pri košíku."""
        code = (product_code or "").strip()
        vid = (variant_id or "").strip()
        if not code or not vid:
            return None
        blob = await self.search_product(code)
        for v in blob.get("variants") or []:
            if not isinstance(v, dict):
                continue
            if not _mekrs_variant_uuid_equal(str(v.get("variant_id") or ""), vid):
                continue
            st = v.get("stock_level")
            if st is None:
                return None
            try:
                return int(st)
            except (TypeError, ValueError):
                return None
        return None

    async def get_cart(
        self,
        cart_id: Optional[str] = None,
        *,
        recalculate_prices: bool = True,
    ) -> dict[str, Any]:
        cid = cart_id or self._active_cart_id
        if not cid:
            await self.fetch_user()
            cid = self._active_cart_id
        if not cid:
            raise RuntimeError("Nie je známe ID košíka (activeCartId). Najprv fetch_user().")
        if recalculate_prices:
            try:
                await self.calculate_cart_price(cart_id=cid, referer_path="/")
            except Exception:
                # GET môže mať ceny z predchádzajúceho stavu; pri prázdnych sumách skús bez recalc.
                pass
        r = await self._client.get(
            f"/api/cart/{cid}",
            headers={"Referer": f"{self.base_url}/"},
        )
        r.raise_for_status()
        return r.json()

    async def enrich_cart_lines_variant_codes(
        self,
        cart: dict[str, Any],
        lines: list[dict[str, Any]],
    ) -> None:
        """
        Doplní ``variant_code`` tam, kde košík vráti len UUID variantu bez kódu.

        Namiesto sekvenčného volania pre každý riadok: paralelné rozlíšenie slug→id,
        jedno ``/variants`` na unikátny produkt (viac položiek z rovnakého produktu =
        jeden request).
        """
        items = _mekrs_cart_item_dicts(cart)
        n = min(len(items), len(lines))
        referer = f"{self.base_url}/kosik"

        pending: list[tuple[int, str, Optional[str], Optional[str]]] = []
        for i in range(n):
            line = lines[i]
            if (line.get("variant_code") or "").strip():
                continue
            it = items[i]
            uuid = _mekrs_cart_item_variant_uuid(it)
            if not uuid:
                continue
            slug = _mekrs_cart_item_product_slug(it)
            pid = _mekrs_cart_item_product_id(it)
            pending.append((i, uuid, slug, pid))

        if not pending:
            return

        slug_to_pid: dict[str, str] = {}
        need_slug = sorted(
            {s for _, _, s, p in pending if s and not (p or "").strip()},
            key=lambda x: x,
        )
        if need_slug:

            async def _slug_to_id(slg: str) -> tuple[str, Optional[str]]:
                pr = await self._client.get(
                    f"/api/product/{quote(slg, safe='')}",
                    headers={"Referer": referer},
                )
                if pr.status_code != 200:
                    return slg, None
                pj = pr.json()
                if isinstance(pj, dict) and pj.get("id") is not None:
                    return slg, str(pj.get("id")).strip()
                return slg, None

            for slg, p in await asyncio.gather(*[_slug_to_id(s) for s in need_slug]):
                if p:
                    slug_to_pid[slg] = p

        resolved: list[tuple[int, str, str]] = []
        for i, uuid, slug, pid in pending:
            eff = (pid or "").strip()
            if not eff and slug:
                eff = slug_to_pid.get(slug, "")
            if eff:
                resolved.append((i, uuid, eff))

        unique_pids = sorted({p for _, _, p in resolved})
        pid_to_rows: dict[str, list[dict[str, Any]]] = {}

        if unique_pids:

            async def _fetch_variants(
                prod_id: str,
            ) -> tuple[str, list[dict[str, Any]]]:
                vr = await self._client.get(
                    f"/api/product/{quote(prod_id, safe='')}/variants",
                    headers={"Referer": referer},
                )
                if vr.status_code != 200:
                    return prod_id, []
                vj = vr.json()
                rows = vj.get("productVariants") if isinstance(vj, dict) else None
                if not isinstance(rows, list):
                    return prod_id, []
                out: list[dict[str, Any]] = [
                    x for x in rows if isinstance(x, dict)
                ]
                return prod_id, out

            for prod_id, rows in await asyncio.gather(
                *[_fetch_variants(p) for p in unique_pids]
            ):
                if rows:
                    pid_to_rows[prod_id] = rows

        vid_to_sku: dict[str, str] = {}
        for rows in pid_to_rows.values():
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("id") or "").strip().lower()
                s = str(row.get("sku2") or row.get("sku") or "").strip()
                if rid and s:
                    vid_to_sku[rid] = s

        for i, uuid, _pid in resolved:
            sku = vid_to_sku.get(uuid.strip().lower())
            if sku:
                lines[i]["variant_code"] = sku

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
        body_qty = int(quantity)
        payload = {"productVariant": product_variant_id, "quantity": body_qty}
        headers = {
            "Referer": ref,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        r = await self._client.post(
            f"/api/cart/{cid}/add",
            json=payload,
            headers=headers,
        )
        if r.status_code >= 400:
            cap = _mekrs_stock_cap_from_add_cart_error(r.text or "")
            if cap is not None and body_qty > cap and cap >= 1:
                r = await self._client.post(
                    f"/api/cart/{cid}/add",
                    json={"productVariant": product_variant_id, "quantity": cap},
                    headers=headers,
                )
        if r.status_code >= 400:
            friendly: Optional[str] = None
            try:
                blob = r.json()
                if isinstance(blob, dict):
                    inner = blob.get("data")
                    if isinstance(inner, dict):
                        viol = inner.get("violations")
                        if isinstance(viol, list) and viol:
                            v0 = viol[0]
                            if isinstance(v0, dict):
                                friendly = (
                                    (v0.get("title") or "").strip()
                                    or (v0.get("detail") or "").strip()
                                )
                        if not friendly:
                            friendly = (inner.get("detail") or "").strip()
                    if not friendly:
                        friendly = (blob.get("message") or "").strip() or (
                            blob.get("statusMessage") or ""
                        ).strip()
            except Exception:
                pass
            snippet = (r.text or "").strip()
            if len(snippet) > 1200:
                snippet = snippet[:1200] + "…"
            if friendly:
                raise RuntimeError(
                    f"Mekrs košík (HTTP {r.status_code}, variant {product_variant_id!r}): {friendly}"
                )
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


def _mekrs_parse_money(obj: Any) -> Optional[float]:
    """
    Peniaze z Mekrs JSON: rovnako ako pri variantoch na PDP — v objekte s kľúčom ``price`` /
    ``priceWithVAT`` sú hodnoty už v **hlavných jednotkách** meny (nie halévre).

    Holé ``int`` (bez vnoreného objektu) sa berú ako **minor units** (÷ 100), čo zodpovedá
    bežnému API pre „surové“ číselné sumy.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in ("price", "priceWithVAT", "price_with_vat"):
            v = obj.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return round(float(v), 4)
            if isinstance(v, str) and v.strip():
                try:
                    return round(float(v.strip().replace(",", ".")), 4)
                except ValueError:
                    pass
        for k in ("amount", "value"):
            v = obj.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return round(float(v) / 100.0, 4)
            if isinstance(v, float):
                return round(v, 4)
            if isinstance(v, str) and v.strip():
                try:
                    x = float(v.strip().replace(",", "."))
                except ValueError:
                    continue
                if x == int(x) and abs(x) >= 100:
                    return round(x / 100.0, 4)
                return round(x, 4)
        return None
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return round(float(obj) / 100.0, 4)
    if isinstance(obj, bool):
        return None
    if isinstance(obj, str) and obj.strip():
        try:
            return round(float(obj.strip().replace(",", ".")), 4)
        except ValueError:
            return None
    return None


def _mekrs_cart_item_dicts(blob: dict[str, Any]) -> list[dict[str, Any]]:
    raw = blob.get("items")
    if not isinstance(raw, list):
        for alt in ("cartItems", "entries", "lines"):
            if isinstance(blob.get(alt), list):
                raw = blob[alt]
                break
        else:
            raw = []
    return [x for x in raw if isinstance(x, dict)]


def _mekrs_split_cart_title(raw: str) -> tuple[str, Optional[str]]:
    """
    Na webe je kód často druhý riadok pod názvom — API niekedy vráti jeden reťazec s ``\\n``.
    """
    t = (raw or "").strip()
    if not t or "\n" not in t and "\r" not in t:
        return t, None
    norm = t.replace("\r\n", "\n").replace("\r", "\n")
    parts = [p.strip() for p in norm.split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return parts[0] if parts else "", None


def _mekrs_cart_item_variant_uuid(item: dict[str, Any]) -> Optional[str]:
    pv = item.get("productVariant") or item.get("variant")
    if isinstance(pv, str):
        s = pv.strip()
        if _MEKRS_VARIANT_UUID_RE.match(s):
            return s
        if "/" in s:
            tail = s.rstrip("/").split("/")[-1]
            if _MEKRS_VARIANT_UUID_RE.match(tail):
                return tail
    if isinstance(pv, dict):
        vid = pv.get("id") or pv.get("uuid")
        if isinstance(vid, str) and vid.strip():
            return vid.strip()
    return None


_MEKRS_PRODUKTY_SLUG_RE = re.compile(r"/produkty/([^/?#]+)", re.IGNORECASE)


def _mekrs_cart_item_product_slug(item: dict[str, Any]) -> Optional[str]:
    p = item.get("product")
    if isinstance(p, dict):
        for k in ("slug", "slugUrl", "urlSlug"):
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for k in ("url", "link", "href"):
            v = p.get(k)
            if isinstance(v, str):
                m = _MEKRS_PRODUKTY_SLUG_RE.search(v)
                if m and m.group(1).strip():
                    return m.group(1).strip()
    for k in ("productSlug", "slug"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("url", "link", "productUrl"):
        v = item.get(k)
        if isinstance(v, str):
            m = _MEKRS_PRODUKTY_SLUG_RE.search(v)
            if m and m.group(1).strip():
                return m.group(1).strip()
    return None


def _mekrs_cart_item_product_id(item: dict[str, Any]) -> Optional[str]:
    p = item.get("product")
    if isinstance(p, dict) and p.get("id") is not None:
        s = str(p.get("id")).strip()
        return s or None
    v = item.get("productId")
    if v is not None:
        s = str(v).strip()
        return s or None
    return None


def mekrs_parse_cart_json(blob: dict[str, Any]) -> dict[str, Any]:
    """
    Z odpovede ``GET /api/cart/{id}`` — riadky a súčet (podľa cookie meny, zvyčajne EUR).
    """
    lines: list[dict[str, Any]] = []

    def _first_money(*candidates: Any) -> Optional[float]:
        for c in candidates:
            m = _mekrs_parse_money(c)
            if m is not None:
                return m
        return None

    def _line_product_code(item: dict[str, Any], variant: dict[str, Any]) -> Optional[str]:
        """Kód z riadku košíka (sku2 / sku / vnorený product) — rovnaká logika ako v katalógu."""
        keys = (
            "sku2",
            "sku",
            "supplierSku",
            "productCode",
            "code",
            "catalogNumber",
            "variantCode",
            "reference",
        )
        for d in (item, variant):
            for k in keys:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        prod_v = variant.get("product")
        if isinstance(prod_v, dict):
            for k in keys:
                v = prod_v.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        prod_i = item.get("product")
        if isinstance(prod_i, dict):
            for k in keys:
                v = prod_i.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    for it in _mekrs_cart_item_dicts(blob):
        qty_raw = it.get("quantity")
        try:
            qn = int(qty_raw) if qty_raw is not None else 0
        except (TypeError, ValueError):
            qn = 0
        pv_raw = it.get("productVariant") or it.get("variant")
        pv: dict[str, Any] = pv_raw if isinstance(pv_raw, dict) else {}
        raw_title = str(
            pv.get("title") or it.get("title") or it.get("name") or ""
        ).strip()
        title, code_nl = _mekrs_split_cart_title(raw_title)
        code = _line_product_code(it, pv) or code_nl
        label = title or (code or "") or "—"
        line_total = _first_money(
            it.get("totalPrice"),
            it.get("discountedTotalPrice"),
            it.get("rowTotal"),
            it.get("total"),
        )
        unit_eur = _first_money(
            it.get("unitPrice"),
            it.get("discountedUnitPrice"),
            it.get("price"),
        )
        if unit_eur is None:
            unit_eur = _mekrs_parse_money(pv.get("price"))
        if unit_eur is None and line_total is not None and qn > 0:
            unit_eur = round(line_total / qn, 4)
        lines.append(
            {
                "label": label,
                "quantity": qn,
                "unit_price_eur": unit_eur,
                "line_total_eur": line_total,
                "variant_code": code,
            }
        )

    total_eur: Optional[float] = None
    for tk in (
        "totalPrice",
        "itemsTotalPrice",
        "itemsPrice",
        "cartTotal",
        "total",
        "price",
    ):
        tv = blob.get(tk)
        total_eur = _mekrs_parse_money(tv)
        if total_eur is not None:
            break
    if total_eur is None and lines:
        s = sum(
            float(x["line_total_eur"])
            for x in lines
            if isinstance(x.get("line_total_eur"), (int, float))
        )
        if s > 0:
            total_eur = round(s, 4)

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": len(lines),
    }


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
