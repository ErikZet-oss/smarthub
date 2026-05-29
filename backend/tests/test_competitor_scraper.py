"""Testy pre verejný scraper cien konkurencie."""

from app.services.competitor_scraper_service import (
    _extract_price_from_html,
    _parse_eur_amount,
    competitor_product_url,
    load_competitor_scrape_config,
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


def test_extract_price_secondary_svx_style() -> None:
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
