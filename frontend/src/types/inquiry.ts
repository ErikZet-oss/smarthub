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
