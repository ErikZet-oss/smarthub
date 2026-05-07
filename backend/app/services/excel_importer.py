from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from openpyxl import load_workbook
from sqlmodel import Session, select

from app.models.entities import FieldMapping, Product, ProductMapping, Supplier

FIELD_DEFAULTS: dict[str, str] = {
    "code": "číslo Smart",
    "norma": "STN",
    "surface": "Surface treatments (long)",
    "diameter": "Diameter [M/Tr]",
    "length": "Length [mm]",
    # Excel: stĺpec V → hlavička „Class“, stĺpec Y → „Money názov“
    "v_class": "Class",
    "y_money_name": "Money názov",
    # Používateľ môže mapovať aj priamo písmenom stĺpca (W).
    "image_filename": "W",
}


def _field_column_name(field_key: str, fm: FieldMapping | None) -> str | None:
    attr_map = {
        "code": "code_column",
        "norma": "norma_column",
        "surface": "surface_column",
        "diameter": "diameter_column",
        "length": "length_column",
        "v_class": "v_class_column",
        "y_money_name": "y_money_name_column",
        "image_filename": "image_filename_column",
    }
    if fm:
        raw = getattr(fm, attr_map[field_key], None)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return FIELD_DEFAULTS.get(field_key)


@dataclass
class ImportResult:
    products_upserted: int = 0
    suppliers_upserted: int = 0
    mappings_upserted: int = 0
    rows_scanned: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class ColumnProfileResult:
    sheet: str
    columns: list[str]
    preview_rows: list[dict[str, str]]
    unique_values: dict[str, list[str]]


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalized_sheet_headers(first_row: Any) -> list[str]:
    """Rovnaká logika ako pri profile_excel_columns — náhľad a import musia vidieť rovnaké názvy stĺpcov."""
    cells = list(first_row) if first_row is not None else []
    headers = [_normalize(value) for value in cells]
    return [h if h else f"Column {idx + 1}" for idx, h in enumerate(headers)]


def _resolve_header_col_index(headers: list[str], mapped_name: str | None) -> int | None:
    """Nájde stĺpec podľa mapovania — presná zhoda, potom bez rozlišovania veľkosti písmen (ako vo fronte)."""
    if not mapped_name:
        return None
    name = str(mapped_name).strip()
    if not name:
        return None
    for idx, h in enumerate(headers):
        if h == name:
            return idx
    lower = name.lower()
    for idx, h in enumerate(headers):
        if h.lower() == lower:
            return idx
    # Alternatíva: písmeno stĺpca Excelu (A..Z, AA..), napr. "W".
    if re.fullmatch(r"[A-Za-z]{1,4}", name):
        n = 0
        for ch in name.upper():
            n = n * 26 + (ord(ch) - 64)
        idx = n - 1
        if 0 <= idx < len(headers):
            return idx
    return None


def _to_supplier_name(header: str) -> str:
    # "Fabory kód" -> "Fabory"
    return header.rsplit(" ", 1)[0].strip()


def _allocate_supplier_sort_order(session: Session) -> int:
    rows = session.exec(select(Supplier)).all()
    if not rows:
        return 0
    return max((r.sort_order or 0) for r in rows) + 10


def _set_product_field_if_cell_nonempty(
    product: Product,
    field: str,
    row: Any,
    col_idx: int | None,
) -> None:
    """Pri viacerých riadkoch s rovnakým kódom neprepisuj prázdnej bunkou (posledný riadok inak vymaže Class / Money názov)."""
    if col_idx is None:
        return
    r = tuple(row) if row is not None else ()
    if col_idx >= len(r):
        return
    cell = _normalize(r[col_idx])
    if not cell:
        return
    setattr(product, field, cell)


def _excel_image_basename(value: str) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return raw.split("/")[-1].strip()


def import_gamechanger_excel(
    file_path: str,
    session: Session,
    *,
    sheet_name: str = "DIN",
) -> ImportResult:
    name = (sheet_name or "DIN").strip() or "DIN"
    wb = load_workbook(file_path, read_only=True, data_only=True)
    if name not in wb.sheetnames:
        preview = ", ".join(wb.sheetnames[:30])
        suffix = "…" if len(wb.sheetnames) > 30 else ""
        raise ValueError(
            f"List {name!r} v súbore nie je. Dostupné listy: {preview}{suffix}"
        )

    ws = wb[name]
    rows = ws.iter_rows(min_row=1, values_only=True)
    headers = _normalized_sheet_headers(next(rows))

    fm = session.get(FieldMapping, 1)

    def col_idx(field_key: str) -> int | None:
        col_name = _field_column_name(field_key, fm)
        return _resolve_header_col_index(headers, col_name)

    internal_code_idx = col_idx("code")
    norma_idx = col_idx("norma")
    diameter_idx = col_idx("diameter")
    length_idx = col_idx("length")
    surface_idx = col_idx("surface")
    v_class_idx = col_idx("v_class")
    y_money_name_idx = col_idx("y_money_name")
    image_filename_idx = col_idx("image_filename")

    if internal_code_idx is None:
        expected = _field_column_name("code", fm)
        raise ValueError(
            f"Stĺpec pre interný kód sa nenašiel (očakávaná hlavička: {expected!r})."
        )

    result = ImportResult()
    code_hdr = headers[internal_code_idx]
    ch_low = code_hdr.casefold()
    if "katalóg" in ch_low or "katalog" in ch_low:
        result.warnings.append(
            f"Stĺpec „Kód“ je mapovaný na „{code_hdr}“. Pre riadky ako v Gamechangeri "
            f"mapuj pole Kód na hlavičku „{FIELD_DEFAULTS['code']}“ (dlhé interné číslo), "
            "nie na katalógový stĺpec — inak sa Class / Money názov neprepoja na kód v tabuľke."
        )

    # Stĺpce s kódmi dodávateľov: z DB (code_column) + doplnenie stĺpcov končiacich na " kód".
    supplier_code_columns: list[tuple[int, str]] = []
    seen_indices: set[int] = set()
    suppliers_rows = session.exec(select(Supplier)).all()
    for supplier in suppliers_rows:
        if supplier.code_column:
            col_name = supplier.code_column.strip()
            idx = _resolve_header_col_index(headers, col_name)
            if idx is not None and idx not in seen_indices:
                supplier_code_columns.append((idx, headers[idx]))
                seen_indices.add(idx)
    for idx, header in enumerate(headers):
        if header.endswith(" kód") and idx not in seen_indices:
            supplier_code_columns.append((idx, header))
            seen_indices.add(idx)

    explicit_header_to_supplier: dict[str, str] = {}
    for supplier in suppliers_rows:
        if supplier.code_column and supplier.code_column.strip():
            explicit_header_to_supplier[supplier.code_column.strip()] = supplier.name

    supplier_cache: dict[str, Supplier] = {}
    product_cache: dict[str, Product] = {}

    for row in rows:
        result.rows_scanned += 1
        internal_code = _normalize(row[internal_code_idx])
        if not internal_code:
            continue

        product = product_cache.get(internal_code)
        if product is None:
            product = session.exec(
                select(Product).where(Product.internal_code == internal_code)
            ).first()
            if product is None:
                product = Product(internal_code=internal_code)
                session.add(product)
            product_cache[internal_code] = product

        _set_product_field_if_cell_nonempty(product, "norma", row, norma_idx)
        _set_product_field_if_cell_nonempty(product, "diameter", row, diameter_idx)
        _set_product_field_if_cell_nonempty(product, "length", row, length_idx)
        _set_product_field_if_cell_nonempty(product, "surface", row, surface_idx)
        _set_product_field_if_cell_nonempty(product, "v_class", row, v_class_idx)
        _set_product_field_if_cell_nonempty(
            product, "y_money_name", row, y_money_name_idx
        )
        if image_filename_idx is not None:
            r = tuple(row) if row is not None else ()
            if image_filename_idx < len(r):
                img_raw = _normalize(r[image_filename_idx])
                if img_raw:
                    product.image_filename = _excel_image_basename(img_raw) or None
        result.products_upserted += 1

        session.flush()

        for col_idx, header in supplier_code_columns:
            supplier_code = _normalize(row[col_idx])
            if not supplier_code:
                continue

            supplier_name = explicit_header_to_supplier.get(header.strip()) or _to_supplier_name(
                header
            )
            supplier = supplier_cache.get(supplier_name)
            if supplier is None:
                supplier = session.exec(
                    select(Supplier).where(Supplier.name == supplier_name)
                ).first()
                if supplier is None:
                    supplier = Supplier(
                        name=supplier_name,
                        shop_url="",
                        username="",
                        password="",
                        is_connected=False,
                        sort_order=_allocate_supplier_sort_order(session),
                    )
                    session.add(supplier)
                    result.suppliers_upserted += 1
                supplier_cache[supplier_name] = supplier
                session.flush()

            mapping = session.exec(
                select(ProductMapping).where(
                    ProductMapping.product_id == product.id,
                    ProductMapping.supplier_id == supplier.id,
                )
            ).first()
            if mapping is None:
                mapping = ProductMapping(
                    product_id=product.id, supplier_id=supplier.id, supplier_code=supplier_code
                )
                session.add(mapping)
            else:
                mapping.supplier_code = supplier_code
            result.mappings_upserted += 1

    session.commit()
    return result


def profile_excel_columns(
    file_path: str,
    sheet_name: str = "DIN",
    max_unique_values: int = 50_000,
    max_scan_rows: int = 500_000,
    preview_row_count: int = 8,
) -> ColumnProfileResult:
    wb = load_workbook(file_path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Sheet '{sheet_name}' was not found in workbook.")

    ws = wb[sheet_name]
    rows = ws.iter_rows(min_row=1, values_only=True)
    headers = _normalized_sheet_headers(next(rows))

    preview_rows: list[dict[str, str]] = []
    unique_sets = [set() for _ in headers]

    scanned = 0
    for row in rows:
        scanned += 1
        normalized_row = [_normalize(value) for value in row[: len(headers)]]

        if len(preview_rows) < preview_row_count:
            preview_rows.append(
                {headers[idx]: normalized_row[idx] if idx < len(normalized_row) else "" for idx in range(len(headers))}
            )

        for idx in range(len(headers)):
            value = normalized_row[idx] if idx < len(normalized_row) else ""
            if value and len(unique_sets[idx]) < max_unique_values:
                unique_sets[idx].add(value)

        if max_scan_rows > 0 and scanned >= max_scan_rows:
            break

    unique_values = {
        headers[idx]: sorted(unique_sets[idx]) for idx in range(len(headers)) if unique_sets[idx]
    }
    return ColumnProfileResult(
        sheet=sheet_name,
        columns=headers,
        preview_rows=preview_rows,
        unique_values=unique_values,
    )
