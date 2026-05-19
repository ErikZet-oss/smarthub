import json

from app.services.scraper_service import (
    _parse_cart_config_text,
    sanitize_cart_config_json_text,
)


def test_sanitize_multiline_inoxmare_cookie_header():
    raw = """{
  "login_url": "https://example.com/login",
  "inoxmare_session_cookie_header": "store=en;
PHPSESSID=abc123;
form_key=xyz"
}"""
    cleaned = sanitize_cart_config_json_text(raw)
    parsed = json.loads(cleaned)
    cookie = parsed["inoxmare_session_cookie_header"]
    assert "store=en;" in cookie
    assert "PHPSESSID=abc123;" in cookie
    assert "\n" not in cookie


def test_parse_cart_config_collapses_cookie_newlines():
    raw = """{
  "login_url": "https://example.com/login",
  "username_selector": "#user",
  "password_selector": "#pass",
  "login_button_selector": "#btn",
  "inoxmare_session_cookie_header": "a=1
b=2"
}"""
    cfg = _parse_cart_config_text(raw)
    assert cfg.inoxmare_session_cookie_header == "a=1 b=2"
