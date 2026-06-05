from __future__ import annotations

import json

from app.services.excel_importer import (
    _profile_unique_column_indices,
    profile_excel_columns,
    resolve_gamechanger_xlsx_path,
)


def test_profile_unique_columns_excludes_supplier_codes() -> None:
    headers = [
        "Material Number",
        "Leading standard",
        "Fabory kód",
        "Mekrs kód",
        "W",
    ]
    indices = _profile_unique_column_indices(headers)
    assert headers.index("Leading standard") in indices
    assert headers.index("W") in indices
    assert headers.index("Material Number") not in indices
    assert headers.index("Fabory kód") not in indices


def test_profile_excel_columns_response_is_small() -> None:
    path = str(resolve_gamechanger_xlsx_path(""))
    result = profile_excel_columns(path, "DIN")
    assert len(result.columns) > 50
    assert "Leading standard" in result.unique_values
    assert "Material Number" not in result.unique_values
    assert "Fabory kód" not in result.unique_values
    assert "DIN 6914" in result.unique_values["Leading standard"]

    payload = {
        "sheet": result.sheet,
        "columns": result.columns,
        "preview_rows": result.preview_rows,
        "unique_values": result.unique_values,
    }
    size_mb = len(json.dumps(payload, ensure_ascii=False)) / (1024 * 1024)
    assert size_mb < 1.5
