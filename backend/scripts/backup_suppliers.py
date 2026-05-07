"""
Záloha všetkých dodávateľov: SQLite riadky, logá (data/supplier_logos),
voliteľne Playwright relácie (data/playwright_sessions).

Spustenie z priečinka backend:
  .venv\\Scripts\\python scripts\\backup_suppliers.py
  .venv\\Scripts\\python scripts\\backup_suppliers.py C:\\cesta\\kam\\zalohovat

Výstup: data/backups/suppliers_YYYYMMDD_HHMMSS/
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

# backend/ ako koreň importov
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db import engine  # noqa: E402
from app.models.entities import Supplier  # noqa: E402
from app.services.supplier_logos import supplier_logos_dir  # noqa: E402


def _data_dir() -> Path:
    return _BACKEND_ROOT / "data"


def _playwright_sessions_dir() -> Path:
    return _data_dir() / "playwright_sessions"


def run_backup(dest_parent: Path | None = None) -> Path:
    dest_parent = dest_parent or (_data_dir() / "backups")
    dest_parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = dest_parent / f"suppliers_{stamp}"
    logos_out = out / "logos"
    logos_out.mkdir(parents=True, exist_ok=True)

    logos_src = Path(supplier_logos_dir())

    rows: list[dict] = []
    with Session(engine) as session:
        suppliers = session.exec(select(Supplier).order_by(Supplier.id)).all()
        for s in suppliers:
            d = s.model_dump()
            rows.append(d)
            lp = d.get("logo_path")
            if lp and str(lp).strip():
                name = Path(str(lp)).name
                src = logos_src / name
                if src.is_file():
                    shutil.copy2(src, logos_out / name)

    pw_src = _playwright_sessions_dir()
    if pw_src.is_dir() and any(pw_src.iterdir()):
        shutil.copytree(pw_src, out / "playwright_sessions", dirs_exist_ok=True)

    manifest = {
        "backup_type": "suppliers",
        "version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "supplier_count": len(rows),
        "includes_logos": True,
        "includes_playwright_sessions": pw_src.is_dir()
        and any(pw_src.iterdir()),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / "suppliers.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out


def main() -> None:
    dest = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if dest is not None and not dest.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
    out = run_backup(dest_parent=dest)
    print(out)


if __name__ == "__main__":
    main()
