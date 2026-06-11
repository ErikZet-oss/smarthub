from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.services.inquiry.norm_rules import search_key

_MAP_FILE = Path(__file__).with_name("_stn_map_pairs.txt")

_STN_IN_TEXT = re.compile(
    r"\bSTN\s*(?:EN\s*(?:ISO\s*)?)?(?:0?\s*2\s*)?(\d{4})(?:\.\d+)?(?:\.\d+)?",
    re.IGNORECASE,
)
_STN_COMPACT = re.compile(r"\bSTN\s*(\d{5,7})(?:\.\d+)?", re.IGNORECASE)
_ISO_IN_TEXT = re.compile(r"\bISO\s*[-]?\s*(\d{4,5})\b", re.IGNORECASE)
_DIN_IN_TEXT = re.compile(r"\bDIN\s*[-]?\s*(\d+[A-Za-z]?)\b", re.IGNORECASE)
_CSN_IN_TEXT = re.compile(
    r"\b(?:ČSN|CSN)\s*(?:0?\s*2\s*)?(\d{4})(?:\.\d+)?",
    re.IGNORECASE,
)

_PROMPT_EXAMPLES = (
    ("STN 02 1103 / STN 02 1101", "DIN933 / DIN931 — skrutky so šesťhrannou hlavou"),
    ("STN 02 1401", "DIN934 — šesťhranná matica"),
    ("STN 02 1401.55", "DIN934, surface Oceľ pozinkovaná, v_class 8.8"),
    ("STN 02 1401.05", "DIN934, surface Oceľ pozinkovaná, v_class 5.8"),
    ("STN 02 1401.90 / .92", "DIN934, surface Nerez A2, v_class A2-70"),
    ("STN 02 1143", "DIN912 — imbus (valcová hlava, vnútorný šesťhran)"),
    ("STN 02 1702", "DIN125 — plochá podložka"),
    ("STN 02 1741 / STN 02 1745", "DIN127 / DIN6798 — pružné podložky"),
    ("STN 02 1174", "DIN938 — závrtná skrutka do ocele"),
    ("STN 02 1131", "DIN84 — skrutka s valcovou hlavou"),
    ("STN 02 1814", "DIN97 — skrutka do dreva"),
    ("ISO 4017", "DIN933"),
    ("ISO 4032", "DIN934"),
    ("ISO 4762", "DIN912"),
    ("ISO 7089", "DIN125"),
)


@lru_cache(maxsize=1)
def _load_maps() -> tuple[dict[str, str], dict[str, str]]:
    stn: dict[str, str] = {}
    iso: dict[str, str] = {}
    if not _MAP_FILE.is_file():
        return stn, iso
    for line in _MAP_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("ISO:"):
            _, rest = line.split(":", 1)
            code, din = rest.split(maxsplit=1)
            iso[code.strip()] = din.strip()
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        stn[parts[0].strip()] = parts[1].strip()
    return stn, iso


def normalize_stn_base(raw: str) -> str | None:
    """STN021401 / 21401 / 1401 → štvormiestny kód radu 02 xxxx."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) >= 6 and digits.startswith("02"):
        base = digits[2:6]
    elif len(digits) == 5 and digits.startswith("2"):
        base = digits[1:5]
    elif len(digits) >= 4:
        base = digits[:4]
    else:
        return None
    if not base.isdigit() or base[0] not in "12":
        return None
    return base


def extract_stn_base(text: str) -> str | None:
    """Vytiahne STN/ČSN 02 xxxx z textu (ignoruje desatinné suffixy .55)."""
    t = text or ""
    m = _STN_IN_TEXT.search(t) or _CSN_IN_TEXT.search(t)
    if m:
        return normalize_stn_base(m.group(1))
    m = _STN_COMPACT.search(t)
    if m:
        return normalize_stn_base(m.group(1))
    return None


def extract_iso_base(text: str) -> str | None:
    m = _ISO_IN_TEXT.search(text or "")
    return m.group(1) if m else None


def _din_catalog_value(din_number: str) -> str:
    num = re.sub(r"[^0-9A-Za-z]", "", (din_number or "").upper())
    if num.startswith("DIN"):
        num = num[3:]
    return f"DIN{num}" if num else ""


def stn_base_to_din(base: str | None) -> str | None:
    if not base:
        return None
    stn_map, _ = _load_maps()
    din_num = stn_map.get(base)
    if not din_num:
        return None
    return _din_catalog_value(din_num)


def iso_base_to_din(base: str | None) -> str | None:
    if not base:
        return None
    _, iso_map = _load_maps()
    din_num = iso_map.get(base)
    if not din_num:
        return None
    return _din_catalog_value(din_num)


def map_standard_to_catalog_din(
    norma: str | None,
    raw_text: str = "",
) -> str | None:
    """
    Premapuje STN/ČSN/ISO v poli norma alebo v texte riadku na DIN pre katalóg SmartHub.
    Ak je už DIN, vráti normalizovaný tvar DIN###.
    """
    combined = " ".join(x for x in (norma, raw_text) if x and str(x).strip()).strip()
    if not combined:
        return None

    key = search_key(norma or "")
    if key.startswith("DIN") and len(key) > 3:
        return _din_catalog_value(key[3:])

    stn_base = extract_stn_base(combined)
    if stn_base:
        mapped = stn_base_to_din(stn_base)
        if mapped:
            return mapped

    iso_base = extract_iso_base(combined)
    if iso_base:
        mapped = iso_base_to_din(iso_base)
        if mapped:
            return mapped

    # Norma môže byť len „STN 02 1401“ bez slova STN v raw_text
    if norma and re.search(r"\bSTN\b|\bČSN\b|\bCSN\b", norma, re.I):
        stn_only = extract_stn_base(norma)
        if stn_only:
            return stn_base_to_din(stn_only)

    m = _DIN_IN_TEXT.search(combined)
    if m:
        return _din_catalog_value(m.group(1))

    return None


def stn_to_din_prompt_section() -> str:
    lines = [
        "STN / ČSN 02 xxxx a ISO — v poli norma VŽDY vráť ekvivalentný DIN pre katalóg SmartHub "
        "(nie pôvodné STN). Desiatkový suffix STN určuje materiál a pevnosť — doplň surface a v_class:",
        "- .55 → Oceľ pozinkovaná, 8.8  |  .05 → Oceľ pozinkovaná, 5.8  |  .52 → Oceľ, 8.8",
        "- .90 / .92 → Nerez A2, A2-70  |  .50 → Nerez A2, A2-80  |  .8 → Mosadz",
        "- .5 s A4 v texte → Nerez A4, A4-70",
    ]
    for left, right in _PROMPT_EXAMPLES:
        lines.append(f"- {left} → {right}")
    return "\n".join(lines)
