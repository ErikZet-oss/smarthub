"""Testy pre verejný scraper cien konkurencie."""

from app.services.competitor_scraper_service import (
    _extract_price_from_html,
    _feva_score_slug,
    _feva_search_query_variants,
    _feva_slug_from_input,
    _parse_eur_amount,
    _wc_store_price_eur,
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


def test_resolve_vkpsteel_preset() -> None:
    cfg = resolve_competitor_scrape_config("https://eshop.vkpsteel.com/", None)
    assert "controller=search" in (cfg.search_via_url_template or "")
    assert (
        competitor_product_url("https://eshop.vkpsteel.com/", "049751010100", cfg)
        == "https://eshop.vkpsteel.com/vyhladavanie?controller=search&s=049751010100"
    )


def test_resolve_bbtechnik_preset() -> None:
    cfg = resolve_competitor_scrape_config("https://www.bbtechnik.sk/", None)
    assert cfg.search_via_url_template == "{shop_url}/vyhladavanie/?string={code}"
    assert (
        competitor_product_url("https://www.bbtechnik.sk/", "000975001000100000", cfg)
        == "https://www.bbtechnik.sk/vyhladavanie/?string=000975001000100000"
    )


def test_resolve_oramat_preset() -> None:
    cfg = resolve_competitor_scrape_config("https://www.oramat.sk/", None)
    assert cfg.search_via_url_template == "{shop_url}/vyhladavanie/?string={code}"
    assert (
        competitor_product_url("https://www.oramat.sk/", "PGO-101000-X63", cfg)
        == "https://www.oramat.sk/vyhladavanie/?string=PGO-101000-X63"
    )


def test_resolve_feva_preset() -> None:
    cfg = resolve_competitor_scrape_config("https://feva.sk/", None)
    assert cfg.search_via_url_template == "{shop_url}/?s={code}"
    assert (
        competitor_product_url("https://feva.sk/", "DIN 931 M08 x 30", cfg)
        == "https://feva.sk/?s=DIN%20931%20M08%20x%2030"
    )


def test_competitor_product_url_direct_feva_url() -> None:
    cfg = resolve_competitor_scrape_config("https://feva.sk/", None)
    url = "https://feva.sk/product/din-975-zavitova-tyc-m10-x-1000-2"
    assert competitor_product_url("https://feva.sk/", url, cfg) == url


def test_feva_wc_store_price_minor_unit() -> None:
    assert _wc_store_price_eur({"price": "5284", "currency_minor_unit": 5}) == 0.05284


def test_feva_slug_from_product_url() -> None:
    slug = _feva_slug_from_input(
        "https://feva.sk/metricke-skrutky/din-931-skrutka-metricka-so-sesthrannou-hlavou-polzavit-m8-x-30/"
    )
    assert slug == "din-931-skrutka-metricka-so-sesthrannou-hlavou-polzavit-m8-x-30"


def test_feva_score_prefers_x30_over_x130() -> None:
    q = "DIN 931 M08 x 30"
    s30 = _feva_score_slug(
        "din-931-skrutka-metricka-so-sesthrannou-hlavou-polzavit-m8-x-30",
        q,
    )
    s130 = _feva_score_slug(
        "din-931-skrutka-metricka-so-sesthrannou-hlavou-polzavit-m8-x-130",
        q,
    )
    assert s30 > s130


def test_feva_search_query_variants_normalizes_en_dash() -> None:
    variants = _feva_search_query_variants("DIN 975 Závitová tyč – M10 x 1000")
    assert "DIN 975 Závitová tyč M10 x 1000" in variants
    assert "DIN 975 M10 x 1000" in variants


def test_resolve_oramat_price_regex() -> None:
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
