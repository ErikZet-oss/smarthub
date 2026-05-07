from typing import Optional

from pydantic import BaseModel


class ProductSearchFilters(BaseModel):
    norma: Optional[str] = None
    surface: Optional[str] = None
    diameter: Optional[str] = None
    length: Optional[str] = None
    v_class: Optional[str] = None
    y_money_name: Optional[str] = None
    code: Optional[str] = None
    limit: Optional[int] = 50
    # Ak True, search volá Playwright/HTTP pre každú ponuku (veľmi pomalé). Predvolene len DB + ceny po rozbalení riadku.
    prefetch_live_prices: bool = False


class SupplierOffer(BaseModel):
    supplier: str
    price_eur: float
    stock: int
    supplier_code: Optional[str] = None
    logo_url: Optional[str] = None
    supplier_id: Optional[int] = None


class ProductComparison(BaseModel):
    internal_code: str
    norma: Optional[str]
    diameter: Optional[str]
    length: Optional[str]
    surface: Optional[str] = None
    v_class: Optional[str] = None
    y_money_name: Optional[str] = None
    image_filename: Optional[str] = None
    offers: list[SupplierOffer]
