from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import (
    infer_v_class_from_surface,
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
