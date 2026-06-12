from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models.entities import Product
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import (
    _norm_num_key,
    _pin_base_norm,
    catalog_value_in_options,
    extract_pin_catalog_norma,
    extract_pin_tolerance_fit,
    extract_snap_ring_diameter,
    inquiry_catalog_fields_to_validate,
    is_pin_norm,
    is_snap_ring_norm,
    norm_requires_length,
    norm_requires_v_class,
    search_key,
)
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

_EXPLICIT_V_CLASS = re.compile(
    r"\b("
    r"4[,.]6|4[,.]8|5[,.]6|5[,.]8|6[,.]8|8[,.]8|10[,.]9|12[,.]9"
    r"|A2-70|A2-80|A4-70|A4-80"
    r")\b",
    re.IGNORECASE,
)

_NUT_TEXT = re.compile(
    r"\b(matic(?:a|e|ou|i|ami)?|sestihrann(?:a|e|ych|ou|y)?\s+matic)\b",
    re.IGNORECASE,
)

_NUT_NORM_KEYS = frozenset({"934", "985", "6923", "439", "4032", "315"})

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

        prepared = prepare_inquiry_catalog_filters(filters, known_norma=self.norma_values)
        key = self._filters_key(prepared)
        cached = self._filter_opts.get(key)
        if cached is not None:
            return cached
        result = _build_conditional_filter_options(session, prepared)
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
                (filters.internal_code or "").strip(),
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


def extract_explicit_v_class(raw_text: str) -> str | None:
    """Pevnosť uvedená priamo v texte (10.9, 8.8, A2-70, …)."""
    m = _EXPLICIT_V_CLASS.search(raw_text or "")
    if not m:
        return None
    return m.group(1).replace(",", ".")


def _is_nut_row(norma: str | None, raw_text: str) -> bool:
    if _NUT_TEXT.search(raw_text or ""):
        return True
    key = search_key(norma)
    num = key[3:] if key.startswith("DIN") and len(key) > 3 else key
    return num in _NUT_NORM_KEYS


def infer_v_class_for_row(
    *,
    norma: str | None,
    surface: str | None,
    raw_text: str,
) -> str | None:
    """Matice — class len ak je v texte alebo pri podložkách z materiálu; inak radšej prázdne."""
    explicit = extract_explicit_v_class(raw_text)
    if explicit:
        return explicit
    if _is_washer_norm(norma, raw_text):
        return _infer_washer_v_class(surface, raw_text)
    if _is_nut_row(norma, raw_text):
        low = (raw_text or "").casefold()
        if "a2-80" in low or "a2 80" in low:
            return "A2-80"
        if "a4" in low and ("nerez" in low or "nerez" in (surface or "").casefold()):
            return "A4-70"
        if "a2" in low or "nerez" in low or "nerez" in (surface or "").casefold():
            return "A2-70"
        return None
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


def resolve_pin_catalog_norma(
    norma: str | None,
    *,
    v_class: str | None,
    raw_text: str,
    known: list[str],
) -> str | None:
    """6325 + M6 → „6325 M6“, DIN 7979 D → „7979 D“ podľa katalógu."""
    pin_norm = extract_pin_catalog_norma(raw_text, base_norm=norma)
    if pin_norm and pin_norm in known:
        return pin_norm
    if pin_norm:
        for val in known:
            if search_key(val) == search_key(pin_norm):
                return val

    base = _pin_base_norm(norma)
    if base == "6325":
        fit = (v_class or "").strip().upper() or extract_pin_tolerance_fit(raw_text)
        if fit:
            candidate = f"6325 {fit}"
            if candidate in known:
                return candidate
            for val in known:
                if search_key(val) == search_key(candidate):
                    return val
    return None


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
    m = re.match(r"^DIN(\d+[A-Za-z]?)", key)
    if m:
        base = m.group(1).lower()
        if base in known:
            return base
        alt = f"{base}a"
        if alt in known:
            return alt
        for val in known:
            if search_key(val) == base:
                return val
        # Katalóg ukladá normu bez prefixu DIN — DIN471 → 471 aj keď known zoznam mešká.
        return base
    return raw


def _resolve_catalog_norma_spaced_variant(
    norma: str | None,
    raw_text: str,
    *,
    known: list[str],
) -> str | None:
    """DIN 439-2 / 439-2 → „439 2“ podľa zápisu v DB."""
    blob = f"{norma or ''} {raw_text or ''}"
    for m in re.finditer(r"\b(\d{3,4})\s*[-]\s*(\d+)\b", blob):
        candidate = f"{m.group(1)} {m.group(2)}"
        if candidate in known:
            return candidate
        cand_key = search_key(candidate)
        for val in known:
            if search_key(val) == cand_key:
                return val
    return None


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
    """Zosúladí s katalógom; ak katalóg nemá zoznam, ponechá parsovanú hodnotu."""
    if not value:
        return value
    if not options:
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
    if value.casefold() in ("oceľ", "ocel"):
        steel = [o for o in options if o.casefold().startswith(("oceľ", "ocel"))]
        if len(steel) == 1:
            return steel[0]
    return None


def prepare_inquiry_catalog_filters(
    filters: ProductSearchFilters,
    *,
    known_norma: list[str],
) -> ProductSearchFilters:
    """DIN471 → 471; pri normách bez dĺžky/class nefiltruj podľa týchto polí (DB má length=0)."""
    data = filters.model_dump()
    norma = str(data.get("norma") or "").strip()
    if norma:
        data["norma"] = resolve_catalog_norma(norma, known=known_norma) or norma
    catalog_norma = str(data.get("norma") or "").strip() or None
    if not norm_requires_length(catalog_norma):
        data["length"] = None
    if not norm_requires_v_class(catalog_norma):
        data["v_class"] = None
    return ProductSearchFilters.model_validate(data)


def _row_to_filters(row: InquiryLineParsed) -> ProductSearchFilters:
    return ProductSearchFilters(
        norma=row.norma,
        surface=row.surface,
        diameter=row.diameter,
        length=row.length,
        v_class=row.v_class,
        internal_code=row.internal_code,
    )


def _apply_internal_code_product(session: Session, data: dict[str, object]) -> bool:
    code = str(data.get("internal_code") or "").strip()
    if not code:
        return False
    product = session.exec(select(Product).where(Product.internal_code == code)).first()
    if product is None:
        return False
    data["norma"] = product.norma
    data["surface"] = product.surface
    data["diameter"] = product.diameter
    data["length"] = product.length
    if is_snap_ring_norm(product.norma):
        data["v_class"] = None
    else:
        data["v_class"] = product.v_class or None
    return True


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
        field_opts = opts.get(name, [])
        snapped = snap_value_to_options(str(current), field_opts)
        if snapped is not None:
            data[name] = snapped
        elif name == "v_class" and field_opts:
            data[name] = None


_FIELD_LABELS = {
    "norma": "Norma",
    "surface": "Povrch",
    "diameter": "Priemer",
    "length": "Dĺžka",
    "v_class": "Class",
    "internal_code": "Číslo Smart",
}


def _product_query_from_row(
    row: InquiryLineParsed,
    *,
    known_norma: list[str] | None = None,
):
    query = select(Product)
    norma = row.norma
    if known_norma is not None:
        norma = resolve_catalog_norma(row.norma, known=known_norma) or row.norma
    if norma:
        query = query.where(Product.norma == norma)
    if row.surface:
        query = query.where(Product.surface == row.surface)
    if row.diameter:
        query = query.where(Product.diameter == row.diameter)
    length = str(row.length or "").strip()
    if length:
        query = query.where(Product.length == length)
    elif is_snap_ring_norm(norma, row.raw_text):
        query = query.where(Product.length == "0")
    v_class = str(row.v_class or "").strip()
    if v_class and norm_requires_v_class(norma, row.raw_text):
        if not (v_class == "0" and _is_nut_row(norma, row.raw_text)):
            query = query.where(Product.v_class == v_class)
    return query


def _match_product_by_raw_text(products: list[Product], raw_text: str) -> Product | None:
    if len(products) == 1:
        return products[0]
    if not products:
        return None
    text = (raw_text or "").casefold()
    if not text.strip():
        return None
    if not re.search(r"\bx\s*\d", text, re.IGNORECASE):
        plain = [
            p
            for p in products
            if not re.search(r"\bx\s*\d", p.y_money_name or "", re.IGNORECASE)
        ]
        if len(plain) == 1:
            return plain[0]
        if len(plain) > 1 and "lava" not in text:
            non_lava = [p for p in plain if "lava" not in (p.y_money_name or "").casefold()]
            if len(non_lava) == 1:
                return non_lava[0]
    best_score = 0
    best: list[Product] = []
    for product in products:
        name = (product.y_money_name or "").casefold()
        if not name:
            continue
        score = 0
        if re.search(r"\bx\s*1[,.]5\b", text) and "x1,5" in name.replace(".", ","):
            score += 5
        elif re.search(r"\bx\s*1[,.]25\b", text) and "x1,25" in name.replace(".", ","):
            score += 5
        elif re.search(r"\bx\s*1\b", text) and re.search(r"\bx\s*1\b", name):
            score += 5
        for token in re.findall(r"[a-záäčďéíĺľňóôŕšťúýž0-9]+", name):
            if len(token) >= 2 and token in text:
                score += 1
        if score > best_score:
            best_score = score
            best = [product]
        elif score == best_score and score > 0:
            best.append(product)
    if len(best) == 1:
        return best[0]
    return None


def _row_norma_candidates(row: InquiryLineParsed, *, known: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(val: str | None) -> None:
        v = (val or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    add(row.norma)
    add(resolve_catalog_norma(row.norma, known=known))
    add(_resolve_catalog_norma_spaced_variant(row.norma, row.raw_text, known=known))
    return out


def _lookup_unique_internal_code(
    session: Session,
    row: InquiryLineParsed,
    cache: CatalogSnapCache,
) -> str | None:
    for norma in _row_norma_candidates(row, known=cache.norma_values):
        candidate_row = row.model_copy(update={"norma": norma})
        products = session.exec(
            _product_query_from_row(candidate_row, known_norma=cache.norma_values).limit(20)
        ).all()
        if len(products) == 1:
            return products[0].internal_code
        matched = _match_product_by_raw_text(products, row.raw_text)
        if matched is not None:
            return matched.internal_code
    return None


def _catalog_mismatch_warnings(
    session: Session,
    row: InquiryLineParsed,
    cache: CatalogSnapCache,
) -> list[str]:
    warnings: list[str] = []
    validate_fields = inquiry_catalog_fields_to_validate(
        row.norma,
        row.raw_text,
        length=row.length,
        v_class=row.v_class,
        internal_code=row.internal_code,
    )
    prep = prepare_inquiry_catalog_filters(_row_to_filters(row), known_norma=cache.norma_values)
    opts = cache.filter_options(session, prep)
    for field in validate_fields:
        label = _FIELD_LABELS.get(field, field)
        val = getattr(row, field, None)
        if not (val or "").strip():
            continue
        field_opts = opts.get(field, [])
        if not field_opts:
            continue
        if not catalog_value_in_options(str(val), field_opts):
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
    return session.exec(_product_query_from_row(row).limit(1)).first() is not None


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
    pin_norma = resolve_pin_catalog_norma(
        catalog_norma or row.norma,
        v_class=str(row.v_class or "") or None,
        raw_text=row.raw_text,
        known=snap_cache.norma_values,
    )
    if pin_norma:
        data["norma"] = pin_norma
    elif catalog_norma:
        data["norma"] = catalog_norma
    spaced = _resolve_catalog_norma_spaced_variant(
        str(data.get("norma") or row.norma or ""),
        row.raw_text,
        known=snap_cache.norma_values,
    )
    if spaced:
        data["norma"] = spaced

    if is_pin_norm(data.get("norma"), row.raw_text) or is_pin_norm(row.norma, row.raw_text):
        tol = extract_pin_tolerance_fit(row.raw_text)
        if tol:
            data["v_class"] = tol
        elif _norm_num_key(str(data.get("norma") or "")) == "7979" or str(data.get("norma") or "").startswith("7979"):
            data["v_class"] = "0"

    washer = _is_washer_norm(str(data.get("norma") or ""), row.raw_text)
    washer_v_class = infer_v_class_for_row(
        norma=str(data.get("norma") or ""),
        surface=str(data.get("surface") or "") or None,
        raw_text=row.raw_text,
    )
    if washer and washer_v_class:
        data["v_class"] = washer_v_class
    elif not data.get("v_class") and not is_snap_ring_norm(
        str(data.get("norma") or ""), row.raw_text
    ) and not is_pin_norm(str(data.get("norma") or ""), row.raw_text):
        inferred = infer_v_class_for_row(
            norma=str(data.get("norma") or ""),
            surface=str(data.get("surface") or "") or None,
            raw_text=row.raw_text,
        )
        if inferred:
            data["v_class"] = inferred

    if is_snap_ring_norm(str(data.get("norma") or ""), row.raw_text):
        snap_d = extract_snap_ring_diameter(row.raw_text)
        if snap_d and not data.get("diameter"):
            data["diameter"] = snap_d
        data["v_class"] = None

    if not norm_requires_length(str(data.get("norma") or catalog_norma or row.norma or ""), row.raw_text) and not data.get("length"):
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
        data["v_class"] = snap_value_to_options(washer_v_class, opts.get("v_class", []))
    elif not data.get("v_class") and not is_snap_ring_norm(
        str(data.get("norma") or ""), row.raw_text
    ) and not is_pin_norm(str(data.get("norma") or ""), row.raw_text):
        inferred = infer_v_class_for_row(
            norma=str(data.get("norma") or ""),
            surface=str(data.get("surface") or "") or None,
            raw_text=row.raw_text,
        )
        if inferred:
            data["v_class"] = snap_value_to_options(inferred, opts.get("v_class", []))

    if is_snap_ring_norm(str(data.get("norma") or ""), row.raw_text):
        data["v_class"] = None
    elif is_pin_norm(str(data.get("norma") or ""), row.raw_text):
        valid = opts.get("v_class", [])
        tol = extract_pin_tolerance_fit(row.raw_text)
        current = str(data.get("v_class") or "")
        candidate = tol or (current if re.fullmatch(r"[MH]\d+", current, re.IGNORECASE) else None)
        data["v_class"] = snap_value_to_options(candidate, valid) if candidate else None

    if not norm_requires_length(str(data.get("norma") or ""), row.raw_text):  # type: ignore[arg-type]
        data["length"] = snap_value_to_options(str(data.get("length") or "0"), opts.get("length", [])) or "0"

    codes = opts.get("internal_code", [])
    if not data.get("internal_code") and len(codes) == 1:
        data["internal_code"] = codes[0]
    if not data.get("internal_code"):
        unique_code = _lookup_unique_internal_code(
            session,
            InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text}),
            snap_cache,
        )
        if unique_code:
            data["internal_code"] = unique_code
    if data.get("internal_code"):
        if _apply_internal_code_product(session, data):
            base = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
            opts = snap_cache.filter_options(session, _row_to_filters(base))

    if is_snap_ring_norm(str(data.get("norma") or ""), row.raw_text):
        data["v_class"] = None

    snapped = InquiryLineParsed.model_validate({**data, "raw_text": row.raw_text})
    warnings = _catalog_mismatch_warnings(session, snapped, snap_cache)
    return snapped.model_copy(update={"catalog_warnings": warnings or None})


def enrich_inquiry_rows_internal_codes(
    session: Session,
    rows: list[InquiryLineParsed],
    *,
    cache: CatalogSnapCache | None = None,
) -> list[InquiryLineParsed]:
    """Doplní číslo Smart tam, kde kombinácia jednoznačne určí produkt."""
    snap_cache = cache or CatalogSnapCache.load(session)
    out: list[InquiryLineParsed] = []
    for row in rows:
        if (row.internal_code or "").strip():
            out.append(row)
            continue
        code = _lookup_unique_internal_code(session, row, snap_cache)
        if not code:
            out.append(row)
            continue
        variant = _resolve_catalog_norma_spaced_variant(
            row.norma, row.raw_text, known=snap_cache.norma_values
        )
        updates: dict[str, object] = {"internal_code": code}
        if variant and variant != row.norma:
            updates["norma"] = variant
        out.append(row.model_copy(update=updates))
    return out


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
