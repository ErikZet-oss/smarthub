from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Supplier(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    shop_url: str
    username: str
    password: str
    is_connected: bool = True
    # Názov stĺpca v importnom Exceli (napr. "Fabory kód"), kde je kód daného dodávateľa.
    code_column: Optional[str] = None
    # JSON konfigurácia pre Playwright (selektory košíka) — pozri CartAutomationConfig.
    cart_config_json: Optional[str] = None
    # Basename súboru v data/supplier_logos (napr. "3.png").
    logo_path: Optional[str] = None
    # Suma v EUR: pod touto hranicou sa v UI košíka zvýrazní tlačidlo „iná“ farba (doprava zdarma).
    free_shipping_threshold_eur: Optional[float] = None
    # Poradie v zoznamoch (vyhľadávanie, košík, dodávatelia) — nižšie číslo = vyššie v zozname.
    sort_order: int = 0


class SmarthubUser(SQLModel, table=True):
    """Používateľ webovej appky (admin alebo pobočka)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    is_admin: bool = False
    display_label: Optional[str] = None


class UserSupplierCredential(SQLModel, table=True):
    """Prihlasovacie údaje do B2B e-shopu pre konkrétneho používateľa / pobočku."""

    __table_args__ = (
        UniqueConstraint("user_id", "supplier_id", name="uq_usersuppliercred_user_sup"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="smarthubuser.id")
    supplier_id: int = Field(foreign_key="supplier.id")
    username: str = ""
    password: str = ""


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    internal_code: str = Field(index=True, unique=True)
    norma: Optional[str] = None
    diameter: Optional[str] = None
    length: Optional[str] = None
    surface: Optional[str] = None
    v_class: Optional[str] = None
    y_money_name: Optional[str] = None
    image_filename: Optional[str] = None


class ProductMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="supplier.id")
    product_id: int = Field(foreign_key="product.id")
    supplier_code: str = Field(index=True)


class FieldMapping(SQLModel, table=True):
    """Singleton (id=1): ktorý stĺpec Excelu zodpovedá ktorému internému poľu."""

    id: int = Field(default=1, primary_key=True)
    code_column: Optional[str] = None
    norma_column: Optional[str] = None
    surface_column: Optional[str] = None
    diameter_column: Optional[str] = None
    length_column: Optional[str] = None
    v_class_column: Optional[str] = None
    y_money_name_column: Optional[str] = None
    image_filename_column: Optional[str] = None


class ProductList(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="smarthubuser.id", index=True)
    name: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProductListItem(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("list_id", "product_id", name="uq_productlistitem_list_product"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="productlist.id", index=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
