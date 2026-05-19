"""Test lazy image filter options endpoint helper."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.api.routes import _build_image_filter_options
from app.models.entities import Product
from app.schemas.common import ProductSearchFilters


def _session_with_products() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        Product(
            internal_code="A1",
            norma="DIN 933",
            image_filename="hex.png",
        )
    )
    session.add(
        Product(
            internal_code="A2",
            norma="DIN 933",
            image_filename="hex.png",
        )
    )
    session.add(
        Product(
            internal_code="B1",
            norma="DIN 912",
            image_filename="imbus.png",
        )
    )
    session.add(
        Product(
            internal_code="C1",
            norma="DIN 933",
            image_filename=None,
        )
    )
    session.commit()
    return session


def test_image_filter_options_respects_norma_cascade() -> None:
    session = _session_with_products()
    try:
        all_imgs = _build_image_filter_options(session, ProductSearchFilters())
        assert {x["filename"] for x in all_imgs} == {"hex.png", "imbus.png"}

        din933 = _build_image_filter_options(
            session, ProductSearchFilters(norma="DIN 933")
        )
        assert len(din933) == 1
        assert din933[0]["filename"] == "hex.png"
        assert din933[0]["count"] == 2
    finally:
        session.close()


def test_search_filters_include_image_filename_field() -> None:
    f = ProductSearchFilters(image_filename="hex.png")
    assert f.image_filename == "hex.png"
