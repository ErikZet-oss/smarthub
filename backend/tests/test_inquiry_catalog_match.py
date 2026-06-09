from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.models.entities import Product, ProductMapping, Supplier
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_match import find_catalog_products


@pytest.fixture
def session():
    db = Path(__file__).resolve().parents[1] / "procurement.db"
    if not db.is_file():
        pytest.skip("procurement.db not available")
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_find_pozink_washer_maps_m3_to_inner_diameter(session) -> None:
    row = InquiryLineParsed(
        row_index=36,
        raw_text="Plochá podložka DIN 125-1A Oceľ Pozinkované 140 HV M3",
        norma="DIN125",
        diameter="3",
        surface="Oceľ pozinkovaná",
        quantity=200,
    )
    products = find_catalog_products(session, row, limit=3)
    assert products
    assert products[0].internal_code == "311702150032"
    assert products[0].diameter == "3.2"


def test_find_pozink_washer_prefers_product_with_supplier_mapping(session) -> None:
    row = InquiryLineParsed(
        row_index=36,
        raw_text="Plochá podložka DIN 125-1A Oceľ Pozinkované 140 HV M3",
        norma="125a",
        diameter="3.2",
        surface="Oceľ pozinkovaná",
        v_class="0",
        length="0",
        quantity=200,
    )
    mekrs = session.exec(select(Supplier).where(Supplier.name == "Mekrs")).first()
    if mekrs is None or mekrs.id is None:
        pytest.skip("Mekrs supplier missing")
    products = find_catalog_products(session, row, limit=5, supplier_ids=[int(mekrs.id)])
    assert products
    assert products[0].internal_code == "311702150032"
    maps = session.exec(
        select(ProductMapping).where(
            ProductMapping.product_id == products[0].id,
            ProductMapping.supplier_id == mekrs.id,
        )
    ).all()
    assert maps
