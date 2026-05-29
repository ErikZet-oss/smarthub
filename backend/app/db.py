import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    # Predvolená lokálna DB (backend/procurement.db). V produkcii môžeš namiesto
    # DATABASE_URL použiť SMARTHUB_DB_PATH na persistent disk.
    _db_path_raw = (os.environ.get("SMARTHUB_DB_PATH") or "").strip()
    if _db_path_raw:
        _candidate = Path(_db_path_raw).expanduser()
        if not _candidate.is_absolute():
            _candidate = (_BACKEND_ROOT / _candidate).resolve()
        DATABASE_FILE = _candidate
    else:
        DATABASE_FILE = _BACKEND_ROOT / "procurement.db"
    DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"
else:
    DATABASE_FILE = Path("<external-db>")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
if IS_SQLITE:
    engine = create_engine(
        DATABASE_URL, echo=False, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def _table_columns(table: str) -> set[str]:
    try:
        insp = inspect(engine)
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    if column in _table_columns(table):
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def migrate_schema() -> None:
    """Pridá chýbajúce stĺpce (SQLite aj PostgreSQL — create_all ich nepridá)."""
    if IS_SQLITE:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(supplier)"))
            columns = {row[1] for row in result}
            if "code_column" not in columns:
                conn.execute(text("ALTER TABLE supplier ADD COLUMN code_column VARCHAR"))
                conn.commit()
            result2 = conn.execute(text("PRAGMA table_info(supplier)"))
            columns2 = {row[1] for row in result2}
            if "cart_config_json" not in columns2:
                conn.execute(text("ALTER TABLE supplier ADD COLUMN cart_config_json VARCHAR"))
                conn.commit()
            result3 = conn.execute(text("PRAGMA table_info(supplier)"))
            columns3 = {row[1] for row in result3}
            if "logo_path" not in columns3:
                conn.execute(text("ALTER TABLE supplier ADD COLUMN logo_path VARCHAR"))
                conn.commit()
            result4 = conn.execute(text("PRAGMA table_info(supplier)"))
            columns4 = {row[1] for row in result4}
            if "free_shipping_threshold_eur" not in columns4:
                conn.execute(
                    text("ALTER TABLE supplier ADD COLUMN free_shipping_threshold_eur REAL")
                )
                conn.commit()
            result_so = conn.execute(text("PRAGMA table_info(supplier)"))
            cols_so = {row[1] for row in result_so}
            if "sort_order" not in cols_so:
                conn.execute(text("ALTER TABLE supplier ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.commit()
            prod_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(product)"))}
            if "v_class" not in prod_cols:
                conn.execute(text("ALTER TABLE product ADD COLUMN v_class VARCHAR"))
                conn.commit()
            prod_cols2 = {row[1] for row in conn.execute(text("PRAGMA table_info(product)"))}
            if "y_money_name" not in prod_cols2:
                conn.execute(text("ALTER TABLE product ADD COLUMN y_money_name VARCHAR"))
                conn.commit()
            prod_cols3 = {row[1] for row in conn.execute(text("PRAGMA table_info(product)"))}
            if "image_filename" not in prod_cols3:
                conn.execute(text("ALTER TABLE product ADD COLUMN image_filename VARCHAR"))
                conn.commit()

            fm_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(fieldmapping)"))}
            if "v_class_column" not in fm_cols:
                conn.execute(text("ALTER TABLE fieldmapping ADD COLUMN v_class_column VARCHAR"))
                conn.commit()
            fm_cols2 = {row[1] for row in conn.execute(text("PRAGMA table_info(fieldmapping)"))}
            if "y_money_name_column" not in fm_cols2:
                conn.execute(
                    text("ALTER TABLE fieldmapping ADD COLUMN y_money_name_column VARCHAR")
                )
                conn.commit()
            fm_cols3 = {row[1] for row in conn.execute(text("PRAGMA table_info(fieldmapping)"))}
            if "image_filename_column" not in fm_cols3:
                conn.execute(
                    text("ALTER TABLE fieldmapping ADD COLUMN image_filename_column VARCHAR")
                )
                conn.commit()

    _add_column_if_missing("offer", "default_margin_percent", "REAL DEFAULT 0")
    for col, ddl in (
        ("purchase_unit_price_eur", "REAL"),
        ("margin_percent", "REAL DEFAULT 0"),
        ("supplier_id", "INTEGER"),
        ("supplier_name", "VARCHAR"),
        ("supplier_code", "VARCHAR"),
    ):
        _add_column_if_missing("offerline", col, ddl)

    _add_column_if_missing(
        "companysettings",
        "sections_unlock_password_hash",
        "VARCHAR",
    )

    _ensure_search_indexes()


def _create_index_if_missing(
    table: str, index_name: str, column: str
) -> None:
    """SQLite + Postgres: idempotentné CREATE INDEX IF NOT EXISTS."""
    try:
        with engine.begin() as conn:
            conn.execute(
                text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ("{column}")')
            )
    except Exception:
        # Niektoré staršie engines (napr. SQLAlchemy bez schema podpory) — radšej ignoruj
        # ako spadnúť pri štarte. Bez indexu beží všetko, len pomalšie.
        pass


def _ensure_search_indexes() -> None:
    """Indexy pre filtre vo vyhľadávaní (SELECT DISTINCT + WHERE).

    Bez indexov sa každý filter (norma, diameter, length, surface, v_class, y_money_name)
    musí čítať celým seq scanom. Pri ~30k produktoch v Postgrese to znamená 100–300 ms
    per query, čo sa pri 6 filtroch + jednom search-i pekne nasčítava.
    """
    for col in (
        "norma",
        "diameter",
        "length",
        "surface",
        "v_class",
        "y_money_name",
    ):
        _create_index_if_missing("product", f"ix_product_{col}", col)
    # ProductMapping — kvôli rýchlemu načítaniu mappings pre product_id IN (...)
    _create_index_if_missing("productmapping", "ix_productmapping_product_id", "product_id")
    _create_index_if_missing("productmapping", "ix_productmapping_supplier_id", "supplier_id")
    _create_index_if_missing(
        "competitorproductmapping", "ix_competitorproductmapping_product_id", "product_id"
    )
    _create_index_if_missing(
        "competitorproductmapping", "ix_competitorproductmapping_competitor_id", "competitor_id"
    )


def migrate_sqlite_schema() -> None:
    """Spätná kompatibilita — volá univerzálnu migráciu."""
    migrate_schema()


def get_session():
    with Session(engine) as session:
        yield session
