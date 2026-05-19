from __future__ import annotations

from pathlib import Path

import pytest

from app.services.product_images import load_product_image_response, resolve_product_image_path


def test_resolve_product_image_path_finds_020_tif() -> None:
    p = resolve_product_image_path("020.tif")
    assert p is not None
    assert p.name == "020.tif"


def test_load_product_image_response_converts_tif_to_jpeg() -> None:
    p = resolve_product_image_path("020.tif")
    if p is None:
        pytest.skip("images/020.tif not present")
    body, media_type = load_product_image_response("020.tif")
    assert media_type == "image/jpeg"
    assert len(body) > 100
    assert body[:2] == b"\xff\xd8"
