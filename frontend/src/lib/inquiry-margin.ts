/** Rovnaký výpočet ako backend `selling_unit_price` — marža v % navrch nákupnej ceny. */

export type InquiryPriceOffer = {
  price_eur?: number | null;
  price_unit?: string | null;
  pack_quantity?: number | null;
  supplier_name?: string | null;
  supplier_id?: number;
  error?: string | null;
};

export function parseMarginPercentInput(value: string): number | null {
  const trimmed = value.trim().replace(",", ".");
  if (!trimmed) return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}

export function applyMarginPercent(amount: number, marginPercent: number): number {
  return Math.round(amount * (1 + marginPercent / 100) * 10000) / 10000;
}

export function effectiveRowMarginPercent(
  rowIndex: number,
  globalMarginInput: string,
  rowMargins: Record<number, string>,
): number | null {
  const rowVal = parseMarginPercentInput(rowMargins[rowIndex] ?? "");
  if (rowVal !== null) return rowVal;
  return parseMarginPercentInput(globalMarginInput);
}

function supplierDefaultsPer100(supplierName: string | null | undefined): boolean {
  const name = (supplierName || "").toLowerCase();
  if (!name) return true;
  if (name.includes("bmkco") || name.includes("bmk")) return false;
  return true;
}

/** Celková cena riadku — rovnaká logika ako backend `inquiry_line_total_eur`. */
export function inquiryLineTotalEur(
  priceEur: number,
  quantity: number | null | undefined,
  offer: Pick<InquiryPriceOffer, "price_unit" | "pack_quantity" | "supplier_name">,
): number {
  const qty = Math.max(1, Math.round(quantity ?? 1));
  const unit = (offer.price_unit || "").trim().toLowerCase();

  if (unit === "per_1_ks") {
    return Math.round(priceEur * qty * 10000) / 10000;
  }

  if (unit === "per_sks") {
    const pack = Math.max(1, Math.round(offer.pack_quantity ?? 1));
    const packages = Math.ceil(qty / pack);
    return Math.round(priceEur * packages * 10000) / 10000;
  }

  if (unit === "per_100_ks" || unit === "100" || (!unit && supplierDefaultsPer100(offer.supplier_name))) {
    return Math.round(((priceEur * qty) / 100) * 10000) / 10000;
  }

  return Math.round(((priceEur * qty) / 100) * 10000) / 10000;
}

export function isSelectableInquiryOffer(offer: InquiryPriceOffer): boolean {
  return !offer.error && offer.price_eur != null && offer.price_eur > 0;
}

export function resolveActiveOffer<T extends InquiryPriceOffer>(
  row: { best_offer: T | null; offers: T[] },
  selectedSupplierId: number | null | undefined,
): T | null {
  if (selectedSupplierId != null) {
    const picked = row.offers.find((o) => o.supplier_id === selectedSupplierId);
    if (picked && isSelectableInquiryOffer(picked)) return picked;
  }
  return row.best_offer;
}
