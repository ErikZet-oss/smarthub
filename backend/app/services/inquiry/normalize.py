from __future__ import annotations

import re

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import norm_requires_length, search_key
from app.services.inquiry.product_norm_hints import infer_norma_from_text
from app.services.inquiry.stn_suffix import infer_material_from_stn_text
from app.services.inquiry.stn_to_din import map_standard_to_catalog_din


def norm_display_candidates(parsed: InquiryLineParsed) -> list[str]:
    """Varianty normy pre presný match v DB (DIN933 vs DIN 933 vs 933)."""
    raw = (parsed.norma or "").strip()
    if not raw:
        return []

    seen: set[str] = set()
    out: list[str] = []

    def add(val: str) -> None:
        v = val.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    add(raw)
    key = search_key(raw)
    if key.startswith("DIN") and len(key) > 3:
        num = key[3:]
        add(f"DIN{num}")
        add(f"DIN {num}")
        add(f"DIN-{num}")
        if num:
            add(num)
            add(f"{num}a")
            add(f"DIN {num}a")
    elif key.startswith("ISO") and len(key) > 3:
        rest = key[3:]
        add(f"ISO {rest}")
        add(f"ISO-{rest}")
    return out


def normalize_diameter(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip().upper().replace(",", ".")
    s = re.sub(r"\s+", "", s)
    if s.startswith("M"):
        s = s[1:]
    return s or None


def normalize_length_mm(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip().lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    num = m.group(1)
    if num.endswith(".0"):
        num = num[:-2]
    return num


def normalize_v_class(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip().replace(",", ".")
    return s or None


def normalize_surface(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    low = s.casefold()
    mapping = (
        ("pozink", "Oceľ pozinkovaná"),
        ("nerez a4", "Nerez A4"),
        ("nerez a2", "Nerez A2"),
        ("nerez", "Nerez A2"),
        ("mosadz", "Mosadz"),
        ("polyamid", "Polyamid"),
        ("nylon", "Polyamid"),
        ("oceľ", "Oceľ"),
        ("ocel", "Oceľ"),
    )
    for key, label in mapping:
        if key in low:
            return label
    return s


def infer_surface_from_text(text: str) -> str | None:
    """Materiál z voľného textu — keď AI/heuristika nevyplní surface."""
    low = (text or "").casefold()
    if not low.strip():
        return None
    if "polyamid" in low or "nylon" in low or re.search(r"\bplast\b", low):
        return "Polyamid"
    if "hliník" in low or "hlinik" in low or "alumini" in low:
        return "Hliník"
    if "a4" in low and "nerez" in low:
        return "Nerez A4"
    if "a2" in low or "nerez" in low:
        return "Nerez A2"
    if "pozink" in low:
        return "Oceľ pozinkovaná"
    if "mosadz" in low:
        return "Mosadz"
    if "ocel" in low or "oceľ" in low:
        return "Oceľ"
    return None


def apply_plastic_material_rules(data: dict[str, object], raw_text: str) -> None:
    """
    Polyamid (nylon) 6.6 — „6.6“ je stupeň materiálu, nie class v katalógu (matice majú class 0).
    """
    if infer_surface_from_text(raw_text) != "Polyamid":
        return
    data["surface"] = "Polyamid"
    data["v_class"] = "0"


def apply_normalization(parsed: InquiryLineParsed) -> InquiryLineParsed:
    """Po AI alebo manuálnej editácii — zjednotí formát polí."""
    data = parsed.model_dump()
    data["diameter"] = normalize_diameter(parsed.diameter)
    data["length"] = normalize_length_mm(parsed.length)
    data["v_class"] = normalize_v_class(parsed.v_class)
    data["surface"] = normalize_surface(parsed.surface)
    if not data.get("surface"):
        inferred = infer_surface_from_text(parsed.raw_text)
        if inferred:
            data["surface"] = inferred
    apply_plastic_material_rules(data, parsed.raw_text)
    stn_hint = infer_material_from_stn_text(parsed.raw_text, norma=parsed.norma)
    if stn_hint:
        if not data.get("surface") and stn_hint.surface:
            data["surface"] = stn_hint.surface
        if not data.get("v_class") and stn_hint.v_class:
            data["v_class"] = stn_hint.v_class
    mapped = map_standard_to_catalog_din(parsed.norma, parsed.raw_text)
    if mapped:
        data["norma"] = mapped
    elif parsed.norma:
        data["norma"] = parsed.norma.strip().upper().replace("  ", " ")
    else:
        inferred = infer_norma_from_text(parsed.raw_text)
        if inferred:
            data["norma"] = inferred
        else:
            remapped = map_standard_to_catalog_din(None, parsed.raw_text)
            if remapped:
                data["norma"] = remapped
    final_norma = data.get("norma")
    if not norm_requires_length(final_norma, parsed.raw_text) and not data.get("length"):
        data["length"] = "0"
    return InquiryLineParsed.model_validate(data)
