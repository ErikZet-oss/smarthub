from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import (
    inquiry_required_field_names,
    norm_requires_length,
)
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
    assert "v_class" in props
    assert "norma" in props
    assert "surface" in props


def test_norm_requires_length_matica() -> None:
    assert norm_requires_length("DIN934", "Šesťhranná matica DIN 934 M3") is False
    assert norm_requires_length("DIN933", "skrutka M10x50 DIN933") is True


def test_inquiry_required_fields_matica() -> None:
    fields = inquiry_required_field_names("DIN934", "matica M3")
    assert "length" not in fields
    assert "v_class" not in fields
    assert "norma" in fields
    assert "diameter" in fields


def test_matica_parsed_not_missing_length() -> None:
    parsed = InquiryLineParsed(
        row_index=1,
        raw_text="Šesťhranná matica DIN 934 Oceľ Pozinkované M3",
        norma="DIN934",
        diameter="M3",
        surface="Oceľ pozinkovaná",
        length="0",
        quantity=1,
    )
    assert "length" not in parsed.missing_required_fields()


def test_heuristic_parse_matica() -> None:
    ai = _heuristic_parse("Šesťhranná matica DIN 934 Oceľ Pozinkované M3")
    assert ai is not None
    assert ai.diameter == "M3"
    assert ai.norma == "DIN934"
    assert ai.surface == "Oceľ pozinkovaná"
    assert ai.length is None


def test_heuristic_parse_skrutka() -> None:
    ai = _heuristic_parse("skrutka M10x50 DIN933 8.8 pozinkovaná")
    assert ai is not None
    assert ai.diameter == "M10"
    assert ai.length == "50"
    assert ai.norma == "DIN933"
    assert ai.v_class == "8.8"


def test_parse_inquiry_line_heuristic_fallback(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parsed = parse_inquiry_line("Šesťhranná matica DIN 934 Nerezoceľ A2 M4", row_index=1)
    assert parsed.parse_error is None
    assert parsed.diameter == "M4"
    assert parsed.norma == "DIN934"
    assert parsed.surface == "Nerez A2"
    assert "length" not in parsed.missing_required_fields()


def test_parse_inquiry_line_with_mock_model() -> None:
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "diameter": "M10",
            "length": "50",
            "norma": "DIN933",
            "v_class": "8.8",
            "surface": "Oceľ pozinkovaná",
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
    assert parsed.v_class == "8.8"
    assert parsed.is_valid
