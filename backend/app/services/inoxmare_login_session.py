"""Interaktívne obnovenie Inoxmare relácie (CAPTCHA login cez server → uloženie cookies)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.services.inoxmare_http_client import (
    _inoxmare_base_headers,
    _inoxmare_navigation_headers,
    inoxmare_cookie_header_from_client,
    inoxmare_fetch_login_captcha_image,
    inoxmare_login_post,
    inoxmare_login_requires_captcha,
    inoxmare_origin,
    inoxmare_store_path,
    parse_inoxmare_form_key,
    InoxmareHttpClient,
)
from app.services.scraper_service import (
    load_scraper_config,
    sanitize_cart_config_json_text,
)

_SESSION_TTL_SEC = 30 * 60


@dataclass
class _PendingLogin:
    client: httpx.AsyncClient
    origin: str
    store: str
    username: str
    password: str
    login_html: str
    form_key: str
    captcha_required: bool
    created_at: float = field(default_factory=time.monotonic)


_PENDING: dict[str, _PendingLogin] = {}


def _purge_expired() -> None:
    now = time.monotonic()
    dead = [k for k, v in _PENDING.items() if now - v.created_at > _SESSION_TTL_SEC]
    for k in dead:
        entry = _PENDING.pop(k, None)
        if entry is not None:
            try:
                import asyncio

                asyncio.get_running_loop().create_task(entry.client.aclose())
            except Exception:
                pass


def merge_inoxmare_cookie_into_cart_config(
    raw_cfg: str | None, cookie_header: str
) -> str:
    text = sanitize_cart_config_json_text((raw_cfg or "").strip())
    parsed: dict[str, Any] = {}
    if text:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                parsed = loaded
        except json.JSONDecodeError:
            pass
    parsed["inoxmare_session_cookie_header"] = (cookie_header or "").strip()
    return json.dumps(parsed, ensure_ascii=False, indent=2)


async def _new_login_client(shop_url: str, store_path: Optional[str]) -> tuple[
    httpx.AsyncClient, str, str
]:
    origin = inoxmare_origin(shop_url)
    store = inoxmare_store_path(shop_url, store_path)
    try:
        client = httpx.AsyncClient(
            base_url=origin,
            headers=_inoxmare_base_headers(),
            follow_redirects=True,
            timeout=httpx.Timeout(35.0, connect=8.0),
            http2=True,
        )
    except ImportError:
        client = httpx.AsyncClient(
            base_url=origin,
            headers=_inoxmare_base_headers(),
            follow_redirects=True,
            timeout=httpx.Timeout(35.0, connect=8.0),
        )
    return client, origin, store


async def _warmup_client(client: httpx.AsyncClient, origin: str, store: str) -> None:
    try:
        await client.get(
            f"{store}/",
            headers=_inoxmare_navigation_headers(),
        )
    except httpx.HTTPError:
        pass


async def start_inoxmare_login_session(
    shop_url: str,
    store_path: Optional[str],
    username: str,
    password: str,
) -> dict[str, Any]:
    """Začne login reláciu; ak CAPTCHA nie je potrebná, prihlási hneď."""
    _purge_expired()
    u = (username or "").strip()
    p = (password or "").strip()
    if not u or not p:
        raise ValueError("Chýba prihlasovacie meno alebo heslo u dodávateľa.")

    client, origin, store = await _new_login_client(shop_url, store_path)
    await _warmup_client(client, origin, store)

    login_path = f"{store}/customer/account/login/"
    r = await client.get(
        login_path,
        headers=_inoxmare_navigation_headers(referer=f"{origin}{store}/"),
    )
    r.raise_for_status()
    login_html = r.text or ""
    fk = parse_inoxmare_form_key(login_html)
    if not fk:
        await client.aclose()
        raise RuntimeError("Inoxmare: na prihlasovacej stránke sa nenašiel form_key.")

    captcha_required = inoxmare_login_requires_captcha(login_html)
    token = uuid.uuid4().hex
    _PENDING[token] = _PendingLogin(
        client=client,
        origin=origin,
        store=store,
        username=u,
        password=p,
        login_html=login_html,
        form_key=fk,
        captcha_required=captcha_required,
    )

    login_url = f"{origin}{login_path}"
    out: dict[str, Any] = {
        "session_token": token,
        "login_url": login_url,
        "username": u,
        "captcha_required": captcha_required,
        "captcha_image_base64": None,
        "captcha_mime": "image/png",
        "auto_completed": False,
        "cookie_header": None,
    }

    if captcha_required:
        mime, b64 = await inoxmare_fetch_login_captcha_image(
            client, origin, store, fk
        )
        out["captcha_image_base64"] = b64
        out["captcha_mime"] = mime
        return out

    try:
        await inoxmare_login_post(
            client,
            origin,
            store,
            u,
            p,
            login_html,
            captcha_text=None,
        )
        cookie_header = inoxmare_cookie_header_from_client(client)
        await client.aclose()
        _PENDING.pop(token, None)
        out["auto_completed"] = True
        out["cookie_header"] = cookie_header
        return out
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "captcha" not in msg:
            _PENDING.pop(token, None)
            await client.aclose()
            raise
        entry = _PENDING.get(token)
        if entry is None:
            raise
        entry.captcha_required = True
        out["captcha_required"] = True
        mime, b64 = await inoxmare_fetch_login_captcha_image(
            client, origin, store, fk
        )
        out["captcha_image_base64"] = b64
        out["captcha_mime"] = mime
        return out


async def refresh_inoxmare_login_captcha(session_token: str) -> tuple[str, str]:
    """Nový CAPTCHA obrázok (mime, base64)."""
    _purge_expired()
    entry = _PENDING.get(session_token)
    if entry is None:
        raise RuntimeError("Platnosť relácie vypršala — spusti obnovenie znova.")
    return await inoxmare_fetch_login_captcha_image(
        entry.client, entry.origin, entry.store, entry.form_key
    )


async def complete_inoxmare_login_session(
    session_token: str,
    *,
    captcha_text: str | None,
) -> str:
    _purge_expired()
    entry = _PENDING.pop(session_token, None)
    if entry is None:
        raise RuntimeError("Platnosť relácie vypršala — spusti obnovenie znova.")
    try:
        if entry.captcha_required and not (captcha_text or "").strip():
            raise ValueError("Zadaj kód z obrázka CAPTCHA.")
        await inoxmare_login_post(
            entry.client,
            entry.origin,
            entry.store,
            entry.username,
            entry.password,
            entry.login_html,
            captcha_text=(captcha_text or "").strip() or None,
        )
        return inoxmare_cookie_header_from_client(entry.client)
    finally:
        await entry.client.aclose()


async def verify_inoxmare_cookie_header(
    shop_url: str,
    store_path: Optional[str],
    cookie_header: str,
) -> bool:
    ck = (cookie_header or "").strip()
    if not ck:
        return False
    async with InoxmareHttpClient(
        shop_url,
        store_path,
        manual_cookie_header=ck,
    ) as client:
        try:
            await client._ensure_session_from_manual_cookies()
            return True
        except Exception:
            return False


def inoxmare_login_url_for_supplier(supplier) -> str:
    cfg = load_scraper_config(supplier)
    origin = inoxmare_origin(supplier.shop_url or "")
    store = inoxmare_store_path(supplier.shop_url or "", cfg.inoxmare_store_path)
    login = (cfg.login_url or "").strip()
    if login.startswith("http"):
        return login
    return f"{origin}{store}/customer/account/login/"


async def capture_inoxmare_cookies_via_playwright(supplier, config) -> str:
    """
    Otvorí Chrome s predvyplneným loginom — používateľ dokončí CAPTCHA ručne.
    Funguje len tam, kde beží headed Playwright (nie Render).
    """
    from playwright.async_api import async_playwright

    from app.services.scraper_service import (
        _chromium_launch_kwargs,
        _dismiss_cookies,
        _dismiss_cookiescript_if_present,
        _inoxmare_browser_warmup,
        _new_browser_context,
    )

    login_url = inoxmare_login_url_for_supplier(supplier)
    u = (supplier.username or "").strip()
    p = (supplier.password or "").strip()
    if not u or not p:
        raise ValueError("Chýba prihlasovacie meno alebo heslo.")

    headed_cfg = config.model_copy(update={"headless": False})
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            **_chromium_launch_kwargs(headed_cfg, supplier)
        )
        try:
            context = await _new_browser_context(browser, supplier, 0)
            page = await context.new_page()
            page.set_default_timeout(max(60_000, int(config.navigation_timeout_ms)))
            if config.inoxmare_browser_warmup:
                await _inoxmare_browser_warmup(
                    page,
                    supplier,
                    config,
                    run_label="inox-refresh",
                    run_id="",
                )
            await page.goto(login_url, wait_until="domcontentloaded")
            await _dismiss_cookies(page, config.optional_cookie_dismiss_selector)
            await _dismiss_cookiescript_if_present(
                page,
                run_label="inox-refresh",
                supplier=supplier,
                run_id="",
                config_selector=config.optional_cookie_dismiss_selector,
            )
            user_sel = (config.username_selector or "").strip()
            pass_sel = (config.password_selector or "").strip()
            if user_sel:
                await page.locator(user_sel).first.fill(u)
            if pass_sel:
                await page.locator(pass_sel).first.fill(p)
            # Prihlásenie necháme na používateľa (CAPTCHA) — len čakáme na účet.

            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                url = page.url.lower()
                if "/customer/account" in url and "/login" not in url:
                    break
                await asyncio.sleep(0.8)
            else:
                raise RuntimeError(
                    "Časový limit — prihlás sa v okne prehliadača (CAPTCHA) a skús znova."
                )

            pairs: list[str] = []
            for c in await context.cookies():
                name = (c.get("name") or "").strip()
                value = (c.get("value") or "").strip()
                if name:
                    pairs.append(f"{name}={value}")
            if not pairs:
                raise RuntimeError("Nepodarilo sa načítať cookies z prehliadača.")
            return "; ".join(pairs)
        finally:
            await browser.close()
