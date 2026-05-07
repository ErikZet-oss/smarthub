"""
Haspl (haspl.cz): Sylius Shop API v2 — JWT cez POST /authentication-token, katalóg cez
GET /product-variants, košík ako Order (tokenValue), položky POST …/orders/{token}/items.

Hlavičky ako front: x-channel (CZ_WEB_STORE), x-currency (EUR), Accept application/ld+json.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def haspl_norm_code(text: str) -> str:
    t = (text or "").strip().upper().replace("\xa0", "")
    t = re.sub(r"\s+", "", t)
    return t


def haspl_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return "https://www.haspl.cz"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def supplier_shop_cart_url(shop_url: str) -> str:
    """
    Verejná stránka košíka v novom okne: doména z ``shop_url`` + ``/kosik``
    (Mekrs, Haspl a bežné CZ e-shopy). Inoxmare (Magento): ``/{store}/checkout/cart/``.
    Pri prázdnej URL prázdny reťazec.
    """
    raw = (shop_url or "").strip()
    if not raw:
        return ""
    if "inoxmare.com" in raw.lower():
        from app.services.inoxmare_http_client import inoxmare_cart_url

        return inoxmare_cart_url(raw, None)
    return f"{haspl_base_url(raw)}/kosik"


def _haspl_channel() -> str:
    return (os.environ.get("HASPL_X_CHANNEL") or "CZ_WEB_STORE").strip()


def _haspl_currency() -> str:
    return (os.environ.get("HASPL_X_CURRENCY") or "EUR").strip().upper()


def _hydra_members(blob: Any) -> list[dict[str, Any]]:
    if not isinstance(blob, dict):
        return []
    m = blob.get("hydra:member")
    if not isinstance(m, list):
        return []
    return [x for x in m if isinstance(x, dict)]


def _minor_to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v) / 100.0, 4)
    except (TypeError, ValueError):
        return None


def haspl_gross_price_eur(member: dict[str, Any]) -> Optional[float]:
    """Cena s DPH z API (minor units → EUR)."""
    return _minor_to_float(member.get("price"))


def haspl_net_price_eur(member: dict[str, Any]) -> Optional[float]:
    """Cena bez DPH — rovnaké polia ako „Cena bez DPH“ na PDP (minor units → EUR)."""
    for key in ("unitPriceWithoutTax", "priceWithoutTax"):
        x = _minor_to_float(member.get(key))
        if x is not None:
            return x
    return None


def haspl_price_unit_key(member: dict[str, Any]) -> Optional[str]:
    """Kľúč pre UI suffix (napr. per_sks)."""
    u = str(member.get("unitOfMeasure") or "").strip().lower()
    if u == "sks":
        return "per_sks"
    return None


def _parse_first_positive_int(text: str) -> Optional[int]:
    m = re.search(r"(\d+)", (text or "").lower())
    if not m:
        return None
    try:
        q = int(m.group(1))
        return q if q >= 1 else None
    except ValueError:
        return None


def haspl_variant_pack_quantity(member: dict[str, Any]) -> int:
    """
    Kusov v jednom balení ako na PDP („Balení obsahuje 100 ks“).

    API často má ``unitsInPackage`` = 1 (jedna predajná jednotka) a skutočný počet
    kusov v ``unitsInPackageText`` (napr. „100 ks“) — berieme maximum z oboch zdrojov.
    """
    txt = str(member.get("unitsInPackageText") or "").strip()
    from_text = _parse_first_positive_int(txt)

    uip = member.get("unitsInPackage")
    from_uip: Optional[int] = None
    if isinstance(uip, int) and uip >= 1:
        from_uip = uip
    else:
        try:
            n = int(uip)
            if n >= 1:
                from_uip = n
        except (TypeError, ValueError):
            pass

    if from_text is not None and from_uip is not None:
        return max(from_text, from_uip)
    if from_text is not None:
        return from_text
    if from_uip is not None:
        return from_uip
    return 1


def haspl_pack_display_text(member: dict[str, Any], *, pack_quantity: int) -> str:
    """
    Text ako na PDP (napr. „Balení obsahuje 100 ks“).

    API niekedy vráti len skrátený tvar („100 ks“); pri prihlásení môže
    ``unitsInPackageText`` chýbať — vtedy použijeme ``pack_quantity``.
    """
    t = str(member.get("unitsInPackageText") or "").strip()
    pq = max(1, int(pack_quantity) if pack_quantity else 1)
    tl = t.lower()
    if "balení" in tl and "obsahuje" in tl:
        return t
    if t:
        if re.match(r"^\d", t):
            return f"Balení obsahuje {t}"
        return t
    return f"Balení obsahuje {pq} ks"


def haspl_pieces_to_pack_units(pieces: int, pack_q: int) -> int:
    """Počet predajných jednotiek (balení) pre API — zaokrúhli nahor."""
    pq = max(1, int(pack_q) if pack_q else 1)
    pc = max(1, int(pieces) if pieces else 1)
    return max(1, math.ceil(pc / pq))


def haspl_parse_open_order(order: dict[str, Any]) -> dict[str, Any]:
    """
    Z odpovede ``GET /api/v2/shop/orders/{token}`` — riadky košíka a súčet (EUR z minor units).

    Prednostne **bez DPH** (``*WithoutTax``, ``subtotal`` / ``itemsSubtotal`` pred ``total``),
    aby UI zodpovedalo „cena bez DPH“ z eshopu.
    """
    lines: list[dict[str, Any]] = []
    raw_items = order.get("items")
    if not isinstance(raw_items, list):
        raw_items = order.get("orderItems") if isinstance(order.get("orderItems"), list) else []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        qty_raw = it.get("quantity")
        try:
            qn = int(qty_raw) if qty_raw is not None else 0
        except (TypeError, ValueError):
            qn = 0
        label = str(
            it.get("productName")
            or it.get("variantName")
            or it.get("name")
            or ""
        ).strip()
        variant_code: Optional[str] = None
        v = it.get("variant")
        if isinstance(v, str) and "product-variants" in v:
            variant_code = v.rstrip("/").split("/")[-1] or None
        elif isinstance(v, dict):
            variant_code = str(v.get("code") or "").strip() or None
            if not label:
                label = str(v.get("name") or "").strip()
        line_total: Optional[float] = None
        for tk in (
            "totalWithoutTax",
            "subtotalWithoutTax",
            "subtotal",
        ):
            line_total = _minor_to_float(it.get(tk))
            if line_total is not None:
                break

        unit_eur: Optional[float] = None
        for uk in ("unitPriceWithoutTax", "discountedUnitPriceWithoutTax"):
            unit_eur = _minor_to_float(it.get(uk))
            if unit_eur is not None:
                break

        if unit_eur is None and line_total is not None and qn > 0:
            unit_eur = round(line_total / qn, 4)

        if line_total is None and unit_eur is not None and qn > 0:
            line_total = round(unit_eur * qn, 4)

        # Fallback s DPH len ak bez DPH v JSON nie je (Sylius niekedy vráti len hrubé polia).
        if line_total is None:
            for tk in ("total", "unitsTotal"):
                line_total = _minor_to_float(it.get(tk))
                if line_total is not None:
                    break
        if unit_eur is None:
            for uk in ("unitPrice", "discountedUnitPrice"):
                unit_eur = _minor_to_float(it.get(uk))
                if unit_eur is not None:
                    break
        if unit_eur is None and line_total is not None and qn > 0:
            unit_eur = round(line_total / qn, 4)
        if line_total is None and unit_eur is not None and qn > 0:
            line_total = round(unit_eur * qn, 4)
        lines.append(
            {
                "label": label or "—",
                "quantity": qn,
                "unit_price_eur": unit_eur,
                "line_total_eur": line_total,
                "variant_code": variant_code,
            }
        )
    total_eur: Optional[float] = None
    for tk in (
        "itemsTotalWithoutTax",
        "itemsSubtotal",
        "subtotal",
        "totalWithoutTax",
        "itemsTotal",
        "total",
        "orderTotal",
    ):
        if tk in order and order.get(tk) is not None:
            total_eur = _minor_to_float(order.get(tk))
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


class HasplHttpClient:
    def __init__(self, base_url: str) -> None:
        self._base = haspl_base_url(base_url)
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "application/ld+json",
                "Accept-Language": "cs_CZ",
                "x-channel": _haspl_channel(),
                "x-currency": _haspl_currency(),
            },
            follow_redirects=True,
            timeout=httpx.Timeout(60.0),
        )
        self._jwt: Optional[str] = None

    async def __aenter__(self) -> HasplHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    def _auth_headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._jwt:
            h["Authorization"] = f"Bearer {self._jwt}"
        return h

    async def login(self, email: str, password: str) -> None:
        em = (email or "").strip()
        pw = password or ""
        if not em or not pw:
            raise ValueError("Haspl: chýba e-mail alebo heslo (username = e-mail).")
        r = await self._client.post(
            "/api/v2/shop/authentication-token",
            json={"email": em, "password": pw},
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if r.status_code >= 400:
            try:
                blob = r.json()
                if isinstance(blob, dict) and blob.get("message"):
                    raise RuntimeError(f"Haspl prihlásenie: {blob.get('message')}")
            except RuntimeError:
                raise
            except Exception:
                pass
            raise RuntimeError(f"Haspl prihlásenie HTTP {r.status_code}: {r.text!r}")
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Haspl prihlásenie: neočakávaná odpoveď {data!r}")
        token = data.get("token") or data.get("access_token")
        if not token or not isinstance(token, str):
            raise RuntimeError(f"Haspl prihlásenie: v odpovedi chýba token: {data!r}")
        self._jwt = token

    async def ensure_session(self, email: str, password: str) -> None:
        await self.login(email, password)

    def _anonymous_variant_headers(self) -> dict[str, str]:
        """GET katalógu bez JWT (B2B odpoveď niekedy vynechá ``unitsInPackageText``)."""
        return {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/ld+json",
            "Accept-Language": "cs_CZ",
            "x-channel": _haspl_channel(),
            "x-currency": _haspl_currency(),
        }

    async def _enrich_variants_pack_from_public(
        self,
        members: list[dict[str, Any]],
        search_key: str,
    ) -> list[dict[str, Any]]:
        if not members:
            return members
        if not any(
            not str(m.get("unitsInPackageText") or "").strip() for m in members
        ):
            return members
        try:
            r = await self._client.get(
                "/api/v2/shop/product-variants",
                params={"productCodeOrExternal": search_key, "itemsPerPage": 30},
                headers=self._anonymous_variant_headers(),
            )
            r.raise_for_status()
            pub = _hydra_members(r.json())
        except (httpx.HTTPError, ValueError):
            return members
        by_code = {
            str(x.get("code") or "").strip(): x
            for x in pub
            if isinstance(x, dict) and str(x.get("code") or "").strip()
        }
        out: list[dict[str, Any]] = []
        for m in members:
            if str(m.get("unitsInPackageText") or "").strip():
                out.append(m)
                continue
            code = str(m.get("code") or "").strip()
            p = by_code.get(code) if code else None
            if not p:
                out.append(m)
                continue
            mm = dict(m)
            pit = str(p.get("unitsInPackageText") or "").strip()
            if pit:
                mm["unitsInPackageText"] = pit
            puip = p.get("unitsInPackage")
            try:
                pub_uip = int(puip) if puip is not None else None
            except (TypeError, ValueError):
                pub_uip = None
            try:
                cur_uip = int(mm.get("unitsInPackage"))
            except (TypeError, ValueError):
                cur_uip = None
            if pub_uip is not None and pub_uip >= 1:
                if cur_uip is None or cur_uip < pub_uip:
                    mm["unitsInPackage"] = pub_uip
            out.append(mm)
        return out

    async def fetch_variants_by_supplier_code(self, product_code: str) -> list[dict[str, Any]]:
        """
        Verejný endpoint: productCodeOrExternal zodpovedá kódu v e-shope (code / externý kód).
        """
        keys = []
        raw = (product_code or "").strip()
        if raw:
            keys.append(raw)
        nk = haspl_norm_code(product_code)
        if nk and nk not in keys:
            keys.append(nk)
        last_empty = False
        for key in keys:
            r = await self._client.get(
                "/api/v2/shop/product-variants",
                params={"productCodeOrExternal": key, "itemsPerPage": 30},
                headers=self._auth_headers(),
            )
            r.raise_for_status()
            blob = r.json()
            mem = _hydra_members(blob)
            if mem:
                mem = await self._enrich_variants_pack_from_public(mem, key)
                return mem
            last_empty = True
        if last_empty:
            return []
        return []

    async def _find_cart_token(self) -> Optional[str]:
        for params in (
            {"itemsPerPage": 30, "checkoutState": "cart"},
            {"itemsPerPage": 30},
        ):
            r = await self._client.get(
                "/api/v2/shop/orders",
                params=params,
                headers=self._auth_headers(),
            )
            if r.status_code >= 400:
                continue
            blob = r.json()
            for o in _hydra_members(blob):
                tv = o.get("tokenValue")
                if not isinstance(tv, str) or not tv.strip():
                    continue
                st = str(o.get("state") or "")
                cs = str(o.get("checkoutState") or "")
                if st == "cart" or cs == "cart":
                    return tv.strip()
        return None

    async def _create_cart(self) -> str:
        r = await self._client.post(
            "/api/v2/shop/orders",
            content=b"{}",
            headers={
                **self._auth_headers(),
                "Content-Type": "application/ld+json",
                "Accept": "application/ld+json",
            },
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Haspl: vytvorenie košíka HTTP {r.status_code}: {r.text!r}")
        data = r.json()
        if isinstance(data, dict):
            tv = data.get("tokenValue")
            if isinstance(tv, str) and tv.strip():
                return tv.strip()
        raise RuntimeError(f"Haspl: v odpovedi chýba tokenValue košíka: {data!r}")

    async def get_or_create_cart_token(self) -> str:
        if not self._jwt:
            raise RuntimeError("Haspl: najprv prihlásenie (JWT).")
        tok = await self._find_cart_token()
        if tok:
            return tok
        return await self._create_cart()

    async def fetch_open_cart_order(self) -> dict[str, Any]:
        """Aktuálny košík (Order) — Sylius Shop API."""
        if not self._jwt:
            raise RuntimeError("Haspl: najprv prihlásenie (JWT).")
        token = await self.get_or_create_cart_token()
        enc = quote(token, safe="")
        r = await self._client.get(
            f"/api/v2/shop/orders/{enc}",
            params={"tokenValue": token},
            headers={**self._auth_headers(), "Accept": "application/ld+json"},
        )
        if r.status_code == 404:
            return {"items": [], "total": 0}
        r.raise_for_status()
        blob = r.json()
        return blob if isinstance(blob, dict) else {}

    async def add_to_cart(
        self,
        *,
        variant_code: str,
        quantity_packs: int,
    ) -> dict[str, Any]:
        if not self._jwt:
            raise RuntimeError("Haspl: najprv prihlásenie.")
        vc = (variant_code or "").strip()
        if not vc:
            raise ValueError("Haspl: prázdny kód variantu.")
        q = int(quantity_packs)
        if q < 1:
            raise ValueError("Haspl: množstvo balení musí byť aspoň 1.")
        token = await self.get_or_create_cart_token()
        r = await self._client.post(
            f"/api/v2/shop/orders/{token}/items",
            params={"tokenValue": token},
            json={"items": [{"productVariant": vc, "quantity": q}]},
            headers={
                **self._auth_headers(),
                "Content-Type": "application/ld+json",
                "Accept": "application/ld+json",
            },
        )
        if r.status_code >= 400:
            try:
                blob = r.json()
                if isinstance(blob, dict) and blob.get("message"):
                    raise RuntimeError(f"Haspl košík: {blob.get('message')}")
            except RuntimeError:
                raise
            except Exception:
                pass
            raise RuntimeError(f"Haspl košík HTTP {r.status_code}: {r.text!r}")
        return r.json() if r.content else {}
