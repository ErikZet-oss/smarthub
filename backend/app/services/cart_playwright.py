"""Spätná kompatibilita — logika je v `scraper_service.ScraperService`."""

from __future__ import annotations

from app.services.scraper_service import ScraperConfig as CartAutomationConfig
from app.services.scraper_service import ScraperService
from app.models.entities import Supplier


async def add_to_cart_with_playwright(
    supplier: Supplier,
    supplier_code: str,
    config: CartAutomationConfig,
) -> None:
    await ScraperService.add_to_cart(supplier, supplier_code, 1, config)
