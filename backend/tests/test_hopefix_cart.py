from app.services.hopefix_http_client import (
    find_hopefix_row,
    find_hopefix_row_in_html,
    hopefix_norm_code,
    hopefix_parse_cart_html,
    parse_hopefix_rows,
)

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


ROW_NO_LINE_ID = """
<tbody>
<tr><td>933</td><td>D933A212016</td><td class="t-right">M12x16 A2</td><td>10,00&nbsp;€</td></tr>
</tbody>
"""


def test_find_row_fallback_second_td_when_no_line_id():
    row = find_hopefix_row_in_html(ROW_NO_LINE_ID, "D933A212016")
    assert row is not None
    assert row["product_nr"] == "D933A212016"


def test_find_row_variant_suffix_b1():
    rows = parse_hopefix_rows(
        '<tr id="line-D933A212016B1"><td>933</td><td>x</td></tr>'
    )
    hit = find_hopefix_row(rows, "D933A212016")
    assert hit is not None
    assert hit["product_nr"] == "D933A212016B1"


def test_hopefix_norm_code_nfkc_and_zwsp():
    s = "D933A212016\u200b"
    assert hopefix_norm_code(s) == "D933A212016"
