from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_HALFMANN_BASE = "https://shop.halfmann-schrauben.de"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def halfmann_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return DEFAULT_HALFMANN_BASE
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def halfmann_norm_artid(text: str) -> str:
    return re.sub(r"\D+", "", (text or "").strip())


def _parse_decimal(text: Any) -> Optional[float]:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None
    t = str(text).strip()
    if not t:
        return None
    t = t.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    m = re.search(r"(-?\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_int(text: Any) -> Optional[int]:
    x = _parse_decimal(text)
    if x is None:
        return None
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


# PDP / API HTML: <div class="dinart_txt">DIN 933 8.8   M 12x60</div>
_DINART_TXT_INNER_RE = re.compile(
    r"<[^>]+\bclass\s*=\s*[\"'][^\"']*\bdinart_txt\b[^\"']*[\"'][^>]*>(.*?)</[a-zA-Z][\w:.-]*\s*>",
    re.I | re.DOTALL,
)


def _text_from_dinart_txt_html(html: str) -> str:
    if not html or "dinart_txt" not in html:
        return ""
    m = _DINART_TXT_INNER_RE.search(html)
    if not m:
        return ""
    inner = re.sub(r"<[^>]+>", " ", m.group(1))
    return re.sub(r"\s+", " ", inner).strip()


def _product_title_from_findpreis_row(row: dict[str, Any]) -> str:
    """Textový názov artiklu z /lbpserver/findpreis → erg (plain polia alebo HTML s .dinart_txt)."""
    for v in row.values():
        if v is None:
            continue
        t = _text_from_dinart_txt_html(str(v))
        if t:
            return t
    for key in (
        "artbez",
        "artikelbez",
        "artbezeichnung",
        "bezeichnung",
        "bez",
        "bez1",
        "artikelbezeichnung",
        "langtext",
        "kurztext",
    ):
        v = row.get(key)
        if v is None:
            continue
        raw = str(v).replace("\r", " ").strip()
        if not raw or "dinart_txt" in raw.lower():
            continue
        return raw
    return ""


class HalfmannHttpClient:
    """
    Halfmann B2B endpoints observed in HAR:
    - POST /welcome/do_login (x-www-form-urlencoded)
    - POST /session/get_data
    - POST /lbpserver/findpreis (artid, menge)
    - POST /lbpserver/verfbest (artid, menge)
    - POST /warenkorb/update_korb (aktion=u, artid, menge)
    """

    def __init__(self, base_url: str) -> None:
        self._base = halfmann_base_url(base_url)
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept-Language": "de,de-DE;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(45.0),
        )
        self._login_ok = False

    async def __aenter__(self) -> HalfmannHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def ensure_login(self, username: str, password: str) -> None:
        if self._login_ok:
            return
        user = (username or "").strip()
        pwd = password or ""
        if not user or not pwd:
            raise ValueError("Halfmann: chýba meno alebo heslo.")
        r = await self._client.post(
            "/welcome/do_login",
            data={"als_gast": "0", "uname": user, "psw": pwd},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        r.raise_for_status()
        ses = await self._client.post("/session/get_data")
        ses.raise_for_status()
        blob = ses.json()
        login_name = str(((blob or {}).get("optionen") or {}).get("login_name") or "").strip()
        benutzer = str((blob or {}).get("benutzer") or "").strip()
        if not benutzer and not login_name:
            raise RuntimeError("Halfmann: prihlásenie zlyhalo (session/get_data bez používateľa).")
        self._login_ok = True

    async def find_price_per_100(self, artid: str) -> dict[str, Any]:
        code = halfmann_norm_artid(artid)
        if not code:
            raise ValueError("Halfmann: prázdne artid.")
        r = await self._client.post(
            "/lbpserver/findpreis",
            data={"artid": code, "menge": "100"},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        r.raise_for_status()
        blob = r.json()
        return blob.get("erg") if isinstance(blob, dict) else {}

    async def find_stock(self, artid: str) -> dict[str, Any]:
        code = halfmann_norm_artid(artid)
        if not code:
            raise ValueError("Halfmann: prázdne artid.")
        r = await self._client.post(
            "/lbpserver/verfbest",
            data={"artid": code, "menge": "1"},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        r.raise_for_status()
        blob = r.json()
        return blob if isinstance(blob, dict) else {}

    async def add_to_cart(self, artid: str, quantity: int) -> None:
        code = halfmann_norm_artid(artid)
        q = int(quantity)
        if not code:
            raise ValueError("Halfmann: prázdne artid.")
        if q < 1:
            raise ValueError("Halfmann: množstvo musí byť aspoň 1.")
        r = await self._client.post(
            "/warenkorb/update_korb",
            data={"aktion": "u", "artid": code, "menge": str(q)},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        r.raise_for_status()
        blob = r.json() if r.text else {}
        if isinstance(blob, dict) and int(blob.get("success") or 0) != 1:
            raise RuntimeError(f"Halfmann košík zlyhal: {blob!r}")

    @staticmethod
    def parse_supplier_data(*, artid: str, price_row: dict[str, Any], stock_row: dict[str, Any]) -> dict[str, Any]:
        code = halfmann_norm_artid(artid)
        price_num = _parse_decimal(price_row.get("preis"))
        stock_num = _parse_int(stock_row.get("verfbest"))
        raw_price = None
        if price_num is not None:
            raw_price = f"{price_num:.2f} €"
        raw_stock = None
        if stock_row.get("verfbest") not in (None, ""):
            raw_stock = str(stock_row.get("verfbest")).strip()
        ptitle = _product_title_from_findpreis_row(price_row)
        if not ptitle and stock_row:
            ptitle = _product_title_from_findpreis_row(stock_row)
        pv_label = ptitle if ptitle else f"Halfmann artid {code}"
        pv = {
            "label": pv_label,
            "pack_quantity": 100,
            "price_eur": price_num,
            "raw_price": raw_price,
            "stock": stock_num,
            "raw_stock": raw_stock,
            "halfmann_artid": code,
        }
        out: dict[str, Any] = {
            "price_eur": price_num,
            "stock": stock_num,
            "pack_quantity": 100,
            "raw_price": raw_price,
            "raw_stock": raw_stock,
            "raw_pack_quantity": "100",
            "packaging_variants": [pv],
            "logged_in": True,
            "halfmann_via_http": True,
            "price_includes_vat": False,
            "currency_code": "eur",
            "currency_symbol": "€",
            "price_unit": "100",
        }
        if ptitle:
            out["product_title"] = ptitle
        return out
