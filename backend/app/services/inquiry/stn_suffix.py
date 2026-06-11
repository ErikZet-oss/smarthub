from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.inquiry.stn_to_din import extract_stn_base

_STN_SUFFIX = re.compile(
    r"\b(?:STN|ČSN|CSN)\s*(?:EN\s*(?:ISO\s*)?)?(?:0?\s*2\s*)?(\d{4})\.(\d+)\b",
    re.IGNORECASE,
)

# Podložky — prvý digit = materiál (K2L tabuľka pre ČSN 02 1702.X0).
_WASHER_STN_BASES = frozenset(
    {
        "1702",
        "1703",
        "1706",
        "1708",
        "1721",
        "1724",
        "1726",
        "1727",
        "1728",
        "1731",
        "1733",
        "1734",
        "1739",
        "1740",
        "1741",
        "1744",
        "1745",
        "1746",
    }
)

# Skrutky / matice — prvý digit = pevnosť, druhý = povrch (K2L, ČSN 02 1103.X0 / .0X).
_FASTENER_STRENGTH: dict[str, str | None] = {
    "0": "5.8",  # prax: 1401.05 = 5.8 zinek (srouby.net)
    "1": "5.6",
    "2": "5.8",
    "3": "6.8",
    "5": "8.8",
    "7": "10.9",
    "8": None,  # mosadz
    "9": "12.9",
}

_FASTENER_SURFACE: dict[str, str | None] = {
    "0": "Oceľ",
    "1": "Oceľ",
    "2": "Oceľ",
    "3": "Oceľ",
    "4": "Oceľ",
    "5": "Oceľ pozinkovaná",
    "6": "Mosadz",
    "7": "Oceľ",
    "8": "Oceľ",
    "9": None,
}

_WASHER_MATERIAL: dict[str, str] = {
    "0": "Oceľ",
    "1": "Oceľ",
    "2": "Hliník",
    "3": "Oceľ",
    "4": "Oceľ",
    "5": "Mosadz",
    "6": "Oceľ",
    "7": "Oceľ",
    "8": "Oceľ",
    "9": "Oceľ",
}

# Presné suffixy pre nerez (ISO 3506) — majú prednosť pred XY dekódovaním.
_STAINLESS_SUFFIX: dict[str, tuple[str, str]] = {
    "50": ("Nerez A2", "A2-80"),
    "90": ("Nerez A2", "A2-70"),
    "92": ("Nerez A2", "A2-70"),
}

_SINGLE_DIGIT_SUFFIX: dict[str, tuple[str, str | None]] = {
    "8": ("Mosadz", "0"),
}


@dataclass(frozen=True)
class StnSuffixMatch:
    base: str
    suffix: str


@dataclass(frozen=True)
class StnMaterialHint:
    surface: str | None
    v_class: str | None


def extract_stn_suffix(text: str) -> StnSuffixMatch | None:
    """Vytiahne desatinný suffix STN/ČSN 02 xxxx.NN z textu."""
    m = _STN_SUFFIX.search(text or "")
    if not m:
        return None
    return StnSuffixMatch(base=m.group(1), suffix=m.group(2))


def _stainless_from_text(raw_text: str) -> tuple[str, str] | None:
    low = (raw_text or "").casefold()
    if "a4" in low and ("nerez" in low or "a4" in low):
        return "Nerez A4", "A4-70"
    if "a2-80" in low or "a2 80" in low:
        return "Nerez A2", "A2-80"
    if "a2" in low or "nerez" in low:
        return "Nerez A2", "A2-70"
    return None


def _decode_two_digit_fastener(suffix: str) -> StnMaterialHint:
    strength_digit, surface_digit = suffix[0], suffix[1]
    if strength_digit == "8":
        return StnMaterialHint(surface="Mosadz", v_class="0")
    surface = _FASTENER_SURFACE.get(surface_digit)
    v_class = _FASTENER_STRENGTH.get(strength_digit)
    if surface_digit == "5" and surface:
        # Pozink — ak pevnosť nie je známa, necháme infer z povrchu.
        pass
    return StnMaterialHint(surface=surface, v_class=v_class)


def _decode_two_digit_washer(suffix: str) -> StnMaterialHint:
    material_digit, surface_digit = suffix[0], suffix[1]
    material = _WASHER_MATERIAL.get(material_digit, "Oceľ")
    surface_code = _FASTENER_SURFACE.get(surface_digit)
    if surface_code == "Oceľ pozinkovaná":
        return StnMaterialHint(surface="Oceľ pozinkovaná", v_class="0")
    if material == "Mosadz":
        return StnMaterialHint(surface="Mosadz", v_class="0")
    if material == "Hliník":
        return StnMaterialHint(surface="Hliník", v_class="P40")
    if surface_code == "Mosadz":
        return StnMaterialHint(surface="Mosadz", v_class="0")
    return StnMaterialHint(surface=material, v_class="0")


def decode_stn_suffix(
    *,
    base: str | None,
    suffix: str | None,
    raw_text: str = "",
    is_washer: bool = False,
) -> StnMaterialHint | None:
    """
    Dekóduje STN suffix na surface + v_class pre katalóg SmartHub.
    Vracia None ak suffix chýba alebo nie je rozpoznaný.
    """
    if not suffix:
        return None

    stainless_text = _stainless_from_text(raw_text)

    if suffix in _STAINLESS_SUFFIX:
        surf, cls = _STAINLESS_SUFFIX[suffix]
        if suffix == "50" and stainless_text and stainless_text[1] == "A2-80":
            return StnMaterialHint(surface=stainless_text[0], v_class=stainless_text[1])
        return StnMaterialHint(surface=surf, v_class=cls)

    if suffix == "91":
        if stainless_text:
            return StnMaterialHint(surface=stainless_text[0], v_class=stainless_text[1])
        return StnMaterialHint(surface="Nerez A2", v_class="A2-70")

    if len(suffix) == 1:
        if suffix == "5":
            if stainless_text:
                return StnMaterialHint(surface=stainless_text[0], v_class=stainless_text[1])
            return None
        mapped = _SINGLE_DIGIT_SUFFIX.get(suffix)
        if mapped:
            return StnMaterialHint(surface=mapped[0], v_class=mapped[1])
        return None

    if len(suffix) >= 2:
        washer = is_washer or (base in _WASHER_STN_BASES)
        if washer:
            hint = _decode_two_digit_washer(suffix[:2])
            if suffix.startswith("9") and stainless_text:
                return StnMaterialHint(surface=stainless_text[0], v_class=stainless_text[1])
            return hint
        return _decode_two_digit_fastener(suffix[:2])

    return None


def infer_material_from_stn_text(
    raw_text: str,
    *,
    norma: str | None = None,
    is_washer: bool = False,
) -> StnMaterialHint | None:
    """Convenience: suffix z raw_text / norma + dekódovanie."""
    combined = " ".join(x for x in (norma, raw_text) if x and str(x).strip()).strip()
    if not combined:
        return None
    match = extract_stn_suffix(combined)
    if match is None:
        return None
    base = match.base or extract_stn_base(combined)
    return decode_stn_suffix(
        base=base,
        suffix=match.suffix,
        raw_text=combined,
        is_washer=is_washer,
    )
