from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.services import excel_startup_sync as mod

_ENABLED = {"SMARTHUB_ENABLE_AUTO_EXCEL_SYNC": "1", "SMARTHUB_DISABLE_AUTO_EXCEL_SYNC": ""}


def test_sync_disabled_by_default() -> None:
    env = {k: v for k, v in os.environ.items()
           if k not in ("SMARTHUB_ENABLE_AUTO_EXCEL_SYNC",)}
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(mod, "_excel_din_row_count", return_value=77858),
        patch.object(mod, "_db_product_count", return_value=0),
        patch.object(mod, "threading") as threading_mod,
    ):
        mod._start_excel_sync_if_stale()
        threading_mod.Thread.assert_not_called()


def test_sync_skips_when_db_caught_up() -> None:
    with (
        patch.dict(os.environ, _ENABLED),
        patch.object(mod, "resolve_gamechanger_xlsx_path", return_value="/x.xlsx"),
        patch.object(mod, "_excel_din_row_count", return_value=1000),
        patch.object(mod, "_db_product_count", return_value=996),
        patch.object(mod, "threading") as threading_mod,
    ):
        mod._start_excel_sync_if_stale()
        threading_mod.Thread.assert_not_called()


def test_sync_starts_when_enabled_and_many_rows_missing() -> None:
    with (
        patch.dict(os.environ, _ENABLED),
        patch.object(mod, "resolve_gamechanger_xlsx_path", return_value="/x.xlsx"),
        patch.object(mod, "_excel_din_row_count", return_value=77858),
        patch.object(mod, "_db_product_count", return_value=25877),
        patch.object(mod, "threading") as threading_mod,
    ):
        fake_thread = MagicMock()
        threading_mod.Thread.return_value = fake_thread
        mod._start_excel_sync_if_stale()
        threading_mod.Thread.assert_called_once()
        fake_thread.start.assert_called_once()
