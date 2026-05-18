import pytest

from app.services.mekrs_http_client import (
    _fulltext_items_for_code,
    _mekrs_code_key,
    _mekrs_code_keys_compatible,
)


def test_mekrs_code_key_strips_commas_and_dots():
    assert _mekrs_code_key("10000,20,00,200,000") == _mekrs_code_key(
        "10000.20.00.200.000"
    )


def test_mekrs_code_keys_compatible_trailing_zeros():
    canon = _mekrs_code_key("10000.20.00.200.000")
    short = _mekrs_code_key("10000.20.00.200")
    assert _mekrs_code_keys_compatible(short, canon)
    assert _mekrs_code_keys_compatible(canon, short)


def test_mekrs_code_keys_compatible_rejects_unrelated():
    a = _mekrs_code_key("10000.20.00.200.000")
    b = _mekrs_code_key("10000.20.00.300.000")
    assert not _mekrs_code_keys_compatible(a, b)


def test_fulltext_items_relaxed_when_db_code_shorter():
    items = [
        {
            "sku2": "10000.20.00.200.000",
            "slug": "mat-presna-a2-m20-19562",
        }
    ]
    key = _mekrs_code_key("10000.20.00.200")
    hit = _fulltext_items_for_code(items, key, fulltext_count=1)
    assert len(hit) == 1
    assert hit[0]["slug"] == "mat-presna-a2-m20-19562"


def test_fulltext_items_exact_with_commas_in_db_code():
    items = [
        {
            "sku2": "10000.20.00.200.000",
            "slug": "mat-presna-a2-m20-19562",
        }
    ]
    key = _mekrs_code_key("10000,20,00,200,000")
    hit = _fulltext_items_for_code(items, key, fulltext_count=1)
    assert len(hit) == 1


@pytest.mark.asyncio
async def test_search_product_live_shorter_code() -> None:
    """Integrácia proti eshop.mekrs.cz — preskočí ak sieť nie je."""
    from app.services.mekrs_http_client import MekrsHttpClient

    try:
        async with MekrsHttpClient() as c:
            c._ensure_display_currency_cookie()
            blob = await c.search_product("10000.20.00.200")
    except Exception as exc:
        pytest.skip(f"Mekrs API nedostupné: {exc}")
    assert len(blob.get("variants") or []) >= 1
