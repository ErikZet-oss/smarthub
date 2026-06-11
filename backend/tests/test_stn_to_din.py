from __future__ import annotations

import pytest

from app.services.inquiry.normalize import apply_normalization
from app.services.inquiry.parser import _heuristic_parse, parse_inquiry_line
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.stn_suffix import decode_stn_suffix, extract_stn_suffix, infer_material_from_stn_text
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
        ("KLINEC 4,0X120 STN 02 2825", "DIN1151"),
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


def test_extract_stn_suffix() -> None:
    m = extract_stn_suffix("MATICA M 12 STN 02 1401.55")
    assert m is not None
    assert m.base == "1401"
    assert m.suffix == "55"


@pytest.mark.parametrize(
    ("text", "surface", "v_class"),
    [
        ("MATICA M 12 STN 02 1401.55", "Oceľ pozinkovaná", "8.8"),
        ("MATICA M 10 STN 02 1401.05", "Oceľ pozinkovaná", "5.8"),
        ("MATICA M 12 STN 02 1401.52", "Oceľ", "8.8"),
        ("MATICA M 8 A2 STN 02 1401.90", "Nerez A2", "A2-70"),
        ("MATICA M 4 STN 02 1401.8", "Mosadz", "0"),
        ("PODLOZKA 10 STN 02 1702.15", "Oceľ pozinkovaná", "0"),
    ],
)
def test_decode_stn_suffix_material(text: str, surface: str, v_class: str) -> None:
    hint = infer_material_from_stn_text(text)
    assert hint is not None
    assert hint.surface == surface
    assert hint.v_class == v_class


def test_parse_stn_1401_55_fills_surface_and_class(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    raw = "MATICA MATICA M 12 STN 02 1401.55  M 12; Norma : STN 02 1401.55;"
    parsed = parse_inquiry_line(raw, row_index=149)
    assert parsed.parse_error is None
    assert parsed.norma == "DIN934"
    assert parsed.diameter == "12"
    assert parsed.surface == "Oceľ pozinkovaná"
    assert parsed.v_class == "8.8"
    assert parsed.length == "0"


def test_heuristic_parse_stn_klinec(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    raw = "KLINEC 4,0X120 STN 02 2825 — 4,0X120; Norma : STN 02 2825;"
    parsed = parse_inquiry_line(raw, row_index=52)
    assert parsed.parse_error is None
    assert parsed.norma == "DIN1151"
    assert parsed.diameter == "4.0"
    assert parsed.length == "120"


def test_heuristic_parse_snap_ring_din471() -> None:
    from app.services.inquiry.norm_rules import inquiry_required_field_names, norm_requires_v_class

    raw = "KRUZOK POISTNY 10 STN 02 2930 - 10; Norma : STN 02 2930;"
    ai = _heuristic_parse(raw)
    assert ai is not None
    assert ai.norma == "DIN471"
    assert ai.diameter == "10"
    assert ai.v_class is None
    assert ai.length is None
    assert "v_class" not in inquiry_required_field_names("DIN471", raw)
    assert norm_requires_v_class("DIN471", raw) is False


def test_heuristic_parse_snap_ring_d100() -> None:
    raw = "KRUZOK POISTNY D 100 CSN 02 2930 — D1 = 100; D3 = 94,5;"
    ai = _heuristic_parse(raw)
    assert ai is not None
    assert ai.norma == "DIN471"
    assert ai.diameter == "100"
    assert ai.v_class is None
