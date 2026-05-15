from app.services.halfmann_http_client import halfmann_parse_cart_json

SNAPSHOT_ONE_LINE = {
    "gesamt": {"anzpos": 1, "wert": 11.54, "korbid": 1954},
    "warenkorb_list": [
        {
            "artid": 13335,
            "artikel": {
                "artid": 13335,
                "artkubez": "DIN 933 8.8   M 12x60",
                "artnr": "933-88-12-60",
                "pe": 100,
            },
            "korb": {"artid": 13335, "menge": 100, "korbid": 1954},
        }
    ],
    "line_prices": {
        "13335": {
            "artid": "13335",
            "menge": 100,
            "pe": 100,
            "preis": 11.54,
            "netwert": "11.54",
        }
    },
}


def test_parse_cart_one_line():
    parsed = halfmann_parse_cart_json(SNAPSHOT_ONE_LINE)
    assert parsed["line_count"] == 1
    assert parsed["total_eur"] == 11.54
    assert len(parsed["lines"]) == 1
    line = parsed["lines"][0]
    assert line["variant_code"] == "13335"
    assert line["quantity"] == 100
    assert line["line_total_eur"] == 11.54
    assert "DIN 933" in line["label"]


def test_parse_cart_empty():
    parsed = halfmann_parse_cart_json({"gesamt": {"anzpos": 0, "wert": 0}})
    assert parsed["line_count"] == 0
    assert parsed["lines"] == []
    assert parsed["total_eur"] == 0.0
