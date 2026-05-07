"""
Obnova dodávateľov zo zálohy vytvorenej backup_suppliers.py.

Zachováva ID záznamov (dôležité pre productmapping.supplier_id).

Spustenie z priečinka backend:
  .venv\\Scripts\\python scripts\\restore_suppliers.py data\\backups\\suppliers_20260503_120000
  .venv\\Scripts\\python scripts\\restore_suppliers.py --latest --yes
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from sqlmodel import Session, select

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db import engine  # noqa: E402
from app.models.entities import Supplier  # noqa: E402
from app.services.supplier_logos import supplier_logos_dir  # noqa: E402


def _allowed_supplier_keys() -> set[str]:
    return set(Supplier.model_fields.keys())


def _latest_backup_dir(backups_root: Path) -> Path | None:
    dirs = [p for p in backups_root.glob("suppliers_*") if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def restore(backup_dir: Path, *, assume_yes: bool) -> None:
    backup_dir = backup_dir.resolve()
    if not backup_dir.is_dir():
        raise SystemExit(f"Adresár neexistuje: {backup_dir}")
    data_file = backup_dir / "suppliers.json"
    if not data_file.is_file():
        raise SystemExit(f"Chýba suppliers.json v {backup_dir}")

    raw = json.loads(data_file.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("suppliers.json musí byť pole objektov.")

    keys = _allowed_supplier_keys()
    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append({k: item[k] for k in keys if k in item})

    if not assume_yes:
        print(
            f"Obnovím {len(rows)} dodávateľov z {backup_dir} (prepíšem / doplním podľa ID). "
            "Logá a relácie Playwright sa skopírujú, ak sú v zálohe."
        )
        a = input("Pokračovať? [y/N]: ").strip().lower()
        if a != "y":
            print("Zrušené.")
            return

    logos_dst = Path(supplier_logos_dir())
    logos_bak = backup_dir / "logos"
    if logos_bak.is_dir():
        for f in logos_bak.iterdir():
            if f.is_file():
                shutil.copy2(f, logos_dst / f.name)

    pw_bak = backup_dir / "playwright_sessions"
    pw_dst = _BACKEND_ROOT / "data" / "playwright_sessions"
    if pw_bak.is_dir():
        pw_dst.mkdir(parents=True, exist_ok=True)
        for f in pw_bak.iterdir():
            if f.is_file():
                shutil.copy2(f, pw_dst / f.name)

    with Session(engine) as session:
        for d in sorted(rows, key=lambda x: x.get("id") or 0):
            sid = d.get("id")
            if sid is None:
                continue
            existing = session.get(Supplier, sid)
            if existing is None:
                session.add(Supplier(**d))
            else:
                for k, v in d.items():
                    setattr(existing, k, v)
        session.commit()

    print(f"Hotovo: obnovených {len(rows)} záznamov.")


def main() -> None:
    flags = {"--yes", "--latest"}
    args = [a for a in sys.argv[1:] if a not in flags]
    assume_yes = "--yes" in sys.argv
    if "--latest" in sys.argv:
        root = _BACKEND_ROOT / "data" / "backups"
        picked = _latest_backup_dir(root)
        if picked is None:
            raise SystemExit(f"V {root} nie je žiadna záloha suppliers_*")
        backup_dir = picked
        print(f"Používam zálohu: {backup_dir}")
    elif args:
        backup_dir = Path(args[0])
    else:
        raise SystemExit(
            "Použitie: restore_suppliers.py <cesta_k_zálohe> [--yes]\n"
            "       alebo restore_suppliers.py --latest [--yes]"
        )
    restore(backup_dir, assume_yes=assume_yes)


if __name__ == "__main__":
    main()
