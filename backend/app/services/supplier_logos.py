"""Ukladanie a servovanie log dodávateľov (súbory v data/supplier_logos)."""

from __future__ import annotations

import os
from typing import Optional

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_LOGOS_SUBDIR = "supplier_logos"

_CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
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
) -> str:
    """
    Uloží obrázok ako `{id}.{ext}`. Vymaže predchádzajúce varianty toho istého id.
    Vráti basename (pre stĺpec logo_path).
    """
    if len(data) > _MAX_BYTES:
        raise ValueError("Súbor je príliš veľký (max 2 MB).")
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = _CONTENT_TYPE_EXT.get(ct)
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
