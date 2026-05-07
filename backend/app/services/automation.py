from app.schemas.common import ProductSearchFilters, SupplierOffer


class AutomationEngine:
    """
    Placeholder service for Playwright automation.
    Replace with real login/search/cart flows per supplier.
    """

    async def fetch_supplier_offers(
        self, supplier_name: str, filters: ProductSearchFilters
    ) -> list[SupplierOffer]:
        # TODO: Implement Playwright scripts for each supplier portal.
        return [
            SupplierOffer(
                supplier=supplier_name,
                price_eur=0.41,
                stock=450,
                supplier_id=None,
            ),
        ]

    async def add_to_supplier_cart(self, supplier_name: str, supplier_code: str) -> bool:
        # TODO: Implement supplier-specific "add to cart" automation.
        return True
