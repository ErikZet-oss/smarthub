from app.services.bmkco_http_client import bmkco_parse_cart_html

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
