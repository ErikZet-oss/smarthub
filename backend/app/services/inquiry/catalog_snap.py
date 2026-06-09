from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models.entities import Product
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import norm_requires_length, search_key
from app.services.inquiry.normalize import norm_display_candidates, normalize_diameter

# Približné mapovanie povrchu na class v katalógu (matice DIN 934, …).
_SURFACE_V_CLASS: tuple[tuple[str, str], ...] = (
    ("pozink", "8.8"),
    ("nerez a4", "A4-70"),
    ("nerez a2", "A2-70"),
    ("nerez", "A2-70"),
    ("mosadz", "0"),
    ("polyamid", "0"),
    ("hliník", "P40"),
    ("hlinik", "P40"),
    ("oceľ", "8.8"),
    ("ocel", "8.8"),
)


@dataclass
class CatalogSnapCache:
    norma_values: list[str] = field(default_factory=list)
    _filter_opts: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session) -> CatalogSnapCache:
        rows = session.exec(
            select(Product.norma).distinct().where(Product.norma.is_not(None))  # type: ignore[union-attr]
        ).all()
        norma_values = sorted({str(v).strip() for v in rows if v is not None and str(v).strip()})
        return cls(norma_values=norma_values)

    def filter_options(self, session: Session, filters: ProductSearchFilters) -> dict[str, list[str]]:
        from app.api.routes import _build_conditional_filter_options

        key = self._filters_key(filters)
        cached = self._filter_opts.get(key)
        if cached is not None:
            return cached
        result = _build_conditional_filter_options(session, filters)
        self._filter_opts[key] = result
        return result

    @staticmethod
    def _filters_key(filters: ProductSearchFilters) -> str:
        return "|".join(
            [
                (filters.norma or "").strip(),
                (filters.surface or "").strip(),
                (filters.diameter or "").strip(),
                (filters.length or "").strip(),
                (filters.v_class or "").strip(),
            ]
        )


def infer_v_class_from_surface(surface: str | None) -> str | None:
    if not surface:
        return None
    low = surface.casefold()
    for key, cls in _SURFACE_V_CLASS:
        if key in low:
            return cls
    return None


def resolve_catalog_norma(norma: str | None, *, known: list[str] | None = None) -> str | None:
    """DIN934 → 934 podľa hodnôt v DB."""
    raw = (norma or "").strip()
    if not raw:
        return None
    if known is None:
        return raw
    if raw in known:
        return raw
    key = search_key(raw)
    for val in known:
        if search_key(val) == key:
            return val
    parsed = InquiryLineParsed(row_index=0, raw_text="", norma=raw)
    for candidate in norm_display_candidates(parsed):
        if candidate in known:
            return candidate
        if search_key(candidate) == key:
            for val in known:
                if search_key(val) == search_key(candidate):
                    return val
    return raw


def snap_value_to_options(value: str | None, options: list[str]) -> str | None:
    if not value:
        return value
    if value in options:
        return value
    val_key = search_key(value)
    for opt in options:
        if opt.casefold() == value.casefold():
            return opt
        if search_key(opt) == val_key:
            return opt
    bare = value.upper().removeprefix("M")
    if bare != value.upper():
        for opt in options:
            if opt == bare or search_key(opt) == search_key(bare):
                return opt
    return value


def _row_to_filters(row: InquiryLineParsed) -> ProductSearchFilters:
    return ProductSearchFilters(
        norma=row.norma,
        surface=row.surface,
        diameter=row.diameter,
        length=row.length,
        v_class=row.v_class,
    )


def _snap_fields_from_options(
    data: dict[str, object],
    opts: dict[str, list[str]],
    *,
    fields: tuple[str, ...] = ("norma", "surface", "diameter", "length", "v_class"),
) -> None:
    for name in fields:
        current = data.get(name)
        if current is None:
            continue
        snapped = snap_value_to_options(str(current), opts.get(name, []))
        if snapped is not None:
            data[name] = snapped


_FIELD_LABELS = {
    "norma": "Norma",
    "surface": "Povrch",
    "diameter": "Priemer",
    "length": "Dĺžka",
    "v_class": "Class",
}


def _catalog_mismatch_warnings(
    session: Session,
    row: InquiryLineParsed,
    cache: CatalogSnapCache,
) -> list[str]:
    warnings: list[str] = []
    for field, label in _FIELD_LABELS.items():
        val = getattr(row, field, None)
        if not (val or "").strip():
            continue
        opts = cache.filter_options(session, _row_to_filters(row)).get(field, [])
        if not opts:
            continue
        if str(val) not in opts:
            warnings.append(f'{label} „{val}" v katalógu pre túto kombináciu neexistuje')
    if warnings:
        return warnings

    required = [row.norma, row.diameter, row.quantity]
    if not all(x is not None and str(x).strip() for x in required[:2]):
        return warnings
    if not _product_exists(session, row):
        warnings.append("Táto kombinácia parametrov v katalógu neexistuje")
    return warnings


def _product_exists(session: Session, row: InquiryLineParsed) -> bool:
    query = select(Product)
    if row.norma:
        query = query.where(Product.norma == row.norma)
    if row.surface:
        query = query.where(Product.surface == row.surface)
    if row.diameter:
        query = query.where(Product.diameter == row.diameter)
    if row.length:
        query = query.where(Product.length == row.length)
    if row.v_class:
        query = query.where(Product.v_class == row.v_class)
    return session.exec(query.limit(1)).first() is not None


def snap_inquiry_line_to_catalog(
    session: Session,
    row: InquiryLineParsed,
    *,
    cache: CatalogSnapCache | None = None,
) -> InquiryLineParsed:
    """Zosúladí parsované hodnoty s presnými hodnotami filtrov v DB."""
    snap_cache = cache or CatalogSnapCache.load(session)
    data = row.model_dump()
    data["diameter"] = normalize_diameter(row.diameter)

    catalog_norma = resolve_catalog_norma(row.norma, known=snap_cache.norma_values)
    if catalog_norma:
        data["norma"] = catalog_norma

    if not data.get("v_class"):
        inferred = infer_v_class_from_surface(data.get("surface"))  # type: ignore[arg-type]
        if inferred:
            data["v_class"] = inferred

    if not norm_requires_length(catalog_norma or row.norma, row.raw_text) and not data.get("length"):
        data["length"] = "0"

    base = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
    opts = snap_cache.filter_options(session, _row_to_filters(base))
    _snap_fields_from_options(data, opts)

    if not data.get("v_class"):
        inferred = infer_v_class_from_surface(data.get("surface"))  # type: ignore[arg-type]
        if inferred:
            data["v_class"] = snap_value_to_options(inferred, opts.get("v_class", [])) or inferred

    if not norm_requires_length(data.get("norma"), row.raw_text):  # type: ignore[arg-type]
        data["length"] = snap_value_to_options(str(data.get("length") or "0"), opts.get("length", [])) or "0"

    snapped = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
    warnings = _catalog_mismatch_warnings(session, snapped, snap_cache)
    return snapped.model_copy(update={"catalog_warnings": warnings or None})


def snap_inquiry_batch_to_catalog(
    session: Session,
    rows: list[InquiryLineParsed],
    *,
    progress_cb=None,
) -> list[InquiryLineParsed]:
    cache = CatalogSnapCache.load(session)
    out: list[InquiryLineParsed] = []
    total = len(rows)
    for i, row in enumerate(rows, start=1):
        out.append(snap_inquiry_line_to_catalog(session, row, cache=cache))
        if progress_cb is not None:
            progress_cb(i, total)
    return out
