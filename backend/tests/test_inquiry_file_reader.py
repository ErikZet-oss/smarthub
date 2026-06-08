from __future__ import annotations

from app.services.inquiry.file_reader import read_inquiry_rows_from_bytes


def test_read_csv_single_column() -> None:
    data = b"popis\nskrutka M10x50 DIN933 8.8\nmatice M8\n"
    rows = read_inquiry_rows_from_bytes(data, filename="dopyt.csv")
    assert len(rows) == 2
    assert "M10x50" in rows[0].raw_text


def test_read_csv_with_quantity_column() -> None:
    data = b"ks,text\n10,skrutka M8x40 DIN933\n"
    rows = read_inquiry_rows_from_bytes(data, filename="dopyt.csv")
    assert len(rows) == 1
    assert rows[0].quantity_hint == 10
