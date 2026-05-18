"""
Hopefix (hopefix.cz): prihlásenie cez formulár /prihlaseni, čítanie katalógovej HTML
tabuľky a POST /api/add_to_cart (application/x-www-form-urlencoded).

Z HAR: cmd=add_cart, product_nr, product_id, qty, package_type, ajax=true.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

DEFAULT_BASE = "https://www.hopefix.cz"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def hopefix_norm_code(text: str) -> str:
    """Kód z Excel/URL zjednotí (NBSP, zero-width, Unicode kompatibilné tvary)."""
    t = unicodedata.normalize("NFKC", (text or "").strip())
    t = t.upper().replace("\xa0", "")
    t = re.sub(r"[\u200b-\u200f\ufeff\u2060]", "", t)
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


def hopefix_row_likely_no_cart_form(row: dict[str, Any]) -> bool:
    """Hopefix pri OOS / plánovanom naskladnení často nevykreslí expander s ``product_id`` / košíkom."""
    if hopefix_row_is_oos(row):
        return True
    raw = _strip_tags((row.get("raw_stock") or "")).lower()
    if any(
        x in raw
        for x in (
            "nasklad",
            "naskladnění",
            "naskladneni",
            "dočasn",
            "docasn",
            "termín dod",
            "termin dod",
            "nedostup",
        )
    ):
        return True
    return False


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
    t = t.replace("\xa0", " ").strip()
    while re.search(r"(?<=\d)\s+(?=\d)", t):
        t = re.sub(r"(?<=\d)\s+(?=\d)", "", t, count=1)
    t = t.replace(" ", "")
    t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _hopefix_parse_pack_cell(text: str) -> Optional[float]:
    """Počet z bunky Box (0,50 → 0.5); N/A → None — zladené s Playwright scraperom."""
    t = (text or "").replace("\xa0", " ").strip()
    if not t or t.upper() in ("N/A", "-"):
        return None
    while re.search(r"(?<=\d)\s+(?=\d)", t):
        t = re.sub(r"(?<=\d)\s+(?=\d)", "", t, count=1)
    t = t.replace(" ", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _hopefix_header_implies_100_pcs_unit(header_plain: str) -> bool:
    h = re.sub(r"\s+", " ", (header_plain or "").lower())
    if re.search(r"100\s*pcs", h) or "100pcs" in h.replace(" ", ""):
        return True
    if re.search(r"100\s*ks", h) or re.search(r"100\s*kus", h):
        return True
    if re.search(r"\(\s*100\s*", h):
        return True
    return False


def _hopefix_column_headers_for_row(html: str, row_start: int) -> Optional[list[str]]:
    """Z nájdeného <tr> vezme nadradenú tabuľku a texty <th> z prvého thead."""
    if row_start < 0:
        return None
    before = html[:row_start]
    tbl = before.rfind("<table")
    if tbl < 0:
        return None
    chunk = html[tbl:row_start]
    thead_m = re.search(r"<thead[^>]*>(.*?)</thead>", chunk, re.I | re.DOTALL)
    if not thead_m:
        return None
    ths = re.findall(r"<th[^>]*>(.*?)</th>", thead_m.group(1), re.I | re.DOTALL)
    if not ths:
        return None
    return [_strip_tags(t).strip() for t in ths]


def _hopefix_align_row_cells_to_headers(
    texts: list[str],
    headers: list[str],
) -> tuple[list[str], list[str]]:
    """Jedna extra bunka (checkbox) bez hlavičky → posun o 1."""
    if len(texts) == len(headers) + 1:
        return headers, texts[1:]
    if len(texts) == len(headers):
        return headers, texts
    return headers, texts


def _hopefix_pick_column_indices(headers: list[str]) -> dict[str, Any]:
    """Indices: eur (cena / 100 ks), sklad (100 ks), box (100 pcs Box)."""
    eur_candidates: list[tuple[int, int]] = []
    stock_idx: Optional[int] = None
    stock_h100 = False
    box_idx: Optional[int] = None
    box_h100 = False
    label_idx: Optional[int] = None
    skip_stock = re.compile(
        r"nasklad|naskladnění|další|dalsi|restock|dodání|termin|termín",
        re.I,
    )
    for i, raw in enumerate(headers):
        plain = _strip_tags(raw).strip()
        flat = re.sub(r"\s+", " ", plain.lower())
        if not flat:
            continue
        if re.search(r"rozměr|rozmer|dimension", flat):
            label_idx = i
        if "box" in flat and "carton" not in flat and "pallet" not in flat:
            box_idx = i
            box_h100 = _hopefix_header_implies_100_pcs_unit(plain)
        if ("sklad" in flat or "stock" in flat) and not skip_stock.search(flat):
            stock_idx = i
            stock_h100 = _hopefix_header_implies_100_pcs_unit(plain)
        plain_u = plain.upper()
        if "€" in plain or "EUR" in plain_u:
            if any(x in flat for x in ("celkem", "total", "součet", "soucet")):
                continue
            score = 0
            if "100" in flat or "/100" in flat or "pcs" in flat or "ks" in flat:
                score = 2
            elif "kus" in flat:
                score = 1
            eur_candidates.append((i, score))
    eur_idx: Optional[int] = None
    if eur_candidates:
        eur_candidates.sort(key=lambda x: -x[1])
        eur_idx = eur_candidates[0][0]
    return {
        "eur_idx": eur_idx,
        "stock_idx": stock_idx,
        "stock_header_100": stock_h100,
        "box_idx": box_idx,
        "box_header_100": box_h100,
        "label_idx": label_idx,
    }


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


def _hopefix_product_id_from_expander_html(html: str, norm_nr: str) -> Optional[str]:
    """Hopefix dáva ``product_id`` v skrytom riadku expander-row hneď za ``tr id=line-…``."""
    if not norm_nr:
        return None
    esc = re.escape(norm_nr)
    m = re.search(
        r"<input[^>]+name\s*=\s*[\"']product_nr[\"'][^>]+value\s*=\s*[\"']"
        + esc
        + r"[\"'][^>]*>\s*"
        r"<input[^>]+name\s*=\s*[\"']product_id[\"'][^>]+value\s*=\s*[\"'](\d+)[\"']",
        html,
        re.I | re.DOTALL,
    )
    if m:
        return m.group(1)
    m2 = re.search(
        r"<input[^>]+value\s*=\s*[\"']"
        + esc
        + r"[\"'][^>]+name\s*=\s*[\"']product_nr[\"'][^>]*>\s*"
        r"<input[^>]+name\s*=\s*[\"']product_id[\"'][^>]+value\s*=\s*[\"'](\d+)[\"']",
        html,
        re.I | re.DOTALL,
    )
    return m2.group(1) if m2 else None


def hopefix_merge_expander_product_id(html: str, row: dict[str, Any]) -> None:
    """Dopočíta ``hopefix_product_id`` z expander formulára v celom HTML."""
    if (row.get("hopefix_product_id") or "").strip():
        return
    nr = hopefix_norm_code((row.get("product_nr") or "").strip())
    if not nr:
        return
    pid = _hopefix_product_id_from_expander_html(html, nr)
    if pid:
        row["hopefix_product_id"] = pid


def _hopefix_row_dict_from_line_inner(
    line_key: str,
    inner: str,
    column_headers: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Jeden riadok katalógovej tabuľky z vnútra <tr>… (bez obálky <tr>)."""
    tds = re.findall(r"<td[^>]*>(.*?)</td>", inner, re.I | re.DOTALL)
    texts = [_strip_tags(td) for td in tds]
    product_nr = hopefix_norm_code(line_key)
    if not product_nr and len(texts) > 1:
        product_nr = hopefix_norm_code(texts[1])
    label = texts[2] if len(texts) > 2 else None

    price_eur: Optional[float] = None
    raw_price: Optional[str] = None
    pack_quantity: Optional[int] = None
    stock: Optional[int] = None
    raw_stock: Optional[str] = None

    if column_headers and len(column_headers) >= 3:
        h_aln, t_aln = _hopefix_align_row_cells_to_headers(texts, column_headers)
        if len(h_aln) == len(t_aln) and len(t_aln) >= 3:
            idxmap = _hopefix_pick_column_indices(h_aln)
            li = idxmap.get("label_idx")
            if li is not None and 0 <= li < len(t_aln):
                lab = (t_aln[li] or "").strip()
                if lab:
                    label = lab
            ei = idxmap.get("eur_idx")
            if ei is not None and 0 <= ei < len(t_aln):
                pe = _parse_eur_cell(t_aln[ei])
                if pe is not None:
                    price_eur = pe
                    raw_price = (t_aln[ei] or "").strip()[:120]
            si = idxmap.get("stock_idx")
            if si is not None and 0 <= si < len(t_aln):
                cell = (t_aln[si] or "").strip()
                if cell and cell.upper() not in ("N/A", "-"):
                    plain_s = _strip_tags(cell)
                    if hopefix_raw_suggests_oos(plain_s):
                        stock = 0
                        raw_stock = plain_s[:120]
                    else:
                        val = _cz_float(cell)
                        if val is not None and val >= 0:
                            if idxmap.get("stock_header_100"):
                                stock = max(0, int(round(val * 100)))
                            else:
                                stock = max(0, int(round(val)))
                            raw_stock = cell[:120]
            bi = idxmap.get("box_idx")
            if bi is not None and 0 <= bi < len(t_aln):
                cell_b = (t_aln[bi] or "").strip()
                if cell_b and cell_b.upper() not in ("N/A", "-"):
                    pq_f = _hopefix_parse_pack_cell(cell_b)
                    if pq_f is not None and pq_f > 0:
                        if idxmap.get("box_header_100"):
                            pack_quantity = max(1, int(round(pq_f * 100)))
                        else:
                            pq_r = round(pq_f)
                            pack_quantity = max(
                                1,
                                int(pq_r)
                                if abs(pq_f - pq_r) < 0.001
                                else int(round(pq_f)),
                            )

    if price_eur is None:
        for td_html, plain in zip(tds, texts):
            if "prihlaseni" in td_html.lower():
                continue
            pe = _parse_eur_cell(plain)
            if pe is not None:
                price_eur = pe
                raw_price = plain.strip()[:120]
                break

    if pack_quantity is None and len(texts) >= 3:
        pq_f = _cz_float(texts[-3])
        if pq_f is not None and pq_f > 0:
            pack_quantity = (
                int(pq_f) if abs(pq_f - int(pq_f)) < 0.001 else int(round(pq_f))
            )
            if pack_quantity < 1:
                pack_quantity = 1

    if stock is None and raw_stock is None:
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
    return {
        "product_nr": product_nr,
        "hopefix_product_id": hopefix_product_id,
        "label": label,
        "price_eur": price_eur,
        "raw_price": raw_price,
        "pack_quantity": pack_quantity,
        "stock": stock,
        "raw_stock": raw_stock,
    }


def parse_hopefix_rows(html: str) -> list[dict[str, Any]]:
    """Parsuje <tr id=line-…> / id=\"line-…\" — aj variant bez úvodzoviek okolo hodnoty."""
    out: list[dict[str, Any]] = []
    pat = re.compile(
        r'<tr\b[^>]*\bid\s*=\s*(?:"line-([^"]+)"|\'line-([^\']+)\'|line-([^\s>]+))[^>]*>(.*?)</tr>',
        re.I | re.DOTALL,
    )
    for m in pat.finditer(html):
        line_key = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        inner = m.group(4)
        headers = _hopefix_column_headers_for_row(html, m.start())
        out.append(_hopefix_row_dict_from_line_inner(line_key, inner, headers))
    return out


def find_hopefix_row(rows: list[dict[str, Any]], product_code: str) -> Optional[dict[str, Any]]:
    key = hopefix_norm_code(product_code)
    if not key:
        return None
    for r in rows:
        if r.get("product_nr") == key:
            return r
    for r in rows:
        pn = (r.get("product_nr") or "")
        if pn.startswith(key) and len(pn) > len(key):
            suf = pn[len(key) :]
            if re.match(r"^[A-Z0-9]{1,6}$", suf):
                return r
    return None


def _find_hopefix_row_tr_by_registration_td(html: str, key: str) -> Optional[dict[str, Any]]:
    """Nájde <tr> podľa bunky s registračným kódom (ľubovoľný <td>, nie len 2.)."""
    if not key:
        return None
    line_pat = re.compile(
        r'\bid\s*=\s*(?:"line-([^"]+)"|\'line-([^\']+)\'|line-([^\s>]+))',
        re.I,
    )
    for m in re.finditer(r"<tr\b([^>]*)>(.*?)</tr>", html, re.I | re.DOTALL):
        tr_start = m.start()
        open_attrs, inner = m.group(1), m.group(2)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", inner, re.I | re.DOTALL)
        matched = any(hopefix_norm_code(_strip_tags(td)) == key for td in tds)
        if not matched:
            continue
        im = line_pat.search(open_attrs)
        line_key = ""
        if im:
            line_key = (im.group(1) or im.group(2) or im.group(3) or "").strip()
        if not line_key:
            line_key = key
        headers = _hopefix_column_headers_for_row(html, tr_start)
        return _hopefix_row_dict_from_line_inner(line_key, inner, headers)
    return None


def _find_hopefix_row_tr_by_code_occurrence(html: str, key: str) -> Optional[dict[str, Any]]:
    """Posledná záloha: výskyt kódu v bunke (pattern `>KÓD</td`) → obalový <tr>."""
    if not key or len(key) < 4:
        return None
    line_pat = re.compile(
        r'\bid\s*=\s*(?:"line-([^"]+)"|\'line-([^\']+)\'|line-([^\s>]+))',
        re.I,
    )
    needle = re.compile(re.escape(f">{key}</td"), re.I)
    for m in needle.finditer(html):
        pos = m.start()
        tr_start = html.rfind("<tr", 0, pos)
        if tr_start < 0:
            continue
        tr_end = html.find("</tr>", pos)
        if tr_end < 0:
            continue
        tr_end += 5
        frag = html[tr_start:tr_end]
        if frag.lower().count("<td") < 1:
            continue
        open_m = re.match(r"<tr\b([^>]*)>", frag, re.I)
        if not open_m:
            continue
        open_attrs = open_m.group(1)
        inner = frag[open_m.end() : tr_end - 5]
        tds_raw = re.findall(r"<td[^>]*>(.*?)</td>", inner, re.I | re.DOTALL)
        if not any(hopefix_norm_code(_strip_tags(td)) == key for td in tds_raw):
            continue
        im = line_pat.search(open_attrs)
        line_key = key
        if im:
            line_key = (im.group(1) or im.group(2) or im.group(3) or "").strip() or key
        headers = _hopefix_column_headers_for_row(html, tr_start)
        return _hopefix_row_dict_from_line_inner(line_key, inner, headers)
    return None


def find_hopefix_row_in_html(html: str, product_code: str) -> Optional[dict[str, Any]]:
    """Štandardné parsovanie line-* + tolerancia variantov + zálohy (stĺpce / výskyt v HTML)."""
    rows = parse_hopefix_rows(html)
    hit = find_hopefix_row(rows, product_code)
    if hit:
        hopefix_merge_expander_product_id(html, hit)
        return hit
    key = hopefix_norm_code(product_code)
    if not key:
        return None
    hit = _find_hopefix_row_tr_by_registration_td(html, key)
    if hit:
        hopefix_merge_expander_product_id(html, hit)
        return hit
    hit = _find_hopefix_row_tr_by_code_occurrence(html, key)
    if hit:
        hopefix_merge_expander_product_id(html, hit)
    return hit


async def hopefix_fetch_html_anonymous(url_or_path: str) -> str:
    """GET bez cookies — verejný katalóg (inkognito). Hopefix B2B niekedy vôbec nevloží artikel do HTML."""
    raw = (url_or_path or "").strip()
    if not raw:
        raise ValueError("Hopefix: prázdna URL katalógu.")
    if raw.startswith("http://") or raw.startswith("https://"):
        target = raw
    else:
        target = urljoin(f"{DEFAULT_BASE}/", raw.lstrip("/"))
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(28.0, connect=5.0),
        http2=False,
        headers={
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "cs,sk;q=0.9,en;q=0.8",
        },
    ) as ac:
        r = await ac.get(target)
        r.raise_for_status()
        return r.text


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
        # HTTP/1.1: HTTP/2 za niektorými CDN/proxy kombináciami vracal iné telo
        # alebo rozbité paralelné odpovede voči hopefix.cz (B2B katalóg).
        self._client = httpx.AsyncClient(
            base_url=self._origin,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "cs,sk;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(28.0, connect=5.0),
            http2=False,
        )
        self._login_ok = False

    @property
    def login_ok(self) -> bool:
        return self._login_ok

    async def __aenter__(self) -> HopefixHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

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
        final_u = str(r.url).lower()
        body_lo = (r.text or "").lower()
        if "prihlaseni" in final_u and any(
            x in body_lo
            for x in (
                "neplatné přihlašovací",
                "neplatne prihlasovaci",
                "chybně zadan",
                "chybne zadan",
                "špatné heslo",
                "spatne heslo",
                "incorrect password",
                "přihlášení se nezdařilo",
                "prihlaseni se nezdarilo",
            )
        ):
            raise ValueError(
                "Hopefix: prihlásenie zlyhalo — v odpovedi je chyba prihlásenia (skontroluj email a heslo v admine)."
            )
        # Doplnkový GET „úvod“ niekedy dokončí JSESSION / následné katalógové GET-y.
        try:
            home = await self._client.get("/")
            home.raise_for_status()
        except Exception:
            pass
        self._login_ok = True

    async def fetch_cart_snapshot(self) -> dict[str, Any]:
        html = await _hopefix_fetch_kosik_html(self._client)
        return {"kosik_html": html}

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


def hopefix_cart_url(base_url: str = DEFAULT_BASE) -> str:
    return f"{(base_url or DEFAULT_BASE).rstrip('/')}/kosik"


def _hopefix_cart_product_code_from_cell(text: str) -> str:
    plain = _strip_tags(text)
    if not plain:
        return ""
    m = re.match(r"([A-Za-z0-9]+)", plain)
    if not m:
        return ""
    code = hopefix_norm_code(m.group(1))
    if len(code) < 8:
        return ""
    if not re.search(r"[A-Z]", code) or not re.search(r"\d", code):
        return ""
    return code


def hopefix_parse_cart_html(html: str) -> dict[str, Any]:
    """
    Z ``GET /kosik`` — riadky ``table.table_cart`` (variant[id][qty], ceny v EUR).
    """
    page = html or ""
    if not page.strip():
        return {"lines": [], "total_eur": None, "line_count": 0, "empty_cart": True}

    lines: list[dict[str, Any]] = []
    for m in re.finditer(
        r"<tr[^>]*>([\s\S]*?name=[\"']variant\[(\d+)\]\[qty\][\"'][^>]*value=[\"'](\d+)[\"'][\s\S]*?)</tr>",
        page,
        re.I,
    ):
        inner = m.group(1)
        if "td_price_total" in inner or "Celkem bez DPH" in inner:
            continue
        try:
            qty = int(m.group(3))
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            continue
        tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", inner, re.I | re.DOTALL)
        product_nr = ""
        label = ""
        product_idx: Optional[int] = None
        for i, td in enumerate(tds):
            code = _hopefix_cart_product_code_from_cell(td)
            if code and len(code) >= 6:
                product_nr = code
                label = _strip_tags(td) or code
                product_idx = i
                break
        if not product_nr:
            continue
        eur_vals: list[float] = []
        for td in tds[(product_idx or 0) + 1 :]:
            pe = _parse_eur_cell(_strip_tags(td))
            if pe is not None:
                eur_vals.append(pe)
        unit_eur = eur_vals[0] if eur_vals else None
        line_total = eur_vals[1] if len(eur_vals) > 1 else eur_vals[0] if eur_vals else None
        if line_total is None and unit_eur is not None:
            line_total = round(unit_eur * qty, 4)
        lines.append(
            {
                "label": label,
                "quantity": qty,
                "unit_price_eur": unit_eur,
                "line_total_eur": line_total,
                "variant_code": product_nr,
                "hopefix_variant_id": m.group(2),
            }
        )

    total_eur: Optional[float] = None
    tm = re.search(
        r"Celkem bez DPH[\s\S]{0,120}?class=[\"']price_total[\"'][^>]*>([^<]+)",
        page,
        re.I,
    )
    if tm:
        total_eur = _parse_eur_cell(tm.group(1))
    if total_eur is None:
        tm2 = re.search(r'class=["\']price_total["\'][^>]*>([^<]+)', page, re.I)
        if tm2:
            total_eur = _parse_eur_cell(tm2.group(1))
    if total_eur is None and lines:
        total_eur = round(
            sum((ln.get("line_total_eur") or 0.0) for ln in lines),
            4,
        )

    line_count = len(lines)
    empty = line_count <= 0
    if empty and total_eur is None:
        total_eur = 0.0

    return {
        "lines": lines,
        "total_eur": total_eur,
        "line_count": line_count,
        "empty_cart": empty,
    }


async def _hopefix_fetch_kosik_html(client: httpx.AsyncClient) -> str:
    r = await client.get("/kosik")
    r.raise_for_status()
    body = r.text or ""
    if "/prihlaseni" in str(r.url.path).lower() or 'name="cmd" value="log_me_in"' in body:
        raise RuntimeError(
            "Hopefix: košík vyžaduje prihlásenie (skontroluj email/heslo dodávateľa)."
        )
    return body


