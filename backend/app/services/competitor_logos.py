"""Logá konkurencie — data/competitor_logos (rovnaký pattern ako supplier_logos)."""

from __future__ import annotations

import os
from typing import Optional

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
_REPO_ROOT = os.path.abspath(os.path.join(_DATA_DIR, "..", ".."))
_REPO_LOGO_DIR = os.path.join(_REPO_ROOT, "logo")
_LOGOS_SUBDIR = "competitor_logos"

_REPO_LOGO_SEEDS: list[tuple[str, str]] = [
    ("svx", "SVX.png"),
    ("oramat", "oramat.png"),
    ("bbtechnik", "bbtechnik.jpg"),
    ("vkpsteel", "vkp.png"),
    ("vkp", "vkp.png"),
    ("feva", "Feva.png"),
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


def _competitor_name_compact(name: str) -> str:
    return (name or "").casefold().replace(" ", "").replace("-", "")


def _repo_logo_for_competitor_name(name: str) -> Optional[str]:
    compact = _competitor_name_compact(name)
    if not compact:
        return None
    for key, filename in _REPO_LOGO_SEEDS:
        if key in compact:
            path = os.path.join(_REPO_LOGO_DIR, filename)
            if os.path.isfile(path):
                return path
    return None


def seed_competitor_logos_from_repo(session) -> int:
    """Skopíruje logá z logo/ do data/competitor_logos/ pri štarte API."""
    from sqlmodel import select

    from app.models.entities import Competitor

    if not os.path.isdir(_REPO_LOGO_DIR):
        return 0
    updated = 0
    for competitor in session.exec(select(Competitor)).all():
        if competitor.id is None:
            continue
        src = _repo_logo_for_competitor_name(competitor.name or "")
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
        basename = save_competitor_logo_upload(
            int(competitor.id),
            content_type,
            data,
            filename=os.path.basename(src),
        )
        competitor.logo_path = basename
        session.add(competitor)
        updated += 1
    return updated
