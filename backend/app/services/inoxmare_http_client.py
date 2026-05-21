"""
Inoxmare (inoxmare.com): Adobe Commerce / Magento 2 — prihlásenie, quicksearch resolve,
načítanie PDP a POST checkout/cart/add (application/x-www-form-urlencoded).

Pri B2B môže byť potrebné pole custom_price (z ceny na PDP po prihlásení).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

import httpx

# Pravidelne aktualizujeme major verziu Chrome — staré UA (Chrome 120/131) Cloudflare aj
# Magento Captcha modul vyhodnocujú ako podozrivý fingerprint a vyvolajú CAPTCHA challenge.
CHROME_MAJOR = "147"
DEFAULT_UA = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{CHROME_MAJOR}.0.0.0 Safari/537.36"
)
SEC_CH_UA = (
    f'"Google Chrome";v="{CHROME_MAJOR}", '
    f'"Not.A/Brand";v="8", '
    f'"Chromium";v="{CHROME_MAJOR}"'
)


def _inoxmare_base_headers() -> dict[str, str]:
    """Hlavičky 1:1 ako Chrome 147 v reálnom prehliadači — zhoduje sa s HAR z DevTools."""
    return {
        "User-Agent": DEFAULT_UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "sk,cs;q=0.9,en-US;q=0.8,en;q=0.7,bg;q=0.6,pl;q=0.5",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "DNT": "1",
    }


def _inoxmare_navigation_headers(referer: Optional[str] = None) -> dict[str, str]:
    """Hlavná navigácia (PDP, login GET, homepage). Referer je voliteľný."""
    h = {
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
    }
    if referer:
        h["Referer"] = referer
    return h


def _inoxmare_xhr_headers(referer: str) -> dict[str, str]:
    """Section load / page_cache render / cart add — XHR z prihlásenej zóny."""
    return {
        "Accept": "*/*",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
    }


def inoxmare_norm_code(text: str) -> str:
    t = (text or "").strip().upper().replace("\xa0", "")
    t = re.sub(r"\s+", "", t)
    return t


def inoxmare_origin(shop_url: str) -> str:
    raw = (shop_url or "").strip()
    if not raw:
        return "https://www.inoxmare.com"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return raw.rstrip("/")


def inoxmare_parse_cookie_header(header: str) -> dict[str, str]:
    """Hodnota HTTP hlavičky Cookie (ako v DevTools → Sieť → požiadavka → Request Headers)."""
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        lk = k.lower()
        if lk in ("path", "expires", "max-age", "domain", "secure", "httponly", "samesite"):
            continue
        if k:
            out[k] = v.strip()
    return out


def inoxmare_cookie_header_from_client(client: httpx.AsyncClient) -> str:
    """Zo session cookie jar zostaví hodnotu hlavičky Cookie."""
    parts: list[str] = []
    for name, value in client.cookies.items():
        if name:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def inoxmare_login_requires_captcha(html: str) -> bool:
    if re.search(r'<input[^>]+name=["\']captcha', html or "", re.I):
        return True
    if re.search(
        r"captcha\s*[\"']?\s*:\s*\{\s*\"required\"\s*:\s*true",
        html or "",
        re.I,
    ):
        return True
    return False


async def inoxmare_fetch_login_captcha_image(
    client: httpx.AsyncClient,
    origin: str,
    store: str,
    form_key: str,
) -> tuple[str, str]:
    """Magento captcha/refresh → (mime, base64 bez prefixu data:…)."""
    post_url = f"{store}/captcha/refresh/"
    r = await client.post(
        post_url,
        data={"form_key": form_key, "formId": "user_login"},
        headers={
            **_inoxmare_xhr_headers(referer=f"{origin}{store}/customer/account/login/"),
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    r.raise_for_status()
    img_src = ""
    try:
        payload = r.json()
        if isinstance(payload, dict):
            img_src = str(payload.get("imgSrc") or "").strip()
    except json.JSONDecodeError:
        pass
    if not img_src:
        raise RuntimeError("Inoxmare: nepodarilo sa načítať obrázok CAPTCHA.")
    if img_src.startswith("data:"):
        m = re.match(r"data:([^;]+);base64,(.+)", img_src, re.I | re.S)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    if img_src.startswith("/"):
        r_img = await client.get(
            img_src,
            headers=_inoxmare_navigation_headers(
                referer=f"{origin}{store}/customer/account/login/"
            ),
        )
        r_img.raise_for_status()
        ctype = (r_img.headers.get("content-type") or "image/png").split(";")[0]
        import base64

        return ctype, base64.b64encode(r_img.content).decode("ascii")
    raise RuntimeError("Inoxmare: neznámy formát CAPTCHA obrázka.")


async def inoxmare_login_post(
    client: httpx.AsyncClient,
    origin: str,
    store: str,
    username: str,
    password: str,
    login_html: str,
    *,
    captcha_text: str | None = None,
) -> None:
    """POST loginPost; pri úspechu zostane relácia v client cookies."""
    fk = parse_inoxmare_form_key(login_html)
    if not fk:
        raise RuntimeError("Inoxmare: chýba form_key.")
    data = _inoxmare_login_form_hidden_fields(login_html)
    data["form_key"] = fk
    data["login[username]"] = (username or "").strip()
    data["login[password]"] = (password or "").strip()
    if captcha_text:
        data["captcha[user_login]"] = captcha_text.strip()
    data["send"] = ""
    login_path = f"{store}/customer/account/login/"
    post_path = f"{store}/customer/account/loginPost/"
    r2 = await client.post(
        post_path,
        data=data,
        headers={
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin,
            "Referer": f"{origin}{login_path}",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        },
    )
    r2.raise_for_status()
    low = (r2.text or "").lower()
    if "incorrect captcha" in low or "incorrectcaptcha" in low.replace(" ", ""):
        raise RuntimeError("Nesprávny kód CAPTCHA — skús znova.")
    err_markers = (
        "sign-in was incorrect",
        "account sign-in was incorrect",
        "the account sign-in was incorrect",
        "incorrect username",
        "incorrect password",
    )
    if any(x in low for x in err_markers):
        raise RuntimeError("Nesprávne prihlasovacie údaje.")
    lock_markers = (
        "account is disabled temporarily",
        "please wait and try again later",
        "too many failed login attempts",
    )
    if any(x in low for x in lock_markers):
        raise RuntimeError(
            "Účet je dočasne zablokovaný — počkaj a skús neskôr."
        )
    path_after = (urlparse(str(r2.url)).path or "").lower()
    if path_after.rstrip("/").endswith("/customer/account/login"):
        raise RuntimeError(
            "Prihlásenie zlyhalo — skontroluj údaje a CAPTCHA."
        )
    r_chk = await client.get(
        f"{store}/customer/account/",
        headers=_inoxmare_navigation_headers(referer=f"{origin}{login_path}"),
    )
    if "customer/account/login" in str(r_chk.url).lower():
        raise RuntimeError("Relácia nie je prihlásená — skontroluj údaje.")


def inoxmare_httpx_cookie_host(shop_url: str) -> str:
    return (urlparse(inoxmare_origin(shop_url)).hostname or "www.inoxmare.com").lower()


def inoxmare_playwright_cookie_domain(shop_url: str) -> str:
    host = (urlparse(inoxmare_origin(shop_url)).hostname or "www.inoxmare.com").lower()
    if host.startswith("www."):
        return "." + host[4:]
    if host and not host.startswith("."):
        return "." + host
    return host or ".inoxmare.com"


def inoxmare_store_path(shop_url: str, config_path: Optional[str]) -> str:
    if (config_path or "").strip():
        s = config_path.strip().strip("/")
        return "/" + s if s else "/en"
    raw = (shop_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    p = urlparse(raw)
    parts = [x for x in (p.path or "").split("/") if x]
    if parts and re.match(r"^[a-z]{2}$", parts[0], re.I):
        return "/" + parts[0].lower()
    return "/en"


def inoxmare_cart_url(shop_url: str, store_path: Optional[str] = None) -> str:
    sp = inoxmare_store_path(shop_url, store_path)
    return f"{inoxmare_origin(shop_url)}{sp}/checkout/cart/"


def parse_inoxmare_form_key(html: str) -> Optional[str]:
    for pat in (
        r'name=["\']form_key["\'][^>]*value=["\']([^"\']+)',
        r'value=["\']([^"\']+)["\'][^>]*name=["\']form_key["\']',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1).strip()
    return None


def parse_inoxmare_product_id(html: str) -> Optional[str]:
    for pat in (
        r'name=["\']product["\'][^>]*value=["\'](\d+)',
        r'value=["\'](\d+)["\'][^>]*name=["\']product["\']',
        r'"productId"\s*:\s*(\d+)',
        r'data-product-id=["\'](\d+)',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1).strip()
    return None


def inoxmare_product_code_from_pdp_path(path: str) -> Optional[str]:
    m = re.search(r"[?&]art=([^&]+)", path or "", re.I)
    if not m:
        return None
    return inoxmare_norm_code(unquote(m.group(1).strip()))


def parse_inoxmare_page_cache_render_path(html: str) -> Optional[str]:
    """
    Magento 2: v PDP je v x-magento-init URL, ktorá po načítaní doplní súkromné bloky
    (ceny/sklad v tabuľke). Bez tohto requestu ostáva v HTML len bunka not-login.
    """
    m = re.search(r'"pageCache"\s*:\s*\{\s*"url"\s*:\s*"([^"]+)"', html or "", re.I)
    if not m:
        return None
    raw = m.group(1).replace(r"\/", "/")
    p = urlparse(raw)
    if not p.path:
        return None
    out = p.path
    if p.query:
        out = f"{out}?{p.query}"
    return out


def _inoxmare_login_form_hidden_fields(html: str) -> dict[str, str]:
    fm = re.search(
        r'<form[^>]+action=["\'][^"\']*loginPost[^"\']*["\'][^>]*>([\s\S]*?)</form>',
        html or "",
        re.I,
    )
    if not fm:
        return {}
    fragment = fm.group(1)
    out: dict[str, str] = {}
    for tag_m in re.finditer(
        r"<input\b[^>]*type\s*=\s*[\"']hidden[\"'][^>]*>",
        fragment,
        re.I,
    ):
        tag = tag_m.group(0)
        nm = re.search(r"name\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        if not nm:
            continue
        val_m = re.search(r"value\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        out[nm.group(1)] = val_m.group(1) if val_m else ""
    return out


def _inoxmare_strip_tags(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", t).strip()


def _inoxmare_extract_product_row_html(html: str, product_code: str) -> Optional[str]:
    code = inoxmare_norm_code(product_code)
    if not code:
        return None
    matches = list(
        re.finditer(
            rf'<tr[^>]*\bid\s*=\s*["\']{re.escape(code)}["\'][^>]*>[\s\S]*?</tr>',
            html,
            re.I,
        )
    )
    if not matches:
        return None
    # Po zlúčení s page_cache fragmentom môžu byť dva riadky — bez not-login je ten správny.
    for m in reversed(matches):
        tr = m.group(0)
        if "not-login" not in tr.lower():
            return tr
    return matches[-1].group(0)


def _inoxmare_extract_td_content(row_html: str, class_token: str) -> Optional[str]:
    m = re.search(
        rf'<td[^>]*class\s*=\s*["\'][^"\']*\b{re.escape(class_token)}\b[^"\']*["\'][^>]*>'
        rf"([\s\S]*?)</td>",
        row_html,
        re.I,
    )
    return m.group(1) if m else None


def _inoxmare_row_hidden_numeric(row_html: str, id_suffix: str) -> Optional[float]:
    for pat in (
        rf'<input[^>]*id\s*=\s*["\'][^"\']*{re.escape(id_suffix)}["\'][^>]*value\s*=\s*["\']([^"\']+)["\']',
        rf'<input[^>]*value\s*=\s*["\']([^"\']+)["\'][^>]*id\s*=\s*["\'][^"\']*{re.escape(id_suffix)}["\']',
    ):
        m = re.search(pat, row_html, re.I)
        if not m:
            continue
        raw = (m.group(1) or "").strip().replace(",", ".")
        try:
            v = float(raw)
            if 0 < v < 1_000_000:
                return v
        except ValueError:
            continue
    return None


def _inoxmare_parse_price_fragment(fragment: str) -> tuple[Optional[float], Optional[str]]:
    cleaned = _inoxmare_row_remove_old_price_markup(fragment or "")
    amounts: list[tuple[float, str]] = []
    for m in re.finditer(
        r'data-price-amount\s*=\s*["\']([\d.]+)["\'][^>]{0,240}?'
        r'data-price-type\s*=\s*["\']finalPrice["\']',
        cleaned,
        re.I | re.DOTALL,
    ):
        try:
            v = float(m.group(1))
            if 0 < v < 1_000_000:
                amounts.append((v, m.group(0)[:100]))
        except ValueError:
            continue
    for m in re.finditer(
        r'data-price-type\s*=\s*["\']finalPrice["\'][^>]{0,240}?'
        r'data-price-amount\s*=\s*["\']([\d.]+)["\']',
        cleaned,
        re.I | re.DOTALL,
    ):
        try:
            v = float(m.group(1))
            if 0 < v < 1_000_000:
                amounts.append((v, m.group(0)[:100]))
        except ValueError:
            continue
    if amounts:
        v0, r0 = amounts[0]
        return round(v0, 4), r0
    for m in re.finditer(r"(\d+[.,]\d{1,4})\s*(?:€|&euro;|EUR)", cleaned, re.I):
        t = m.group(1).replace(",", ".")
        try:
            v = float(t)
            if 0 < v < 100_000:
                return round(v, 4), m.group(0).strip()[:80]
        except ValueError:
            continue
    return None, None


def _inoxmare_row_remove_old_price_markup(row_html: str) -> str:
    out = row_html
    for _ in range(24):
        new = re.sub(
            r"<[^>]*class\s*=\s*[\"'][^\"']*old-price[^\"']*[\"'][^>]*>[\s\S]*?</[^>\s]+>",
            "",
            out,
            count=1,
            flags=re.I,
        )
        if new == out:
            break
        out = new
    return out


def _inoxmare_parse_pack_level(descr_html: str, kind: str) -> Optional[int]:
    if kind == "box":
        needle = r"fa-cube(?!s)"
    elif kind == "master":
        needle = r"fa-cubes"
    elif kind == "pallet":
        needle = r"porto-icon-mode-grid"
    else:
        return None
    m = re.search(
        rf'class\s*=\s*["\'][^"\']*{needle}[^"\']*["\'][^>]*qty\s*=\s*["\']?(\d+)',
        descr_html,
        re.I,
    )
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # Napr. <i class="fas fa-cube"></i>200 — číslo je až za </i>.
    m = re.search(
        rf'class\s*=\s*["\'][^"\']*{needle}[^"\']*["\'][^>]*>[\s]*(?:</i>[\s]*)?(\d+)',
        descr_html,
        re.I,
    )
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def parse_inoxmare_row_fields(row_html: str) -> dict[str, Any]:
    """Údaje z <tr id=\"kód\">: popis, balenia (box / MC / paleta), sklad, cena (ak je v HTML)."""
    out: dict[str, Any] = {
        "label": None,
        "pack_quantity": None,
        "master_pack_quantity": None,
        "pallet_pack_quantity": None,
        "price_eur": None,
        "raw_price": None,
        "master_pack_price_eur": None,
        "master_pack_raw_price": None,
        "stock": None,
        "raw_stock": None,
    }
    dm = re.search(
        r'<td[^>]*class\s*=\s*["\']descr["\'][^>]*>([\s\S]*?)</td>',
        row_html,
        re.I,
    )
    descr = dm.group(1) if dm else ""
    if descr:
        head = descr.split("<p")[0] if "<p" in descr else descr
        label = _inoxmare_strip_tags(head).strip()
        if label:
            out["label"] = label
        out["pack_quantity"] = _inoxmare_parse_pack_level(descr, "box")
        out["master_pack_quantity"] = _inoxmare_parse_pack_level(descr, "master")
        out["pallet_pack_quantity"] = _inoxmare_parse_pack_level(descr, "pallet")
        mc_hidden = _inoxmare_row_hidden_numeric(row_html, "-mc-qty")
        if (
            mc_hidden is not None
            and mc_hidden >= 1
            and isinstance(mc_hidden, float)
            and mc_hidden == int(mc_hidden)
        ):
            out["master_pack_quantity"] = int(mc_hidden)
        if out["pack_quantity"] is None:
            box_qty = _inoxmare_row_hidden_numeric(row_html, "-box-qty")
            if (
                box_qty is not None
                and box_qty >= 1
                and isinstance(box_qty, float)
                and box_qty == int(box_qty)
            ):
                out["pack_quantity"] = int(box_qty)

    if re.search(r'class\s*=\s*["\'][^"\']*not-login', row_html, re.I):
        nm = re.search(
            r'<td[^>]*class\s*=\s*["\'][^"\']*not-login[^"\']*["\'][^>]*>([\s\S]*?)</td>',
            row_html,
            re.I,
        )
        msg = _inoxmare_strip_tags(nm.group(1) if nm else "").strip()
        out["raw_stock"] = (msg[:220] if msg else None) or (
            "Sign up or log in to view availability and prices."
        )
        out["stock"] = None
    else:
        tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, re.I)
        texts = [_inoxmare_strip_tags(x).strip() for x in tds]
        for idx, t in enumerate(texts):
            if idx < 3:
                continue
            if not t or "€" in t or "EUR" in t.upper():
                continue
            if re.fullmatch(r"\d{1,12}", t):
                try:
                    out["stock"] = int(t)
                    out["raw_stock"] = t
                except ValueError:
                    pass
                break
            if len(t) < 40 and re.fullmatch(r"\d{2,12}", t):
                try:
                    out["stock"] = int(t)
                    out["raw_stock"] = t
                except ValueError:
                    pass
                break

    box_td = _inoxmare_extract_td_content(row_html, "price-box")
    mc_td = _inoxmare_extract_td_content(row_html, "price-mc")
    box_pe, box_rp = (
        _inoxmare_parse_price_fragment(box_td) if box_td else (None, None)
    )
    mc_pe, mc_rp = (
        _inoxmare_parse_price_fragment(mc_td) if mc_td else (None, None)
    )
    if box_pe is None:
        box_pe = _inoxmare_row_hidden_numeric(row_html, "-custom-price")
        if box_pe is not None:
            box_rp = f"hidden custom-price={box_pe}"
    if mc_pe is None:
        mc_pe = _inoxmare_row_hidden_numeric(row_html, "-custom-price-mc")
        if mc_pe is not None:
            mc_rp = f"hidden custom-price-mc={mc_pe}"

    cleaned = _inoxmare_row_remove_old_price_markup(row_html)
    if box_pe is None or mc_pe is None:
        amounts: list[tuple[float, str]] = []
        for m in re.finditer(
            r'data-price-amount\s*=\s*["\']([\d.]+)["\'][^>]{0,240}?'
            r'data-price-type\s*=\s*["\']finalPrice["\']',
            cleaned,
            re.I | re.DOTALL,
        ):
            try:
                v = float(m.group(1))
                if 0 < v < 1_000_000:
                    amounts.append((v, m.group(0)[:100]))
            except ValueError:
                continue
        for m in re.finditer(
            r'data-price-type\s*=\s*["\']finalPrice["\'][^>]{0,240}?'
            r'data-price-amount\s*=\s*["\']([\d.]+)["\']',
            cleaned,
            re.I | re.DOTALL,
        ):
            try:
                v = float(m.group(1))
                if 0 < v < 1_000_000:
                    amounts.append((v, m.group(0)[:100]))
            except ValueError:
                continue
        if box_pe is None and amounts:
            v0, r0 = amounts[0]
            box_pe, box_rp = round(v0, 4), r0
        if mc_pe is None and len(amounts) >= 2:
            v1, r1 = amounts[1]
            mc_pe, mc_rp = round(v1, 4), r1

    if box_pe is None and mc_pe is None:
        for m in re.finditer(r"(\d+[.,]\d{1,4})\s*(?:€|&euro;|EUR)", cleaned, re.I):
            t = m.group(1).replace(",", ".")
            try:
                v = float(t)
                if 0 < v < 100_000:
                    box_pe = round(v, 4)
                    box_rp = m.group(0).strip()[:80]
                    break
            except ValueError:
                continue

    if box_pe is not None:
        out["price_eur"] = box_pe
        out["raw_price"] = box_rp
    if mc_pe is not None:
        out["master_pack_price_eur"] = mc_pe
        out["master_pack_raw_price"] = mc_rp

    return out


def parse_inoxmare_product_title(html: str) -> Optional[str]:
    """Názov produktu z <h1 class=\"page-title\"> na PDP."""
    for pat in (
        r'<h1[^>]*class\s*=\s*["\'][^"\']*page-title[^"\']*["\'][^>]*>\s*'
        r'(?:<span[^>]*>\s*)?([^<]+)',
        r'data-ui-id\s*=\s*["\']page-title-wrapper["\'][^>]*>\s*([^<]+)',
        r'<h1[^>]*>\s*([^<]{3,240})',
    ):
        m = re.search(pat, html or "", re.I | re.DOTALL)
        if not m:
            continue
        title = _inoxmare_strip_tags(m.group(1)).strip()
        if title and title.lower() not in ("inoxmare", "product"):
            return title
    return None


def _inoxmare_descr_label_from_row(row_html: str) -> Optional[str]:
    dm = re.search(
        r'<td[^>]*class\s*=\s*["\']descr["\'][^>]*>([\s\S]*?)</td>',
        row_html,
        re.I,
    )
    if not dm:
        return None
    descr = dm.group(1)
    head = descr.split("<p")[0] if "<p" in descr else descr
    label = _inoxmare_strip_tags(head).strip()
    return label or None


def parse_inoxmare_price_eur(html: str) -> tuple[Optional[float], Optional[str]]:
    for m in re.finditer(r'["\']price["\']\s*:\s*["\']([\d.]+)', html, re.I):
        try:
            v = float(m.group(1))
            if 0 < v < 1_000_000:
                return round(v, 4), m.group(0)[:80]
        except ValueError:
            continue
    m = re.search(r'data-price-amount\s*=\s*["\']([\d.]+)', html, re.I)
    if m:
        try:
            return round(float(m.group(1)), 4), m.group(0)
        except ValueError:
            pass
    m = re.search(r'class="[^"]*price[^"]*"[^>]*>([^<]*€[^<]*)', html, re.I)
    if m:
        raw = m.group(1)
        t = raw.replace("€", "").replace("EUR", "").strip().replace(",", ".")
        mg = re.search(r"(\d+(?:\.\d+)?)", t)
        if mg:
            try:
                return round(float(mg.group(1)), 4), raw.strip()[:80]
            except ValueError:
                pass
    return None, None


def parse_inoxmare_stock(html: str) -> tuple[Optional[int], Optional[str]]:
    if re.search(r"schema\.org/InStock", html, re.I):
        m = re.search(
            r"availability[^\"]{0,80}stock[^\"]{0,40}(\d+)",
            html,
            re.I,
        )
        if m:
            try:
                q = int(m.group(1))
                return q, f"Sklad {q} ks"
            except ValueError:
                pass
        return 1, "Na sklade"
    m = re.search(r"(?:stock|qty)\s*[:=]\s*(\d+)", html, re.I)
    if m:
        try:
            q = int(m.group(1))
            if q >= 0:
                return q, f"{q} ks"
        except ValueError:
            pass
    m = re.search(r"stock\s+available[^>]*>([^<]+)", html, re.I)
    if m:
        raw = re.sub(r"<[^>]+>", " ", m.group(1))
        raw = re.sub(r"\s+", " ", raw).strip()
        digits = re.sub(r"\D", "", raw)
        if digits:
            try:
                return int(digits), raw[:120]
            except ValueError:
                pass
        return None, raw[:120] if raw else None
    return None, None


def parse_inoxmare_pdp(html: str, product_code: Optional[str] = None) -> dict[str, Any]:
    """
    PDP s tabuľkou variantov: cena/sklad musia ísť z <tr id=\"kód\">, nie z horného
    price-boxu konfigurovateľného produktu (často €1.00 placeholder).
    """
    pid = parse_inoxmare_product_id(html)
    product_title = parse_inoxmare_product_title(html)
    code = inoxmare_norm_code(product_code) if product_code else ""
    row_html = _inoxmare_extract_product_row_html(html, code) if code else None
    if row_html:
        rf = parse_inoxmare_row_fields(row_html)
        row_label = (rf.get("label") or "").strip() or None
        if not row_label and code:
            for m in re.finditer(
                rf'<tr[^>]*\bid\s*=\s*["\']{re.escape(code)}["\'][^>]*>[\s\S]*?</tr>',
                html,
                re.I,
            ):
                alt = _inoxmare_descr_label_from_row(m.group(0))
                if alt:
                    row_label = alt
                    break
        display_label = row_label or product_title
        return {
            "inoxmare_product_id": pid,
            "product_title": product_title or row_label,
            "pdp_label": display_label,
            "pack_quantity": rf.get("pack_quantity"),
            "master_pack_quantity": rf.get("master_pack_quantity"),
            "pallet_pack_quantity": rf.get("pallet_pack_quantity"),
            "price_eur": rf.get("price_eur"),
            "raw_price": rf.get("raw_price"),
            "master_pack_price_eur": rf.get("master_pack_price_eur"),
            "master_pack_raw_price": rf.get("master_pack_raw_price"),
            "stock": rf.get("stock"),
            "raw_stock": rf.get("raw_stock"),
        }
    if code:
        return {
            "inoxmare_product_id": pid,
            "product_title": product_title,
            "pdp_label": product_title,
            "pack_quantity": None,
            "master_pack_quantity": None,
            "pallet_pack_quantity": None,
            "price_eur": None,
            "raw_price": None,
            "stock": None,
            "raw_stock": f"V PDP sa nenašiel riadok pre kód {code}.",
        }
    pe, rp = parse_inoxmare_price_eur(html)
    st, rs = parse_inoxmare_stock(html)
    return {
        "inoxmare_product_id": pid,
        "product_title": product_title,
        "pdp_label": product_title,
        "pack_quantity": None,
        "master_pack_quantity": None,
        "pallet_pack_quantity": None,
        "price_eur": pe,
        "raw_price": rp,
        "stock": st,
        "raw_stock": rs,
    }


class InoxmareHttpClient:
    """Session s cookies: login, resolve kódu, PDP, add to cart."""

    def __init__(
        self,
        shop_url: str,
        store_path_config: Optional[str] = None,
        manual_cookie_header: Optional[str] = None,
    ) -> None:
        self._origin = inoxmare_origin(shop_url)
        self._store = inoxmare_store_path(shop_url, store_path_config)
        self._manual_cookie_header = (manual_cookie_header or "").strip()
        jar = httpx.Cookies()
        if self._manual_cookie_header:
            host = inoxmare_httpx_cookie_host(shop_url)
            for name, value in inoxmare_parse_cookie_header(
                self._manual_cookie_header
            ).items():
                try:
                    jar.set(name, value, domain=host, path="/")
                except Exception:
                    jar.set(name, value, path="/")
        # http2=True priblíži flow Chromu (Cloudflare Bot Management si všíma protokol),
        # ale vyžaduje balík `h2`. Bez neho httpx pri inicializácii hodí ImportError —
        # urobíme tichý fallback na HTTP/1.1.
        try:
            self._client = httpx.AsyncClient(
                base_url=self._origin,
                headers=_inoxmare_base_headers(),
                cookies=jar,
                follow_redirects=True,
                timeout=httpx.Timeout(35.0, connect=8.0),
                http2=True,
            )
        except ImportError:
            self._client = httpx.AsyncClient(
                base_url=self._origin,
                headers=_inoxmare_base_headers(),
                cookies=jar,
                follow_redirects=True,
                timeout=httpx.Timeout(35.0, connect=8.0),
            )
        self._login_ok = False
        self._warmed_up = False

    async def __aenter__(self) -> InoxmareHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    @property
    def store_path(self) -> str:
        return self._store

    async def warmup(self) -> None:
        """
        „Reálny prehliadač“ začína návštevou domovskej stránky — Magento Page Cache si
        nastaví form_key / X-Magento-Vary aj pre anonymného návštevníka a Cloudflare
        bot-score klesne. Bez tohto kroku ide hneď prvý request na /customer/account/
        a CAPTCHA modul si nás okamžite označí.
        """
        if self._warmed_up:
            return
        try:
            await self._client.get(
                f"{self._store}/",
                headers=_inoxmare_navigation_headers(),
            )
            await self._client.get(
                f"{self._store}/customer/section/load/",
                params={
                    "sections": "cart,messages,directory-data",
                    "force_new_section_timestamp": "true",
                },
                headers=_inoxmare_xhr_headers(referer=f"{self._origin}{self._store}/"),
            )
        except httpx.HTTPError:
            pass
        self._warmed_up = True

    async def _ensure_session_from_manual_cookies(self) -> None:
        """Relácia z prehliadača (cart_config inoxmare_session_cookie_header) — obíde CAPTCHA na loginPost."""
        await self.warmup()
        try:
            await self._client.get(
                f"{self._store}/customer/section/load/",
                params={
                    "sections": "customer,cart",
                    "force_new_section_timestamp": "1",
                },
                headers=_inoxmare_xhr_headers(referer=f"{self._origin}{self._store}/"),
            )
        except httpx.HTTPError:
            pass
        r_chk = await self._client.get(
            f"{self._store}/customer/account/",
            headers=_inoxmare_navigation_headers(referer=f"{self._origin}{self._store}/"),
        )
        if "customer/account/login" in str(r_chk.url).lower():
            raise RuntimeError(
                "Inoxmare: inoxmare_session_cookie_header je neplatná alebo vypršala. "
                "Prihlás sa v Chrome na inoxmare.com, v DevTools (F12) otvor ľubovoľnú sieťovú požiadavku "
                "na tú istú doménu a skopíruj celú hodnotu hlavičky „Cookie“ do cart_config_json."
            )
        self._login_ok = True

    async def ensure_login(self, username: str, password: str) -> None:
        if self._login_ok:
            return
        if self._manual_cookie_header:
            await self._ensure_session_from_manual_cookies()
            return
        u = (username or "").strip()
        p = (password or "").strip()
        if not u or not p:
            raise ValueError("Inoxmare: chýba prihlasovacie meno alebo heslo.")
        await self.warmup()
        login_path = f"{self._store}/customer/account/login/"
        r = await self._client.get(
            login_path,
            headers=_inoxmare_navigation_headers(referer=f"{self._origin}{self._store}/"),
        )
        r.raise_for_status()
        login_html = r.text or ""
        if re.search(r'<input[^>]+name=["\']captcha', login_html, re.I) or re.search(
            r"captcha\s*[\"']?\s*:\s*\{\s*\"required\"\s*:\s*true",
            login_html,
            re.I,
        ):
            raise RuntimeError(
                "Inoxmare: prihlasovací formulár vyžaduje CAPTCHA (Magento). "
                "HTTP login nevie obísť obrázkový kód. Použi cart_config_json "
                "inoxmare_session_cookie_header (cookies z Chrome po ručnom prihlásení) "
                "alebo prejdi cez Playwright (headless: false)."
            )
        fk = parse_inoxmare_form_key(login_html)
        if not fk:
            raise RuntimeError("Inoxmare: na prihlasovacej stránke sa nenašiel form_key.")
        data = _inoxmare_login_form_hidden_fields(login_html)
        data["form_key"] = fk
        data["login[username]"] = u
        data["login[password]"] = p
        data["send"] = ""
        post_path = f"{self._store}/customer/account/loginPost/"
        r2 = await self._client.post(
            post_path,
            data=data,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self._origin,
                "Referer": f"{self._origin}{login_path}",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            },
        )
        r2.raise_for_status()
        low = (r2.text or "").lower()
        if "incorrect captcha" in low or "incorrectcaptcha" in low.replace(" ", ""):
            raise RuntimeError(
                "Inoxmare: prihlásenie vyžaduje CAPTCHA (Magento). Bez kódu z obrázka loginPost zlyhá. "
                "Riešenie: po ručnom prihlásení v prehliadači skopíruj hlavičku „Cookie“ (DevTools → Sieť) "
                "do cart_config_json ako inoxmare_session_cookie_header. Alternatíva: vypnúť CAPTCHA "
                "pre Customer Login v Magento alebo výnimka od obchodu."
            )
        lock_markers = (
            "account is disabled temporarily",
            "please wait and try again later",
            "temporarily disabled",
            "too many failed login attempts",
        )
        if any(x in low for x in lock_markers):
            raise RuntimeError(
                "Inoxmare: účet je dočasne zablokovaný po neúspešných pokusoch "
                "(\"Please wait and try again later\"). Počkaj na odblokovanie a potom "
                "použi reláciu z prehliadača (inoxmare_session_cookie_header), aby sa lock neopakoval."
            )
        err_markers = (
            "sign-in was incorrect",
            "account sign-in was incorrect",
            "the account sign-in was incorrect",
            "non è corretto",
            "non corretta",
            "incorrect username",
            "incorrect password",
        )
        if any(x in low for x in err_markers):
            raise RuntimeError("Inoxmare: nesprávne prihlasovacie údaje.")
        path_after = (urlparse(str(r2.url)).path or "").lower()
        if path_after.rstrip("/").endswith("/customer/account/login"):
            raise RuntimeError(
                "Inoxmare: prihlásenie zlyhalo — ostáva prihlasovacia stránka. "
                "Skontroluj meno a heslo. Ak server tlačí CAPTCHA (typicky pri "
                "prihlasovaní z cloudového IP, napr. Render), v admin paneli "
                "v cart_config_json dopíš `inoxmare_session_cookie_header` "
                "s hodnotou hlavičky Cookie z prihláseného Chrome (DevTools → "
                "Network → request → Request Headers → Cookie)."
            )
        if "loginpost" in path_after and "login[password]" in low:
            raise RuntimeError("Inoxmare: prihlásenie zlyhalo — skontroluj údaje.")
        try:
            await self._client.get(
                f"{self._store}/customer/section/load/",
                params={
                    "sections": "customer,cart",
                    "force_new_section_timestamp": "1",
                },
                headers=_inoxmare_xhr_headers(referer=f"{self._origin}{login_path}"),
            )
        except httpx.HTTPError:
            pass
        r_chk = await self._client.get(
            f"{self._store}/customer/account/",
            headers=_inoxmare_navigation_headers(referer=f"{self._origin}{login_path}"),
        )
        if "customer/account/login" in str(r_chk.url).lower():
            raise RuntimeError(
                "Inoxmare: účet sa nepodarilo overiť — skontroluj prihlasovacie údaje alebo obchod (store path)."
            )
        self._login_ok = True

    async def resolve_product_path(self, product_code: str) -> str:
        code = inoxmare_norm_code(product_code)
        if not code:
            raise ValueError("Inoxmare: prázdny kód produktu.")
        await self.warmup()
        q = quote(code, safe="")
        path_qs = f"{self._store}/quicksearch/index/resolve/?item={q}&din=&uni=&iso="
        r = await self._client.get(
            path_qs,
            headers=_inoxmare_navigation_headers(referer=f"{self._origin}{self._store}/"),
        )
        r.raise_for_status()
        final = urlparse(str(r.url))
        path = final.path or ""
        if "customer/account/login" in path.lower():
            raise RuntimeError("Inoxmare: presmerovanie na prihlásenie — neplatná relácia.")
        if not path or path.rstrip("/").endswith("resolve"):
            raise RuntimeError(
                f"Inoxmare: kód {code!r} sa nepodarilo presmerovať na produkt (skontroluj číslo)."
            )
        out = path
        if final.query:
            out = f"{out}?{final.query}"
        return out

    async def fetch_pdp_html(self, product_path: str) -> str:
        pp = product_path if product_path.startswith("/") else "/" + product_path
        await self.warmup()
        r = await self._client.get(
            pp,
            headers=_inoxmare_navigation_headers(referer=f"{self._origin}{self._store}/"),
        )
        r.raise_for_status()
        return r.text

    async def fetch_page_cache_product_block(
        self, pdp_html: str, product_path: str
    ) -> str:
        rel = parse_inoxmare_page_cache_render_path(pdp_html)
        if not rel:
            return ""
        ref = product_path if product_path.startswith("/") else "/" + product_path
        try:
            r = await self._client.get(
                rel,
                headers=_inoxmare_xhr_headers(referer=f"{self._origin}{ref}"),
            )
        except httpx.HTTPError:
            return ""
        if r.status_code != 200:
            return ""
        raw = (r.text or "").strip()
        if not raw or raw == "[]":
            return ""
        if raw.startswith("["):
            try:
                blob = json.loads(raw)
                if (
                    isinstance(blob, list)
                    and blob
                    and isinstance(blob[0], str)
                    and "<" in blob[0]
                ):
                    return blob[0]
            except json.JSONDecodeError:
                pass
        if "<" in raw:
            return raw
        return ""

    async def fetch_pdp_html_hydrated(self, product_path: str) -> str:
        """PDP + Magento page_cache render (ceny/sklad v tabuľke pre prihláseného)."""
        html = await self.fetch_pdp_html(product_path)
        if self._login_ok:
            ref = product_path if product_path.startswith("/") else "/" + product_path
            try:
                await self._client.get(
                    f"{self._store}/customer/section/load/",
                    params={
                        "sections": "customer,cart",
                        "force_new_section_timestamp": "1",
                    },
                    headers=_inoxmare_xhr_headers(referer=f"{self._origin}{ref}"),
                )
            except httpx.HTTPError:
                pass
        blk = await self.fetch_page_cache_product_block(html, product_path)
        if (blk or "").strip():
            return html + "\n<!--inoxmare_page_cache-->\n" + blk
        return html

    async def add_to_cart(
        self,
        *,
        product_id: str,
        quantity: int,
        product_path_for_context: str,
        custom_price: Optional[float] = None,
    ) -> None:
        pid = (product_id or "").strip()
        if not pid:
            raise ValueError("Inoxmare add_to_cart: chýba product_id.")
        if quantity < 1:
            raise ValueError("Inoxmare add_to_cart: množstvo < 1.")
        ctx = (
            product_path_for_context
            if product_path_for_context.startswith("/")
            else "/" + product_path_for_context
        )
        html = await self.fetch_pdp_html_hydrated(ctx)
        fk = parse_inoxmare_form_key(html)
        if not fk:
            raise RuntimeError("Inoxmare: na stránke produktu sa nenašiel form_key.")
        html_pid = parse_inoxmare_product_id(html)
        if html_pid and html_pid != pid:
            raise RuntimeError(
                f"Inoxmare: product_id z UI ({pid}) nesedí s PDP ({html_pid})."
            )
        cp = custom_price
        if cp is None or cp <= 0:
            row_code = inoxmare_product_code_from_pdp_path(ctx)
            meta = parse_inoxmare_pdp(html, product_code=row_code)
            pe = meta.get("price_eur")
            if isinstance(pe, (int, float)) and float(pe) > 0:
                cp = float(pe)
        data: dict[str, str] = {
            "product": pid,
            "form_key": fk,
            "qty": str(int(quantity)),
        }
        if cp is not None and cp > 0:
            data["custom_price"] = f"{round(float(cp), 2):.2f}"
        ref = f"{self._origin}{ctx}"
        r = await self._client.post(
            f"{self._store}/checkout/cart/add/",
            data=data,
            headers={
                **_inoxmare_xhr_headers(referer=ref),
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": self._origin,
            },
        )
        r.raise_for_status()
        txt = (r.text or "").strip()
        if not txt or txt == "[]":
            return
        try:
            blob = json.loads(txt)
        except json.JSONDecodeError:
            if "error" in txt.lower():
                raise RuntimeError(f"Inoxmare košík: {txt[:300]}")
            return
        if isinstance(blob, dict) and blob.get("error"):
            raise RuntimeError(f"Inoxmare košík: {blob.get('error')}")
