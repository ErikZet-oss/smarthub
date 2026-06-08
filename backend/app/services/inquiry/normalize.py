from __future__ import annotations

import re

from app.schemas.inquiry import InquiryLineParsed


def search_key(value: str | None) -> str:
    """Jednotný kľúč pre porovnanie: DIN 933 → DIN933."""
    if not value:
        return ""
    s = str(value).upper().strip()
    s = re.sub(r"[\s\-_./]+", "", s)
    return s


def norm_display_candidates(parsed: InquiryLineParsed) -> list[str]:
    """Varianty normy pre presný match v DB (DIN933 vs DIN 933 vs 933)."""
    raw = (parsed.norm or parsed.leading_standard or "").strip()
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


def normalize_class(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip().replace(",", ".")
    return s or None


def apply_normalization(parsed: InquiryLineParsed) -> InquiryLineParsed:
    """Po AI alebo manuálnej editácii — zjednotí formát polí."""
    data = parsed.model_dump()
    data["diameter"] = normalize_diameter(parsed.diameter)
    data["length"] = normalize_length_mm(parsed.length)
    data["class_"] = normalize_class(parsed.class_)
    if parsed.norm:
        data["norm"] = parsed.norm.strip().upper().replace("  ", " ")
    if parsed.leading_standard:
        data["leading_standard"] = parsed.leading_standard.strip().upper().replace("  ", " ")
    return InquiryLineParsed.model_validate(data)
