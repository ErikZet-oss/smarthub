from app.services.valenta_http_client import valenta_parse_cart_html

ORDER_EDIT_SNIPPET = """
<script>
var areboHeaderShopOrderPrice="1,20";
var JS_ActiveShopOrderItemsCount=1;
var areboActiveShopOrderId=1778491529;
</script>
<table class="ASClsTblShopProductDetails"><tr><th colspan="2">zavitovy tyc DIN 976</th></tr>
<tbody>
<tr><td class="CWClsTDInfoLabel">Kód položky:</td><td class="CWClsTDInfoValue">1112</td></tr>
<tr><td class="CWClsTDInfoLabel">Jméno produktu:</td><td class="CWClsTDInfoValue">zavitovy tyc DIN 976-1</td></tr>
<tr><td class="CWClsTDInfoLabel">Cena bez DPH:</td><td class="CWClsTDInfoValue">1,2010 EUR </td></tr>
</tbody></table>
<table id="arebotaocsoib"><tr id="arebooedeidpjscd22">
<td><input type="text" id="arebocsoiddeidpdci22" value="2"
class="ASClsInputCountChangeShopOrderItemDetails ASClsInputShopOrderItemCountChangeNumber22"/>
</td></tr></table>
"""


def test_parse_cart_overview_and_line_from_order_edit():
    parsed = valenta_parse_cart_html(ORDER_EDIT_SNIPPET)
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 1.2
    assert len(parsed["lines"]) == 1
    assert parsed["lines"][0]["variant_code"] == "1112"
    assert parsed["lines"][0]["quantity"] == 2
    assert parsed["lines"][0]["unit_price_eur"] == 1.201


def test_parse_cart_empty():
    parsed = valenta_parse_cart_html("<html></html>")
    assert parsed["line_count"] == 0
    assert parsed["lines"] == []
