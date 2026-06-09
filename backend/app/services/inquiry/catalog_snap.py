from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models.entities import Product
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import norm_requires_length, search_key
from app.services.inquiry.normalize import (
    apply_plastic_material_rules,
    infer_surface_from_text,
    norm_display_candidates,
    normalize_diameter,
)

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

# Priemer dier podložky DIN 125 (vnútorný Ø) podľa veľkosti skrutky M*.
_WASHER_BOLT_TO_INNER: dict[str, str] = {
    "2": "2.2",
    "2.5": "2.7",
    "3": "3.2",
    "4": "4.3",
    "5": "5.3",
    "6": "6.4",
    "8": "8.4",
    "10": "10.5",
    "12": "13",
    "14": "15",
    "16": "17",
    "18": "19",
    "20": "21",
    "22": "23",
    "24": "25",
    "27": "28",
    "30": "31",
}


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


def infer_v_class_for_row(
    *,
    norma: str | None,
    surface: str | None,
    raw_text: str,
) -> str | None:
    """Matice/skrutky — mapovanie z povrchu; podložky DIN 125 majú iné class v DB."""
    if _is_washer_norm(norma, raw_text):
        return _infer_washer_v_class(surface, raw_text)
    return infer_v_class_from_surface(surface)


def _infer_washer_v_class(surface: str | None, raw_text: str) -> str | None:
    low = (raw_text or "").casefold()
    surf = (surface or "").casefold()
    if "polyamid" in low or "nylon" in low or "plast" in low:
        return "0"
    if "nerez a4" in surf or ("nerez" in surf and "a4" in low):
        return "A4-70"
    if "nerez" in surf or "nerezoceľ" in low or "nerezocel" in low:
        return "A2-50"
    if "mosadz" in surf or "mosadz" in low:
        return "0"
    if "pozink" in surf or "pozink" in low or " zn " in f" {low} ":
        return "0"
    if "hliník" in surf or "hlinik" in low:
        return "P40"
    return infer_v_class_from_surface(surface)


def resolve_catalog_norma(norma: str | None, *, known: list[str] | None = None) -> str | None:
    """DIN934 → 934, DIN125 → 125a podľa hodnôt v DB."""
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
    suffix_a = _resolve_catalog_norma_suffix_a(raw, known=known)
    if suffix_a is not None:
        return suffix_a
    return raw


def _resolve_catalog_norma_suffix_a(raw: str, *, known: list[str]) -> str | None:
    """
    DIN 125 / DIN125 / DIN 125-1A → 125a (v DB bez prefixu DIN).
    Všeobecne: DIN### → ###a ak existuje.
    """
    key = search_key(raw)
    m = re.match(r"^DIN(\d+)", key)
    if not m:
        return None
    base = m.group(1)
    if len(base) > 3 and base.endswith("1"):
        alt = f"{base[:-1]}a"
        if alt in known:
            return alt
    for val in known:
        if val == f"{base}a" or search_key(val) == f"{base}A":
            return val
    return None


def _is_washer_norm(norma: str | None, raw_text: str) -> bool:
    low = (raw_text or "").casefold()
    if "podložk" in low or "podlozk" in low:
        return True
    nk = search_key(norma)
    return nk.startswith("DIN125") or nk in ("125", "125A") or bool(re.match(r"^125A?$", nk))


def is_washer_text(raw_text: str) -> bool:
    return _is_washer_norm(None, raw_text)


def resolve_washer_inner_diameter(bolt_m: str | None, options: list[str]) -> str | None:
    """M3 → 3.2 mm (vnútorný priemer podložky), ak je v katalógu."""
    if not bolt_m or not options:
        return None
    bare = str(bolt_m).strip().upper().removeprefix("M").replace(",", ".")
    if not bare:
        return None
    inner = _WASHER_BOLT_TO_INNER.get(bare)
    if inner and inner in options:
        return inner
    if bare in options:
        return bare
    return None


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
    apply_plastic_material_rules(data, row.raw_text)
    if not data.get("surface"):
        inferred_surface = infer_surface_from_text(row.raw_text)
        if inferred_surface:
            data["surface"] = inferred_surface

    catalog_norma = resolve_catalog_norma(row.norma, known=snap_cache.norma_values)
    if catalog_norma:
        data["norma"] = catalog_norma

    washer = _is_washer_norm(str(data.get("norma") or ""), row.raw_text)
    washer_v_class = infer_v_class_for_row(
        norma=str(data.get("norma") or ""),
        surface=str(data.get("surface") or "") or None,
        raw_text=row.raw_text,
    )
    if washer and washer_v_class:
        data["v_class"] = washer_v_class
    elif not data.get("v_class"):
        inferred = infer_v_class_from_surface(data.get("surface"))  # type: ignore[arg-type]
        if inferred:
            data["v_class"] = inferred

    if not norm_requires_length(catalog_norma or row.norma, row.raw_text) and not data.get("length"):
        data["length"] = "0"

    base = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
    opts = snap_cache.filter_options(session, _row_to_filters(base))

    if washer:
        diam_opts = opts.get("diameter", [])
        current_d = str(data.get("diameter") or "")
        if current_d and current_d not in diam_opts:
            resolved = resolve_washer_inner_diameter(current_d, diam_opts)
            if resolved:
                data["diameter"] = resolved
        base = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
        opts = snap_cache.filter_options(session, _row_to_filters(base))

    _snap_fields_from_options(data, opts)

    if washer and washer_v_class:
        data["v_class"] = snap_value_to_options(washer_v_class, opts.get("v_class", [])) or washer_v_class
    elif not data.get("v_class"):
        inferred = infer_v_class_for_row(
            norma=str(data.get("norma") or ""),
            surface=str(data.get("surface") or "") or None,
            raw_text=row.raw_text,
        )
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
