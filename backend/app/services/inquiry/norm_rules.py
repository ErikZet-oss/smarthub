from __future__ import annotations

import re

def search_key(value: str | None) -> str:
    if not value:
        return ""
    s = str(value).upper().strip()
    return re.sub(r"[\s\-_./]+", "", s)

# Normy / produkty bez merateľnej dĺžky (matice, podložky…) — v DB býva length = "0".
NORMS_WITHOUT_LENGTH_KEYS: frozenset[str] = frozenset(
    {
        "934",
        "DIN934",
        "985",
        "DIN985",
        "6923",
        "DIN6923",
        "125",
        "DIN125",
        "127",
        "DIN127",
        "433",
        "DIN433",
        "439",
        "DIN439",
        "315",
        "DIN315",
        "9021",
        "DIN9021",
        "798",
        "DIN798",
        "4032",
        "ISO4032",
        "7089",
        "ISO7089",
    }
)

_NO_LENGTH_TEXT = re.compile(
    r"\b(matic(?:a|e|ou|i|ami)?|podložk(?:a|y|ou|ami)?|washer|mutter|nut)\b",
    re.IGNORECASE,
)

_BOLT_TEXT = re.compile(
    r"\b(skrutk(?:a|y|ou|ami)?|šroub|bolt|screw|vrut|skrutka)\b",
    re.IGNORECASE,
)


def norm_key(norma: str | None) -> str:
    return search_key(norma)


def norm_requires_length(norma: str | None, raw_text: str = "") -> bool:
    key = norm_key(norma)
    if key in NORMS_WITHOUT_LENGTH_KEYS:
        return False
    if key.startswith("DIN") and key[3:] in NORMS_WITHOUT_LENGTH_KEYS:
        return False
    if _NO_LENGTH_TEXT.search(raw_text or ""):
        return False
    return bool(_BOLT_TEXT.search(raw_text or ""))


def norm_requires_v_class(norma: str | None, raw_text: str = "") -> bool:
    if not norm_requires_length(norma, raw_text):
        return False
    return True


def inquiry_required_field_names(norma: str | None, raw_text: str = "") -> list[str]:
    """Povinné polia zladené s vyhľadávaním (norma, priemer, ks + podmienene dĺžka/trieda)."""
    required = ["norma", "diameter", "quantity"]
    if norm_requires_length(norma, raw_text):
        required.append("length")
    if norm_requires_v_class(norma, raw_text):
        required.append("v_class")
    return required
