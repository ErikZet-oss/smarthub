"""
Jednorazová migrácia celej aplikácie zo SQLite do externej DB (napr. Neon/Postgres).

Prenáša všetky tabuľky používané appkou:
- supplier
- smarthubuser
- product
- fieldmapping
- usersuppliercredential
- productmapping
- productlist
- productlistitem

Spustenie (z priečinka backend):
  .venv\\Scripts\\python scripts\\migrate_sqlite_to_database.py

Voliteľné ENV:
  SOURCE_SQLITE_PATH=..\\backend\\procurement.db
  TARGET_DATABASE_URL=postgresql+psycopg://... (ak nie je, použije DATABASE_URL)

Poznámka:
- Cieľová DB sa pred importom vyčistí (truncate/delete), aby migrácia bola konzistentná.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.entities import (  # noqa: E402
    FieldMapping,
    Product,
    ProductList,
    ProductListItem,
    ProductMapping,
    SmarthubUser,
    Supplier,
    UserSupplierCredential,
)


TABLES_IN_IMPORT_ORDER: list[type[SQLModel]] = [
    Supplier,
    SmarthubUser,
    Product,
    FieldMapping,
    UserSupplierCredential,
    ProductMapping,
    ProductList,
    ProductListItem,
]

TABLES_IN_DELETE_ORDER = [
    "productlistitem",
    "productlist",
    "productmapping",
    "usersuppliercredential",
    "fieldmapping",
    "product",
    "smarthubuser",
    "supplier",
]


def _sqlite_source_path() -> Path:
    raw = (os.environ.get("SOURCE_SQLITE_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_BACKEND_ROOT / p).resolve()
        return p
    return (_BACKEND_ROOT / "procurement.db").resolve()


def _target_database_url() -> str:
    url = (os.environ.get("TARGET_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "Chýba TARGET_DATABASE_URL / DATABASE_URL. Nastav PostgreSQL URL (Neon)."
        )
    return url


def _wipe_target(session: Session, is_postgres: bool) -> None:
    if is_postgres:
        joined = ", ".join(TABLES_IN_DELETE_ORDER)
        session.exec(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
        session.commit()
        return
    for name in TABLES_IN_DELETE_ORDER:
        session.exec(text(f"DELETE FROM {name}"))
    session.commit()


def _copy_table(src: Session, dst: Session, model: type[SQLModel]) -> int:
    rows = src.exec(select(model)).all()
    for row in rows:
        dst.add(model(**row.model_dump()))
    dst.flush()
    return len(rows)


def _sync_postgres_sequences(session: Session) -> None:
    for table in TABLES_IN_DELETE_ORDER:
        session.exec(
            text(
                "SELECT setval("
                "pg_get_serial_sequence(:table_name, 'id'), "
                "COALESCE((SELECT MAX(id) FROM " + table + "), 1), "
                "true)"
            ),
            {"table_name": table},
        )
    session.commit()


def main() -> None:
    source_path = _sqlite_source_path()
    if not source_path.is_file():
        raise SystemExit(f"SQLite súbor neexistuje: {source_path}")

    target_url = _target_database_url()
    if target_url.startswith("sqlite"):
        raise SystemExit(
            "TARGET_DATABASE_URL/DATABASE_URL smeruje na SQLite. Pre migráciu do Neon použi Postgres URL."
        )

    source_engine = create_engine(
        f"sqlite:///{source_path.as_posix()}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    target_engine = create_engine(target_url, echo=False, pool_pre_ping=True)

    print(f"Source SQLite: {source_path}")
    print(f"Target DB: {target_url.split('@')[-1]}")

    SQLModel.metadata.create_all(target_engine)

    copied_counts: dict[str, int] = {}
    with Session(source_engine) as src, Session(target_engine) as dst:
        _wipe_target(dst, is_postgres=True)
        for model in TABLES_IN_IMPORT_ORDER:
            count = _copy_table(src, dst, model)
            copied_counts[model.__tablename__] = count
        dst.commit()
        _sync_postgres_sequences(dst)

    print("Migrácia hotová.")
    for table_name, count in copied_counts.items():
        print(f"- {table_name}: {count}")


if __name__ == "__main__":
    main()

