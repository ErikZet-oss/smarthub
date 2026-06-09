from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.services.inquiry.parser import (
    _gemini_inquiry_response_schema,
    _heuristic_parse,
    parse_inquiry_line,
)


def test_gemini_schema_has_no_reserved_fields() -> None:
    schema = _gemini_inquiry_response_schema()
    assert "title" not in schema
    props = schema["properties"]
    assert isinstance(props, dict)
    assert "class" not in props
    assert "product_class" in props


def test_heuristic_parse_matica() -> None:
    ai = _heuristic_parse("Šesťhranná matica DIN 934 Oceľ Pozinkované M3")
    assert ai is not None
    assert ai.diameter == "M3"
    assert ai.norm == "DIN934"
    assert ai.material == "pozinkované"


def test_heuristic_parse_skrutka() -> None:
    ai = _heuristic_parse("skrutka M10x50 DIN933 8.8 pozinkovaná")
    assert ai is not None
    assert ai.diameter == "M10"
    assert ai.length == "50"
    assert ai.norm == "DIN933"


def test_parse_inquiry_line_heuristic_fallback(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parsed = parse_inquiry_line("Šesťhranná matica DIN 934 Nerezoceľ A2 M4", row_index=1)
    assert parsed.parse_error is None
    assert parsed.diameter == "M4"
    assert parsed.norm == "DIN934"


def test_parse_inquiry_line_with_mock_model() -> None:
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "diameter": "M10",
            "length": "50",
            "norm": "DIN933",
            "product_class": "8.8",
            "material": "pozinkovaná",
            "quantity": 1,
        }
    )
    mock_response.candidates = []
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    parsed = parse_inquiry_line(
        "skrutka M10x50 DIN933 8.8 pozinkovaná",
        row_index=1,
        model=mock_model,
    )
    assert parsed.parse_error is None
    assert parsed.class_ == "8.8"
    assert parsed.is_valid
