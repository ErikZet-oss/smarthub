from pathlib import Path


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
