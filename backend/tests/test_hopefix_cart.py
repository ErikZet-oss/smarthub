from app.services.hopefix_http_client import (
    find_hopefix_row,
    find_hopefix_row_in_html,
    hopefix_norm_code,
    hopefix_parse_cart_html,
    hopefix_row_likely_no_cart_form,
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


UNQUOTED_LINE = (
    '<tr id=line-D933X1><td>933</td><td>D933X1</td><td class="t-right">M12</td>'
    '<td>10,00&nbsp;€</td></tr>'
)


def test_parse_unquoted_line_id():
    rows = parse_hopefix_rows(UNQUOTED_LINE)
    assert len(rows) == 1
    assert rows[0]["product_nr"] == "D933X1"


def test_find_row_with_extra_leading_td():
    html = (
        "<tbody><tr>"
        "<td>chk</td><td>933</td><td>D933A212016</td><td>x</td><td>5,00&nbsp;€</td>"
        "</tr></tbody>"
    )
    row = find_hopefix_row_in_html(html, "D933A212016")
    assert row is not None
    assert row["product_nr"] == "D933A212016"


def test_find_row_by_td_close_pattern():
    html = "<table><tr><td>933</td><td>D933A212016</td><td>x</td></tr></table>"
    row = find_hopefix_row_in_html(html, "D933A212016")
    assert row is not None


# HAR www.hopefix3.cz: product_id je v nasledujúcom <tr class="expander-row">, nie v riadku line-*.
EXPANDER_SNIPPET = """
<tr id="line-D933A212016"><td>933</td><td>D933A212016</td><td>x</td></tr>
<tr class="expander-row"><td colspan="14">
<form><input type="hidden" name="product_nr" value="D933A212016">
<input type="hidden" name="product_id" value="4745"></form></td></tr>
"""


def test_product_id_from_expander_after_line_row():
    rows = parse_hopefix_rows(EXPANDER_SNIPPET)
    assert rows and (rows[0].get("hopefix_product_id") in (None, ""))
    row = find_hopefix_row_in_html(EXPANDER_SNIPPET, "D933A212016")
    assert row is not None
    assert row.get("hopefix_product_id") == "4745"


def test_row_likely_no_cart_when_oos_or_restock():
    assert hopefix_row_likely_no_cart_form({"stock": 0, "raw_stock": ""})
    assert hopefix_row_likely_no_cart_form(
        {"stock": None, "raw_stock": "Další naskladnění od 15.6.2026"}
    )
    assert not hopefix_row_likely_no_cart_form({"stock": 50, "raw_stock": "100 ks"})
