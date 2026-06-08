from __future__ import annotations

from unittest.mock import MagicMock

from app.schemas.inquiry import InquiryLineAIOutput
from app.services.inquiry.parser import parse_inquiry_line


def test_parse_inquiry_line_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parsed = parse_inquiry_line("skrutka M10x50 DIN933 8.8", row_index=1)
    assert parsed.parse_error
    assert "GEMINI" in parsed.parse_error.upper()


def test_parse_inquiry_line_with_mock_model() -> None:
    ai_json = InquiryLineAIOutput(
        diameter="M10",
        length="50",
        norm="DIN933",
        class_="8.8",
        material="pozinkovaná",
        quantity=1,
    )
    mock_response = MagicMock()
    mock_response.text = ai_json.model_dump_json(by_alias=True)
    mock_response.candidates = []

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    parsed = parse_inquiry_line(
        "skrutka M10x50 DIN933 8.8 pozinkovaná",
        row_index=1,
        model=mock_model,
    )
    assert parsed.parse_error is None
    assert parsed.diameter == "M10"
    assert parsed.length == "50"
    assert parsed.norm == "DIN933"
    assert parsed.class_ == "8.8"
    assert parsed.quantity == 1
    assert parsed.is_valid
