from __future__ import annotations

from io import BytesIO
from pathlib import Path

_TIFF_SUFFIXES = {".tif", ".tiff"}
_RASTER_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def product_images_dir() -> Path:
    """
    Lokálny adresár s obrázkami produktov (mimo backend foldera):
    <workspace>/images
    """
    backend_root = Path(__file__).resolve().parent.parent.parent
    workspace_root = backend_root.parent
    p = workspace_root / "images"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_basename(filename: str) -> str:
    name = Path((filename or "").strip().replace("\\", "/")).name
    if not name or name in {".", ".."}:
        return ""
    return name


def resolve_product_image_path(filename: str) -> Path | None:
    """Nájde súbor v images/ (presný názov, potom case-insensitive)."""
    name = _safe_basename(filename)
    if not name:
        return None
    base = product_images_dir()
    direct = base / name
    if direct.is_file():
        return direct
    key = name.lower()
    for entry in base.iterdir():
        if entry.is_file() and entry.name.lower() == key:
            return entry
    return None


def _tiff_to_jpeg_bytes(path: Path) -> bytes:
    from PIL import Image

    with Image.open(path) as im:
        rgb = im.convert("RGB")
        buf = BytesIO()
        rgb.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()


def load_product_image_response(filename: str) -> tuple[bytes, str]:
    """Telá + media-type pre HTTP odpoveď. TIFF konvertuje na JPEG (prehliadače ho nezobrazia)."""
    path = resolve_product_image_path(filename)
    if path is None:
        raise FileNotFoundError(filename)
    ext = path.suffix.lower()
    if ext in _TIFF_SUFFIXES:
        return _tiff_to_jpeg_bytes(path), "image/jpeg"
    media = _RASTER_MEDIA.get(ext)
    if media:
        return path.read_bytes(), media
    return path.read_bytes(), "application/octet-stream"
