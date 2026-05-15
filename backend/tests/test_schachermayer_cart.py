from app.services.schachermayer_http_client import (
    schachermayer_parse_basket_summary_html,
    schachermayer_parse_cart_json,
)


def test_parse_basket_summary_html():
    html = """
    <div class="appbar-basket-details">0,47 EUR</motion>
    <span data-cy="appbar-shoppingcart-count-span">3</span>
    """
    parsed = schachermayer_parse_basket_summary_html(html)
    assert parsed["line_count"] == 3
    assert parsed["total_eur"] == 0.47


def test_parse_cart_json_articles_list():
    basket = {
        "articles": [
            {
                "articleNr": "104426727",
                "title": "Skrutka M12",
                "amount": 2,
                "totalPrice": 0.94,
            }
        ],
        "totalPrice": 0.94,
    }
    parsed = schachermayer_parse_cart_json(basket)
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 0.94
    assert len(parsed["lines"]) == 1
    assert parsed["lines"][0]["variant_code"] == "104426727"
    assert parsed["lines"][0]["quantity"] == 2


def test_parse_cart_json_nested_basket():
    basket = {
        "baskets": [
            {
                "basketType": "Standard",
                "items": [
                    {
                        "articleNumber": "999",
                        "name": "Test",
                        "quantity": 1,
                        "lineTotal": 1.5,
                    }
                ],
                "total": 1.5,
            }
        ]
    }
    parsed = schachermayer_parse_cart_json(basket)
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 1.5
