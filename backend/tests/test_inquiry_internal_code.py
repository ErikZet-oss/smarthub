from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import (
    CatalogSnapCache,
    _lookup_unique_internal_code,
    enrich_inquiry_rows_internal_codes,
)
from app.services.inquiry.norm_rules import norm_requires_length


@pytest.fixture
def session():
    db = Path(__file__).resolve().parents[1] / "procurement.db"
    if not db.is_file():
        pytest.skip("procurement.db not available")
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_din912_requires_length() -> None:
    assert norm_requires_length("912") is True
    assert norm_requires_length("DIN912") is True


def test_lookup_internal_code_din912(session: Session) -> None:
    row = InquiryLineParsed(
        row_index=5,
        raw_text="skrutka",
        norma="912",
        surface="Oceľ pozinkovaná",
        diameter="5",
        length="16",
        v_class="8.8",
        quantity=10,
    )
    cache = CatalogSnapCache.load(session)
    assert _lookup_unique_internal_code(session, row, cache) == "309543505016"


def test_lookup_internal_code_din471(session: Session) -> None:
    row = InquiryLineParsed(
        row_index=3,
        raw_text="Poistný hriadeľový krúžok DIN 471",
        norma="471",
        surface="Oceľ čierna",
        diameter="19",
        length="0",
        quantity=10,
    )
    cache = CatalogSnapCache.load(session)
    assert _lookup_unique_internal_code(session, row, cache) == "311930000019"


def test_enrich_rows_fills_missing_codes(session: Session) -> None:
    rows = [
        InquiryLineParsed(
            row_index=3,
            raw_text="x",
            norma="471",
            surface="Oceľ čierna",
            diameter="19",
            length="0",
            quantity=10,
        ),
        InquiryLineParsed(
            row_index=5,
            raw_text="y",
            norma="912",
            surface="Oceľ pozinkovaná",
            diameter="5",
            length="16",
            v_class="8.8",
            quantity=10,
        ),
    ]
    enriched = enrich_inquiry_rows_internal_codes(session, rows)
    assert enriched[0].internal_code == "311930000019"
    assert enriched[1].internal_code == "309543505016"
