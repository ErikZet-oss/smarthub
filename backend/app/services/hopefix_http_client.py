"""
Hopefix (hopefix.cz): prihlásenie cez formulár /prihlaseni, čítanie katalógovej HTML
tabuľky a POST /api/add_to_cart (application/x-www-form-urlencoded).

Z HAR: cmd=add_cart, product_nr, product_id, qty, package_type, ajax=true.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

DEFAULT_BASE = "https://www.hopefix.cz"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def hopefix_norm_code(text: str) -> str:
    t = (text or "").strip().upper().replace("\xa0", "")
    t = re.sub(r"\s+", "", t)
    return t


_HOPEFIX_OOS_MARKERS = (
    "není skladem",
    "neni skladem",
    "nie je skladom",
    "vyprodáno",
    "vyprodano",
    "není na sklad",
    "neni na sklad",
    "nedostupné",
    "nedostupne",
    "momentálně nedostupné",
    "momentálne nedostupné",
    "dočasně nedostupné",
    "docasne nedostupne",
    "ne skladem",
)


def hopefix_raw_suggests_oos(text: str) -> bool:
    """Text bunky skladu / dostupnosti (čeština)."""
    s = _strip_tags(text).lower()
    return any(m in s for m in _HOPEFIX_OOS_MARKERS)


def hopefix_row_is_oos(row: dict[str, Any]) -> bool:
    st = row.get("stock")
    if isinstance(st, int) and st <= 0:
        return True
    raw = (row.get("raw_stock") or "").strip().lower()
    if not raw:
        return False
    return any(m in raw for m in _HOPEFIX_OOS_MARKERS)


def _strip_tags(html: str) -> str:
    t = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style.*?>.*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _cz_float(text: str) -> Optional[float]:
    t = _strip_tags(text)
    if not t or t.upper() in ("N/A", "-"):
        return None
    t = t.replace(" ", "").replace("\xa0", "")
    t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _parse_eur_cell(text: str) -> Optional[float]:
    t = _strip_tags(text)
    if "€" not in t and "eur" not in t.lower():
        return None
    t = (
        t.replace("\xa0", " ")
        .replace(" ", "")
        .replace("EUR", "")
        .replace("€", "")
        .strip()
    )
    t = t.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_product_id(row_inner: str) -> Optional[str]:
    patterns = (
        r'name\s*=\s*["\']product_id["\'][^>]*value\s*=\s*["\'](\d+)',
        r'value\s*=\s*["\'](\d+)["\'][^>]*name\s*=\s*["\']product_id["\']',
        r'data-product[_-]?id\s*=\s*["\'](\d+)',
        r'data-product-id\s*=\s*["\'](\d+)',
        r'\bproduct_id\s*=\s*(\d+)',
        r'product_id["\']?\s*:\s*(\d+)',
    )
    for pat in patterns:
        m = re.search(pat, row_inner, re.I)
        if m:
            return m.group(1)
    return None


def parse_hopefix_rows(html: str) -> list[dict[str, Any]]:
    """Parsuje <tr id="line-…"> z #rows — funguje na verejnej aj prihlásenej tabuľke."""
    out: list[dict[str, Any]] = []
    for m in re.finditer(
        r'<tr\b[^>]*\bid\s*=\s*["\']line-([^"\']+)["\'][^>]*>(.*?)</tr>',
        html,
        re.I | re.DOTALL,
    ):
        line_key = m.group(1).strip()
        inner = m.group(2)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", inner, re.I | re.DOTALL)
        texts = [_strip_tags(td) for td in tds]
        product_nr = hopefix_norm_code(line_key)
        if not product_nr and len(texts) > 1:
            product_nr = hopefix_norm_code(texts[1])
        label = texts[2] if len(texts) > 2 else None

        price_eur: Optional[float] = None
        raw_price: Optional[str] = None
        for td_html, plain in zip(tds, texts):
            if "prihlaseni" in td_html.lower():
                continue
            pe = _parse_eur_cell(plain)
            if pe is not None:
                price_eur = pe
                raw_price = plain.strip()[:120]
                break

        pack_quantity: Optional[int] = None
        if len(texts) >= 3:
            pq_f = _cz_float(texts[-3])
            if pq_f is not None and pq_f > 0:
                pack_quantity = int(pq_f) if abs(pq_f - int(pq_f)) < 0.001 else int(round(pq_f))
                if pack_quantity < 1:
                    pack_quantity = 1

        stock: Optional[int] = None
        raw_stock: Optional[str] = None
        if texts:
            tail = texts[-2:]
            for t in reversed(tail):
                if not t or t.upper() in ("N/A", "-"):
                    continue
                plain_tail = _strip_tags(t)
                if hopefix_raw_suggests_oos(plain_tail):
                    stock = 0
                    raw_stock = plain_tail[:120]
                    break
                if re.search(r"\d", t):
                    raw_stock = t[:120]
                    digits = re.sub(r"[^\d]", "", t)
                    if digits:
                        try:
                            stock = int(digits)
                        except ValueError:
                            pass
                    break
            if stock is None and raw_stock is None:
                for t in reversed(texts):
                    if not t or not str(t).strip():
                        continue
                    plain = _strip_tags(t)
                    if hopefix_raw_suggests_oos(plain):
                        stock = 0
                        raw_stock = plain[:120]
                        break

        hopefix_product_id = _extract_product_id(inner)

        out.append(
            {
                "product_nr": product_nr,
                "hopefix_product_id": hopefix_product_id,
                "label": label,
                "price_eur": price_eur,
                "raw_price": raw_price,
                "pack_quantity": pack_quantity,
                "stock": stock,
                "raw_stock": raw_stock,
            }
        )
    return out


def find_hopefix_row(rows: list[dict[str, Any]], product_code: str) -> Optional[dict[str, Any]]:
    key = hopefix_norm_code(product_code)
    if not key:
        return None
    for r in rows:
        if r.get("product_nr") == key:
            return r
    return None


def build_hopefix_catalog_url(template: str, product_code: str) -> str:
    tmpl = (template or "").strip()
    if not tmpl:
        raise ValueError("Prázdna šablóna URL katalógu Hopefix.")
    code = (product_code or "").strip()
    if "{code}" in tmpl:
        enc = quote(code, safe=".-_~")
        return tmpl.replace("{code}", enc)
    return tmpl


class HopefixHttpClient:
    """Jedna async session: login, GET stránky, POST add_to_cart."""

    def __init__(self, base_url: str = DEFAULT_BASE) -> None:
        self._base = (base_url or DEFAULT_BASE).rstrip("/")
        parsed = urlparse(self._base)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else self._base
        self._origin = origin
        # Kratšie read ako 60 s — katalóg má byť do pár s; skorší fail pri výpadku.
        self._client = httpx.AsyncClient(
            base_url=self._origin,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "cs,sk;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(28.0, connect=5.0),
        )
        self._login_ok = False

    async def __aenter__(self) -> HopefixHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    async def ensure_login(self, email: str, password: str) -> None:
        if self._login_ok:
            return
        em = (email or "").strip()
        pw = (password or "").strip()
        if not em or not pw:
            raise ValueError("Hopefix: chýba email alebo heslo dodávateľa.")
        r = await self._client.post(
            "/prihlaseni",
            data={
                "cmd": "log_me_in",
                "email": em,
                "password": pw,
            },
        )
        r.raise_for_status()
        self._login_ok = True

    def _abs_url(self, url_or_path: str) -> str:
        u = (url_or_path or "").strip()
        if u.startswith("http://") or u.startswith("https://"):
            return u
        if not u.startswith("/"):
            u = "/" + u
        return f"{self._origin}{u}"

    async def get_text(self, url_or_path: str) -> str:
        abs_url = self._abs_url(url_or_path)
        r = await self._client.get(abs_url)
        r.raise_for_status()
        return r.text

    async def add_to_cart(
        self,
        *,
        product_nr: str,
        product_id: str,
        quantity: int,
        package_type: str = "box",
        referer_path: str = "/",
    ) -> None:
        nr = (product_nr or "").strip()
        pid = (product_id or "").strip()
        if not nr or not pid:
            raise ValueError("Hopefix add_to_cart: chýba product_nr alebo product_id.")
        if quantity < 1:
            raise ValueError("Hopefix add_to_cart: množstvo < 1.")
        pkg = (package_type or "box").strip() or "box"
        ref = referer_path if referer_path.startswith("/") else "/"
        headers = {"Referer": f"{self._origin}{ref}"}
        data = {
            "cmd": "add_cart",
            "product_nr": nr,
            "product_id": pid,
            "qty": str(int(quantity)),
            "package_type": pkg,
            "ajax": "true",
        }
        r = await self._client.post("/api/add_to_cart", data=data, headers=headers)
        r.raise_for_status()
        text = (r.text or "").strip()
        if not text:
            return
        try:
            blob = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(blob, dict):
            err = blob.get("error") or blob.get("err")
            if err and blob.get("ok") is False:
                raise RuntimeError(f"Hopefix API: {err}")
