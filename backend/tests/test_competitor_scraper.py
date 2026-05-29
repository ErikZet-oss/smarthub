"""Testy pre verejný scraper cien konkurencie."""

from app.services.competitor_scraper_service import (
    _extract_price_from_html,
    _parse_eur_amount,
    competitor_product_url,
    load_competitor_scrape_config,
    resolve_competitor_scrape_config,
)


def test_parse_eur_amount_sk_format() -> None:
    assert _parse_eur_amount("12,34 €") == 12.34
    assert _parse_eur_amount("1 234,50") == 1234.5


def test_extract_price_itemprop() -> None:
    html = '<span itemprop="price" content="17.05">17,05 €</span>'
    cfg = load_competitor_scrape_config(None)
    price, raw = _extract_price_from_html(html, cfg)
    assert price == 17.05
    assert raw == "17.05"


def test_competitor_product_url_template() -> None:
    cfg = load_competitor_scrape_config(
        '{"product_url_template": "https://shop.test/p/{code}"}'
    )
    assert (
        competitor_product_url("https://shop.test", "ABC-1", cfg)
        == "https://shop.test/p/ABC-1"
    )


def test_competitor_product_url_shop_url_placeholder() -> None:
    cfg = load_competitor_scrape_config(
        '{"search_via_url_template": "{shop_url}/vyhladavanie/?search_query={code}"}'
    )
    assert (
        competitor_product_url("https://www.svx.sk/", "1975110", cfg)
        == "https://www.svx.sk/vyhladavanie/?search_query=1975110"
    )


def test_competitor_product_url_adds_https() -> None:
    cfg = load_competitor_scrape_config(
        '{"search_via_url_template": "{shop_url}/search?q={code}"}'
    )
    assert (
        competitor_product_url("www.svx.sk", "ABC", cfg)
        == "https://www.svx.sk/search?q=ABC"
    )


def test_resolve_svx_replaces_broken_search_template() -> None:
    cfg = resolve_competitor_scrape_config(
        "https://www.svx.sk/",
        '{"search_via_url_template": "{shop_url}/search?q={code}"}',
    )
    assert cfg.search_via_url_template == "https://www.svx.sk/vyhladavanie/?search_query={code}"
    assert cfg.follow_product_link_regex
    assert cfg.price_selector_regex
    assert (
        competitor_product_url("https://www.svx.sk/", "1975110", cfg)
        == "https://www.svx.sk/vyhladavanie/?search_query=1975110"
    )


def test_resolve_oramat_ignores_example_sk_template() -> None:
    bad = (
        '{"search_via_url_template": '
        '"https://www.example.sk/vyhladavanie/?search_query={code}"}'
    )
    cfg = resolve_competitor_scrape_config("https://www.oramat.sk/", bad)
    assert cfg.search_via_url_template == "{shop_url}/vyhladavanie/?string={code}"
    assert (
        competitor_product_url("https://www.oramat.sk/", "PGO-101000-X63", cfg)
        == "https://www.oramat.sk/vyhladavanie/?string=PGO-101000-X63"
    )


def test_resolve_oramat_preset() -> None:
    cfg = resolve_competitor_scrape_config("https://www.oramat.sk/", None)
    assert cfg.search_via_url_template == "{shop_url}/vyhladavanie/?string={code}"
    assert (
        competitor_product_url("https://www.oramat.sk/", "PGO-101000-X63", cfg)
        == "https://www.oramat.sk/vyhladavanie/?string=PGO-101000-X63"
    )
    html = (
        '<div data-config-product-price-secondary class="text-p-small">'
        " 1,32 € <span>bez DPH</span></div>"
    )
    cfg = load_competitor_scrape_config(
        '{"price_selector_regex": '
        '"data-config-product-price-secondary[^>]*>\\\\s*([0-9,.]+)\\\\s*€"}'
    )
    price, raw = _extract_price_from_html(html, cfg)
    assert price == 1.32
    assert raw == "1,32"
