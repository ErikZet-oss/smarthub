from __future__ import annotations

import pytest

from app.services.inquiry.normalize import apply_normalization
from app.services.inquiry.parser import _heuristic_parse, parse_inquiry_line
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.stn_to_din import (
    extract_stn_base,
    map_standard_to_catalog_din,
    normalize_stn_base,
    stn_base_to_din,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("STN 02 1103", "1103"),
        ("STN 02 1401.55", "1401"),
        ("STN021401", "1401"),
        ("Norma : STN 02 1814;", "1814"),
        ("ČSN 02 1702", "1702"),
    ],
)
def test_extract_stn_base(raw: str, expected: str) -> None:
    assert extract_stn_base(raw) == expected
    assert normalize_stn_base(expected) == expected


@pytest.mark.parametrize(
    ("text", "din"),
    [
        ("SKRUTKA M10X100 STN 02 1103 A4", "DIN933"),
        ("MATICA M 12 STN 02 1401", "DIN934"),
        ("SKRUTKA IMBUSOVA M 8X45 STN 02 1143", "DIN912"),
        ("PODLOZKA 10 A2 STN 02 1702", "DIN125"),
        ("DREVOSKRUTKA M 4X50 STN 02 1814", "DIN97"),
        ("CAP S HLAVOU ISO 4017 B A2", "DIN933"),
    ],
)
def test_map_standard_to_catalog_din(text: str, din: str) -> None:
    assert map_standard_to_catalog_din(None, text) == din


def test_stn_base_to_din_known() -> None:
    assert stn_base_to_din("1103") == "DIN933"
    assert stn_base_to_din("9999") is None


def test_heuristic_parse_stn_skrutka() -> None:
    ai = _heuristic_parse("SKRUTKA M10X100 STN 02 1103 A4")
    assert ai is not None
    assert ai.norma == "DIN933"
    assert ai.diameter == "10"
    assert ai.length == "100"


def test_heuristic_parse_stn_podlozka() -> None:
    ai = _heuristic_parse("PODLOZKA 10 A2 STN 02 1702")
    assert ai is not None
    assert ai.norma == "DIN125"


def test_parse_inquiry_line_stn_heuristic(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parsed = parse_inquiry_line("MATICA M 16 STN 02 1401 A2", row_index=1)
    assert parsed.parse_error is None
    assert parsed.norma == "DIN934"
    assert parsed.diameter == "16"
    assert parsed.surface == "Nerez A2"


def test_apply_normalization_remaps_stn_in_norma_field() -> None:
    row = InquiryLineParsed(
        row_index=1,
        raw_text="SKRUTKA M8X40",
        norma="STN 02 1103",
        diameter="8",
        length="40",
        surface="Oceľ",
        v_class="8.8",
        quantity=1,
    )
    out = apply_normalization(row)
    assert out.norma == "DIN933"
