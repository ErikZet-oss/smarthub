from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductNormHint:
    """
    Ak text dopytu neobsahuje explicitné DIN/ISO, doplní normu podľa typu produktu.

    Nové pravidlo: pridaj riadok do PRODUCT_NORM_HINTS (vzor je dole).
    """

    patterns: tuple[str, ...]
    norma: str
    description_sk: str
    requires_length: bool = True
    requires_v_class: bool = True


# Poradie má význam — prvé zhoda vyhrá.
PRODUCT_NORM_HINTS: tuple[ProductNormHint, ...] = (
    ProductNormHint(
        patterns=(
            r"závitov(?:á|é|ých|ou|e|y)?\s+ty",
            r"zavitov(?:a|e|ych|ou|y)?\s+ty",
            r"threaded\s+rod",
        ),
        norma="DIN976",
        description_sk=(
            "Závitová tyč (aj bez uvedenia DIN v texte) — v katalógu SmartHub norma 976 / DIN 976. "
            "Na trhu sa niekedy uvádza DIN 975; pre náš katalóg preferuj DIN976."
        ),
    ),
    ProductNormHint(
        patterns=(
            r"šesťhrann(?:á|é|ých|ou|e|y)?\s+matic",
            r"sestihrann(?:a|e|ych|ou|y)?\s+matic",
            r"\bmatic(?:a|e|ou|i|ami)?\b",
        ),
        norma="DIN934",
        description_sk="Šesťhranná matica bez uvedenej normy → DIN 934",
        requires_length=False,
        requires_v_class=False,
    ),
    ProductNormHint(
        patterns=(
            r"podložk(?:a|y|ou|ami)?",
            r"washer",
        ),
        norma="DIN125",
        description_sk="Podložka bez uvedenej normy → DIN 125 (v katalógu 125a)",
        requires_length=False,
        requires_v_class=False,
    ),
)

_COMPILED: list[tuple[re.Pattern[str], ProductNormHint]] = [
    (re.compile(p, re.IGNORECASE), hint)
    for hint in PRODUCT_NORM_HINTS
    for p in hint.patterns
]

_EXPLICIT_NORM = re.compile(
    r"\b(?:DIN|ISO|EN|STN|CSN|ČSN)\s*[-]?\s*\d",
    re.IGNORECASE,
)


def infer_norma_from_text(raw_text: str) -> str | None:
    """Doplní normu z názvu produktu, ak v texte nie je explicitné DIN/ISO."""
    text = (raw_text or "").strip()
    if not text or _EXPLICIT_NORM.search(text):
        return None
    seen: set[str] = set()
    for pattern, hint in _COMPILED:
        if hint.norma in seen:
            continue
        if pattern.search(text):
            seen.add(hint.norma)
            return hint.norma
    return None


def hint_for_norma(norma: str | None) -> ProductNormHint | None:
    key = search_key(norma)
    for hint in PRODUCT_NORM_HINTS:
        hint_key = search_key(hint.norma)
        if key == hint_key or key == hint_key.replace("DIN", ""):
            return hint
    return None


def search_key(value: str | None) -> str:
    if not value:
        return ""
    s = str(value).upper().strip()
    return re.sub(r"[\s\-_./]+", "", s)


def product_norm_hints_for_prompt() -> str:
    lines = [
        "Mapovanie typu produktu na normu (ak v texte nie je explicitné DIN/ISO):"
    ]
    for hint in PRODUCT_NORM_HINTS:
        lines.append(f"- {hint.description_sk} → norma {hint.norma}")
    return "\n".join(lines)


_THREADED_ROD = re.compile(
    r"závitov(?:á|é|ých|ou|e|y)?\s+ty|zavitov(?:a|e|ych|ou|y)?\s+ty|threaded\s+rod",
    re.IGNORECASE,
)


def threaded_rod_text(raw_text: str) -> bool:
    return bool(_THREADED_ROD.search(raw_text or ""))
