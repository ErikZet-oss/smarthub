from app.services.bmkco_http_client import bmkco_parse_cart_datatable

CART_ROW = {
    "DT_RowId": "0",
    "0": "123<br/>",
    "2": (
        '<h2 class="product-name"><a href="/cs/Product/Detail/123">'
        "Vrut zapuštěná hlava drážka PZ celý závit</a></h2>"
    ),
    "7": '<div style="text-align:right;"><span style="white-space: nowrap">0,61 €</span></div>',
    "10": (
        '<input title="Požadovaný počet kusů" type="text" value="1000" '
        'class="form-control input-sm" />'
    ),
}


def test_parse_cart_one_line():
    blob = {"aaData": [CART_ROW], "iTotalRecords": 1}
    parsed = bmkco_parse_cart_datatable(
        blob, total_eur=610.0, line_count=1
    )
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 610.0
    assert len(parsed["lines"]) == 1
    line = parsed["lines"][0]
    assert line["variant_code"] == "123"
    assert line["bmkco_karta"] == "123"
    assert line["quantity"] == 1000
    assert line["unit_price_eur"] == 0.61
    assert line["line_total_eur"] == 610.0


def test_parse_cart_empty():
    parsed = bmkco_parse_cart_datatable(
        {"aaData": []}, total_eur=0.0, line_count=0
    )
    assert parsed["line_count"] == 0
    assert parsed["lines"] == []
    assert parsed["total_eur"] == 0.0
    assert parsed.get("empty_cart") is True
