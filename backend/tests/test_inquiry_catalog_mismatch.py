from __future__ import annotations

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.catalog_snap import _catalog_mismatch_warnings, CatalogSnapCache


def test_catalog_mismatch_diameter_not_in_options() -> None:
    cache = CatalogSnapCache(norma_values=["934"])
    cache._filter_opts["934|Polyamid|||0"] = {
        "norma": ["934"],
        "surface": ["Polyamid", "Oceľ pozinkovaná"],
        "diameter": ["4", "5", "6"],
        "length": ["0"],
        "v_class": ["0"],
    }

    class FakeSession:
        pass

    row = InquiryLineParsed(
        row_index=1,
        raw_text="matica polyamid M3",
        norma="934",
        surface="Polyamid",
        diameter="3",
        length="0",
        v_class="0",
        quantity=1,
    )

    original_filter = cache.filter_options

    def fake_filter(session, filters):  # noqa: ARG001
        return cache._filter_opts["934|Polyamid|||0"]

    cache.filter_options = fake_filter  # type: ignore[method-assign]
    warnings = _catalog_mismatch_warnings(FakeSession(), row, cache)
    assert any("Priemer" in w and "3" in w for w in warnings)
