from __future__ import annotations

import csv
import io
from pathlib import Path

from openpyxl import load_workbook

from app.schemas.inquiry import InquiryInputRow

MAX_INQUIRY_ROWS = 500


def read_inquiry_rows_from_bytes(
    data: bytes,
    *,
    filename: str,
) -> list[InquiryInputRow]:
    name = (filename or "").strip().lower()
    if name.endswith(".csv"):
        return _read_csv(data)
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return _read_xlsx(data)
    raise ValueError("Podporované formáty: .xlsx, .xlsm, .csv")


def read_inquiry_rows_from_path(path: str | Path) -> list[InquiryInputRow]:
    p = Path(path)
    return read_inquiry_rows_from_bytes(p.read_bytes(), filename=p.name)


def _read_csv(data: bytes) -> list[InquiryInputRow]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return _rows_from_matrix(list(reader))


def _read_xlsx(data: bytes) -> list[InquiryInputRow]:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        wb = load_workbook(tmp.name, read_only=True, data_only=True)
        try:
            ws = wb.active
            if ws is None:
                return []
            matrix: list[list[str]] = []
            for row in ws.iter_rows(values_only=True):
                matrix.append(["" if c is None else str(c).strip() for c in row])
        finally:
            wb.close()
    return _rows_from_matrix(matrix)


def _rows_from_matrix(matrix: list[list[str]]) -> list[InquiryInputRow]:
    if not matrix:
        return []

    col_idx = _pick_text_column(matrix)
    out: list[InquiryInputRow] = []
    start_row = 1 if _looks_like_header(matrix[0]) else 0

    for i, row in enumerate(matrix[start_row:], start=start_row + 1):
        if len(out) >= MAX_INQUIRY_ROWS:
            break
        text = row[col_idx].strip() if col_idx < len(row) else ""
        if not text:
            continue
        qty = _parse_quantity_from_row(row, skip_col=col_idx)
        out.append(
            InquiryInputRow(
                row_index=len(out) + 1,
                raw_text=text,
                quantity_hint=qty,
            )
        )
    return out


def _pick_text_column(matrix: list[list[str]]) -> int:
    """Stĺpec s najdlhšími textami (typicky popis položky)."""
    if not matrix:
        return 0
    max_cols = max(len(r) for r in matrix)
    best_idx = 0
    best_score = -1.0
    for col in range(max_cols):
        texts = [r[col].strip() for r in matrix if col < len(r) and r[col].strip()]
        if not texts:
            continue
        avg_len = sum(len(t) for t in texts) / len(texts)
        if avg_len > best_score:
            best_score = avg_len
            best_idx = col
    return best_idx


def _looks_like_header(row: list[str]) -> bool:
    joined = " ".join(c.casefold() for c in row if c)
    hints = ("popis", "položka", "polozka", "text", "dopyt", "názov", "nazov", "item")
    return any(h in joined for h in hints)


def _parse_quantity_from_row(row: list[str], *, skip_col: int) -> int | None:
    for idx, cell in enumerate(row):
        if idx == skip_col:
            continue
        s = cell.strip()
        if not s:
            continue
        if s.isdigit():
            q = int(s)
            if 0 < q < 1_000_000:
                return q
    return None
