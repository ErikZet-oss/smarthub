"""Testy pre Fabory JSON API (/sk/product/price, /sk/product/stock)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.fabory_http_client import (
    FaboryHttpClient,
    _fabory_pack_quantity_from_pdp_html,
    _fabory_price_for_pack,
    _fabory_product_title_from_pdp_html,
)


# Snapshot z HAR-u zákazníka (zhodný formát ako vracia produkčný server).
PRICE_RESPONSE = {
    "01210120060": {
        "currencyIso": "EUR",
        "unitGrossPrice": 23.600,
        "formattedUnitGrossPrice": "23,60\u00a0\u20ac",
        "unitNetPrice": 11.800,
        "formattedUnitNetPrice": "11,80\u00a0\u20ac",
        "unitQuantity": 100,
        "unitVatPrice": 14.16,
        "formattedUnitVatPrice": "14,16\u00a0\u20ac",
    }
}
STOCK_RESPONSE = {
    "01210120060": {
        "stockLevelMessage": "Na sklade",
        "stockLevelStatus": "INSTOCK",
        "stockQuantity": 0,
        "expectedDeliveryDate": "2026-05-17",
        "expectedDeliveryDateFormatted": "ne 17/05",
    }
}


def _build_mock_client(price_payload: Any, stock_payload: Any) -> FaboryHttpClient:
    """Postaví ``FaboryHttpClient``, ktorý nepôjde von do siete — všetko cez ``httpx.MockTransport``."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/product/price"):
            return httpx.Response(200, json=price_payload)
        if path.endswith("/product/stock"):
            return httpx.Response(200, json=stock_payload)
        if path.endswith("/search/"):
            return httpx.Response(
                302,
                headers={"Location": "/sk/skrutka/p/01210120060"},
            )
        if "/p/" in path:
            return httpx.Response(
                200,
                text=(
                    '<html><body><h1>Válcová hlava IMB 12.9 M10×240</h1>'
                    '<input type="number" class="form-control alp-add-to-cart js-alp-add-to-cart" '
                    'value="100" data-add-to-cart-quantity></body></html>'
                ),
            )
        return httpx.Response(404, text="not found")

    client = FaboryHttpClient("https://www.fabory.com/sk")
    client._client = httpx.AsyncClient(
        base_url=client._prefix,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True  # login obišli sme mockom
    return client


@pytest.mark.asyncio
async def test_fetch_product_price_and_stock_happy_path() -> None:
    client = _build_mock_client(PRICE_RESPONSE, STOCK_RESPONSE)
    try:
        data = await client.fetch_product_price_and_stock("01210120060")
    finally:
        await client.aclose()

    assert data["price_eur"] == 11.8
    assert data["pack_price_eur"] == 11.8
    assert data["price_unit"] == "per_100_ks"
    assert data["raw_price"] == PRICE_RESPONSE["01210120060"]["formattedUnitNetPrice"]
    assert data["pack_quantity"] == 100
    # stockQuantity = 0 a stockLevelStatus = INSTOCK ⇒ mapujeme na "dostupné" (1).
    assert data["stock"] == 1
    assert data["raw_stock"] == "Na sklade"
    assert data["fabory_via_http"] is True
    assert data["currency"] == "EUR"
    assert data["product_title"] == "Válcová hlava IMB 12.9 M10×240"
    assert isinstance(data["packaging_variants"], list) and data["packaging_variants"]


def test_fabory_product_title_from_pdp_html() -> None:
    html = '<h1 itemprop="name">Skrutka DIN 933 M8</h1>'
    assert _fabory_product_title_from_pdp_html(html) == "Skrutka DIN 933 M8"
    assert _fabory_product_title_from_pdp_html("07000.100.240") is None


def test_fabory_pack_quantity_from_pdp_html() -> None:
    html_10 = (
        '<input type="number" class="form-control text-center alp-add-to-cart js-alp-add-to-cart" '
        'value="10" data-add-to-cart-quantity brokenBox="true">'
        '<div>Ambalat la 10</div>'
    )
    html_100 = (
        '<input type="number" class="alp-add-to-cart js-alp-add-to-cart" value="100">'
    )
    assert _fabory_pack_quantity_from_pdp_html(html_10) == 10
    assert _fabory_pack_quantity_from_pdp_html(html_100) == 100
    assert _fabory_pack_quantity_from_pdp_html("<html></html>") is None


def test_fabory_price_for_pack_scales_when_unit_quantity_differs() -> None:
    assert _fabory_price_for_pack(170.5, unit_quantity=100, pack_quantity=100) == 170.5
    assert _fabory_price_for_pack(170.5, unit_quantity=100, pack_quantity=10) == 17.05
    assert _fabory_price_for_pack(2.35, unit_quantity=100, pack_quantity=500) == 11.75


@pytest.mark.asyncio
async def test_fetch_product_price_and_stock_pack_500_shows_unit_price_per_100() -> None:
    price = {
        "01210040050": {
            "currencyIso": "EUR",
            "unitNetPrice": 2.35,
            "formattedUnitNetPrice": "2,35\u00a0€",
            "unitQuantity": 100,
        }
    }
    stock = {
        "01210040050": {
            "stockLevelMessage": "Na sklade",
            "stockLevelStatus": "INSTOCK",
            "stockQuantity": 0,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/product/price"):
            return httpx.Response(200, json=price)
        if path.endswith("/product/stock"):
            return httpx.Response(200, json=stock)
        if path.endswith("/search/"):
            return httpx.Response(
                302,
                headers={"Location": "/sk/skrutka/p/01210040050"},
            )
        if "/p/" in path:
            return httpx.Response(
                200,
                text=(
                    '<html><body><h1>Skrutka M4×50</h1>'
                    '<input class="alp-add-to-cart js-alp-add-to-cart" value="500">'
                    "<div>Ambalat la 500</div></body></html>"
                ),
            )
        return httpx.Response(404, text="not found")

    client = FaboryHttpClient("https://www.fabory.com/sk")
    client._client = httpx.AsyncClient(
        base_url=client._prefix,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True
    try:
        data = await client.fetch_product_price_and_stock("01210.040.050")
    finally:
        await client.aclose()

    assert data["price_eur"] == 2.35
    assert data["pack_price_eur"] == 11.75
    assert data["pack_quantity"] == 500
    assert data["price_unit"] == "per_100_ks"


@pytest.mark.asyncio
async def test_fetch_product_price_and_stock_pack_10_not_unit_quantity_100() -> None:
    price = {
        "01210300090": {
            "currencyIso": "EUR",
            "unitNetPrice": 170.5,
            "formattedUnitNetPrice": "170,50\u00a0€",
            "unitQuantity": 100,
        }
    }
    stock = {
        "01210300090": {
            "stockLevelMessage": "Na sklade",
            "stockLevelStatus": "INSTOCK",
            "stockQuantity": 0,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/product/price"):
            return httpx.Response(200, json=price)
        if path.endswith("/product/stock"):
            return httpx.Response(200, json=stock)
        if path.endswith("/search/"):
            return httpx.Response(
                302,
                headers={"Location": "/sk/skrutka/p/01210300090"},
            )
        if "/p/" in path:
            return httpx.Response(
                200,
                text=(
                    '<html><body><h1>M30×90</h1>'
                    '<input class="alp-add-to-cart js-alp-add-to-cart" value="10">'
                    "</body></html>"
                ),
            )
        return httpx.Response(404, text="not found")

    client = FaboryHttpClient("https://www.fabory.com/sk")
    client._client = httpx.AsyncClient(
        base_url=client._prefix,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True
    try:
        data = await client.fetch_product_price_and_stock("01210.300.090")
    finally:
        await client.aclose()

    assert data["pack_quantity"] == 10
    assert data["price_eur"] == 170.5
    assert data["pack_price_eur"] == 17.05
    assert data["price_unit"] == "per_100_ks"
    assert data["raw_pack_quantity"] == "10"
    assert data["packaging_variants"][0]["pack_quantity"] == 10
    assert data["packaging_variants"][0]["pack_price_eur"] == 17.05


@pytest.mark.asyncio
async def test_fetch_product_price_missing_code_raises() -> None:
    client = _build_mock_client({}, {})
    try:
        with pytest.raises(RuntimeError, match="cena pre kód"):
            await client.fetch_product_price_and_stock("XX99999")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_product_outofstock_maps_to_zero() -> None:
    price = dict(PRICE_RESPONSE)
    stock = {
        "01210120060": {
            "stockLevelMessage": "Vypredané",
            "stockLevelStatus": "OUTOFSTOCK",
            "stockQuantity": 0,
        }
    }
    client = _build_mock_client(price, stock)
    try:
        data = await client.fetch_product_price_and_stock("01210120060")
    finally:
        await client.aclose()
    assert data["stock"] == 0
    assert data["raw_stock"] == "Vypredané"
