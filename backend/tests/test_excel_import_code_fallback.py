from __future__ import annotations

from app.services.excel_importer import (
    _material_number_col_indices,
    _resolve_row_internal_code,
)


def test_resolve_row_internal_code_prefers_smart_number() -> None:
    row = ("04025.120.001", "20300.040.001", "short")
    code = _resolve_row_internal_code(
        row,
        primary_idx=1,
        smart_code_idx=1,
        money_catalog_idx=2,
        material_number_indices=[0],
    )
    assert code == "20300.040.001"


def test_resolve_row_internal_code_falls_back_to_material_number() -> None:
    row = ("04025.120.001", "", "")
    code = _resolve_row_internal_code(
        row,
        primary_idx=1,
        smart_code_idx=1,
        money_catalog_idx=2,
        material_number_indices=[0],
    )
    assert code == "04025.120.001"


def test_material_number_col_indices_prefers_without_dots() -> None:
    headers = ["Material Number", "Material Number (without dots)", "EAN code"]
    indices = _material_number_col_indices(headers)
    assert indices == [1, 0]
