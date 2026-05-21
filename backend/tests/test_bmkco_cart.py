from app.services.bmkco_http_client import (
    BmkcoHttpClient,
    _bmkco_pick_pack_quantity,
    bmkco_parse_cart_html,
)


DETAIL_7862 = {
    "karta": "7862",
    "nazev": "DIN 931, šroub se šestihrannou hlavou, částečný závit",
    "pocetMjProObjednani": 200,
    "mernaJednotkaWEB": "KUS",
    "baleni": "100 KUS",
    "prepocetBaleninaMJ": 2,
    "pocetMJvBaleni": 100,
    "mnozstviSkladem": 100,
    "zakaznikCena": "4.63",
}

STOCK_7862 = [
    {"Quantity": 875, "MernaJednotkaWeb": "KUS", "CentralStore": 0},
    {"Name": "BMCo sklad", "QuantityConverted": 875, "MernaJednotkaWeb": "KUS"},
]

KOSIK_ROW = """
<tr>
<td><a onclick="DetailZbozi(123);">img</a></td>
<td><h2 class="product-name">Vrut zapuštěná hlava</h2></td>
<td></td><td></td><td></td>
<td><span>0,61 €</span></td>
<td colspan="2"><input name="r[1][qty]" value="1000" /></td>
<td><span>610,00 €</span></td>
<td><button onclick="OdstranitPolozkuKosiku(123);"></button></td>
</tr>
<input type="hidden" id="cenaRadekPocet" value="1" />
<input type="hidden" id="cenaRadek1" value="610" />
"""


def test_parse_cart_one_line():
    parsed = bmkco_parse_cart_html(
        KOSIK_ROW, total_eur=610.0, line_count=1
    )
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 610.0
    assert len(parsed["lines"]) == 1
    line = parsed["lines"][0]
    assert line["variant_code"] == "123"
    assert line["quantity"] == 1000
    assert line["unit_price_eur"] == 0.61
    assert line["line_total_eur"] == 610.0


def test_parse_cart_empty():
    parsed = bmkco_parse_cart_html("", total_eur=0.0, line_count=0)
    assert parsed["line_count"] == 0
    assert parsed["lines"] == []
    assert parsed["total_eur"] == 0.0
    assert parsed.get("empty_cart") is True


def test_bmkco_pack_quantity_7862_uses_pocet_mj_pro_objednani():
    assert _bmkco_pick_pack_quantity(DETAIL_7862) == 200


def test_bmkco_parse_supplier_data_7862_stock_from_skladovy_stav():
    data = BmkcoHttpClient.parse_supplier_data(DETAIL_7862, stock_state=STOCK_7862)
    assert data["stock"] == 875
    assert data["pack_quantity"] == 200
    assert data["packaging_variants"][0]["pack_quantity"] == 200
    assert "875" in (data["raw_stock"] or "")
