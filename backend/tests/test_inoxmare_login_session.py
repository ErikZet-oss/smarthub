import httpx

from app.services.inoxmare_http_client import (
    inoxmare_cookie_header_from_client,
    inoxmare_login_requires_captcha,
)


def test_inoxmare_login_requires_captcha_detects_input():
    html = '<form><input name="captcha[user_login]" /></form>'
    assert inoxmare_login_requires_captcha(html) is True


def test_inoxmare_cookie_header_from_client():
    client = httpx.AsyncClient()
    client.cookies.set("PHPSESSID", "abc", domain="www.inoxmare.com", path="/")
    client.cookies.set("store", "en", domain="www.inoxmare.com", path="/")
    header = inoxmare_cookie_header_from_client(client)
    assert "PHPSESSID=abc" in header
    assert "store=en" in header
