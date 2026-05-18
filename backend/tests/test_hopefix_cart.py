from urllib.parse import quote

import pytest

from app.services.hopefix_http_client import (
    find_hopefix_row,
    find_hopefix_row_in_html,
    hopefix_norm_code,
    hopefix_parse_cart_html,
    hopefix_referer_path_from_catalog_url,
    hopefix_row_has_live_offer_cells,
    hopefix_row_is_guest_price_row,
    hopefix_row_likely_no_cart_form,
    hopefix_row_parse_quality,
    hopefix_row_pick_better,
    parse_hopefix_rows,
)
from app.services.scraper_service import _hopefix_narrow_catalog_paths

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


HOPEFIX_NESTED_TR_INSIDE_LINE = """
<table>
<thead><tr><th>DIN</th><th>Reg</th><th>Rozměr</th><th>EUR/100 pcs</th></tr></thead>
<tbody>
<tr id="line-D933NEST1"><td>933</td>
<td><table><tr><td>nested</td></tr></table></td>
<td>M10</td>
<td class="t-right tdprice">5,00&nbsp;€</td>
</tr>
</tbody>
</table>
"""


def test_find_row_balanced_tr_when_inner_table_before_price():
    row = find_hopefix_row_in_html(HOPEFIX_NESTED_TR_INSIDE_LINE, "D933NEST1")
    assert row is not None
    assert row["price_eur"] == pytest.approx(5.0)


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


# Široká tabuľka Hopefix: sklad a box sú v „100 ks“ jednotkách (41 → 4100 ks, 0,50 box → 50 ks).
HOPEFIX_WIDE_CATALOG = """
<table>
<thead><tr>
<th>DIN</th><th>Registrační číslo</th><th>Rozměr</th><th>ISO</th><th>Materiál</th><th>Vaše číslo</th>
<th>Sklad (100 ks)</th><th>EUR / 100 ks</th><th>Další naskladnění</th><th>100 pcs Box</th><th>100 pcs Carton</th><th>100 pcs Pallet</th>
</tr></thead>
<tbody>
<tr id="line-D933A212040"><td>933</td><td>D933A212040</td><td>M12*40 A2</td><td>4017</td><td>A2</td><td></td>
<td>41,00</td><td>13,76&nbsp;€</td><td></td><td>0,50</td><td>N/A</td><td>N/A</td></tr>
</tbody>
</table>
"""


def test_hopefix_wide_row_stock_pack_and_price_100_units():
    row = find_hopefix_row_in_html(HOPEFIX_WIDE_CATALOG, "D933A212040")
    assert row is not None
    assert row["product_nr"] == "D933A212040"
    assert row["price_eur"] == pytest.approx(13.76)
    assert row["pack_quantity"] == 50
    assert row["stock"] == 4100
    assert row["label"] == "M12*40 A2"


HOPEFIX_B1_ROW = """
<table>
<thead><tr>
<th>DIN</th><th>Registrační číslo</th><th>Rozměr</th><th>ISO</th><th>Materiál</th><th>Vaše číslo</th>
<th>Stock (100 pcs)</th><th>EUR/100 pcs</th><th>Další naskladnění</th><th>100 pcs Box</th><th>100 pcs Carton</th><th>100 pcs Pallet</th>
</tr></thead>
<tbody>
<tr id="line-D9338810016B1"><td>933</td><td>D9338810016B1</td><td>M10x16 zn</td><td>4017</td><td>8.8</td><td></td>
<td>44,00</td><td>3,82&nbsp;€</td><td>od 08.06.2026</td><td>2,00</td><td>10,00</td><td>360,00</td></tr>
</tbody>
</table>
"""


def test_hopefix_b1_stock_not_pallet_uses_sklad_column():
    row = find_hopefix_row_in_html(HOPEFIX_B1_ROW, "D9338810016B1")
    assert row is not None
    assert row.get("_hopefix_login_gate") is False
    assert row["stock"] == 4400
    assert row["pack_quantity"] == 200
    assert row["price_eur"] == pytest.approx(3.82)


HOPEFIX_PUBLIC_LOGIN_CELLS = """
<table>
<thead><tr>
<th>DIN</th><th>Registrační číslo</th><th>Rozměr</th><th>Sklad</th><th>EUR</th>
</tr></thead>
<tbody>
<tr id="line-D9338810016B1"><td>933</td><td>D9338810016B1</td><td>M10</td>
<td class="center"><a href="/prihlaseni">Přihlásit se</a></td>
<td class="center"><a href="/prihlaseni">Přihlásit se</a></td></tr>
</tbody>
</table>
"""


def test_hopefix_login_gate_when_price_cells_are_login_links():
    row = find_hopefix_row_in_html(HOPEFIX_PUBLIC_LOGIN_CELLS, "D9338810016B1")
    assert row is not None
    assert row.get("_hopefix_login_gate") is True


HOPEFIX_B1_EXTRA_TD = """
<table>
<thead><tr>
<th>DIN</th><th>Registrační číslo</th><th>Rozměr</th><th>ISO</th><th>Materiál</th><th>Vaše číslo</th>
<th>Stock (100 pcs)</th><th>EUR/100 pcs</th><th>Další naskladnění</th><th>100 pcs Box</th><th>100 pcs Carton</th><th>100 pcs Pallet</th>
</tr></thead>
<tbody>
<tr id="line-D9338810016B1"><td></td><td>933</td><td>D9338810016B1</td><td>M10x16 zn</td><td>4017</td><td>8.8</td><td></td>
<td>44,00</td><td>3,82&nbsp;€</td><td>od 08.06.2026</td><td>2,00</td><td>10,00</td><td>360,00</td></tr>
</tbody>
</table>
"""


def test_hopefix_b1_anchor_align_when_leading_empty_td():
    row = find_hopefix_row_in_html(HOPEFIX_B1_EXTRA_TD, "D9338810016B1")
    assert row is not None
    assert row["stock"] == 4400
    assert row["pack_quantity"] == 200


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


def test_hopefix_pick_better_prefers_numeric_row_over_login_gate():
    gate = {"product_nr": "X", "_hopefix_login_gate": True, "price_eur": None}
    live = {
        "product_nr": "X",
        "_hopefix_login_gate": False,
        "price_eur": 1.5,
        "stock": 10,
    }
    assert hopefix_row_pick_better(gate, live) == live
    assert hopefix_row_pick_better(live, gate) == live


def test_hopefix_parse_quality_orders_login_last():
    gate = {"_hopefix_login_gate": True}
    empty = {"_hopefix_login_gate": False}
    num = {"_hopefix_login_gate": False, "price_eur": 1.0}
    assert hopefix_row_parse_quality(num) < hopefix_row_parse_quality(empty)
    assert hopefix_row_parse_quality(empty) < hopefix_row_parse_quality(gate)


def test_hopefix_has_live_offer_ignores_spurious_login_gate_when_priced():
    row = {
        "_hopefix_login_gate": True,
        "price_eur": 1.0,
        "stock": 5,
    }
    assert hopefix_row_has_live_offer_cells(row) is True
    assert hopefix_row_is_guest_price_row(row) is False


def test_hopefix_pick_better_keeps_priced_row_even_if_login_gate_flag():
    empty = {"product_nr": "X", "_hopefix_login_gate": False}
    priced_gate = {
        "product_nr": "X",
        "_hopefix_login_gate": True,
        "price_eur": 3.82,
        "stock": 100,
    }
    assert hopefix_row_pick_better(empty, priced_gate) == priced_gate
    assert hopefix_row_pick_better(priced_gate, empty) == priced_gate


def test_hopefix_referer_path_strips_host_keeps_query():
    assert (
        hopefix_referer_path_from_catalog_url(
            "https://www.hopefix.cz/sortiment/foo?_ref=ABC"
        )
        == "/sortiment/foo?_ref=ABC"
    )
    assert (
        hopefix_referer_path_from_catalog_url("/sortiment/pevnost?_ref=X")
        == "/sortiment/pevnost?_ref=X"
    )
    assert hopefix_referer_path_from_catalog_url("") == "/"


def test_hopefix_narrow_catalog_prefers_pevnost_88_for_hex_8_8():
    """HAR D9338810016B1: B2B riadok je na /sortiment/srouby-…-pevnost-88."""
    code = "D9338810016B1"
    enc = quote(code, safe=".-_~")
    paths = _hopefix_narrow_catalog_paths(code, enc)
    assert paths[0].startswith(
        "/sortiment/srouby-se-sestihrannou-hlavou-pevnost-88?_ref="
    )
    assert paths[1] == "/sortiment/srouby-se-sestihrannou-hlavou-pevnost-88"
