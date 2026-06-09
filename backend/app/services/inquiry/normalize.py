from __future__ import annotations

import re

from app.schemas.inquiry import InquiryLineParsed
from app.services.inquiry.norm_rules import norm_requires_length, search_key


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
    if s and not s.startswith("M") and re.fullmatch(r"\d+(?:\.\d+)?", s):
        s = f"M{s}"
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
        ("oceľ", "Oceľ"),
        ("ocel", "Oceľ"),
    )
    for key, label in mapping:
        if key in low:
            return label
    return s


def apply_normalization(parsed: InquiryLineParsed) -> InquiryLineParsed:
    """Po AI alebo manuálnej editácii — zjednotí formát polí."""
    data = parsed.model_dump()
    data["diameter"] = normalize_diameter(parsed.diameter)
    data["length"] = normalize_length_mm(parsed.length)
    data["v_class"] = normalize_v_class(parsed.v_class)
    data["surface"] = normalize_surface(parsed.surface)
    if parsed.norma:
        data["norma"] = parsed.norma.strip().upper().replace("  ", " ")
    if not norm_requires_length(parsed.norma, parsed.raw_text) and not data.get("length"):
        data["length"] = "0"
    return InquiryLineParsed.model_validate(data)
