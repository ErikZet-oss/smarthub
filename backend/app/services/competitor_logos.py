"""Logá konkurencie — data/competitor_logos (rovnaký pattern ako supplier_logos)."""

from __future__ import annotations

import os
from typing import Optional

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_LOGOS_SUBDIR = "competitor_logos"

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


def competitor_logos_dir() -> str:
    path = os.path.join(_DATA_DIR, _LOGOS_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def competitor_logo_public_url(logo_path: Optional[str]) -> Optional[str]:
    if not logo_path or not str(logo_path).strip():
        return None
    base = os.path.basename(str(logo_path).strip())
    if not base or ".." in base or "/" in base or "\\" in base:
        return None
    full = os.path.join(competitor_logos_dir(), base)
    if os.path.isfile(full):
        try:
            version = int(os.path.getmtime(full))
            return f"/competitor-logos/{base}?v={version}"
        except OSError:
            pass
    return f"/competitor-logos/{base}"


def remove_competitor_logo_files(competitor_id: int) -> None:
    stem = str(int(competitor_id))
    d = competitor_logos_dir()
    try:
        for name in os.listdir(d):
            if name.startswith(f"{stem}.") and os.path.isfile(os.path.join(d, name)):
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
    except OSError:
        pass


def save_competitor_logo_upload(
    competitor_id: int,
    content_type: Optional[str],
    data: bytes,
    filename: Optional[str] = None,
) -> str:
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
    remove_competitor_logo_files(competitor_id)
    basename = f"{int(competitor_id)}{ext}"
    path = os.path.join(competitor_logos_dir(), basename)
    with open(path, "wb") as handle:
        handle.write(data)
    return basename
