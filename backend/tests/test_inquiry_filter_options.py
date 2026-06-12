from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.api.routes import _build_conditional_filter_options
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import (
    CatalogSnapCache,
    prepare_inquiry_catalog_filters,
    resolve_catalog_norma,
    snap_inquiry_line_to_catalog,
)


@pytest.fixture
def session():
    db = Path(__file__).resolve().parents[1] / "procurement.db"
    if not db.is_file():
        pytest.skip("procurement.db not available")
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_prepare_filters_resolves_din471_and_drops_length(session) -> None:
    known = CatalogSnapCache.load(session).norma_values
    raw = ProductSearchFilters(norma="DIN471", diameter="12", length="0", v_class="0")
    prepared = prepare_inquiry_catalog_filters(raw, known_norma=known)
    assert prepared.norma == "471"
    assert prepared.length is None
    assert prepared.v_class is None


def test_resolve_catalog_norma_din471_without_known_match() -> None:
    assert resolve_catalog_norma("DIN471", known=["934", "976"]) == "471"


def test_din471_conditional_surfaces(session) -> None:
    known = CatalogSnapCache.load(session).norma_values
    raw = ProductSearchFilters(norma="DIN471", diameter="12", length="0")
    prepared = prepare_inquiry_catalog_filters(raw, known_norma=known)
    opts = _build_conditional_filter_options(session, prepared)
    assert "Oceľ čierna" in opts["surface"]
    assert "internal_code" in opts


def test_pin_filter_internal_codes(session) -> None:
    known = CatalogSnapCache.load(session).norma_values
    raw = ProductSearchFilters(
        norma="6325 M6",
        surface="Oceľ čierna",
        diameter="3",
        length="30",
        v_class="M6",
    )
    prepared = prepare_inquiry_catalog_filters(raw, known_norma=known)
    opts = _build_conditional_filter_options(session, prepared)
    assert len(opts.get("internal_code", [])) >= 1


def test_snap_ring_din471_to_catalog(session) -> None:
    row = InquiryLineParsed(
        row_index=100,
        raw_text="KRUZOK POISTNY 12 STN 02 2930 — 12; Norma : STN 02 2930;",
        norma="DIN471",
        diameter="12",
        length="0",
        quantity=60,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.norma == "471"
    assert snapped.length == "0"
    assert snapped.v_class is None


def test_snap_ring_din471_pruzinova_snap_to_catalog(session) -> None:
    raw = "Poistný hriadeľový krúžok - normálny typ DIN 471 Pružinová oceľ 36MM"
    row = InquiryLineParsed(
        row_index=4,
        raw_text=raw,
        norma="471",
        surface="Oceľ čierna",
        diameter="36",
        length="0",
        quantity=10,
    )
    snapped = snap_inquiry_line_to_catalog(session, row)
    assert snapped.norma == "471"
    assert snapped.surface == "Oceľ čierna"
    assert snapped.diameter == "36"
    assert snapped.v_class is None
    assert snapped.internal_code == "311930000036"
    assert snapped.catalog_warnings is None


def test_din471_catalog_mismatch_ignores_length_zero(session) -> None:
    from app.services.inquiry.catalog_snap import CatalogSnapCache, _catalog_mismatch_warnings

    raw = "Poistný hriadeľový krúžok - normálny typ DIN 471 Pružinová oceľ 19MM"
    row = InquiryLineParsed(
        row_index=3,
        raw_text=raw,
        norma="471",
        surface="Oceľ čierna",
        diameter="19",
        length="0",
        v_class="0",
        quantity=10,
    )
    cache = CatalogSnapCache.load(session)
    assert _catalog_mismatch_warnings(session, row, cache) == []
