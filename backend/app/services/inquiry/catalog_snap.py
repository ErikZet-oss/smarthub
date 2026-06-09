from __future__ import annotations

from sqlmodel import Session, select

from app.models.entities import Product
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import norm_requires_length, search_key
from app.services.inquiry.normalize import norm_display_candidates

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


def infer_v_class_from_surface(surface: str | None) -> str | None:
    if not surface:
        return None
    low = surface.casefold()
    for key, cls in _SURFACE_V_CLASS:
        if key in low:
            return cls
    return None


def _distinct_norma_values(session: Session) -> list[str]:
    rows = session.exec(
        select(Product.norma).distinct().where(Product.norma.is_not(None))  # type: ignore[union-attr]
    ).all()
    return [str(v).strip() for v in rows if v is not None and str(v).strip()]


def resolve_catalog_norma(session: Session, norma: str | None) -> str | None:
    """DIN934 → 934 podľa hodnôt v DB."""
    raw = (norma or "").strip()
    if not raw:
        return None
    known = _distinct_norma_values(session)
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


def _row_to_filters(row: InquiryLineParsed, *, norma: str | None = None) -> ProductSearchFilters:
    return ProductSearchFilters(
        norma=norma if norma is not None else row.norma,
        surface=row.surface,
        diameter=row.diameter,
        length=row.length,
        v_class=row.v_class,
    )


def _filter_options(session: Session, filters: ProductSearchFilters) -> dict[str, list[str]]:
    from app.api.routes import _build_conditional_filter_options

    return _build_conditional_filter_options(session, filters)


def snap_inquiry_line_to_catalog(session: Session, row: InquiryLineParsed) -> InquiryLineParsed:
    """Zosúladí parsované hodnoty s presnými hodnotami filtrov v DB."""
    data = row.model_dump()
    catalog_norma = resolve_catalog_norma(session, row.norma)
    if catalog_norma:
        data["norma"] = catalog_norma

    base = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
    filters = _row_to_filters(base)

    for field in ("norma", "surface", "diameter", "length", "v_class"):
        opts = _filter_options(session, filters).get(field, [])
        current = data.get(field)
        snapped = snap_value_to_options(current, opts)
        if snapped != current:
            data[field] = snapped
            base = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
            filters = _row_to_filters(base)

    if not data.get("v_class"):
        inferred = infer_v_class_from_surface(data.get("surface"))
        if inferred:
            v_opts = _filter_options(session, _row_to_filters(base)).get("v_class", [])
            data["v_class"] = snap_value_to_options(inferred, v_opts) or inferred

    if not norm_requires_length(base.norma, row.raw_text) and not data.get("length"):
        len_opts = _filter_options(session, _row_to_filters(InquiryLineParsed.model_validate(data))).get(
            "length", []
        )
        data["length"] = snap_value_to_options("0", len_opts) or "0"

    return InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
