"""Schäfer-Peters HTTP klient — parser PDP + qty math + mock-trip k cart.

Test fixture vychádza zo zákazníckeho HAR-u
(``shop.schaefer-peters.com.har``), zachovávame z neho **vzor** markerov, nie
celých 1 MB HTML. To nám stačí, lebo všetky parsovacie regulárky pracujú nad
týmito konkrétnymi selektormi.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.schaef_http_client import (
    SchaefHttpClient,
    _parse_european_decimal,
    _parse_european_int,
    schaef_base_url,
    schaef_parse_pdp_html,
    schaef_round_qty_to_step,
    schaef_title_from_pdp_path,
)


PDP_SNIPPET = """
<html>
<head>
  <link rel="canonical" href="https://shop.schaefer-peters.com/b2b/en/din-933-a2-70-m-12x40-p133791/"/>
</head>
<body>
  <form id="itemcard_order_button_form_std" method="post"
        action="/b2b/en/din-933-a2-70-m-12x40-p133791/queue/?action=shop_add_item_to_basket_card">
    <input type="hidden" name="optional_performances_item_id" value="487613">
    <input type="hidden" name="optional_performances_item_qty" value="1">
    <input name="item_id" value="133791" type="hidden">
    <input name="item_var_code" type="hidden" value="">
    <div class="prices">
      <div class="priceLabel">Price 100 Pcs.</div>
      <div class="basePrice">
        <span itemprop="priceCurrency" content="EUR"></span>
        <span itemprop="price" content="12.2598"></span>
        <span itemprop="lowprice" content="12.2598"></span>
        12,26 €
      </div>
    </div>
    <input type="number" name="item_qty" id="item_qty" value="50" min="50" step="50">
  </form>
  <div class="itemcardPackagingUnits">
    <div class="packagingUnits orderButtonWrapper">
      <div class="js-packagingBox" id="js-packagingBox"><i class="icon icon-karton"></i>50</div>
    </div>
  </div>
  <div class="itemcardOnlineInventory">
    <div class="onlineInventory">
      <h4 class="onlineInventory__headline">Inventory online:</h4>
      <div class="onlineInventory__amount">
        <div class="inventoryWrapper">
          <div class="inventory available">
            <span itemprop="availability" content="http://schema.org/InStock"></span>
            <div class="onlineInventory__label">
              <p>384.500 Pcs.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="amountLine">
    <div class="amountLine__label">Item number</div>
    <div class="amountLine__value" style="white-space: pre">0933212 40</div>
  </div>
</body>
</html>
"""


def test_schaef_parse_pdp_extracts_core_fields() -> None:
    data = schaef_parse_pdp_html(
        PDP_SNIPPET,
        product_title_hint="DIN 933 A2-70 M 12X40",
    )
    assert data["schaef_item_id"] == "133791"
    assert data["product_title"] == "DIN 933 A2-70 M 12X40"
    assert data["price_eur"] == 12.2598
    assert data["pack_quantity"] == 100
    assert data["stock"] == 384_500
    assert data["raw_stock"] and "384.500" in data["raw_stock"]
    assert data["currency_code"] == "eur"
    assert data["schaef_pdp_path"] == "/b2b/en/din-933-a2-70-m-12x40-p133791/"
    assert data["schaef_order_step"] == 50
    assert data["schaef_order_min"] == 50
    assert data["schaef_parcel_size"] == 50
    # Jeden variant — bez expandu na viac packagingov.
    pv = data["packaging_variants"]
    assert len(pv) == 1
    assert pv[0]["schaef_item_id"] == "133791"
    assert pv[0]["label"] == "DIN 933 A2-70 M 12X40"
    assert pv[0]["pack_quantity"] == 100


def test_schaef_parse_pdp_title_from_canonical_slug() -> None:
    data = schaef_parse_pdp_html(PDP_SNIPPET)
    assert data["product_title"] == "DIN 933 A2 70 M 12X40"
    assert data["packaging_variants"][0]["label"] == "DIN 933 A2 70 M 12X40"


def test_schaef_parse_pdp_missing_stock_falls_back_to_availability() -> None:
    # Bez čísla — len availability InStock → stock=1, raw_stock="InStock"
    snippet = PDP_SNIPPET.replace("<p>384.500 Pcs.</p>", "")
    data = schaef_parse_pdp_html(snippet)
    assert data["stock"] == 1
    assert "InStock" in (data["raw_stock"] or "")


def test_schaef_parse_pdp_outofstock_availability() -> None:
    snippet = PDP_SNIPPET.replace(
        "http://schema.org/InStock", "http://schema.org/OutOfStock"
    ).replace("<p>384.500 Pcs.</p>", "")
    data = schaef_parse_pdp_html(snippet)
    assert data["stock"] == 0
    assert "OutOfStock" in (data["raw_stock"] or "")


def test_parse_european_int_handles_dot_thousands() -> None:
    assert _parse_european_int("384.500") == 384_500
    assert _parse_european_int("1.000.000") == 1_000_000
    assert _parse_european_int("12") == 12
    assert _parse_european_int("") is None
    # Európsky desatinný formát „X,YY" — vyhodíme desatinnú časť.
    assert _parse_european_int("384,5") == 384


def test_parse_european_decimal_handles_european_format() -> None:
    assert _parse_european_decimal("12,26") == pytest.approx(12.26)
    assert _parse_european_decimal("1.234,56") == pytest.approx(1234.56)
    # Anglický formát „1,234.56" — bodka je desatinná.
    assert _parse_european_decimal("1,234.56") == pytest.approx(1234.56)


def test_schaef_round_qty_to_step_basic() -> None:
    assert schaef_round_qty_to_step(50, 50, 50) == 50
    assert schaef_round_qty_to_step(100, 50, 50) == 100
    # Pod minimum → tlač na min.
    assert schaef_round_qty_to_step(10, 50, 50) == 50
    # Medzi krokmi → zaokrúhli nahor.
    assert schaef_round_qty_to_step(60, 50, 50) == 100
    assert schaef_round_qty_to_step(149, 50, 50) == 150
    # Step=1 → identita (s minimom).
    assert schaef_round_qty_to_step(7, 1, 1) == 7


def test_schaef_base_url_normalizes_input() -> None:
    assert schaef_base_url("") == "https://shop.schaefer-peters.com"
    assert (
        schaef_base_url("https://shop.schaefer-peters.com/b2b/en/")
        == "https://shop.schaefer-peters.com"
    )
    assert (
        schaef_base_url("shop.schaefer-peters.com") == "https://shop.schaefer-peters.com"
    )


def _build_mock_client_for_pdp(pdp_html: str) -> SchaefHttpClient:
    """Postaví klienta nad ``httpx.MockTransport`` — žiadny externý sieťový hit."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # search redirect → PDP
        if path.startswith("/b2b/en/search/"):
            return httpx.Response(
                302,
                headers={"Location": "/b2b/en/din-933-a2-70-m-12x40-p133791/"},
            )
        if "/b2b/en/" in path and "-p" in path:
            return httpx.Response(200, text=pdp_html)
        if path == "/sp/en/home/":
            return httpx.Response(200, text="<html><body>home</body></html>")
        if path == "/b2b/en/" or path.startswith("/b2b/en/?action=shop_login"):
            # Po prihlásení musí byť „logout" v menu — inak ensure_login spadne.
            return httpx.Response(
                200, text="<html><body><a>Logout</a></body></html>"
            )
        if path.startswith("/module/dcshop/GeneralAjaxData.php"):
            if "function=cart" in (request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query)):
                return httpx.Response(
                    302,
                    headers={
                        "Location": "/module/dcshop/GeneralAjaxData.php?function=readCart&site=b2b&language=en"
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "successful": True,
                    "headerBasketContent": "<span class='js-basketCount'>1</span>",
                },
            )
        return httpx.Response(404, text="not found")

    client = SchaefHttpClient(base_url="https://shop.schaefer-peters.com")
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True
    return client


def test_normalize_supplier_code_handles_nbsp_and_tabs() -> None:
    """Excel kódy obsahujú často \\xa0 (NBSP) medzi cifrou a sufixom.

    Server Schäf-a search-i akceptuje normálnu medzeru (HAR: ``%20``),
    nie NBSP (``%C2%A0``) — bez normalizácie nám vrátil 200 OK s prázdnym
    listom miesto 302 na PDP. Test zachycuje regresiu nahláseného bug-u
    s kódom ``"0933212\\xa016"``.
    """
    norm = SchaefHttpClient._normalize_supplier_code
    assert norm("0933212\xa016") == "0933212 16"
    assert norm("0933212 \t  40") == "0933212 40"
    assert norm("  0933212\u202f40  ") == "0933212 40"
    assert norm("") == ""


async def _stub_algolia_none(self, supplier_code: str) -> None:  # noqa: ARG001
    return None


@pytest.mark.asyncio
async def test_search_to_pdp_uses_normalized_code_for_nbsp(monkeypatch) -> None:
    """Excel-ový kód s NBSP nezhodí search-er — ide cez %20 priamo na PDP.

    Regresia z prevádzky: kód ``"0933212\\xa016"`` v Exceli prešiel ako
    ``%C2%A0`` v query a Schäf-server vrátil 200 s prázdnym výsledkom
    (na PDP nepresmeroval). Klient teraz najprv normalizuje whitespace
    a posiela query s bežnou medzerou (``%20``). Algolia tu vypneme,
    aby sme overili HTML cestu samostatne.
    """
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = (
            request.url.query.decode()
            if isinstance(request.url.query, bytes)
            else str(request.url.query)
        )
        if path.startswith("/b2b/en/search/"):
            attempts.append(query)
            if "%C2%A0" in query:
                return httpx.Response(
                    200, text="<html><body>No results</body></html>"
                )
            return httpx.Response(
                302,
                headers={"Location": "/b2b/en/din-933-a2-70-m-12x16-p133790/"},
            )
        if "/b2b/en/" in path and "-p" in path:
            return httpx.Response(
                200,
                text='<html><body><input name="item_id" value="133790"></body></html>',
            )
        return httpx.Response(404)

    client = SchaefHttpClient(base_url="https://shop.schaefer-peters.com")
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True
    # Vypneme Algolia cestu — chceme overiť čisto HTML logiku (regresia z prevádzky).
    monkeypatch.setattr(
        SchaefHttpClient, "_algolia_find_product", _stub_algolia_none
    )
    try:
        pdp_path, body = await client._search_to_pdp("0933212\xa016")
    finally:
        await client.aclose()
    assert pdp_path == "/b2b/en/din-933-a2-70-m-12x16-p133790/"
    assert 'value="133790"' in body
    assert attempts, "search sa nezavolal vôbec"
    assert "%C2%A0" not in attempts[0], (
        f"Prvý pokus mal byť %20, dostali sme: {attempts[0]!r}"
    )
    assert "0933212%2016" in attempts[0]


@pytest.mark.asyncio
async def test_search_to_pdp_uses_algolia_when_available(monkeypatch) -> None:
    """Algolia vráti exact match → klient skočí priamo na PDP bez /search/.

    Hlavná regresia z prevádzky (`response body 1,014,677 znakov` =
    plná stránka výsledkov, ale `name="item_id"` tam pre nás nebol
    dostupný): teraz najprv pýtame Algolia, ktorá dáva ``itemId``
    + ``itemLink`` deterministicky.
    """

    fake_hit = {
        "itemId": 133778,
        "itemLink": "/din-933-a2-70-m-12x16-p133778/",
        "itemNo": "0933212 16",
        "description": "DIN 933 A2-70 M 12X16",
        "units_per_parcel": "50",
    }

    async def _stub_algolia(self, supplier_code: str):  # noqa: ARG001
        return fake_hit

    monkeypatch.setattr(
        SchaefHttpClient, "_algolia_find_product", _stub_algolia
    )

    search_paths_hit: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/b2b/en/search/"):
            search_paths_hit.append(path)
            return httpx.Response(404, text="should not be called")
        if path == "/b2b/en/din-933-a2-70-m-12x16-p133778/":
            return httpx.Response(
                200,
                text='<html><body><input name="item_id" value="133778"></body></html>',
            )
        return httpx.Response(404)

    client = SchaefHttpClient(base_url="https://shop.schaefer-peters.com")
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True
    try:
        pdp_path, body = await client._search_to_pdp("0933212\xa016")
    finally:
        await client.aclose()
    assert pdp_path == "/b2b/en/din-933-a2-70-m-12x16-p133778/"
    assert 'value="133778"' in body
    # Algolia priamy hit ⇒ HTML /search/ vôbec nesmieme zavolať.
    assert search_paths_hit == [], (
        f"HTML search sa nemal volať, ale boli: {search_paths_hit}"
    )


def test_code_match_key_canonicalizes_separators() -> None:
    """Algolia môže vrátiť ``itemNo`` s rôznymi oddeľovačmi — máme byť tolerantní."""
    norm = SchaefHttpClient._code_match_key
    assert norm("0933212 16") == norm("0933212\xa016")
    assert norm("0933212 16") == norm("0933212-16")
    assert norm("0933212 16") == norm("093321216")
    # Case insensitive (Algolia tu vracia upper case, my hľadáme lower case).
    assert norm("AB-12 cd") == norm("ab12CD")


@pytest.mark.asyncio
async def test_search_to_pdp_falls_back_to_first_link_from_results(monkeypatch) -> None:
    """Algolia nič nenájde, server-side search nepresmeruje, ale v HTML
    zozname je prvý PDP hit — klient ho má použiť."""

    monkeypatch.setattr(
        SchaefHttpClient, "_algolia_find_product", _stub_algolia_none
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/b2b/en/search/"):
            return httpx.Response(
                200,
                text=(
                    '<html><body><ul><li>'
                    '<a href="/b2b/en/iso-7380-a2-m6x12-p99999/">First hit</a>'
                    "</li></ul></body></html>"
                ),
            )
        if path == "/b2b/en/iso-7380-a2-m6x12-p99999/":
            return httpx.Response(
                200,
                text='<input name="item_id" value="99999">',
            )
        return httpx.Response(404)

    client = SchaefHttpClient(base_url="https://shop.schaefer-peters.com")
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client._login_ok = True
    try:
        pdp_path, body = await client._search_to_pdp("UNKNOWN-CODE")
    finally:
        await client.aclose()
    assert pdp_path == "/b2b/en/iso-7380-a2-m6x12-p99999/"
    assert 'value="99999"' in body


@pytest.mark.asyncio
async def test_fetch_product_price_and_stock_via_mock_transport(monkeypatch) -> None:
    # Algolia volá externý host (CDN), v unit-testoch ho zámerne vypneme,
    # aby sme overili tok pri Algolia výpadku → HTML search fallback.
    monkeypatch.setattr(
        SchaefHttpClient, "_algolia_find_product", _stub_algolia_none
    )
    client = _build_mock_client_for_pdp(PDP_SNIPPET)
    try:
        data = await client.fetch_product_price_and_stock("0933212 40")
    finally:
        await client.aclose()
    assert data["schaef_item_id"] == "133791"
    assert data["price_eur"] == 12.2598
    assert data["pack_quantity"] == 100
    assert data["stock"] == 384_500
    assert data["schaef_pdp_path"] == "/b2b/en/din-933-a2-70-m-12x40-p133791/"


@pytest.mark.asyncio
async def test_add_to_cart_via_mock_transport_round_trip() -> None:
    """Cart-add postupnosť: POST function=cart → 302 → GET function=readCart JSON."""
    client = _build_mock_client_for_pdp(PDP_SNIPPET)
    try:
        result = await client.add_to_cart(
            item_id="133791",
            quantity_pieces=100,
            referer_path="/b2b/en/din-933-a2-70-m-12x40-p133791/",
        )
    finally:
        await client.aclose()
    # Mock vráti JSON z readCart endpointu — overujeme, že nás 302 chain priviedol tam.
    assert isinstance(result, dict)
    assert result.get("status") == "ok"


@pytest.mark.asyncio
async def test_add_to_cart_rejects_empty_item_id() -> None:
    client = _build_mock_client_for_pdp(PDP_SNIPPET)
    try:
        with pytest.raises(ValueError, match="item_id"):
            await client.add_to_cart(item_id="", quantity_pieces=50)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ensure_login_failure_when_form_still_visible() -> None:
    """Server vráti login form s ``input_login`` → ensure_login musí padnúť."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sp/en/home/":
            return httpx.Response(200, text="<html><body>home</body></html>")
        if path.startswith("/b2b/en/"):
            # Login zostal: form so vstupom ostáva → nezalogovaní.
            return httpx.Response(
                200,
                text='<html><body><form><input name="input_login"><input name="input_password"></form></body></html>',
            )
        return httpx.Response(404)

    client = SchaefHttpClient(base_url="https://shop.schaefer-peters.com")
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    try:
        with pytest.raises(RuntimeError, match="prihlás"):
            await client.ensure_login("fake_user", "fake_pwd")
    finally:
        await client.aclose()
