from app.services.inquiry.norm_rules import (
    catalog_value_in_options,
    inquiry_catalog_fields_to_validate,
    is_snap_ring_norm,
)


def test_snap_ring_text_hriadeľovy_kružok() -> None:
    raw = "Poistný hriadeľový krúžok - normálny typ DIN 471 Pružinová oceľ 36MM"
    assert is_snap_ring_norm("471", raw) is True
    assert is_snap_ring_norm(None, raw) is True


def test_catalog_fields_to_validate_skips_length_for_din471() -> None:
    raw = "Poistný hriadeľový krúžok - normálny typ DIN 471 Pružinová oceľ 36MM"
    fields = inquiry_catalog_fields_to_validate(
        "471",
        raw,
        length="0",
        v_class="0",
    )
    assert fields == ("norma", "surface", "diameter")


def test_catalog_value_in_options_norma_din_prefix() -> None:
    assert catalog_value_in_options("471", ["471", "472"]) is True
    assert catalog_value_in_options("471", ["DIN471", "472"]) is True
