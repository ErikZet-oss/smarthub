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
from typing import Any, Optional
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
# Valenta často len Kč — raw_price stačí na „živú“ cenu v UI (bez EUR prepočtu).
_PRICE_CZK_RE = re.compile(
    r"(\d+(?:[\s\u00a0\u202f]\d{3})*(?:[.,]\d{2})?)\s*(?:Kč|CZK)\b",
    re.I,
)
_HEADER_PRICE_JS_RE = re.compile(
    r'var\s+areboHeaderShopOrderPrice\s*=\s*"([^"]*)"',
    re.I,
)
_ITEMS_COUNT_JS_RE = re.compile(
    r"var\s+JS_ActiveShopOrderItemsCount\s*=\s*(\d+)",
    re.I,
)
_ACTIVE_ORDER_JS_RE = re.compile(
    r"var\s+areboActiveShopOrderId\s*=\s*(\d+)",
    re.I,
)
_CHANGE_QTY_RE = re.compile(
    r'id="arebocsoiddeidpdci(\d+)"[^>]*value="(\d+)"[^>]*'
    r'class="[^"]*ASClsInputCountChangeShopOrderItemDetails',
    re.I,
)
_CHANGE_QTY_ROW_RE = re.compile(
    r"<tr[^>]*>[\s\S]*?ASClsInputCountChangeShopOrderItemDetails[\s\S]*?</tr>",
    re.I,
)


def _valenta_title_after_article_plain(cu: str, article_code: str) -> str | None:
    """Text hneď za kódom artikla v plain texte (tabuľka objednávky)."""
    code = (article_code or "").strip()
    if len(code) < 1 or not (cu or "").strip():
        return None
    needle = code.upper()
    u = cu.upper()
    pos = u.find(needle)
    if pos < 0:
        return None
    tail = cu[pos + len(needle) :].strip(" \t\r\n:;,.-|—–")
    if len(tail) < 4:
        return None
    one = re.split(r"\s{2,}|\n", tail, maxsplit=1)[0].strip()
    if len(one) < 4 or len(one) > 400:
        return None
    return one


def _extract_valenta_product_title(
    html: str,
    article_code: str,
    *,
    plain_near_form: str,
    plain_full_page: str,
) -> str | None:
    """
    Názov z PDP / tabuľky objednávky (Arebo HTML — heuristika).
    """
    t = html or ""
    for rx in (
        re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.DOTALL),
        re.compile(r"<h2[^>]*>(.*?)</h2>", re.I | re.DOTALL),
    ):
        m = rx.search(t)
        if m:
            inner = _strip_tags(m.group(1))
            if 3 <= len(inner) <= 500:
                return inner.strip()
    tm = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.DOTALL)
    if tm:
        inner = _strip_tags(tm.group(1))
        for sep in (" | ", " – ", " - ", "|"):
            if sep in inner:
                inner = inner.split(sep)[0].strip()
        if 3 <= len(inner) <= 500:
            return inner
    for sample in (plain_near_form, plain_full_page):
        hit = _valenta_title_after_article_plain(sample, article_code)
        if hit:
            return hit
    return None


def _parse_int_cs_digits(text: str) -> Optional[int]:
    t = re.sub(r"[\s\u00a0\u202f]", "", (text or "").strip())
    if not t.isdigit():
        return None
    try:
        return int(t)
    except ValueError:
        return None


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


def parse_valenta_product_page(html: str, *, article_code: str = "") -> dict[str, object]:
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
    clean_page = _strip_tags(txt)
    price_val: Optional[float] = None
    raw_price: Optional[str] = None
    pm = _PRICE_RE.search(clean)
    if pm:
        raw_price = pm.group(0).strip()
        price_val = _parse_float_local(pm.group(1))
    if raw_price is None:
        ck = _PRICE_CZK_RE.search(clean) or _PRICE_CZK_RE.search(clean_page)
        if ck:
            raw_price = ck.group(0).strip()

    stock_val: Optional[int] = None
    raw_stock: Optional[str] = None

    def _apply_stock_from_text(sample: str) -> bool:
        nonlocal stock_val, raw_stock
        stock_match = re.search(
            r"(?:sklad(?:em)?|dostupn(?:ost|é|e)?)[^0-9]{0,20}(\d{1,7})",
            sample,
            re.I,
        )
        if stock_match:
            try:
                stock_val = int(stock_match.group(1))
                raw_stock = stock_match.group(0).strip()
                return True
            except ValueError:
                stock_val = None
                raw_stock = None

        # Najprv „1 500“, až potom jednoduché číslo (inak by \d{1,7} zobralo len „1“).
        digit_group = r"(\d{1,3}(?:[\s\u00a0\u202f]\d{3})+|\d{1,7})"
        for pat in (
            rf"(?:více|vice|víc)\s+(?:než|nez)\s+{digit_group}\s*(?:ks|kus|kusů|kusy)?",
            rf"(?:více|vice|víc)\s+jak\s+{digit_group}\s*(?:ks|kus|kusů|kusy)?",
            rf"\bnad\s+{digit_group}\s*(?:ks|kus|kusů|kusy)?",
            rf"\bpřes\s+{digit_group}\s*(?:ks|kus|kusů|kusy)?",
        ):
            m2 = re.search(pat, sample, re.I)
            if m2:
                n = _parse_int_cs_digits(m2.group(1))
                if n is not None:
                    stock_val = n
                    raw_stock = m2.group(0).strip()
                    return True
        return False

    if not _apply_stock_from_text(clean):
        _apply_stock_from_text(clean_page)

    product_title = _extract_valenta_product_title(
        txt,
        article_code,
        plain_near_form=clean,
        plain_full_page=clean_page,
    )

    return {
        "form_action": action,
        "product_id": pid,
        "price_eur": price_val,
        "raw_price": raw_price,
        "stock": stock_val,
        "raw_stock": raw_stock,
        "product_title": product_title,
    }


def _valenta_product_block(html: str, shop_product_id: str) -> str:
    pid = (shop_product_id or "").strip()
    if not pid:
        return ""
    for anchor in (f"arebooedeidpatof{pid}", f"arebomainproductimg{pid}"):
        m = re.search(rf"{re.escape(anchor)}[\s\S]{{0,14000}}", html, re.I)
        if m:
            return m.group(0)
    pos = html.find(f"arebocsoiddeidpdci{pid}")
    if pos < 0:
        pos = html.find(f"ChangeNumber{pid}")
    if pos >= 0:
        before = html[max(0, pos - 16000) : pos]
        tables = list(
            re.finditer(r"<table[^>]*class=\"[^\"]*ASClsTblShopProductDetails", before, re.I)
        )
        if tables:
            start = tables[-1].start()
            return before[start:] + html[pos : pos + 400]
    return ""


def _valenta_info_value(block: str, label: str) -> str:
    if not block:
        return ""
    lab = re.escape(label)
    m = re.search(
        rf'class="CWClsTDInfoLabel"[^>]*>\s*{lab}\s*:?\s*</td>\s*'
        rf'<td[^>]*class="CWClsTDInfoValue"[^>]*>([\s\S]*?)</td>',
        block,
        re.I,
    )
    if not m:
        return ""
    return _strip_tags(m.group(1))


def _valenta_line_from_block(
    block: str,
    *,
    shop_product_id: str,
    quantity: int,
) -> dict[str, Any]:
    code = _valenta_info_value(block, "Kód položky") or _valenta_info_value(
        block, "Kod polozky"
    )
    label = (
        _valenta_info_value(block, "Jméno produktu")
        or _valenta_info_value(block, "Jmeno produktu")
    )
    if not label:
        m = re.search(r'<th[^>]*colspan="2"[^>]*>([^<]+)</th>', block, re.I)
        if m:
            label = _strip_tags(m.group(1))
    price_raw = _valenta_info_value(block, "Cena bez DPH")
    unit_eur = None
    line_total = None
    if price_raw:
        pm = _PRICE_RE.search(price_raw)
        if pm:
            unit_eur = _parse_float_local(pm.group(1))
        if unit_eur is None:
            pm2 = _PRICE_CZK_RE.search(price_raw)
            if pm2:
                unit_eur = _parse_float_local(pm2.group(1))
    qn = max(1, int(quantity))
    if unit_eur is not None:
        line_total = round(unit_eur * qn, 4)
    if not code:
        code = shop_product_id
    return {
        "label": label or code or f"Valenta #{shop_product_id}",
        "quantity": qn,
        "unit_price_eur": unit_eur,
        "line_total_eur": line_total,
        "variant_code": code or None,
    }


def _valenta_parse_line_from_row(row_html: str) -> dict[str, Any] | None:
    m = re.search(
        r'class="[^"]*ASClsInputCountChangeShopOrderItemDetails[^"]*ChangeNumber(\d+)"',
        row_html,
        re.I,
    )
    if not m:
        m = re.search(
            r'id="arebocsoiddeidpdci(\d+)"[^>]*value="(\d+)"',
            row_html,
            re.I,
        )
        if not m:
            return None
        pid, qty_s = m.group(1), m.group(2)
    else:
        pid = m.group(1)
        qm = re.search(r'value="(\d+)"', row_html)
        qty_s = qm.group(1) if qm else "1"
    try:
        qn = int(qty_s)
    except (TypeError, ValueError):
        qn = 1
    if qn < 1:
        return None
    tds = [_strip_tags(x) for x in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, re.I)]
    code = ""
    label = ""
    price_raw = ""
    for td in tds:
        if not td or td in ("ks", "Změnit počet", "Změnit pocet"):
            continue
        if re.fullmatch(r"\d+(?:[.,]\d+)?", td.replace(" ", "")):
            continue
        if not code and re.fullmatch(r"[0-9A-Z][0-9A-Z\-\./]{2,30}", td, re.I):
            code = td
            continue
        if not label and len(td) > 8 and not re.search(r"^\d+[.,]\d", td):
            label = td
            continue
        if not price_raw and (_PRICE_RE.search(td) or _PRICE_CZK_RE.search(td)):
            price_raw = td
    unit_eur = None
    if price_raw:
        pm = _PRICE_RE.search(price_raw)
        if pm:
            unit_eur = _parse_float_local(pm.group(1))
    line_total = round(unit_eur * qn, 4) if unit_eur is not None else None
    return {
        "label": label or code or f"Valenta #{pid}",
        "quantity": qn,
        "unit_price_eur": unit_eur,
        "line_total_eur": line_total,
        "variant_code": code or None,
    }


def valenta_parse_cart_html(
    order_edit_html: str,
    *,
    orders_html: str = "",
) -> dict[str, Any]:
    """Z HTML order_edit / orders (Arebo) — súčet, počet a riadky aktívnej objednávky."""
    html_parts = [(order_edit_html or ""), (orders_html or "")]
    merged = "\n".join(html_parts)

    total_eur: Optional[float] = None
    for part in html_parts:
        m = _HEADER_PRICE_JS_RE.search(part)
        if m:
            total_eur = _parse_float_local(m.group(1))
            if total_eur is not None:
                break
    if total_eur is None:
        m = re.search(
            r'id="arebophasopwvid"[^>]*>([^<]*)<',
            merged,
            re.I,
        )
        if m:
            total_eur = _parse_float_local(m.group(1))

    line_count = 0
    for part in html_parts:
        m = _ITEMS_COUNT_JS_RE.search(part)
        if m:
            line_count = int(m.group(1))
            break

    lines: list[dict[str, Any]] = []
    seen_pids: set[str] = set()
    for part in html_parts:
        for pid, qty_s in _CHANGE_QTY_RE.findall(part):
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            try:
                qn = int(qty_s)
            except (TypeError, ValueError):
                qn = 1
            if qn < 1:
                continue
            block = _valenta_product_block(part, pid)
            if block:
                lines.append(
                    _valenta_line_from_block(
                        block, shop_product_id=pid, quantity=qn
                    )
                )
            else:
                row_m = re.search(
                    rf'<tr[^>]*>[\s\S]*?arebocsoiddeidpdci{re.escape(pid)}[\s\S]*?</tr>',
                    part,
                    re.I,
                )
                if row_m:
                    ln = _valenta_parse_line_from_row(row_m.group(0))
                    if ln:
                        lines.append(ln)

    if not lines and orders_html:
        for row in _CHANGE_QTY_ROW_RE.findall(orders_html):
            ln = _valenta_parse_line_from_row(row)
            if ln:
                key = f"{ln.get('variant_code')}|{ln.get('label')}"
                if key not in {f"{x.get('variant_code')}|{x.get('label')}" for x in lines}:
                    lines.append(ln)

    if line_count <= 0:
        line_count = len(lines)
    if total_eur is None and lines:
        total_eur = round(
            sum((ln.get("line_total_eur") or 0.0) for ln in lines),
            4,
        )
        if not lines:
            total_eur = None

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": line_count,
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

    def _base_params(self) -> dict[str, str]:
        if not self._session_id:
            raise RuntimeError("Valenta: chýba session id (arebosnid).")
        return {
            "arebosnid": self._session_id,
            "cwcdt": str(int(time.time() * 1000)),
            "arebowsf": "1",
        }

    async def fetch_order_edit_page(self) -> str:
        """Úvodná stránka veľkoobchodnej objednávky (bez vyhľadávania produktu)."""
        if not self._logged_in or not self._session_id:
            raise RuntimeError("Valenta: najprv volaj ensure_login().")
        params = {**self._base_params(), "arebooedt": "11"}
        r = await self._client.get(
            f"{self._base}/order_edit.php",
            params=params,
            headers={"Referer": f"{self._base}/order_edit.php"},
        )
        r.raise_for_status()
        return r.text or ""

    async def fetch_orders_page(self) -> str:
        """Stránka Košík / zoznam položiek objednávky."""
        if not self._logged_in or not self._session_id:
            raise RuntimeError("Valenta: najprv volaj ensure_login().")
        order_html = await self.fetch_order_edit_page()
        active_oid = ""
        m = _ACTIVE_ORDER_JS_RE.search(order_html)
        if m:
            active_oid = m.group(1)
        params = {
            **self._base_params(),
            "arebosdoousoid": "1",
            "areboasmii": "23",
            "arebosocid": "14",
        }
        if active_oid:
            params["arebosocv"] = active_oid
        r = await self._client.get(
            f"{self._base}/orders.php",
            params=params,
            headers={"Referer": f"{self._base}/order_edit.php"},
        )
        r.raise_for_status()
        return r.text or ""

    async def fetch_cart_snapshot(self) -> dict[str, str]:
        order_html = await self.fetch_order_edit_page()
        orders_html = ""
        try:
            orders_html = await self.fetch_orders_page()
        except Exception:
            orders_html = ""
        return {"order_edit_html": order_html, "orders_html": orders_html}

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
        parsed = parse_valenta_product_page(r.text or "", article_code=code)
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

