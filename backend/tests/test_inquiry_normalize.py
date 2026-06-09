from __future__ import annotations

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.normalize import (
    norm_display_candidates,
    normalize_diameter,
    normalize_length_mm,
    search_key,
)


def test_search_key_strips_spaces_and_dashes() -> None:
    assert search_key("DIN 933") == "DIN933"
    assert search_key("din-933") == "DIN933"


def test_norm_display_candidates_variants() -> None:
    parsed = InquiryLineParsed(row_index=1, raw_text="x", norma="DIN933")
    cands = norm_display_candidates(parsed)
    assert "DIN933" in cands
    assert "DIN 933" in cands
    assert "933" in cands


def test_normalize_diameter_strips_m_prefix() -> None:
    assert normalize_diameter("M10") == "10"
    assert normalize_diameter("10") == "10"


def test_normalize_length_mm() -> None:
    assert normalize_length_mm("50 mm") == "50"
    assert normalize_length_mm("120") == "120"
