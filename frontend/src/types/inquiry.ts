export type InquiryLineParsed = {
  row_index: number;
  raw_text: string;
  norma: string | null;
  surface: string | null;
  diameter: string | null;
  length: string | null;
  v_class: string | null;
  quantity: number | null;
  parse_error: string | null;
  catalog_warnings: string[] | null;
};

export type InquiryDraft = {
  savedAt: string;
  sourceFileName: string;
  rows: InquiryLineParsed[];
  parseTaskId?: string;
  selectedSupplierIds?: number[];
};

export const INQUIRY_DRAFT_STORAGE_KEY = "smarthub_inquiry_draft_v1";

import {
  inquiryRequiredFields,
  type InquiryFilterField,
} from "@/lib/inquiry-norm-rules";

export { inquiryRequiredFields, normRequiresLength, normRequiresVClass } from "@/lib/inquiry-norm-rules";
export type { InquiryFilterField } from "@/lib/inquiry-norm-rules";

export function inquiryMissingFields(row: InquiryLineParsed): InquiryFilterField[] {
  const required = inquiryRequiredFields(row.norma, row.raw_text);
  const missing: InquiryFilterField[] = [];
  for (const field of required) {
    if (field === "quantity") {
      if (row.quantity == null || row.quantity <= 0) missing.push(field);
    } else if (!String(row[field] ?? "").trim()) {
      missing.push(field);
    }
  }
  return missing;
}

export function inquiryRowIsValid(row: InquiryLineParsed): boolean {
  return (
    !row.parse_error &&
    inquiryMissingFields(row).length === 0 &&
    !(row.catalog_warnings && row.catalog_warnings.length > 0)
  );
}

export function normalizeInquiryRowFromApi(raw: Record<string, unknown>): InquiryLineParsed {
  return {
    row_index: Number(raw.row_index ?? 0),
    raw_text: String(raw.raw_text ?? ""),
    norma:
      (raw.norma as string | null) ??
      (raw.norm as string | null) ??
      (raw.leading_standard as string | null) ??
      null,
    surface:
      (raw.surface as string | null) ?? (raw.material as string | null) ?? null,
    diameter: (raw.diameter as string | null) ?? null,
    length: (raw.length as string | null) ?? null,
    v_class:
      (raw.v_class as string | null) ??
      (raw.class as string | null) ??
      null,
    quantity:
      raw.quantity === null || raw.quantity === undefined
        ? null
        : Number(raw.quantity),
    parse_error: (raw.parse_error as string | null) ?? null,
    catalog_warnings: Array.isArray(raw.catalog_warnings)
      ? (raw.catalog_warnings as string[])
      : null,
  };
}

export type InquiryScrapedOffer = {
  supplier_id: number;
  supplier_name: string;
  supplier_code: string;
  logo_url: string | null;
  supplier_product_url: string | null;
  price_eur: number | null;
  stock: number | null;
  error: string | null;
  logged_in: boolean | null;
};

export type InquiryLineRunResult = {
  row_index: number;
  raw_text: string;
  quantity: number | null;
  norma: string | null;
  surface: string | null;
  diameter: string | null;
  length: string | null;
  v_class: string | null;
  product_id: number | null;
  internal_code: string | null;
  status: "ok" | "no_product" | "no_mapping" | "no_price" | "error";
  no_stock: boolean;
  best_offer: InquiryScrapedOffer | null;
  offers: InquiryScrapedOffer[];
  line_total_eur: number | null;
  error: string | null;
};

export type InquiryRunTaskResult = {
  rows: InquiryLineRunResult[];
  source_filename: string;
  supplier_ids: number[];
  total_rows: number;
  rows_with_offer: number;
  rows_no_stock: number;
  total_eur: number | null;
};

export function normalizeInquiryRunResult(raw: Record<string, unknown>): InquiryRunTaskResult {
  const rowsRaw = Array.isArray(raw.rows) ? raw.rows : [];
  return {
    rows: rowsRaw.map((r) => normalizeInquiryLineRunResult(r as Record<string, unknown>)),
    source_filename: String(raw.source_filename ?? ""),
    supplier_ids: Array.isArray(raw.supplier_ids)
      ? (raw.supplier_ids as number[]).filter((id) => typeof id === "number")
      : [],
    total_rows: Number(raw.total_rows ?? rowsRaw.length),
    rows_with_offer: Number(raw.rows_with_offer ?? 0),
    rows_no_stock: Number(raw.rows_no_stock ?? 0),
    total_eur:
      raw.total_eur === null || raw.total_eur === undefined
        ? null
        : Number(raw.total_eur),
  };
}

function normalizeInquiryLineRunResult(raw: Record<string, unknown>): InquiryLineRunResult {
  const offersRaw = Array.isArray(raw.offers) ? raw.offers : [];
  const bestRaw = raw.best_offer as Record<string, unknown> | null | undefined;
  return {
    row_index: Number(raw.row_index ?? 0),
    raw_text: String(raw.raw_text ?? ""),
    quantity:
      raw.quantity === null || raw.quantity === undefined
        ? null
        : Number(raw.quantity),
    norma: (raw.norma as string | null) ?? null,
    surface: (raw.surface as string | null) ?? null,
    diameter: (raw.diameter as string | null) ?? null,
    length: (raw.length as string | null) ?? null,
    v_class: (raw.v_class as string | null) ?? null,
    product_id:
      raw.product_id === null || raw.product_id === undefined
        ? null
        : Number(raw.product_id),
    internal_code: (raw.internal_code as string | null) ?? null,
    status: (raw.status as InquiryLineRunResult["status"]) ?? "error",
    no_stock: Boolean(raw.no_stock),
    best_offer: bestRaw ? normalizeInquiryScrapedOffer(bestRaw) : null,
    offers: offersRaw.map((o) => normalizeInquiryScrapedOffer(o as Record<string, unknown>)),
    line_total_eur:
      raw.line_total_eur === null || raw.line_total_eur === undefined
        ? null
        : Number(raw.line_total_eur),
    error: (raw.error as string | null) ?? null,
  };
}

function normalizeInquiryScrapedOffer(raw: Record<string, unknown>): InquiryScrapedOffer {
  return {
    supplier_id: Number(raw.supplier_id ?? 0),
    supplier_name: String(raw.supplier_name ?? ""),
    supplier_code: String(raw.supplier_code ?? ""),
    logo_url: (raw.logo_url as string | null) ?? null,
    supplier_product_url: (raw.supplier_product_url as string | null) ?? null,
    price_eur:
      raw.price_eur === null || raw.price_eur === undefined
        ? null
        : Number(raw.price_eur),
    stock:
      raw.stock === null || raw.stock === undefined ? null : Number(raw.stock),
    error: (raw.error as string | null) ?? null,
    logged_in:
      typeof raw.logged_in === "boolean" ? raw.logged_in : null,
  };
}
