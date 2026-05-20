"""Parsovanie riadku variantu Inoxmare (box + Master Carton)."""

from app.services.inoxmare_http_client import parse_inoxmare_row_fields

_LOGGED_IN_ROW = """
<tr id="57390407002">
  <td class="item">57390407002</td>
  <td class="descr">
    SCR. DIN933/ISO4017-4X70-A2-70
    <p class="info-ui">
      <i class="fas fa-cube"></i>200
      <i class="fas fa-cubes"></i>3000
      <i class="porto-icon-mode-grid"></i>108000
    </p>
  </td>
  <td class="A2">A2</td>
  <td class="col data">5000</td>
  <td class="price-box">
    <p class="price"><span class="price" data-price-amount="45.20" data-price-type="finalPrice">€45.20</span></p>
    <input type="hidden" id="88421-custom-price" value="45.20">
    <input type="hidden" id="88421-box-qty" value="200">
  </td>
  <td class="price-mc">
    <p class="price"><span class="price" data-price-amount="42.50" data-price-type="finalPrice">€42.50</span></p>
    <input type="hidden" id="88421-custom-price-mc" value="42.50">
    <input type="hidden" id="88421-mc-qty" value="3000">
  </td>
</tr>
"""

_NOT_LOGIN_ROW = """
<tr id="57390407002">
  <td class="item">57390407002</td>
  <td class="descr">
    SCR. DIN933/ISO4017-4X70-A2-70
    <p class="info-ui">
      <i class="fas fa-cube"></i>200
      <i class="fas fa-cubes"></i>3000
    </p>
  </td>
  <td class="A2">A2</td>
  <td class="not-login">
    <p>Sign up or log in to view availability and prices.</p>
  </td>
</tr>
"""


def test_inoxmare_logged_in_row_parses_box_and_master_carton_prices() -> None:
    rf = parse_inoxmare_row_fields(_LOGGED_IN_ROW)
    assert rf["pack_quantity"] == 200
    assert rf["master_pack_quantity"] == 3000
    assert rf["price_eur"] == 45.2
    assert rf["master_pack_price_eur"] == 42.5
    assert rf["stock"] == 5000


def test_inoxmare_not_login_row_keeps_pack_quantities_without_prices() -> None:
    rf = parse_inoxmare_row_fields(_NOT_LOGIN_ROW)
    assert rf["pack_quantity"] == 200
    assert rf["master_pack_quantity"] == 3000
    assert rf["price_eur"] is None
    assert rf["master_pack_price_eur"] is None
    assert rf["stock"] is None
    assert "log in" in (rf["raw_stock"] or "").lower()


def test_inoxmare_product_title_from_pdp_html() -> None:
    from pathlib import Path

    from app.services.inoxmare_http_client import (
        parse_inoxmare_pdp,
        parse_inoxmare_product_title,
    )

    html = Path(__file__).resolve().parents[1] / "data" / "tmp_inoxmare_pdp.html"
    if not html.is_file():
        return
    raw = html.read_text(encoding="utf-8", errors="replace")
    title = parse_inoxmare_product_title(raw)
    assert title == "DIN 933/ISO4017 sim. UNI 5739 Hexagon screw"
    meta = parse_inoxmare_pdp(raw, product_code="57390407002")
    assert meta.get("product_title") == title
    assert meta.get("pdp_label") == "SCR. DIN933/ISO4017-4X70-A2-70"
