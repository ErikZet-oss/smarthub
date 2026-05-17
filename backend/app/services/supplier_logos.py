"""Ukladanie a servovanie log dodávateľov (súbory v data/supplier_logos)."""

from __future__ import annotations

import os
from typing import Optional

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_REPO_ROOT = os.path.abspath(os.path.join(_DATA_DIR, "..", ".."))
_REPO_LOGO_DIR = os.path.join(_REPO_ROOT, "logo")
_LOGOS_SUBDIR = "supplier_logos"

# Kľúč v názve dodávateľa (bez medzier) → súbor v priečinku logo/ v repozitári.
_REPO_LOGO_SEEDS: list[tuple[str, str]] = [
    ("schachermayer", "schach.png"),
    ("halfmann", "Halfmann-Schrauben.png"),
    ("hopefix", "Hopefix.png"),
    ("inoxmare", "inoxmare.png"),
    ("fabory", "Fabory.jpg"),
    ("argip", "argip.jpg"),
    ("valenta", "valenta.png"),
    ("haspl", "Haspl.png"),
    ("mekrs", "Mekrs.png"),
    ("bmkco", "bmkco.png"),
    ("bmco", "bmkco.png"),
    # Schäfer-Peters — meno môže prísť ako „Schaef", „Schäfer", „Schäfer-Peters".
    # Hľadáme prefix bez diakritiky (logo seed sa robí proti casefold + bez medzier).
    ("schaef", "Schaefer.png"),
]

_CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/x-png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MAX_BYTES = 2 * 1024 * 1024


def supplier_logos_dir() -> str:
    path = os.path.join(_DATA_DIR, _LOGOS_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def supplier_logo_public_url(logo_path: Optional[str]) -> Optional[str]:
    """Relatívna cesta pre frontend: /supplier-logos/{súbor}."""
    if not logo_path or not str(logo_path).strip():
        return None
    base = os.path.basename(str(logo_path).strip())
    if not base or ".." in base or "/" in base or "\\" in base:
        return None
    full = os.path.join(supplier_logos_dir(), base)
    # Cache-busting: po prepísaní rovnakého mena súboru (napr. "{id}.jpg")
    # nech prehliadač okamžite načíta novú verziu loga.
    if os.path.isfile(full):
        try:
            version = int(os.path.getmtime(full))
            return f"/supplier-logos/{base}?v={version}"
        except OSError:
            pass
    return f"/supplier-logos/{base}"


def _safe_stem(supplier_id: int) -> str:
    return str(int(supplier_id))


def remove_supplier_logo_files(supplier_id: int) -> None:
    """Zmaže všetky súbory začínajúce na `{id}.` v priečinku log."""
    stem = _safe_stem(supplier_id)
    d = supplier_logos_dir()
    try:
        for name in os.listdir(d):
            if name.startswith(f"{stem}.") and os.path.isfile(os.path.join(d, name)):
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
    except OSError:
        pass


def save_supplier_logo_upload(
    supplier_id: int,
    content_type: Optional[str],
    data: bytes,
    filename: Optional[str] = None,
) -> str:
    """
    Uloží obrázok ako `{id}.{ext}`. Vymaže predchádzajúce varianty toho istého id.
    Vráti basename (pre stĺpec logo_path).
    """
    if len(data) > _MAX_BYTES:
        raise ValueError("Súbor je príliš veľký (max 2 MB).")
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = _CONTENT_TYPE_EXT.get(ct)
    if not ext and filename:
        low = str(filename).strip().lower()
        if low.endswith(".png"):
            ext = ".png"
        elif low.endswith(".jpg") or low.endswith(".jpeg"):
            ext = ".jpg"
        elif low.endswith(".webp"):
            ext = ".webp"
        elif low.endswith(".gif"):
            ext = ".gif"
    if not ext:
        raise ValueError("Povolené formáty: PNG, JPEG, WebP, GIF.")
    if not data:
        raise ValueError("Prázdny súbor.")

    remove_supplier_logo_files(supplier_id)
    stem = _safe_stem(supplier_id)
    basename = f"{stem}{ext}"
    path = os.path.join(supplier_logos_dir(), basename)
    with open(path, "wb") as handle:
        handle.write(data)
    return basename


def _supplier_name_compact(name: str) -> str:
    return (name or "").casefold().replace(" ", "").replace("-", "")


def _repo_logo_for_supplier_name(name: str) -> Optional[str]:
    compact = _supplier_name_compact(name)
    if not compact:
        return None
    for key, filename in _REPO_LOGO_SEEDS:
        if key in compact:
            path = os.path.join(_REPO_LOGO_DIR, filename)
            if os.path.isfile(path):
                return path
    return None


def seed_supplier_logos_from_repo(session) -> int:
    """
    Skopíruje logá z priečinka logo/ v repozitári do data/supplier_logos/
    a nastaví supplier.logo_path. Na Renderi inak zostanú prázdne, ak sa nenahrá cez UI.
    """
    from sqlmodel import select

    from app.models.entities import Supplier

    if not os.path.isdir(_REPO_LOGO_DIR):
        return 0
    updated = 0
    for supplier in session.exec(select(Supplier)).all():
        if supplier.id is None:
            continue
        src = _repo_logo_for_supplier_name(supplier.name or "")
        if not src:
            continue
        ext = os.path.splitext(src)[1].lower()
        ct_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        content_type = ct_map.get(ext, "image/png")
        with open(src, "rb") as handle:
            data = handle.read()
        basename = save_supplier_logo_upload(
            int(supplier.id),
            content_type,
            data,
            filename=os.path.basename(src),
        )
        supplier.logo_path = basename
        session.add(supplier)
        updated += 1
    return updated
