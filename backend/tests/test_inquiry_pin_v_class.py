from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import snap_inquiry_line_to_catalog, snap_value_to_options
from app.services.inquiry.norm_rules import (
    extract_pin_tolerance_fit,
    is_pin_norm,
    norm_requires_v_class,
)
from app.services.inquiry.parser import parse_inquiry_line


@pytest.fixture
def session():
    db = Path(__file__).resolve().parents[1] / "procurement.db"
    if not db.is_file():
        pytest.skip("procurement.db not available")
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_extract_pin_tolerance_m6() -> None:
    raw = "Valcový kolík kalený, tolerancia m6 DIN 6325 Oceľ 60±2HRC 3x30MM"
    assert extract_pin_tolerance_fit(raw) == "M6"


def test_norm_requires_v_class_true_for_din6325_pin() -> None:
    raw = "Valcový kolík kalený DIN 6325 Oceľ 60±2HRC 3X16MM"
    assert is_pin_norm("6325", raw)
    assert norm_requires_v_class("6325", raw) is True


def test_parse_pin_extracts_m6_and_dimensions() -> None:
    raw = "Valcový kolík (čapový kolík) kalený, tolerancia m6 DIN 6325 Oceľ 60±2HRC Nelegovaná 3x30MM"
    parsed = parse_inquiry_line(raw, row_index=13)
    assert parsed.norma == "6325"
    assert parsed.diameter == "3"
    assert parsed.length == "30"
    assert parsed.v_class == "M6"
    assert parsed.surface


def test_parse_pin_d7979() -> None:
    raw = "Valcový kolík kalený s vnútorným závitom DIN 7979 D Oceľ 60±2HRC Nelegovaná 16X60MM"
    parsed = parse_inquiry_line(raw, row_index=23)
    assert parsed.norma == "7979 D"
    assert parsed.diameter == "16"
    assert parsed.length == "60"


def test_snap_pin_6325_m6_to_catalog(session) -> None:
    raw = "Valcový kolík (čapový kolík) kalený, tolerancia m6 DIN 6325 Oceľ 60±2HRC Nelegovaná 3x30MM"
    parsed = parse_inquiry_line(raw, row_index=13)
    snapped = snap_inquiry_line_to_catalog(session, parsed)
    assert snapped.norma == "DIN 6325 M6"
    assert snapped.v_class == "M6"
    assert snapped.diameter == "3"
    assert snapped.length == "30"


def test_snap_pin_keeps_values_when_catalog_empty(session) -> None:
    row = InquiryLineParsed(
        row_index=13,
        raw_text="Valcový kolík DIN 6325 Oceľ 60±2HRC 3x30MM",
        norma="6325",
        surface="Oceľ",
        diameter="3",
        length="30",
        v_class="60",
        quantity=10,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.norma == "6325"
    assert snapped.diameter == "3"
    assert snapped.length == "30"
    assert snapped.v_class in (None, "")


def test_snap_value_drops_unknown_v_class() -> None:
    assert snap_value_to_options("60", ["8.8", "10.9"]) is None
    assert snap_value_to_options("6325", []) == "6325"
    assert snap_value_to_options("M6", ["M6", "H6"]) == "M6"
