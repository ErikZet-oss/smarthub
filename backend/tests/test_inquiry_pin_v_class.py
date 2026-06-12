from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import snap_inquiry_line_to_catalog, snap_value_to_options
from app.services.inquiry.norm_rules import is_pin_norm, norm_requires_v_class
from app.services.inquiry.parser import parse_inquiry_line


@pytest.fixture
def session():
    db = Path(__file__).resolve().parents[1] / "procurement.db"
    if not db.is_file():
        pytest.skip("procurement.db not available")
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_norm_requires_v_class_false_for_din6325_pin() -> None:
    raw = "Valcový kolík kalený DIN 6325 Oceľ 60±2HRC 3X16MM"
    assert is_pin_norm("6325", raw)
    assert norm_requires_v_class("6325", raw) is False


def test_parse_pin_does_not_take_hrc_as_v_class() -> None:
    raw = "Valcový kolík (čapový kolík) kalený, tolerancia m6 DIN 6325 Oceľ 60±2HRC Nelegovaná 3X16MM"
    parsed = parse_inquiry_line(raw, row_index=13)
    assert parsed.norma in ("6325", "DIN6325")
    assert parsed.v_class in (None, "")


def test_snap_pin_clears_invalid_v_class(session) -> None:
    row = InquiryLineParsed(
        row_index=13,
        raw_text="Valcový kolík DIN 6325 Oceľ 60±2HRC 3X16MM",
        norma="6325",
        surface="Oceľ",
        diameter="3",
        length="16",
        v_class="60",
        quantity=10,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.v_class in (None, "")


def test_snap_value_drops_unknown_v_class() -> None:
    assert snap_value_to_options("60", ["8.8", "10.9"]) is None
    assert snap_value_to_options("60", []) is None
    assert snap_value_to_options("8.8", ["8.8", "10.9"]) == "8.8"
