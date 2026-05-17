"""Testy pre Fabory JSON API (/sk/product/price, /sk/product/stock)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.fabory_http_client import FaboryHttpClient


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
            return httpx.Response(200, text="<html><body>PDP stub</body></html>")
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
    assert data["raw_price"] == PRICE_RESPONSE["01210120060"]["formattedUnitNetPrice"]
    assert data["pack_quantity"] == 100
    # stockQuantity = 0 a stockLevelStatus = INSTOCK ⇒ mapujeme na "dostupné" (1).
    assert data["stock"] == 1
    assert data["raw_stock"] == "Na sklade"
    assert data["fabory_via_http"] is True
    assert data["currency"] == "EUR"
    assert isinstance(data["packaging_variants"], list) and data["packaging_variants"]


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
