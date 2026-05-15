"""Logo našej firmy pre PDF ponuky (data/company_logos)."""

from __future__ import annotations

import os
from typing import Optional

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_LOGOS_SUBDIR = "company_logos"
_LOGO_BASENAME = "company"

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


def company_logos_dir() -> str:
    path = os.path.join(_DATA_DIR, _LOGOS_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def company_logo_public_url(logo_path: Optional[str]) -> Optional[str]:
    if not logo_path or not str(logo_path).strip():
        return None
    base = os.path.basename(str(logo_path).strip())
    if not base or ".." in base or "/" in base or "\\" in base:
        return None
    full = os.path.join(company_logos_dir(), base)
    if os.path.isfile(full):
        try:
            version = int(os.path.getmtime(full))
            return f"/company-logos/{base}?v={version}"
        except OSError:
            pass
    return f"/company-logos/{base}"


def company_logo_file_path(logo_path: Optional[str]) -> Optional[str]:
    if not logo_path:
        return None
    base = os.path.basename(str(logo_path).strip())
    if not base or ".." in base:
        return None
    full = os.path.join(company_logos_dir(), base)
    return full if os.path.isfile(full) else None


def remove_company_logo_files() -> None:
    d = company_logos_dir()
    prefix = f"{_LOGO_BASENAME}."
    try:
        for name in os.listdir(d):
            if name.startswith(prefix) and os.path.isfile(os.path.join(d, name)):
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
    except OSError:
        pass


def save_company_logo_upload(
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

    remove_company_logo_files()
    basename = f"{_LOGO_BASENAME}{ext}"
    path = os.path.join(company_logos_dir(), basename)
    with open(path, "wb") as handle:
        handle.write(data)
    return basename
