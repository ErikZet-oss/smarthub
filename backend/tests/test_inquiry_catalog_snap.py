from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import (
    infer_v_class_for_row,
    infer_v_class_from_surface,
    resolve_catalog_norma,
    resolve_washer_inner_diameter,
    snap_inquiry_line_to_catalog,
    snap_value_to_options,
)


@pytest.fixture
def session():
    db = Path(__file__).resolve().parents[1] / "procurement.db"
    if not db.is_file():
        pytest.skip("procurement.db not available")
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_infer_v_class_matica_pozink() -> None:
    assert infer_v_class_from_surface("Oceľ pozinkovaná") == "8.8"


def test_infer_v_class_nerez_a2() -> None:
    assert infer_v_class_from_surface("Nerez A2") == "A2-70"


def test_snap_value_diameter_m_prefix() -> None:
    assert snap_value_to_options("M10", ["10", "12"]) == "10"


def test_resolve_catalog_norma_din125_to_125a() -> None:
    known = ["125a", "934", "976"]
    assert resolve_catalog_norma("DIN125", known=known) == "125a"
    assert resolve_catalog_norma("DIN 125-1A", known=known) == "125a"


def test_resolve_washer_inner_diameter_m3() -> None:
    opts = ["2.2", "2.7", "3.2", "4.3"]
    assert resolve_washer_inner_diameter("3", opts) == "3.2"
    assert resolve_washer_inner_diameter("M3", opts) == "3.2"


def test_snap_inquiry_matica_to_catalog(session) -> None:
    row = InquiryLineParsed(
        row_index=1,
        raw_text="Šesťhranná matica DIN 934 Oceľ Pozinkované M3",
        norma="DIN934",
        diameter="M3",
        surface="Oceľ pozinkovaná",
        quantity=1,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.norma == "934"
    assert snapped.diameter == "3"
    assert snapped.length == "0"
    assert snapped.v_class in ("8.8", "10.9")


def test_infer_v_class_nerez_a2_washer() -> None:
    assert infer_v_class_for_row(
        norma="125a",
        surface="Nerez A2",
        raw_text="Plochá podložka DIN 125-1A Nerezoceľ A2 140 HV M4",
    ) == "A2-50"


def test_infer_v_class_pozink_washer() -> None:
    assert infer_v_class_for_row(
        norma="125a",
        surface="Oceľ pozinkovaná",
        raw_text="Plochá podložka DIN 125-1A Oceľ pozinkovaná 140 HV M4",
    ) == "0"


def test_snap_inquiry_nerez_washer_din125(session) -> None:
    row = InquiryLineParsed(
        row_index=44,
        raw_text="Plochá podložka DIN 125-1A Nerezoceľ A2 140 HV M4",
        norma="DIN125",
        diameter="4",
        surface="Nerez A2",
        quantity=200,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.norma == "125a"
    assert snapped.diameter == "4.3"
    assert snapped.surface == "Nerez A2"
    assert snapped.v_class == "A2-50"
    assert not snapped.catalog_warnings


def test_snap_inquiry_polyamid_washer_din125(session) -> None:
    row = InquiryLineParsed(
        row_index=1,
        raw_text="Plochá podložka DIN 125-1A Plast Polyamid (nylon) 6.6 M3",
        norma="DIN125",
        diameter="3",
        surface="Polyamid",
        v_class="0",
        quantity=200,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.norma == "125a"
    assert snapped.diameter == "3.2"
    assert snapped.surface == "Polyamid"
    assert snapped.v_class == "0"
    assert not snapped.catalog_warnings
