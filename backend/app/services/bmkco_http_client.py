"""
BMKCO e-shop (eshop.bmkco.cz): B2C účet + AJAX endpointy pod /cs/Data/*.

HAR flow:
- GET  /cs/Account/LoginB2C
- POST /cs/Account/LoginB2C (form_key, Email, Password) -> 302 /cs/Home/IndexB2C
- POST /cs/Data/IsLogged
- POST /cs/Data/GetZboziDetail (karta, zvolenaMena, jazyk)
- POST /cs/Data/DoKosiku (karta, mnozstvi)
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_BMKCO_BASE = "https://eshop.bmkco.cz"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def bmkco_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return DEFAULT_BMKCO_BASE
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def bmkco_norm_code(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _bmkco_parse_decimal(text: str) -> Optional[float]:
    t = str(text or "").strip()
    if not t:
        return None
    t = t.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _bmkco_parse_int(text: Any) -> Optional[int]:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return None
    t = str(text).strip()
    if not t:
        return None
    m = re.search(r"(-?\d+(?:[.,]\d+)?)", t.replace("\xa0", " ").replace(" ", ""))
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", ".")))
    except ValueError:
        return None


def _pick_first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d.get(k) not in (None, ""):
            return d.get(k)
    return None


def _bmkco_pick_pack_quantity(detail: dict[str, Any]) -> Optional[int]:
    """BMCo nie vždy vracia počet ks v balení pod rovnakým kľúčom.
    Najprv skúsime známe polia, potom fallback na ďalšie "baleni" metadáta.
    """
    pack_raw = _pick_first(
        detail,
        (
            "početMJvBaleni",
            "pocetMJvBaleni",
            "prepocetBaleninaMJ",
            "baleni",
            "mnozstviVBaleni",
            "mnozstviVbaleni",
            "baleniMj",
            "baleniMJ",
        ),
    )
    pack_q = _bmkco_parse_int(pack_raw)
    if pack_q is not None and pack_q > 1:
        return pack_q

    candidates: list[tuple[int, int]] = []

    def _score_key(key_path: str) -> int:
        k = key_path.lower()
        score = 0
        if "pocetmjvbaleni" in k or "početmjvbaleni" in k:
            score += 9
        if "prepocetbaleninamj" in k:
            score += 8
        if "mnozstvivbaleni" in k:
            score += 7
        if "balenimj" in k:
            score += 6
        if "balen" in k and "mj" in k:
            score += 4
        elif "balen" in k or "balik" in k or "karton" in k:
            score += 2
        if any(x in k for x in ("sklad", "stock", "cena", "price", "karta", "id")):
            score -= 4
        return score

    def _walk(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, (*path, str(k)))
            return
        if isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, (*path, f"[{i}]"))
            return
        n = _bmkco_parse_int(node)
        if n is None or n <= 0:
            return
        key_path = ".".join(path)
        s = _score_key(key_path)
        # Povoliť slabé zhody len pre väčšie hodnoty; zabráni to chybnému výberu typu "2".
        if s < 0:
            return
        if s == 0 and n < 10:
            return
        candidates.append((s, n))

    _walk(detail, tuple())
    if pack_q is not None and pack_q > 0:
        # Explicitné top-level pole má prednosť pred heuristikou rovnakej kvality.
        candidates.append((10, pack_q))

    if not candidates:
        return pack_q if pack_q and pack_q > 0 else None
    # Najprv kvalita kľúča, potom väčšia hodnota (napr. 200 pred 2).
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][1]


class BmkcoHttpClient:
    def __init__(self, base_url: str) -> None:
        self._base = bmkco_base_url(base_url)
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept-Language": "cs,sk;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(45.0),
        )
        self._login_ok = False

    async def __aenter__(self) -> BmkcoHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def _get_login_hidden_fields(self) -> dict[str, str]:
        r = await self._client.get("/cs/Account/LoginB2C")
        r.raise_for_status()
        html = r.text or ""
        out: dict[str, str] = {}
        # Vezmi všetky hidden inputy z login formulára (form_key, __RequestVerificationToken, ...).
        for m in re.finditer(
            r"<input[^>]*type\s*=\s*['\"]hidden['\"][^>]*>",
            html,
            re.I | re.DOTALL,
        ):
            tag = m.group(0)
            mn = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I)
            if not mn:
                continue
            name = (mn.group(1) or "").strip()
            if not name:
                continue
            mv = re.search(r"value\s*=\s*['\"]([^'\"]*)['\"]", tag, re.I)
            val = (mv.group(1) if mv else "") or ""
            out[name] = val
        return out

    async def is_logged(self) -> bool:
        r = await self._client.post("/cs/Data/IsLogged")
        r.raise_for_status()
        txt = (r.text or "").strip().lower()
        if txt in ("1", "true", "ok", "yes"):
            return True
        if txt in ("0", "false", "no", ""):
            return False
        # Fallback: endpoint občas vracia neštandardný text; ak nie je explicitne false, ber ako True.
        return "false" not in txt and "0" != txt

    async def ensure_login(self, email: str, password: str) -> None:
        if self._login_ok:
            return
        em = (email or "").strip()
        pw = password or ""
        if not em or not pw:
            raise ValueError("BMCo: chýba email alebo heslo.")
        hidden = await self._get_login_hidden_fields()
        payload = {**hidden, "Email": em, "Password": pw}
        r = await self._client.post(
            "/cs/Account/LoginB2C",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # login endpoint typicky redirectuje 302 -> /cs/Home/IndexB2C
        if r.status_code >= 400:
            raise RuntimeError(f"BMCo login HTTP {r.status_code}: {r.text!r}")
        if not await self.is_logged():
            raise RuntimeError("BMCo: prihlásenie zlyhalo (IsLogged = false).")
        self._login_ok = True

    async def fetch_product_detail(
        self,
        karta: str,
        *,
        currency: str = "EUR",
        language: str = "cs",
    ) -> dict[str, Any]:
        code = bmkco_norm_code(karta)
        if not code:
            raise ValueError("BMCo: prázdny kód produktu (karta).")
        r = await self._client.post(
            "/cs/Data/GetZboziDetail",
            data={"karta": code, "zvolenaMena": currency, "jazyk": language},
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        r.raise_for_status()
        txt = (r.text or "").strip()
        if not txt:
            raise RuntimeError("BMCo: GetZboziDetail vrátil prázdnu odpoveď.")
        try:
            blob = json.loads(txt)
        except json.JSONDecodeError as exc:
            raise RuntimeError("BMCo: GetZboziDetail nevrátil validný JSON.") from exc
        if isinstance(blob, list) and blob and isinstance(blob[0], dict):
            return blob[0]
        if isinstance(blob, dict):
            return blob
        raise RuntimeError(f"BMCo: neočakávaná odpoveď GetZboziDetail: {type(blob).__name__}")

    async def add_to_cart(self, karta: str, quantity: int) -> None:
        code = bmkco_norm_code(karta)
        if not code:
            raise ValueError("BMCo: prázdny kód produktu (karta).")
        q = int(quantity)
        if q < 1:
            raise ValueError("BMCo: množstvo musí byť aspoň 1.")
        r = await self._client.post(
            "/cs/Data/DoKosiku",
            data={"karta": code, "mnozstvi": str(q)},
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        r.raise_for_status()

    @staticmethod
    def parse_supplier_data(detail: dict[str, Any]) -> dict[str, Any]:
        raw_price = _pick_first(detail, ("zakaznikCena", "akcniCena", "zakladniCena"))
        price_eur = _bmkco_parse_decimal(str(raw_price or ""))
        stock_raw = _pick_first(detail, ("mnozstviSkladem", "mnozstviSklademText", "sklad"))
        stock = _bmkco_parse_int(stock_raw)
        # Počet kusov v balení: BMCo mení názvy polí podľa produktu.
        pack_q = _bmkco_pick_pack_quantity(detail)
        label = str(
            _pick_first(detail, ("kratkyNazev", "nazev", "Krizovy_Odkaz", "karta")) or ""
        ).strip()
        karta = str(_pick_first(detail, ("karta", "Karta", "cisloKarty")) or "").strip()
        pv = {
            "label": label or (karta if karta else "BMCo"),
            "pack_quantity": pack_q if pack_q and pack_q > 0 else None,
            "price_eur": price_eur,
            "raw_price": str(raw_price).strip() if raw_price not in (None, "") else None,
            "stock": stock,
            "raw_stock": str(stock_raw).strip() if stock_raw not in (None, "") else None,
            "bmkco_karta": karta or None,
        }
        return {
            "price_eur": price_eur,
            "stock": stock,
            "pack_quantity": pv.get("pack_quantity"),
            "raw_price": pv.get("raw_price"),
            "raw_stock": pv.get("raw_stock"),
            "raw_pack_quantity": str(pv.get("pack_quantity") or ""),
            "product_title": label or None,
            "packaging_variants": [pv],
            "logged_in": True,
            "bmkco_via_http": True,
        }
