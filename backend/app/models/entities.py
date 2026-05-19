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
    # Indexy na filtre — bez nich SELECT DISTINCT + WHERE x = ? skenuje celú tabuľku
    # (na ~30k produktoch v Postgrese to znamená 100–300 ms na každý filter).
    norma: Optional[str] = Field(default=None, index=True)
    diameter: Optional[str] = Field(default=None, index=True)
    length: Optional[str] = Field(default=None, index=True)
    surface: Optional[str] = Field(default=None, index=True)
    v_class: Optional[str] = Field(default=None, index=True)
    y_money_name: Optional[str] = Field(default=None, index=True)
    image_filename: Optional[str] = None


class ProductMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # Bez indexu na product_id musí každý dotaz cez join skenovať celú tabuľku
    # mappingov — N+1 v /products/search je preto najmä waiting na IO.
    supplier_id: int = Field(foreign_key="supplier.id", index=True)
    product_id: int = Field(foreign_key="product.id", index=True)
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


class CompanySettings(SQLModel, table=True):
    """Singleton (id=1): údaje našej firmy na ponukách a v PDF."""

    id: int = Field(default=1, primary_key=True)
    company_name: str = ""
    street: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = "Slovensko"
    ico: Optional[str] = None
    dic: Optional[str] = None
    ic_dph: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    web: Optional[str] = None
    iban: Optional[str] = None
    bank_name: Optional[str] = None
    logo_path: Optional[str] = None
    pdf_accent_color: Optional[str] = "#0284c7"
    offer_footer_note: Optional[str] = None
    # bcrypt hash — heslo na odomknutie sekcií Dodávatelia / Párovanie / Dev pre ne-admin účty.
    sections_unlock_password_hash: Optional[str] = None


class Offer(SQLModel, table=True):
    """Cenová ponuka pre klienta (per-user)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="smarthubuser.id", index=True)
    offer_number: str = Field(index=True)
    title: Optional[str] = None
    status: str = Field(default="draft", index=True)
    valid_until: Optional[datetime] = None
    client_name: str = ""
    client_street: Optional[str] = None
    client_city: Optional[str] = None
    client_zip: Optional[str] = None
    client_country: Optional[str] = None
    client_ico: Optional[str] = None
    client_dic: Optional[str] = None
    client_ic_dph: Optional[str] = None
    client_contact: Optional[str] = None
    client_email: Optional[str] = None
    client_phone: Optional[str] = None
    notes_client: Optional[str] = None
    notes_internal: Optional[str] = None
    default_margin_percent: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OfferLine(SQLModel, table=True):
    """Riadok ponuky (manuálny alebo neskôr z katalógu)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    offer_id: int = Field(foreign_key="offer.id", index=True)
    position: int = Field(default=0, index=True)
    description: str = ""
    quantity: float = Field(default=1.0)
    unit: str = Field(default="ks")
    unit_price_eur: float = Field(default=0.0)
    discount_percent: float = Field(default=0.0)
    purchase_unit_price_eur: Optional[float] = None
    margin_percent: float = Field(default=0.0)
    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id")
    supplier_name: Optional[str] = None
    supplier_code: Optional[str] = None
    product_id: Optional[int] = Field(default=None, foreign_key="product.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
