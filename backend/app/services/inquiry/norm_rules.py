from __future__ import annotations

import re

from app.services.inquiry.product_norm_hints import threaded_rod_text

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
        "471",
        "DIN471",
        "472",
        "DIN472",
    }
)

# Bez povinného class (poistné krúžky, … — nie kolíky DIN 6325 s M6/H6).
NORMS_WITHOUT_V_CLASS_KEYS: frozenset[str] = frozenset(
    {
        "471",
        "DIN471",
        "472",
        "DIN472",
    }
)

_PIN_TEXT = re.compile(
    r"\b("
    r"kol[íi]k|capov[ýy]\s+kol[íi]k|valcov[ýy]\s+kol[íi]k|"
    r"cylindrical\s+pin|dowel\s+pin|spring\s+pin"
    r")\b",
    re.IGNORECASE,
)

_NO_LENGTH_TEXT = re.compile(
    r"\b(matic(?:a|e|ou|i|ami)?|podložk(?:a|y|ou|ami)?|washer|mutter|nut)\b",
    re.IGNORECASE,
)

_SNAP_RING_TEXT = re.compile(
    r"\b("
    r"kru[žz]ok\s+poistn|kruzok\s+poistn|poistn(?:[ýy])?\s+kru[žz]ok|"
    r"segerring|snap\s*ring"
    r")\b",
    re.IGNORECASE,
)

_BOLT_TEXT = re.compile(
    r"\b(skrutk(?:a|y|ou|ami)?|šroub|bolt|screw|vrut|skrutka)\b",
    re.IGNORECASE,
)

_NAIL_TEXT = re.compile(
    r"\b(klin(?:ec|ce|ca|cov|cové|cových)?|hreb(?:ík|ik|iky|íkov|ikov)?|hřeb(?:ík|ik|iky|íkov|ikov)?)\b",
    re.IGNORECASE,
)


def norm_key(norma: str | None) -> str:
    return search_key(norma)


def _norm_num_key(norma: str | None) -> str:
    key = norm_key(norma)
    if key.startswith("DIN") and len(key) > 3:
        return key[3:]
    return key


def _pin_base_norm(norma: str | None) -> str:
    num = _norm_num_key(norma)
    m = re.match(r"^(\d+)", num)
    return m.group(1) if m else num


def is_snap_ring_norm(norma: str | None, raw_text: str = "") -> bool:
    if _norm_num_key(norma) in ("471", "472"):
        return True
    return bool(_SNAP_RING_TEXT.search(raw_text or ""))


def is_pin_norm(norma: str | None, raw_text: str = "") -> bool:
    num = _norm_num_key(norma)
    if num in ("6325", "7979", "1481", "7346"):
        return True
    if num.startswith("6325") or num.startswith("7979"):
        return True
    return bool(_PIN_TEXT.search(raw_text or ""))


def extract_snap_ring_diameter(raw_text: str) -> str | None:
    """Priemer hriadeľa D pre poistný krúžok DIN 471/472."""
    t = raw_text or ""
    m = re.search(r"\bD1\s*=\s*(\d+(?:[,.]\d+)?)\b", t, re.IGNORECASE)
    if m:
        return m.group(1).replace(",", ".")
    m = re.search(
        r"\b(?:kru[žz]ok|kruzok)\s+poistn(?:[ýy])?\s+D\s*(\d+(?:[,.]\d+)?)\b",
        t,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).replace(",", ".")
    m = re.search(
        r"\b(?:kru[žz]ok|kruzok)\s+poistn(?:[ýy])?\s+(\d+(?:[,.]\d+)?)\b",
        t,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).replace(",", ".")
    m = re.search(
        r"(?:ocel|oce[lľ]|nerez|mosadz|pr[uú]žinov(?:[áa]|a))[^0-9]{0,48}(\d+(?:[,.]\d+)?)\s*MM\b",
        t,
        re.IGNORECASE,
    )
    if m:
        val = m.group(1).replace(",", ".")
        if val not in ("471", "472"):
            return val
    m = re.search(r"(\d+(?:[,.]\d+)?)\s*MM\s*$", t.strip(), re.IGNORECASE)
    if m:
        val = m.group(1).replace(",", ".")
        if val not in ("471", "472"):
            return val
    return None


def extract_pin_dimensions(raw_text: str) -> tuple[str, str] | None:
    """Priemer × dĺžka kolíka — typicky na konci textu (3x30MM, 16X60MM)."""
    t = (raw_text or "").strip()
    if not t:
        return None
    m = re.search(
        r"(\d+(?:[,.]\d+)?)\s*[x×X]\s*(\d+(?:[,.]\d+)?)\s*MM\s*$",
        t,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).replace(",", "."), m.group(2).replace(",", ".")
    m = re.search(
        r"(\d+(?:[,.]\d+)?)\s*[x×X]\s*(\d+(?:[,.]\d+)?)\s*MM\b",
        t,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).replace(",", "."), m.group(2).replace(",", ".")
    last: tuple[str, str] | None = None
    for m in re.finditer(
        r"(\d+(?:[,.]\d+)?)\s*[x×X]\s*(\d+(?:[,.]\d+)?)(?:\s*MM\b)?",
        t,
        re.IGNORECASE,
    ):
        d = m.group(1).replace(",", ".")
        ln = m.group(2).replace(",", ".")
        try:
            if float(d) > 80 or float(ln) > 500:
                continue
        except ValueError:
            continue
        last = (d, ln)
    return last


def extract_pin_tolerance_fit(raw_text: str) -> str | None:
    """Tolerancia hriadeľa pre kolík DIN 6325 — „tolerancia m6“ → M6 (v katalógu aj v class)."""
    t = raw_text or ""
    m = re.search(r"toleranc(?:ia|ie|iou)?\s+([mh])(\d+)\b", t, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    return None


def extract_pin_catalog_norma(raw_text: str, *, base_norm: str | None = None) -> str | None:
    """DIN 7979 D → „7979 D“ (suffix v poli norma v DB)."""
    t = raw_text or ""
    base = _norm_num_key(base_norm)
    if base == "7979" or re.search(r"\bDIN\s*7979\s+D\b", t, re.IGNORECASE):
        return "7979 D"
    return None


_NORMS_WITH_LENGTH_KEYS: frozenset[str] = frozenset(
    {
        "933",
        "DIN933",
        "975",
        "DIN975",
        "976",
        "DIN976",
        "931",
        "DIN931",
        "6914",
        "DIN6914",
        "1151",
        "DIN1151",
        "6325",
        "DIN6325",
        "7979",
        "DIN7979",
    }
)

def norm_requires_length(norma: str | None, raw_text: str = "") -> bool:
    key = norm_key(norma)
    if key in NORMS_WITHOUT_LENGTH_KEYS:
        return False
    if key.startswith("DIN") and key[3:] in NORMS_WITHOUT_LENGTH_KEYS:
        return False
    if _NO_LENGTH_TEXT.search(raw_text or ""):
        return False
    if is_snap_ring_norm(norma, raw_text):
        return False
    if is_pin_norm(norma, raw_text):
        return True
    if key in _NORMS_WITH_LENGTH_KEYS:
        return True
    if key.startswith("DIN") and key[3:] in _NORMS_WITH_LENGTH_KEYS:
        return True
    if threaded_rod_text(raw_text):
        return True
    if _NAIL_TEXT.search(raw_text or ""):
        return True
    return bool(_BOLT_TEXT.search(raw_text or ""))


def norm_requires_v_class(norma: str | None, raw_text: str = "") -> bool:
    key = norm_key(norma)
    if key in NORMS_WITHOUT_V_CLASS_KEYS:
        return False
    if key.startswith("DIN") and key[3:] in NORMS_WITHOUT_V_CLASS_KEYS:
        return False
    if _norm_num_key(norma) in ("471", "472"):
        return False
    if is_snap_ring_norm(norma, raw_text):
        return False
    if is_pin_norm(norma, raw_text):
        return _pin_base_norm(norma) == "6325"
    if key in NORMS_WITHOUT_LENGTH_KEYS:
        return True
    if key.startswith("DIN") and key[3:] in NORMS_WITHOUT_LENGTH_KEYS:
        return True
    if _NO_LENGTH_TEXT.search(raw_text or ""):
        return True
    return norm_requires_length(norma, raw_text)


def inquiry_required_field_names(norma: str | None, raw_text: str = "") -> list[str]:
    """Povinné polia zladené s vyhľadávaním."""
    required = ["norma", "surface", "diameter", "quantity"]
    if norm_requires_length(norma, raw_text):
        required.append("length")
    if norm_requires_v_class(norma, raw_text):
        required.append("v_class")
    return required
