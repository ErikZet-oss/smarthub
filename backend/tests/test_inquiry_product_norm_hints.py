from __future__ import annotations

from app.services.inquiry.norm_rules import norm_requires_length
from app.services.inquiry.parser import _heuristic_parse, parse_inquiry_line
from app.services.inquiry.product_norm_hints import infer_norma_from_text


def test_infer_zavitova_tyc_din976() -> None:
    assert infer_norma_from_text("Závitová tyč M10x1000 pozinkovaná") == "DIN976"
    assert infer_norma_from_text("zavitova tyc M12x1000") == "DIN976"


def test_explicit_din_not_overridden() -> None:
    assert infer_norma_from_text("Závitová tyč DIN 975 M10x1000") is None


def test_heuristic_zavitova_tyc() -> None:
    ai = _heuristic_parse("Závitová tyč M10x1000 4.8 pozinkovaná")
    assert ai is not None
    assert ai.norma == "DIN976"
    assert ai.diameter == "10"
    assert ai.length == "1000"


def test_zavitova_tyc_requires_length() -> None:
    assert norm_requires_length("DIN976", "Závitová tyč M10x1000") is True
    assert norm_requires_length(None, "Závitová tyč M10x1000") is True


def test_parse_zavitova_tyc_fallback(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parsed = parse_inquiry_line("Závitová tyč M10x1000 pozinkovaná", row_index=1)
    assert parsed.parse_error is None
    assert parsed.norma == "DIN976"
    assert parsed.length == "1000"
