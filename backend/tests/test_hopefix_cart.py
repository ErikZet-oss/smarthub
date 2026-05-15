from app.services.hopefix_http_client import hopefix_parse_cart_html

KOSIK_SNIPPET = """
<table class="table_cart">
<tr>
<td class="td_count td_50">
<input name="variant[28861][qty]" class="readout" type="number" value="10">
</td>
<td>box</td>
<td>100&nbsp;ks</td>
<td>D9758816100000 Závitové tyče Rozměr: M16x1000</td>
<td>258,03&nbsp;€ / 100 pcs</td>
<td>258,03&nbsp;€</td>
<td>Smazat</td>
</tr>
<tr>
<td class="t-right td_price_total" colspan="6">
<strong>Celkem bez DPH:</strong>
<strong class="price_total">258,03&nbsp;€</strong>
</td>
</tr>
</table>
"""


def test_parse_cart_one_line():
    parsed = hopefix_parse_cart_html(KOSIK_SNIPPET)
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 258.03
    assert len(parsed["lines"]) == 1
    line = parsed["lines"][0]
    assert line["variant_code"] == "D9758816100000"
    assert line["quantity"] == 10
    assert line["line_total_eur"] == 258.03


def test_parse_cart_empty():
    parsed = hopefix_parse_cart_html("<html><body></body></html>")
    assert parsed["line_count"] == 0
    assert parsed["lines"] == []
    assert parsed["total_eur"] == 0.0
    assert parsed.get("empty_cart") is True
