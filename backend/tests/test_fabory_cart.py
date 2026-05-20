from app.services.fabory_http_client import fabory_parse_cart_html

SNIPPET = """
<span id="total-items-in-cart" data-total-items="2"></span>
<form id="updateCartForm0" data-cart="{}" action="/sk/cart/update" method="post">
<input name="productCode" value="51350055001"/>
<input id="quantity_0" class="js-update-entry-quantity-input" value="200"/>
</form>
<div class="item__list" data-page-type="CART">
<input class="js-ga-info" data-ga-product-name="Rosette washer A2 5.5MM" data-ga-variant="51350055001"/>
<div class="item__total js-item-total">
<span class="excluding-price"><span>4,85&nbsp;€</span>&nbsp;bez DPH</span>
</motion>
<form id="updateCartForm1" data-cart="{}" action="/sk/cart/update" method="post">
<input name="productCode" value="07000200060"/>
<input id="quantity_1" class="js-update-entry-quantity-input" value="25"/>
</form>
<div class="item__total js-item-total">
<span class="excluding-price"><span>12,87&nbsp;€</span>&nbsp;bez DPH</span>
</div>
<div class="subtotal-price">Medzisúčet<span class="pull-right">17,72&nbsp;€</span></motion>
<div class="total-price" data-ga-total-price="21.80">
"""


def test_parse_cart_two_lines():
    parsed = fabory_parse_cart_html(SNIPPET)
    assert parsed["line_count"] == 2
    assert len(parsed["lines"]) == 2
    assert parsed["lines"][0]["variant_code"] == "51350055001"
    assert parsed["lines"][0]["quantity"] == 200
    assert parsed["lines"][0]["line_total_eur"] == 4.85
    assert parsed["lines"][1]["variant_code"] == "07000200060"
    assert parsed["lines"][1]["line_total_eur"] == 12.87
    assert parsed["total_eur"] == 17.72


def test_parse_cart_empty():
    parsed = fabory_parse_cart_html("<html></html>")
    assert parsed["line_count"] == 0
    assert parsed["lines"] == []
    assert parsed["total_eur"] == 0.0
    assert parsed.get("empty_cart") is True
