"""
Valenta ZT e-shop (shop.valentazt.cz): legacy Arebo flow cez HTML formuláre.

HAR signály:
- POST /login.php (areboua, arebopwd, arebosb)
- GET /order_edit.php?...arebooesp=<code>&arebosb=HLEDAT
- Add-to-cart formulár: hidden areboshpid + pole areboshpc.
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

DEFAULT_BASE = "https://shop.valentazt.cz"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_SESSION_JS_RE = re.compile(r'var\s+areboSessionId\s*=\s*"([^"]+)"', re.I)
_SESSION_INPUT_RE = re.compile(
    r'<input[^>]+id=["\']arebosiddeid["\'][^>]*value=["\']([^"\']+)["\']',
    re.I | re.DOTALL,
)
_PRODUCT_FORM_RE = re.compile(
    r'<form[^>]+action=["\']([^"\']*arebooeaid=1[^"\']*)["\'][^>]*>(.*?)</form>',
    re.I | re.DOTALL,
)
_HIDDEN_PID_RE = re.compile(
    r'name=["\']areboshpid["\'][^>]*value=["\']([^"\']+)["\']', re.I | re.DOTALL
)
_PRICE_RE = re.compile(r"(\d+(?:[.,]\d{2,4})?)\s*(?:€|EUR)\b", re.I)


def valenta_base_url(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return DEFAULT_BASE
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def valenta_cart_url(shop_url: str) -> str:
    return f"{valenta_base_url(shop_url)}/order_edit.php"


def valenta_norm_code(text: str) -> str:
    return _WS_RE.sub("", (text or "").upper().replace("\xa0", ""))


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def _parse_float_local(text: str) -> Optional[float]:
    t = (text or "").strip().replace("\xa0", " ").replace(" ", "").replace(",", ".")
    m = re.search(r"(-?\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return round(float(m.group(1)), 4)
    except ValueError:
        return None


def _extract_session_id(html: str, current_url: str) -> str | None:
    for rx in (_SESSION_JS_RE, _SESSION_INPUT_RE):
        m = rx.search(html or "")
        if m:
            sid = (m.group(1) or "").strip()
            if sid:
                return sid
    q = parse_qs(urlparse(current_url or "").query)
    sid = (q.get("arebosnid") or [""])[0].strip()
    return sid or None


def parse_valenta_product_page(html: str) -> dict[str, object]:
    """
    Heuristika z HTML: nájde prvý formulár pre add-to-cart + cenu/stock.
    Ak cena/sklad nie sú jasné, nechá ich ako None.
    """
    txt = html or ""
    m = _PRODUCT_FORM_RE.search(txt)
    if not m:
        return {}
    action = (m.group(1) or "").replace("&amp;", "&").strip()
    body = m.group(2) or ""
    pid_m = _HIDDEN_PID_RE.search(body)
    pid = (pid_m.group(1) if pid_m else "").strip()
    if not action or not pid:
        return {}

    block = txt[max(0, m.start() - 2500) : min(len(txt), m.end() + 2500)]
    clean = _strip_tags(block)
    price_val: Optional[float] = None
    raw_price: Optional[str] = None
    pm = _PRICE_RE.search(clean)
    if pm:
        raw_price = pm.group(0).strip()
        price_val = _parse_float_local(pm.group(1))

    stock_val: Optional[int] = None
    raw_stock: Optional[str] = None
    stock_match = re.search(
        r"(?:sklad(?:em)?|dostupn(?:ost|é|e)?)[^0-9]{0,20}(\d{1,6})",
        clean,
        re.I,
    )
    if stock_match:
        try:
            stock_val = int(stock_match.group(1))
            raw_stock = stock_match.group(0).strip()
        except ValueError:
            stock_val = None
            raw_stock = None

    return {
        "form_action": action,
        "product_id": pid,
        "price_eur": price_val,
        "raw_price": raw_price,
        "stock": stock_val,
        "raw_stock": raw_stock,
    }


class ValentaHttpClient:
    def __init__(self, shop_url: str) -> None:
        self._base = valenta_base_url(shop_url)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(40.0),
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "cs,sk;q=0.9,en;q=0.8",
            },
        )
        self._logged_in = False
        self._session_id: Optional[str] = None

    async def __aenter__(self) -> "ValentaHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    async def ensure_login(self, username: str, password: str) -> None:
        if self._logged_in and self._session_id:
            return
        user = (username or "").strip()
        pwd = (password or "").strip()
        if not user or not pwd:
            raise ValueError("Valenta: chýba meno alebo heslo.")

        # Warmup session.
        await self._client.get(f"{self._base}/")
        r = await self._client.post(
            f"{self._base}/login.php",
            data={
                "areboua": user,
                "arebopwd": pwd,
                "arebosb": "Přihlásit",
            },
            headers={
                "Origin": self._base,
                "Referer": f"{self._base}/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        r.raise_for_status()
        sid = _extract_session_id(r.text or "", str(r.url))
        if not sid:
            # Fallback: open order page and parse session id from HTML/URL.
            r2 = await self._client.get(f"{self._base}/order_edit.php")
            r2.raise_for_status()
            sid = _extract_session_id(r2.text or "", str(r2.url))
        if not sid:
            raise RuntimeError("Valenta: po prihlásení sa nenašlo arebosnid (session).")
        self._session_id = sid
        self._logged_in = True

    async def fetch_product_data(self, product_code: str) -> dict[str, object]:
        code = (product_code or "").strip()
        if not code:
            raise ValueError("Valenta: prázdny kód produktu.")
        if not self._logged_in or not self._session_id:
            raise RuntimeError("Valenta: najprv volaj ensure_login().")
        params = {
            "arebosnid": self._session_id,
            "cwcdt": str(int(time.time() * 1000)),
            "arebooedt": "11",
            "arebowsf": "1",
            "arebooesp": code,
            "arebosb": "HLEDAT",
        }
        r = await self._client.get(f"{self._base}/order_edit.php", params=params)
        r.raise_for_status()
        parsed = parse_valenta_product_page(r.text or "")
        if not parsed:
            raise RuntimeError(
                f"Valenta: produkt {code!r} sa nepodarilo nájsť (formulár add-to-cart chýba)."
            )
        form_action = str(parsed.get("form_action") or "")
        parsed["form_action_abs"] = urljoin(str(r.url), form_action)
        parsed["session_id"] = self._session_id
        return parsed

    async def add_to_cart(self, *, product_id: str, form_action_abs: str, quantity: int) -> None:
        pid = (product_id or "").strip()
        action = (form_action_abs or "").strip()
        if not pid or not action:
            raise ValueError("Valenta add_to_cart: chýba product_id alebo form_action.")
        q = int(quantity)
        if q < 1:
            raise ValueError("Valenta add_to_cart: množstvo musí byť aspoň 1.")
        r = await self._client.post(
            action,
            data={
                "areboshpid": pid,
                "areboshpc": str(q),
                "arebosb": "Založit novou objednávku",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()

